# CutMaster AI React Panel

Vite + React + TypeScript. The frontend half of CutMaster AI. Talks to the FastAPI backend ([`cutmaster-ai-panel`](../../src/cutmaster_ai/http/)) over HTTP + SSE on `127.0.0.1:8765`.

End-user install: see [docs/CUTMASTER_SETUP.md](../../docs/CUTMASTER_SETUP.md).
Contributor guide: see [docs/CONTRIBUTING.md — Working on the React panel](../../docs/CONTRIBUTING.md#working-on-the-react-panel-appspanel).

---

## Dev loop

```bash
# First run
npm install

# HMR dev server (Vite) — proxies /cutmaster/* and /ping to :8765
npm run dev                 # → http://localhost:5173

# Production build + ship to the Python package
npm run build               # → dist/ → src/cutmaster_ai/http/static/
```

The Python backend must be running separately (`uv run cutmaster-ai-panel`) for any `/cutmaster/*` call to succeed.

## File layout

```
apps/panel/src/
├── main.tsx                    Entry — mounts <App /> into #root
├── App.tsx                     Router + step state machine
├── design-tokens.css           v3-0 CSS tokens (surfaces, accent, spacing, typography)
├── styles.css                  Base styles + card tiers + button tiers
├── api.ts                      HTTP client for the backend
├── useSSE.ts                   Hook for /cutmaster/events/{run_id}
├── persist.ts                  localStorage resume state
├── types.ts                    Shared TypeScript types (TimelineMode, PresetKey, UserSettings, …)
└── screens/
    ├── PresetPickScreen.tsx    Step 1 — pick preset + timeline mode
    ├── AnalyzeScreen.tsx       Step 2 — live stage progress (SSE)
    ├── ConfigureScreen.tsx     Step 3 — per-preset settings
    ├── ReviewScreen.tsx        Step 4 — candidate cut + build
    └── TokensGate.tsx          v3-0 gate page (?gate=tokens)
```

## Design tokens (v3-0)

All new work should consume [design-tokens.css](src/design-tokens.css). Legacy aliases (`--bg`, `--panel`, `--accent`) are kept so pre-v3 CSS keeps working while each screen migrates. See the v3 tracker for per-screen migration progress.

| Category | Tokens |
|---|---|
| Surfaces | `--surface-0` (app bg) → `--surface-3` (hover) |
| Accent | `--accent-blue`, `--accent-blue-dim`, `--accent-blue-tint` |
| Status | `--ok`, `--warn`, `--err`, `--muted` |
| Text | `--text-primary`, `--text-secondary`, `--text-tertiary` |
| Spacing | `--s-1` (4px) … `--s-6` (32px) |
| Type | `--fs-1` (11) … `--fs-5` (18) |
| Borders | `--border-thin`, `--border-accent` |
| Radii | `--radius-sm`, `--radius-md`, `--radius-lg` |

Card tiers: `.card` (regular), `.card--primary` (raised + accent left-border + larger h2), `.card--advanced` (details/summary chevron, closed by default).
Button tiers: default (primary), `.secondary`, `.btn-ghost`, `.btn-danger`. All 32px tall.

## The v3-0 gate page

Routed at `?gate=tokens`, e.g. `http://127.0.0.1:8765/?gate=tokens`. Renders all three card tiers, all four button tiers, a surface swatch grid, and runs 5 webview capability checks (`CSS vars`, `:has()`, `@container`, `AbortController`, `EventSource`). Use it as a visual regression check after any design-token change.

## Postbuild pipeline

`npm run build` runs `tsc --noEmit && vite build`, then a postbuild script:

```bash
rm -rf ../../src/cutmaster_ai/http/static
cp -r dist ../../src/cutmaster_ai/http/static
touch ../../src/cutmaster_ai/http/static/.gitkeep
```

This is how the FastAPI backend gets the panel bundle to serve at `/`. The `.gitkeep` is important — without it, `git clean` wipes the directory.

## Testing inside DaVinci Resolve

Until v3-6 ships the Workflow Integration plugin, the only way to exercise the panel in Resolve's actual webview is to have Resolve running + open the panel in any browser window positioned next to Resolve. For automated webview gating, Playwright drives the browser side via MCP.

## Stack

- Vite + React 18 + TypeScript
- Native `fetch` for HTTP, native `EventSource` for SSE
- Zero runtime dependencies beyond React (intentional — smaller bundle ships inside Resolve's embedded browser once v3-6 lands)

## Conventions

- All new styles consume design tokens — no magic numbers
- Every async call shows a loading indicator (not just `disabled={true}`)
- Screens are single-responsibility — split into `screens/<name>/` subfolder when > 400 lines (see v3-3 tracker)
- Error states include a specific fix hint, not just the error text (see v3-7)
