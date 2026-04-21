# CLAUDE.md — Celavii-Resolve Developer Guide

## Project Overview

Celavii-Resolve is a maximum-control MCP server for DaVinci Resolve Studio.
It provides ~280 modular tools covering the complete Resolve Scripting API,
compound workflow tools, AI-enhanced features, and Claude Code skills/agents.

## Architecture

```
src/cutmaster_ai/
├── config.py         FastMCP singleton, constants, optional AI clients
├── resolve.py        Connection management, _boilerplate(), media pool helpers
├── errors.py         Exception hierarchy, @safe_resolve_call decorator
├── constants.py      Marker colors, clip colors, track types, pages, codecs
├── resources.py      MCP Resources (resolve://, project://, timeline://)
├── tools/            Modular tool files — one per domain
├── workflows/        Compound workflow tools (multi-step operations)
├── intelligence/     Stateless LLM tools (Gemini vision, color assist, timeline critique) + shared llm.py dispatch
├── cutmaster/        CutMaster AI product — 6 subpackages:
│   ├── core/            pipeline, director, execute, state, snapshot
│   ├── stt/             base + deepgram + gemini providers, speakers, per-clip
│   ├── analysis/        auto_detect, marker_agent, scrubber, tightener, captions, themes
│   ├── media/           frame_math, time_mapping, vfr, ffmpeg_audio, formats
│   ├── resolve_ops/     source_mapper, subclips, assembled, segments
│   └── data/            presets, excludes
├── http/             FastAPI backend for the CutMaster React panel (optional)
└── utils/            Platform detection, path safety, serialisation
```

## Claude Code integration: `.claude/` vs top-level

Two distinct roles, by design:

| Location | Role | Consumer |
|---|---|---|
| `.claude/skills/`, `.claude/settings.json` | **Dev-only** — loaded when you open this repo in Claude Code to work on it | Contributors |
| `agents/`, `skills/`, `hooks/` (top-level) | **Plugin distribution** — shipped when users install the Claude Code plugin (see `.claude-plugin/plugin.json`) | End users |

`.claude/agents/` is intentionally **absent** — the colorist/editor/etc. agents are for end-user video work, not Python development. If you need a dev-facing agent, add it to `.claude/agents/` (not the top-level `agents/`).

## Three consumers, one codebase

| Consumer | Transport | Entry point | Install |
|---|---|---|---|
| **MCP clients** (Claude Code / Desktop, Cursor) | MCP stdio | `cutmaster-ai` | `pip install cutmaster-ai` |
| **Resolve Workflow Integration panel** (React UI, in-Resolve webview) | HTTP on `127.0.0.1:8765` | `cutmaster-ai-panel` | `pip install 'cutmaster-ai[panel]'` |
| **Celavii Studio** (closed-source native macOS app — separate repo) | HTTP via bundled Python | `cutmaster-ai-panel` embedded | Web download from celavii.com |

Studio doesn't fork or patch this repo — it **pins a PyPI release** of `cutmaster-ai` and ships it alongside a private `cutmaster_studio_pro` wheel that registers extra capabilities via **entry points**. The OSS package keeps running exactly as it does today for everyone else; Studio just happens to be the most demanding consumer. See [SURFACE.md](SURFACE.md) for the versioned contract and [`src/cutmaster_ai/plugins.py`](src/cutmaster_ai/plugins.py) for discovery.

All three consumers call the same underlying Resolve logic. Every tool function under `cutmaster/` exposes a **plain Python function** (callable from `http/`) and a thin `@mcp.tool` wrapper (callable over MCP). When adding a new primitive that both consumers need, keep the business logic in the plain function and make the `@mcp.tool` a thin adapter.

## Responsibility model (pick the right bucket)

Every new feature fits into exactly one of these four. If it doesn't, it's two features.

| Bucket | Rule | Location |
|---|---|---|
| **Atomic Resolve op** | One function = one Resolve SDK call = one MCP tool. No logic. | `tools/` |
| **Deterministic compound** | Chains multiple `tools/` ops. No LLM. | `workflows/` |
| **Stateless LLM tool** | One MCP call → one LLM roundtrip → one answer. Exposed as `@mcp.tool`. | `intelligence/` |
| **Stateful AI product** | Owns state, multi-stage pipeline, optionally its own transport. | `cutmaster/` (and future products) |

## Adding a New Tool

1. Choose or create the appropriate file in `tools/`
2. Import `mcp` from `..config` and `safe_resolve_call` from `..errors`
3. Use this pattern:

```python
@mcp.tool
@safe_resolve_call
def cutmaster_your_tool(param: str, optional: int = 1) -> str:
    """Clear docstring explaining what this does."""
    resolve, project, media_pool = _boilerplate()
    # ... Resolve API calls ...
    return "Result message"
```

4. Add the import to `__init__.py` if it's a new module file

## Coding Conventions

- Python 3.11+ (no type stubs needed)
- All tools prefixed with `cutmaster_` (namespace)
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
uv run python -m cutmaster_ai

# Panel HTTP server (React Workflow Integration)
uv run cutmaster-ai-panel
# → http://127.0.0.1:8765/ping
# Override via CUTMASTER_PANEL_HOST / CUTMASTER_PANEL_PORT

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
