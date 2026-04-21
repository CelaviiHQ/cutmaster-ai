# DaVinci Resolve Workflow Integration plugin

> ⚠️ **Placeholder — being rebuilt in v3-6.**
> The files currently in this directory (`manifest.xml`, `index.html`) are **stubs** written against outdated public docs. They do **not** form a loadable plugin on Resolve 21. Do not attempt to install them — the plugin will not appear in the `Workspace → Workflow Integrations` menu.
>
> Track progress: [Implementation/cutmaster_ai/v3/tracker.md](../../Implementation/cutmaster_ai/v3/tracker.md) (phase v3-6).

## What this will be (when v3-6 ships)

A thin Electron-based Workflow Integration plugin whose only job is to open a `BrowserWindow` pointing at `http://127.0.0.1:8765/` — the panel backend. All Resolve interaction happens **server-side** through MCP + the FastAPI backend, not from the plugin itself.

## End-user install (once v3-6 lands)

See [docs/CUTMASTER_SETUP.md](../../docs/CUTMASTER_SETUP.md) — the canonical user-facing install guide.

## Plugin format reference (for contributors)

Resolve ships the canonical sample plugin on your machine at:

```
/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Workflow Integrations/Examples/SamplePlugin/
```

Read [`README.txt`](/Library/Application%20Support/Blackmagic%20Design/DaVinci%20Resolve/Developer/Workflow%20Integrations/README.txt) in that same `Developer/Workflow Integrations/` folder for the authoritative plugin spec.

Key facts (verified 2026-04-18 on Resolve 21.0.0b.20):

| Field | Value |
|---|---|
| Install path (macOS) | `/Library/Application Support/Blackmagic Design/DaVinci Resolve/Workflow Integration Plugins/` |
| Install path (Windows) | `%PROGRAMDATA%\Blackmagic Design\DaVinci Resolve\Support\Workflow Integration Plugins\` |
| Folder name convention | Reverse-DNS: `ai.cutmaster/` |
| Required files | `manifest.xml`, `package.json`, `main.js`, `index.html` |
| Electron version (Resolve 20.1+) | 36.3.2 — sandboxed + context-isolated |
| Dev-mode toggle | **None.** `resolve:SetDeveloperMode()` was removed. Plugins just load. |

## Fallback during development

Since the plugin is not yet functional, use the panel directly in a browser:

```bash
uv run cutmaster-ai-panel   # starts backend on :8765
open http://127.0.0.1:8765/     # opens the panel in your default browser
```

This is the same React bundle that will eventually load inside Resolve — identical UI, identical behaviour, just no docked window.
