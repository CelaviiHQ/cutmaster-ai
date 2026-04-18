# Contributing to Celavii-Resolve

Welcome — and thanks for the interest. This guide gets you from clone to first PR.

## First-time setup

```bash
git clone https://github.com/CelaviiHQ/celavii-davinci-resolve-mcp.git
cd celavii-davinci-resolve-mcp

# 0. Install uv (REQUIRED — the committed .mcp.json uses `uv run`)
brew install uv                                        # macOS
# OR: curl -LsSf https://astral.sh/uv/install.sh | sh  # Linux
# OR: irm https://astral.sh/uv/install.ps1 | iex       # Windows (PowerShell)

# 1. Python side
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[all,dev]"

# 2. Pre-commit hooks (MANDATORY — blocks /Users/ paths + secrets)
pre-commit install
pre-commit run --all-files   # baseline check

# 3. Optional: frontend panel
cd apps/panel && npm install && cd -
```

> **Why uv?** The committed `.mcp.json` invokes the MCP server as `uv run python -m celavii_resolve`. Without `uv` on PATH, Claude Code / Desktop can't start the server. This is intentional — `uv run` handles virtual-env activation automatically so contributors don't have to edit `.mcp.json` with machine-specific paths.

## Responsibility model — where does your feature go?

Every new feature fits exactly one bucket. If it doesn't, it's two features.

| Bucket | Rule | Location |
|---|---|---|
| **Atomic Resolve op** | One function = one Resolve SDK call = one MCP tool. No logic. | `src/celavii_resolve/tools/` |
| **Deterministic compound** | Chains multiple `tools/` ops. No LLM. | `src/celavii_resolve/workflows/` |
| **Stateless LLM tool** | One MCP call → one LLM roundtrip → one answer. | `src/celavii_resolve/intelligence/` |
| **Stateful AI product** | Owns state, multi-stage pipeline, optionally its own transport. | `src/celavii_resolve/cutmaster/` |

## Adding a new tool

1. Pick the bucket (see above).
2. Scaffold with the `add-tool` or `add-workflow` skill, or by hand:

```python
# src/celavii_resolve/tools/my_area.py
from ..config import mcp
from ..errors import safe_resolve_call
from ..resolve import _boilerplate

@mcp.tool
@safe_resolve_call
def celavii_your_tool(param: str, optional: int = 1) -> str:
    """Clear docstring explaining what this does."""
    resolve, project, media_pool = _boilerplate()
    # ... Resolve API calls ...
    return "Result message"
```

3. Register: add `from .tools import my_area` to `src/celavii_resolve/__init__.py`.
4. Add a test: `tests/test_my_area.py` (convention tests will enforce naming).

## Coding conventions

- **Python 3.11+.** Use `X | Y` instead of `Union[X, Y]`.
- **Namespace every tool** with `celavii_` prefix.
- **All tools return `str`** (error strings or success messages / JSON).
- **Private helpers prefix with `_`** (e.g. `_boilerplate`, `_find_bin`).
- **Guard None returns from Resolve:** `items = thing.GetClipList() or []`.
- **1-based indices** (Resolve convention) everywhere.
- **Don't use `tempfile.gettempdir()` for Resolve writes** — use `_resolve_safe_dir()`.

## Local-override pattern (never edit committed configs)

If you need a local variant of any committed config (e.g. `.mcp.json`), create a `.local.<ext>` sibling:

```
.mcp.json           ← committed, portable (uses ${CLAUDE_PROJECT_ROOT})
.mcp.local.json     ← gitignored, per-dev overrides
```

The pre-commit hook blocks `/Users/` and `/home/` paths from being committed — if you catch yourself editing a committed file to hardcode a path, stop and make a `.local.*` instead.

## Development in the open

This repo is public. Most work should be public from day one — that's the point of open source. For genuinely unannounced strategic work, use a private second remote:

```bash
git remote add private git@github.com:CelaviiHQ/celavii-resolve-private.git
git push private feature/unannounced-thing
# When ready, rebase onto public main
```

Scratch space lives in `Implementation/`, `docs/internal/`, `notes/`, `scratch/` — all gitignored.

## Running + testing

```bash
# MCP stdio server (Claude Code / Desktop)
python -m celavii_resolve

# Panel HTTP server (React Workflow Integration)
celavii-resolve-panel
# → http://127.0.0.1:8765/ping

# Full test suite
pytest tests/ -v

# Lint + format
ruff check src/ tests/ --fix
ruff format src/ tests/
```

## Commit message format

Conventional commits:

- `feat:` — new feature or tool
- `fix:` — bug fix
- `docs:` — documentation only
- `refactor:` — code restructure, no behaviour change
- `test:` — add or update tests
- `chore:` — maintenance, dependency updates
- `style:` — formatting only (ruff-format sweeps)

## Pull request flow

1. Branch from `main` — `git checkout -b feat/short-description`
2. Commit with conventional messages
3. `pre-commit run --all-files` locally
4. `pytest tests/ -v` green
5. Open PR, CODEOWNERS auto-assigns review

## Key gotchas (DaVinci Resolve API)

- All indices are **1-based** (timeline, track, node indices)
- Resolve returns **None** for empty lists — always `or []`
- `AddMarker` returns **False** silently on duplicate frame
- Clip markers use **frame offsets**; timeline markers use **seconds**
- Free edition has **no scripting API** — Studio only
- Python 3.13+ may have **ABI issues** with fusionscript

## Questions?

- Architecture questions: start at [ARCHITECTURE.md](ARCHITECTURE.md)
- CutMaster specifics: [CUTMASTER_ARCHITECTURE.md](CUTMASTER_ARCHITECTURE.md)
- Security concerns: [SECURITY.md](../SECURITY.md)
