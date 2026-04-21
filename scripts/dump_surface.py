#!/usr/bin/env python3
"""Dump the cutmaster-ai MCP tool surface to a deterministic JSON snapshot.

The snapshot is the machine-readable counterpart to SURFACE.md: it captures
every `@mcp.tool`'s name, description, input parameter schema, and output
schema. CI (`.github/workflows/ci.yml` → `surface` job) regenerates this at
PR time and fails if the snapshot diverges from `tests/surface_snapshot.json`
without a matching CHANGELOG entry under `## [Unreleased]`.

Regenerate after an intentional signature change:

    uv run python scripts/dump_surface.py > tests/surface_snapshot.json
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import cutmaster_ai  # noqa: F401, E402 — triggers tool registration
from cutmaster_ai.config import mcp  # noqa: E402


async def _build_snapshot() -> dict:
    tools = await mcp.list_tools()
    entries = [
        {
            "name": t.name,
            "description": (t.description or "").strip(),
            "parameters": t.parameters,
            "output_schema": t.output_schema,
        }
        for t in tools
    ]
    entries.sort(key=lambda e: e["name"])
    return {"tools": entries}


def main() -> None:
    snapshot = asyncio.run(_build_snapshot())
    json.dump(snapshot, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
