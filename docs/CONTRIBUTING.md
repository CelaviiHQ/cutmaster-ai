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

## Two plugins, one repo

This repo ships **two entirely separate "plugins"** that share no files and follow different install paths. Confusing them is the #1 documentation bug we've hit — call them by their full names in commits and PRs.

| # | Name | Where source lives | Built by | Installs to |
|---|---|---|---|---|
| 1 | **Claude Code plugin** | [.claude-plugin/plugin.json](../.claude-plugin/plugin.json) + top-level `skills/` / `agents/` / `hooks/` + `.mcp.json` | [scripts/build-plugin.sh](../scripts/build-plugin.sh) → `.zip` | `claude plugin install celavii-resolve-plugin.zip` (Claude Code CLI) |
| 2 | **Resolve Workflow Integration plugin** | [apps/resolve-plugin/](../apps/resolve-plugin/) (placeholder — being rebuilt in v3-6) | Future `scripts/package-resolve-plugin.sh` | `/Library/Application Support/Blackmagic Design/DaVinci Resolve/Workflow Integration Plugins/` (macOS, sudo) |

When you say "plugin" in a commit, PR title, or doc, prefix it: `Claude Code plugin:` or `Resolve plugin:`.

## Working on the React panel (`apps/panel/`)

The panel is a Vite/React app that lives in [apps/panel/](../apps/panel/) and is served by the FastAPI backend at `celavii-resolve-panel` on port 8765.

```bash
# First run — install Node deps
cd apps/panel && npm install

# Dev server with HMR (proxies /cutmaster/* and /ping to the backend)
npm run dev                       # → http://localhost:5173

# Production build + ship dist/ into the Python package's static dir
npm run build                     # writes apps/panel/dist/
# postbuild script then copies dist → src/celavii_resolve/http/static/
```

**Architecture:**
- Entry: [apps/panel/src/main.tsx](../apps/panel/src/main.tsx) → [App.tsx](../apps/panel/src/App.tsx)
- Screens: [apps/panel/src/screens/](../apps/panel/src/screens/) — one file per step (Preset / Analyze / Configure / Review) + `TokensGate.tsx` (v3-0 gate)
- Styles: [apps/panel/src/design-tokens.css](../apps/panel/src/design-tokens.css) (v3 tokens) + [styles.css](../apps/panel/src/styles.css) (base)
- SSE hook: [apps/panel/src/useSSE.ts](../apps/panel/src/useSSE.ts)
- HTTP client: [apps/panel/src/api.ts](../apps/panel/src/api.ts)

To smoke-test the design tokens and capability checks, browse to `http://127.0.0.1:8765/?gate=tokens`. That's the v3-0 go/no-go page.

## Working on the Resolve Workflow Integration plugin (`apps/resolve-plugin/`)

Being rebuilt in v3-6. Don't install the current files — they will not load inside Resolve. See [apps/resolve-plugin/README.md](../apps/resolve-plugin/README.md) for the real format and the reference sample plugin path.

When v3-6 lands, the plugin will be a thin Electron app: `manifest.xml` + `package.json` + a ~25-line `main.js` that opens a `BrowserWindow` at `http://127.0.0.1:8765/`. No Resolve API calls from the plugin itself — all Resolve interaction goes through the Python backend.

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
