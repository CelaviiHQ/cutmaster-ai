"""Idempotent SQLite migration runner for the Panel app-state DB.

Applies every ``NNNN_*.sql`` file in this package in numeric order. Each
applied file is recorded in ``_cutmaster_schema_migrations`` so subsequent
boots skip it. Files are applied inside a transaction; a mid-file failure
rolls back that file only.

The runner is called from Panel startup. A missing DB file is created on
demand — callers own the path.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from importlib import resources
from pathlib import Path

log = logging.getLogger("cutmaster-ai.migrations")

_FILENAME_RE = re.compile(r"^(\d{4})_[A-Za-z0-9_]+\.sql$")
_PACKAGE = "cutmaster_ai.migrations"


def _ensure_bookkeeping(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        create table if not exists _cutmaster_schema_migrations (
            name text primary key,
            applied_at text not null default (datetime('now'))
        )
        """
    )
    conn.commit()


def _applied(conn: sqlite3.Connection) -> set[str]:
    cur = conn.execute("select name from _cutmaster_schema_migrations")
    return {row[0] for row in cur.fetchall()}


def _discover() -> list[tuple[int, str, str]]:
    """Return ``[(seq, filename, sql_text), ...]`` sorted by sequence."""
    out: list[tuple[int, str, str]] = []
    for entry in resources.files(_PACKAGE).iterdir():
        if not entry.is_file():
            continue
        name = entry.name
        m = _FILENAME_RE.match(name)
        if not m:
            continue
        out.append((int(m.group(1)), name, entry.read_text(encoding="utf-8")))
    out.sort(key=lambda row: row[0])
    return out


def apply_migrations(db_path: str | Path) -> list[str]:
    """Apply any un-applied migration files to ``db_path``.

    Creates the parent directory and DB file if missing. Returns the list
    of migration filenames newly applied in this call (empty when the DB
    is already up-to-date).
    """
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    newly_applied: list[str] = []
    conn = sqlite3.connect(path)
    try:
        _ensure_bookkeeping(conn)
        already = _applied(conn)
        for _seq, name, sql in _discover():
            if name in already:
                continue
            log.info("Applying migration: %s", name)
            try:
                conn.executescript(f"begin;\n{sql}\ncommit;")
            except Exception:
                conn.execute("rollback")
                log.exception("Migration %s failed; rolled back", name)
                raise
            conn.execute(
                "insert into _cutmaster_schema_migrations (name) values (?)",
                (name,),
            )
            conn.commit()
            newly_applied.append(name)
    finally:
        conn.close()
    return newly_applied
