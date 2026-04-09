# Celavii-Resolve

Maximum-control MCP server for DaVinci Resolve Studio.

**227 tools** covering the complete Resolve Scripting API, AI-enhanced workflows, Claude Code skills, and domain-specific agents.

## Features

- **203 granular tools** — full coverage of the DaVinci Resolve Scripting API (324 methods)
- **14 workflow tools** — compound operations for ingest, assembly, delivery, conform, and grading
- **10 AI tools** — Gemini-powered frame analysis, OCR, color assist, editorial critique
- **5 MCP resources** — passive context for version, project, timelines, bins, render queue
- **8 skills** — Claude Code slash commands (`/deliver`, `/preflight`, `/color-assist`, etc.)
- **7 agents** — domain-specific roles (editor, colorist, VFX, sound, conform, delivery, producer)
- **Safety hooks** — destructive operation warnings, .env protection, auto-lint
- **Auto-launch** — starts DaVinci Resolve automatically if not running
- **Cross-platform** — macOS, Windows, Linux

## Requirements

- DaVinci Resolve Studio 18.5+ (free edition has no scripting API)
- Python 3.11+
- GEMINI_API_KEY (optional, for AI tools)

## Quick Start

### 1. Clone and install

```bash
git clone https://github.com/celavii/celavii-resolve.git
cd celavii-resolve
python install.py
```

The installer will:
- Find Python 3.11+ and DaVinci Resolve
- Create a virtual environment
- Install dependencies
- Configure your MCP client(s)

### 2. Or install manually

```bash
python3.11 -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -e "."
```

### 3. Configure your MCP client

**Claude Desktop** (`~/Library/Application Support/Claude/claude_desktop_config.json`):
```json
{
  "mcpServers": {
    "celavii-resolve": {
      "command": "/path/to/.venv/bin/python3",
      "args": ["-m", "celavii_resolve"],
      "cwd": "/path/to/celavii-resolve"
    }
  }
}
```

**Claude Code** (`.mcp.json` in project root — already included):
```json
{
  "mcpServers": {
    "celavii-resolve": {
      "command": "uv",
      "args": ["run", "--directory", "${CLAUDE_PROJECT_ROOT}", "python", "-m", "celavii_resolve"]
    }
  }
}
```

**Cursor** (`~/.cursor/mcp.json`):
```json
{
  "mcpServers": {
    "celavii-resolve": {
      "command": "/path/to/.venv/bin/python3",
      "args": ["-m", "celavii_resolve"],
      "cwd": "/path/to/celavii-resolve"
    }
  }
}
```

Run `python install.py --clients manual` to get copy-paste config for all 10 supported clients.

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

| Workflow | Description |
|----------|-------------|
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

For AI-powered tools (color assist, OCR, frame analysis, editorial critique):

```bash
# Copy the example and add your key
cp .env.example .env
# Edit .env and set GEMINI_API_KEY=your-key-here
```

Get a Gemini API key at [aistudio.google.com](https://aistudio.google.com/apikey).

## Development

```bash
# Setup
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# Run tests (no Resolve needed)
pytest tests/ -v

# Lint
ruff check src/ --fix
ruff format src/

# Run server
python -m celavii_resolve
```

## Project Structure

```
celavii-resolve/
├── src/celavii_resolve/
│   ├── config.py          # FastMCP server, constants
│   ├── resolve.py         # Connection management, helpers
│   ├── errors.py          # Exception hierarchy, @safe_resolve_call
│   ├── constants.py       # API constants (colors, modes, types)
│   ├── resources.py       # 5 MCP resources
│   ├── tools/             # 16 modules, 203 granular tools
│   ├── workflows/         # 5 modules, 14 workflow tools
│   ├── ai/                # 3 modules, 10 AI tools
│   └── utils/             # Platform, media, path helpers
├── skills/                # 8 Claude Code skills
├── agents/                # 7 domain agents
├── hooks/                 # Safety hooks
├── tests/                 # 171 tests
├── install.py             # Universal installer
└── pyproject.toml         # Package config
```

## License

MIT
