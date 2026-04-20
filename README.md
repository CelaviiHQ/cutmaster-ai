# Celavii-Resolve

A DaVinci Resolve toolkit with two faces: an **MCP server** (for AI clients like Claude Code / Desktop) and a **CutMaster AI panel** (a React UI that runs inside Resolve as a Workflow Integration).

> **DaVinci Resolve Studio required.** The Scripting API is Studio-only ($295). The free edition will not work. Verify via `DaVinci Resolve → About` — must say "DaVinci Resolve **Studio**".

---

## Which path fits you?

| If you want to… | Start here |
|---|---|
| **Use CutMaster AI inside Resolve** (editors, one-click podcast → YouTube/Shorts cuts) | [docs/CUTMASTER_SETUP.md](docs/CUTMASTER_SETUP.md) |
| **Use MCP tools in Claude Code / Desktop** (chat-driven Resolve automation, 233 tools) | [docs/SETUP.md](docs/SETUP.md) |
| **Contribute code** (Python server, React panel, Resolve plugin, or skills) | [docs/CONTRIBUTING.md](docs/CONTRIBUTING.md) |

### What ships in this repo

| Artifact | What it does | Who consumes it |
|---|---|---|
| **MCP server** (`celavii-resolve`) | 233 stdio tools wrapping the Resolve API | Claude Code, Claude Desktop, Cursor, any MCP client |
| **Panel backend** (`celavii-resolve-panel`) | FastAPI HTTP server on `127.0.0.1:8765` that drives CutMaster AI | The React panel |
| **React panel** ([apps/panel/](apps/panel/)) | The CutMaster UI | A browser, or Resolve's Workflow Integration webview |
| **Resolve Workflow Integration plugin** ([apps/resolve-plugin/](apps/resolve-plugin/)) | Thin Electron wrapper that opens the panel inside Resolve's docked webview | DaVinci Resolve Studio 21+ |
| **Claude Code plugin** ([.claude-plugin/](.claude-plugin/) + `skills/` + `agents/` + `hooks/`) | Packaged skills (`/deliver`, `/grade-log` etc.), domain agents, safety hooks | Claude Code CLI |

