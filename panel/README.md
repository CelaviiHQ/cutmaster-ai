# CutMaster AI panel

React UI for the CutMaster workflow, rendered inside DaVinci Resolve's
Workflow Integration dock (or any browser during development).

## Install

```bash
cd panel
npm install
```

## Develop

Runs Vite at `http://localhost:5173` with HTTP/SSE calls proxied to the
Python backend at `127.0.0.1:8765`:

```bash
# terminal 1 — Python backend
celavii-resolve-panel

# terminal 2 — Vite dev server
cd panel
npm run dev
```

Open `http://localhost:5173`. The panel makes requests to `/cutmaster/*`
— Vite proxies them to the Python server.

## Build for production

```bash
npm run build:install
```

This builds the panel AND copies `dist/` into
`src/celavii_resolve/http/static/` so the Python server serves it directly
at `http://127.0.0.1:8765/`.

Then start the backend only — no Vite needed in production:

```bash
celavii-resolve-panel
# open http://127.0.0.1:8765/
```

## Installing inside DaVinci Resolve

See [resolve-plugin/README.md](./resolve-plugin/README.md) for the
Workflow Integration manifest + install steps.

## Stack

- Vite + React 18 + TypeScript
- Native `fetch` for HTTP, native `EventSource` for SSE
- Zero runtime dependencies beyond React (intentional — smaller bundle
  ships inside Resolve's embedded browser)
