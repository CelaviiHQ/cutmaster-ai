# CLAUDE.md — Celavii-Resolve Developer Guide

## Project Overview

Celavii-Resolve is a maximum-control MCP server for DaVinci Resolve Studio.
It provides ~280 modular tools covering the complete Resolve Scripting API,
compound workflow tools, AI-enhanced features, and Claude Code skills/agents.

## Architecture

```
src/celavii_resolve/
├── config.py         FastMCP singleton, constants, optional AI clients
├── resolve.py        Connection management, _boilerplate(), media pool helpers
├── errors.py         Exception hierarchy, @safe_resolve_call decorator
├── constants.py      Marker colors, clip colors, track types, pages, codecs
├── resources.py      MCP Resources (resolve://, project://, timeline://)
├── tools/            Modular tool files — one per domain
├── workflows/        Compound workflow tools (multi-step operations)
├── ai/               AI-powered tools (Gemini vision, color assist)
├── cutmaster/        CutMaster AI primitives (frame math, source mapping, ffmpeg audio, VFR, snapshot)
├── http/             FastAPI backend for the CutMaster React panel (optional)
└── utils/            Platform detection, path safety, serialisation
```

## Two consumers, one codebase

| Entry point | Transport | Consumer | Install |
|---|---|---|---|
| `celavii-resolve` | MCP stdio | Claude Code / Desktop | `pip install celavii-resolve` |
| `celavii-resolve-panel` | HTTP on `127.0.0.1:8765` | React Workflow Integration panel | `pip install 'celavii-resolve[panel]'` |

Both call the same underlying Resolve logic. Every tool function under `cutmaster/` exposes a **plain Python function** (callable from `http/`) and a thin `@mcp.tool` wrapper (callable over MCP). When adding a new primitive that both consumers need, keep the business logic in the plain function and make the `@mcp.tool` a thin adapter.

## Adding a New Tool

1. Choose or create the appropriate file in `tools/`
2. Import `mcp` from `..config` and `safe_resolve_call` from `..errors`
3. Use this pattern:

```python
@mcp.tool
@safe_resolve_call
def celavii_your_tool(param: str, optional: int = 1) -> str:
    """Clear docstring explaining what this does."""
    resolve, project, media_pool = _boilerplate()
    # ... Resolve API calls ...
    return "Result message"
```

4. Add the import to `__init__.py` if it's a new module file

## Coding Conventions

- Python 3.11+ (no type stubs needed)
- All tools prefixed with `celavii_` (namespace)
- All tools return `str` (not dicts) — error strings or success messages / JSON
- Private helpers prefixed with `_` (e.g. `_boilerplate`, `_find_bin`)
- Guard Resolve's None returns: `items = thing.GetClipList() or []`
- All indices are 1-based (Resolve convention)
- Never use `tempfile.gettempdir()` for paths Resolve writes to — use `_resolve_safe_dir()`
- Formatter: `ruff format`; Linter: `ruff check`

## Commit Messages

Use conventional format:
- `feat:` — new feature or tool
- `fix:` — bug fix
- `docs:` — documentation only
- `refactor:` — code restructure, no behaviour change
- `test:` — add or update tests
- `chore:` — maintenance, dependency updates

## Running

```bash
# MCP stdio server (Claude Code / Desktop)
uv run python -m celavii_resolve

# Panel HTTP server (React Workflow Integration)
uv run celavii-resolve-panel
# → http://127.0.0.1:8765/ping
# Override via CELAVII_PANEL_HOST / CELAVII_PANEL_PORT

# Tests
uv run pytest tests/ -v

# Lint + format
uv run ruff check src/ --fix
uv run ruff format src/
```

## Key Gotchas (DaVinci Resolve API)

- All indices are **1-based** (timeline, track, node indices)
- Resolve returns **None** for empty lists — always `or []`
- `AddMarker` returns **False** silently on duplicate frame
- Clip markers use **frame offsets**; timeline markers use **seconds**
- Free edition has **no scripting API** — Studio only
- Python 3.13+ may have **ABI issues** with fusionscript