> The **Resolve Workflow Integration plugin** and the **Claude Code plugin** are two entirely separate artifacts. They share no files and follow different install paths. See [docs/CONTRIBUTING.md](docs/CONTRIBUTING.md#two-plugins-one-repo) for the disambiguation.

### Three products that depend on this repo

| # | Name | Audience | Source | Distribution |
|---|---|---|---|---|
| 1 | **Celavii MCP** (Claude Code plugin) | AI engineers, Claude Code users | This repo | `pip install celavii-resolve` + Claude Code marketplace |
| 2 | **Celavii Resolve Workflow plugin** | Any Resolve 21+ user | This repo | Copy into Resolve's plugin folder |
| 3 | **Celavii Studio** (paid macOS app) | Colorists, editors, designers | Separate private repo | Notarized DMG from [celavii.com](https://celavii.com) — pins a PyPI version of this package |

Studio extends the OSS core via two **entry-point groups** (`celavii_resolve.tools` and `celavii_resolve.panel_routes`) — third-party plugins can register the same way. See [SURFACE.md](SURFACE.md) for the contract and [`src/celavii_resolve/plugins.py`](src/celavii_resolve/plugins.py) for the discovery code.

---

## Features

- **203 granular tools** — full coverage of the DaVinci Resolve Scripting API (324 methods)
- **14 workflow tools** — compound operations for ingest, assembly, delivery, conform, and grading
- **10 AI tools** — Gemini-powered frame analysis, OCR, color assist, editorial critique
- **5 MCP resources** — passive context for version, project, timelines, bins, render queue
- **9 skills** — Claude Code slash commands (`/deliver`, `/preflight`, `/color-assist`, `/grade-log`, etc.)
- **7 agents** — domain-specific roles (editor, colorist, VFX, sound, conform, delivery, producer)
- **Safety hooks** — destructive operation warnings, .env protection, auto-lint
- **LUT library** — registry + installer for 9 camera brands (DJI, Insta360, GoPro, Canon, Nikon, Fujifilm, etc.)
- **Auto-launch** — starts DaVinci Resolve automatically if not running
- **Cross-platform** — macOS, Windows, Linux

## Quick Start

### One-Command Setup (macOS)

```bash
git clone https://github.com/CelaviiHQ/celavii-davinci-resolve-mcp.git
cd celavii-davinci-resolve-mcp
bash scripts/setup.sh
```

This will create a venv, install all dependencies, configure Claude Desktop, and verify the Resolve connection. For the full setup including global skills, agents, hooks, and auto-start:

```bash
bash scripts/setup.sh --all
```

### Interactive Installer

```bash
git clone https://github.com/CelaviiHQ/celavii-davinci-resolve-mcp.git
cd celavii-davinci-resolve-mcp
python3 scripts/install.py
```

### Manual Install

```bash
git clone https://github.com/CelaviiHQ/celavii-davinci-resolve-mcp.git
cd celavii-davinci-resolve-mcp
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Setup & Configuration

**For detailed setup instructions — Claude Desktop config, Claude Code skills/agents/hooks, LaunchAgent auto-start, and distributable packaging — see the [Complete Setup Guide](docs/SETUP.md).**

### Claude Desktop

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "celavii-resolve": {
      "command": "/path/to/celavii-davinci-resolve-mcp/.venv/bin/python3",
      "args": ["-m", "celavii_resolve"],
      "cwd": "/path/to/celavii-davinci-resolve-mcp"
    }
  }
}
```

Or run `python3 scripts/install.py --clients claude-desktop` to configure automatically.

### Claude Code

The project includes a `.mcp.json` file — Claude Code auto-detects it:

```bash
cd celavii-davinci-resolve-mcp
claude
```

For global access (use from any directory), add the MCP server to `~/.claude/settings.json`. See [Setup Guide](docs/SETUP.md#32-claude-code-cli).

### Skills, Agents & Hooks Installation

Skills/agents/hooks are Claude Code features. Install globally with:

```bash
bash scripts/setup.sh --global
```

This copies all 9 skills to `~/.claude/commands/`, merges hooks into `~/.claude/settings.json`, and configures the global MCP server. See [Setup Guide](docs/SETUP.md#4-skills-agents--hooks-claude-code).

### Auto-Start (macOS LaunchAgent)

Start the MCP server automatically on login:

```bash
bash scripts/setup.sh --launchagent
```

This installs a LaunchAgent at `~/Library/LaunchAgents/com.celavii.resolve-mcp.plist`. See [Setup Guide](docs/SETUP.md#5-auto-start-with-launchagent-macos).

### Claude Code Plugin

Build and install as a Claude Code plugin:

```bash
bash scripts/build-plugin.sh
claude plugin install ./celavii-resolve-plugin-v0.1.0.zip
```

Skills become available as `/celavii-resolve:deliver`, `/celavii-resolve:grade-log`, etc. See [Setup Guide](docs/SETUP.md#5-claude-code-plugin).

### Creating a Distributable Package

Share with team members:

```bash
bash scripts/package.sh
```

Creates `celavii-resolve-v0.1.0.zip` — the recipient runs `bash scripts/setup.sh` and they're ready.

## Requirements

- **DaVinci Resolve Studio 18.5+** — [purchase](https://www.blackmagicdesign.com/products/davinciresolve/studio) ($295)
- **External scripting enabled** — Preferences > System > General > External scripting: **Local**
- **Python 3.11+**
- **[uv](https://docs.astral.sh/uv/)** — fast Python package manager. The committed `.mcp.json` invokes the MCP server via `uv run`, so **Claude Code / Desktop won't connect without it.** Install:
  ```bash
  brew install uv                              # macOS
  curl -LsSf https://astral.sh/uv/install.sh | sh   # Linux
  powershell -c "irm https://astral.sh/uv/install.ps1 | iex"   # Windows
  ```
- **GEMINI_API_KEY** (optional, for AI-powered tools)

## Tool Categories

| Category | Tools | Description |
|----------|-------|-------------|
| Project | 24 | CRUD, import/export/archive, folders, databases, settings |
| Media Pool | 23 | Bins, clips, import, metadata, search, relink, proxy |
| Render | 20 | Formats, codecs, presets, queue, start/stop/monitor |
| Fusion | 19 | Comp lifecycle, node graph, connections, inputs, undo |
| Timeline Mgmt | 18 | Timeline CRUD, tracks, import/export, duplicate |
| Timeline Items | 17 | Versions, takes, flags, colors, color groups, playhead |
| Timeline Edit | 15 | Properties, transform, crop, speed, generators, titles |
| Color | 12 | CDL, node graph, LUTs, cache, grade copy, stills |
| Markers | 11 | Timeline markers, clip markers, media pool markers |
| Layout | 11 | UI presets, burn-in presets, LUT refresh, keyframe mode |
| Gallery | 9 | Albums, stills, export/import, power grades |
| Graph | 7 | Color groups, pre/post-clip node graphs |
| Interchange | 6 | EDL, FCPXML, AAF, OTIO, CSV export/import |
| Media Storage | 5 | Volumes, browse, files, import, reveal |
| Fairlight | 4 | Audio insertion, voice isolation, track info |
| Scripting | 2 | Execute arbitrary Python or Lua |

### Workflow Tools

| Tool | Description |
|------|-------------|
| `celavii_ingest_media` | Scan folder, import, organise bins, set metadata |
| `celavii_ingest_with_bins` | Mirror folder structure as media pool bins |
| `celavii_quick_assembly` | Create timeline from clips or bin |
| `celavii_assembly_from_bin` | Timeline with track configuration |
| `celavii_quick_deliver` | One-command render with preset shorthands |
| `celavii_batch_deliver` | Multi-format render (h264 + prores + proxy) |
| `celavii_render_status` | Poll render queue progress |
| `celavii_conform_timeline` | Import EDL/XML + relink + verify |
| `celavii_relink_offline_clips` | Fix offline media |
| `celavii_verify_timeline_media` | Check all clips online |
| `celavii_quick_grade` | Apply LUT + CDL + grab still |
| `celavii_batch_apply_lut` | LUT to multiple clips |
| `celavii_copy_grade_to_all` | Copy grade from one clip to all |
| `celavii_setup_log_grade` | 6-node structure: WB/EXP/SAT/CURVES/CST/LUT |
| `celavii_list_cst_luts` | List available camera CST LUTs and film looks |
| `celavii_lut_library_status` | Show installed vs available LUTs for all cameras |
| `celavii_install_lut_file` | Install a downloaded .cube/.zip LUT into the library |
| `celavii_get_lut_install_guide` | Step-by-step guide for a camera's LUT |
| `celavii_scan_lut_folder` | Scan all Resolve LUT folders |

### AI Tools (requires GEMINI_API_KEY)

| Tool | Description |
|------|-------------|
| `celavii_analyze_frame` | Gemini vision analysis of current frame |
| `celavii_ocr_frame` | Extract text from current frame |
| `celavii_describe_shot` | Cinematography breakdown |
| `celavii_compare_frames` | A/B comparison with reference image |
| `celavii_frame_info` | Frame metadata (no API key needed) |
| `celavii_color_assist` | AI CDL recommendations with auto-apply |
| `celavii_match_to_reference` | Color match to reference image |
| `celavii_timeline_critique` | AI editorial feedback |
| `celavii_suggest_markers` | AI marker suggestions |
| `celavii_visual_continuity_check` | Cut point analysis |

## Skills (Claude Code)

| Command | Description |
|---------|-------------|
| `/deliver` | Quick render with preset shorthands |
| `/preflight` | Pre-render verification checklist |
| `/color-assist` | AI color grading assistant |
| `/grade-log` | 6-node Log footage grading (WB → EXP → SAT → CURVES → CST → LUT) |
| `/conform` | Timeline conform from EDL/XML |
| `/ingest` | Media import with organisation |
| `/assembly` | Build rough cut from clips |
| `/review` | AI editorial feedback |
| `/export-stills` | Gallery still batch export |

## Agents

| Agent | Role |
|-------|------|
| Editor | Timeline assembly, clip arrangement, tracks |
| Colorist | Node trees, LUTs, CDL, grades, stills |
| VFX | Fusion compositions, node graphs |
| Sound | Fairlight audio, voice isolation |
| Conform | EDL/XML import, relinking, round-trip |
| Delivery | Render config, format selection, QC |
| Producer | Project admin, status reports, databases |

## AI Setup (Optional)

```bash
cp .env.example .env
# Edit .env and set GEMINI_API_KEY=your-key-here
pip install -e ".[all]"
```

Get a Gemini API key at [aistudio.google.com](https://aistudio.google.com/apikey).

## Development

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

pytest tests/ -v          # Run tests (no Resolve needed)
ruff check src/ --fix     # Lint
ruff format src/          # Format
python -m celavii_resolve # Run server
```

## Project Structure

```
celavii-davinci-resolve-mcp/
├── src/celavii_resolve/
│   ├── config.py          # FastMCP server, constants
│   ├── resolve.py         # Connection management, helpers
│   ├── errors.py          # Exception hierarchy, @safe_resolve_call
│   ├── constants.py       # API constants (colors, modes, types)
│   ├── resources.py       # 5 MCP resources
│   ├── tools/             # 17 modules (incl. lut_registry), granular tools
│   ├── workflows/         # 6 modules, compound workflow tools
│   ├── intelligence/      # Single-shot LLM tools (vision, color-assist, critique) + shared llm.py
│   ├── cutmaster/         # CutMaster AI product (core/, stt/, analysis/, media/, resolve_ops/, data/)
│   ├── http/              # FastAPI backend for the panel (split routes/cutmaster/ package)
│   └── utils/             # Platform, media, path helpers
├── .claude-plugin/
│   └── plugin.json        # Claude Code plugin manifest
├── skills/                # Claude Code skills (plugin distribution)
├── agents/                # Domain agents (plugin distribution)
├── hooks/                 # Safety hooks (plugin distribution)
├── apps/
│   ├── panel/             # React panel (Vite, talks to celavii-resolve-panel)
│   └── resolve-plugin/    # DaVinci Resolve Workflow Integration manifest
├── scripts/
│   ├── install.py         # Universal installer (10 MCP clients)
│   ├── build-plugin.sh    # Build Claude Code plugin ZIP
│   ├── setup.sh           # One-command setup (macOS/Linux)
│   ├── package.sh         # Create distributable zip
│   └── launchd/
│       └── com.celavii.resolve-mcp.plist  # macOS auto-start
├── docs/
│   └── SETUP.md           # Complete setup guide
├── tests/                 # Test suite
├── .mcp.json              # Claude Code project config
└── pyproject.toml         # Package config
```

## License

MIT
