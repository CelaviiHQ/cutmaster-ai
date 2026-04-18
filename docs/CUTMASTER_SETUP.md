# CutMaster AI — Setup for End Users

This is the install guide for editors who want **CutMaster AI inside DaVinci Resolve**. It does not cover MCP (for that, see [SETUP.md](SETUP.md)) or code contribution (see [CONTRIBUTING.md](CONTRIBUTING.md)).

> **Status check:** the in-Resolve dock panel (via Workflow Integration plugin) is **not shipping yet** — it's scheduled for v3-6. Right now CutMaster AI runs as a local web app in your browser, sitting next to Resolve. Identical features, no plugin install required.

---

## What CutMaster does

Point it at a raw or assembled timeline; pick a preset (Podcast → YouTube, Vlog → Short, Tightener, Curated); click through 4 screens; get a new timeline with the cut assembled for you, ready to review and adjust inside Resolve.

- **Works on assembled cuts too** — Clip Hunter turns a finished YouTube edit into 3 TikTok shorts in one run
- **Speaker-aware** — multi-speaker transcripts stay attributed
- **Non-destructive** — every run creates a `_AI_Cut_N` timeline; your source is never touched
- **Snapshot-based safety** — auto-snapshots before building; rollback is instant

## Prerequisites

| Requirement | Details |
|---|---|
| **DaVinci Resolve Studio 21.x** (or 18.5+) | The $295 paid version. The free edition has no scripting API. Verify: `DaVinci Resolve → About`. |
| **External scripting enabled** | `DaVinci Resolve → Preferences → System → General → External scripting using` → **Local** (then Save + restart Resolve). |
| **Python 3.11 or 3.12** | 3.13+ has ABI issues with Resolve's fusionscript module. |
| **`uv`** (Python runner) | `brew install uv` on macOS, or `curl -LsSf https://astral.sh/uv/install.sh \| sh` on Linux, or `irm https://astral.sh/uv/install.ps1 \| iex` on Windows. |
| **`GEMINI_API_KEY`** | Required for the AI director. Free tier is fine. [Get one →](https://aistudio.google.com/apikey) |
| **Modern browser** | Chrome, Safari, Firefox, or Arc. (Once v3-6 ships, this is replaced by Resolve's own dock panel.) |

## Install — 5 minutes

### 1. Clone and set up Python

```bash
git clone https://github.com/CelaviiHQ/celavii-davinci-resolve-mcp.git
cd celavii-davinci-resolve-mcp
bash scripts/setup.sh --panel
```

The `--panel` flag installs the extra dependencies needed by CutMaster AI's backend.

### 2. Add your Gemini API key

```bash
cp .env.example .env
# Open .env in any text editor and set:
# GEMINI_API_KEY=your-key-here
```

### 3. Start Resolve and the backend

Order matters:

1. Open **DaVinci Resolve Studio** and load the project you want to cut
2. In a terminal, from the repo root:
   ```bash
   uv run celavii-resolve-panel
   ```
   You should see `Connected to DaVinci Resolve Studio 21.x.x`.
3. Keep that terminal open while you use CutMaster.

### 4. Open the panel

```bash
open http://127.0.0.1:8765/     # macOS — opens default browser
```

Or paste `http://127.0.0.1:8765/` into any browser. Ideally, position the browser window next to Resolve so you can see both.

## Using CutMaster — the 4-screen flow

1. **Preset** — pick what you're making (Clip Hunter for YouTube→Shorts, Tightener to clean a cut, Sequencing for raw → narrative, Curated for take selection)
2. **Analyze** — transcribes + detects themes (~1 min per 10 min of audio)
3. **Configure** — tweak: number of clips, target length, themes to prioritise, excludes, output format
4. **Review** — inspect the proposed cut, hit Build, and a new timeline appears in Resolve

Every run writes:
- A new timeline named `<source>_AI_Cut_<n>` in your current project
- A snapshot of the project state before building (auto-rollback available)
- A run record at `~/.celavii/cutmaster/<run_id>.json`

## If something breaks

| Symptom | Likely cause | Fix |
|---|---|---|
| Backend won't start — "Connected to… failed" | Resolve not running, or wrong edition | Open Resolve Studio; verify edition via `About` menu |
| Backend starts but panel shows `Backend: ✗` | Backend died; check your terminal for a stack trace | Restart `uv run celavii-resolve-panel`; report the trace |
| Analyze stage hangs at STT | No `GEMINI_API_KEY`, or quota exhausted | Check `.env`; fall back to Deepgram if you have a `DEEPGRAM_API_KEY` |
| Build succeeds but timeline not in Resolve | Resolve lost focus during Resolve API call | Refresh Resolve's timeline tab — it's there |
| "VFR detected" error at Analyze | Source is variable-frame-rate (mobile footage, screen recordings) | Transcode to CFR in Resolve first (right-click clip → `Clip Attributes`) |
| Panel looks broken / styling wrong | Stale `dist/` from an old build | `cd apps/panel && npm run build` to regenerate |

For anything not on this list, see the full [Troubleshooting](SETUP.md#9-troubleshooting) section in `SETUP.md`.

## What's coming (v3)

Track the UI overhaul in [Implementation/cutmaster_ai/v3/tracker.md](../Implementation/cutmaster_ai/v3/tracker.md):

- **v3-1** — Preset Pick screen revamp
- **v3-2** — Analyze screen polish + Cancel button
- **v3-3** — Configure screen per-preset split
- **v3-4** — Review screen reorg + "Open in Resolve" button
- **v3-6** — **DaVinci Resolve Workflow Integration plugin** — the panel will dock inside Resolve itself
- **v3-8** — Live progress feedback during LLM calls (stage events, elapsed counters, token-streaming indicator)

Until v3-6 ships, the browser tab is the canonical entry point.

## Deeper reading

- [CUTMASTER_ARCHITECTURE.md](CUTMASTER_ARCHITECTURE.md) — how the pipeline is structured internally
- [SETUP.md](SETUP.md) — MCP server setup (separate from CutMaster)
- [CONTRIBUTING.md](CONTRIBUTING.md) — working on the code
