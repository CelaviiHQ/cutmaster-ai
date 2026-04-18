# apps/ — Non-Python deliverables

The Python MCP server + panel backend live in [`src/celavii_resolve/`](../src/celavii_resolve/).
This directory holds the **two client-side artefacts** that ship alongside it.

| App | What | How it talks to the server |
|---|---|---|
| [`panel/`](panel/) | React + Vite app rendered inside DaVinci Resolve's Workflow Integration panel | HTTP/SSE to `celavii-resolve-panel` on `127.0.0.1:8765` |
| [`resolve-plugin/`](resolve-plugin/) | Workflow Integration plugin manifest + wrapper HTML that loads the panel bundle | Bundled with the panel build output |

## Typical build flow

```bash
# 1. Backend (from project root)
pip install -e '.[panel]'
celavii-resolve-panel   # starts on :8765

# 2. Frontend (from apps/panel/)
cd apps/panel
npm install
npm run build:install   # → writes dist/ into src/celavii_resolve/http/static/
```

Once `http/static/` is populated, the backend serves the built panel at
`http://127.0.0.1:8765/`. Dev mode: `npm run dev` proxies `/cutmaster/*`
and `/ping` back to the Python server.

## Resolve plugin packaging

`scripts/build-plugin.sh` assembles the plugin zip using the manifest in
[`resolve-plugin/`](resolve-plugin/) plus the built panel bundle.
