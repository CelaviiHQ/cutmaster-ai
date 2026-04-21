# apps/ — Non-Python deliverables

The Python MCP server + panel backend live in [`src/cutmaster_ai/`](../src/cutmaster_ai/).
This directory holds the **two client-side artefacts** that ship alongside it.

| App | What | How it talks to the server |
|---|---|---|
| [`panel/`](panel/) | React + Vite app that renders CutMaster AI. Runs in a browser today; will run inside Resolve once v3-6 ships. | HTTP/SSE to `cutmaster-ai-panel` on `127.0.0.1:8765` |
| [`resolve-plugin/`](resolve-plugin/) | **Placeholder** for the Resolve Workflow Integration plugin that will dock the panel inside Resolve. Not yet loadable — being rebuilt in v3-6. | n/a — plugin just opens a BrowserWindow pointed at the panel |

> **Don't confuse this with the Claude Code plugin.** `scripts/build-plugin.sh` builds the **Claude Code plugin** (skills/agents/hooks shipped to Claude Code users). It has nothing to do with `resolve-plugin/`. See [docs/CONTRIBUTING.md](../docs/CONTRIBUTING.md#two-plugins-one-repo).

## Typical build flow

```bash
# 1. Backend (from project root)
pip install -e '.[panel]'
cutmaster-ai-panel   # starts on :8765

# 2. Frontend (from apps/panel/)
cd apps/panel
npm install
npm run build:install   # → writes dist/ into src/cutmaster_ai/http/static/
```

Once `http/static/` is populated, the backend serves the built panel at
`http://127.0.0.1:8765/`. Dev mode: `npm run dev` proxies `/cutmaster/*`
and `/ping` back to the Python server.

## Resolve plugin packaging

Deferred to v3-6. The future packaging script will be `scripts/package-resolve-plugin.sh` (distinct from the Claude Code plugin's `scripts/build-plugin.sh`). Tracker: [Implementation/cutmaster_ai/v3/tracker.md](../Implementation/cutmaster_ai/v3/tracker.md).
