# Celavii-Resolve: Competitive Analysis of DaVinci Resolve MCP Servers

## Executive Summary

Six DaVinci Resolve MCP repos were analyzed to inform the design of Celavii-Resolve — a maximum-control MCP server for automating DaVinci Resolve from Claude Code / IDE.

---

## Repo Comparison Matrix

| Feature | samuelgursky | Positronikal | apvlv | Tooflex | jenkinsm13 | jerelleco |
|---------|-------------|--------------|-------|---------|------------|-----------|
| **Tools** | 342 (granular) / 27 (compound) | 13 | 14 | 33 | 282+ | 342 (fork) |
| **API Coverage** | 100% (324/324 methods) | ~5% | ~5% | ~12% | ~99% | 100% (fork) |
| **Framework** | FastMCP | MCP Server (raw) | FastMCP | FastMCP | FastMCP 3.0+ | FastMCP |
| **Transport** | stdio | stdio | stdio | stdio | stdio | stdio |
| **Language** | Python | Python | Python | Python | Python | Python |
| **Fusion Support** | Yes (20 actions) | No | Yes (node chains + Lua) | Yes (basic + Lua) | Yes (22 tools) | Yes (20 actions) |
| **Fairlight Audio** | Basic (via timeline_item) | No | No | Basic | Yes (4 tools) | Basic |
| **Color Grading** | Yes (nodes, CDL, grades) | No | No | Yes (nodes, stills) | Yes + AI (Gemini) | Yes |
| **AI Features** | Scene cuts, subtitles | No | No | No | Color AI, Gemini agent loop | Scene cuts, subtitles |
| **Auto-Launch Resolve** | Yes (60s timeout) | No | No | No | No | Yes |
| **MCP Clients** | 10 | 2 (Claude Desktop, Cursor) | Any (generic) | Claude Desktop | Claude Code + Desktop + any | 10 |
| **Testing** | 319/324 live (98.5%) | Integration + security | Import smoke only | None | Import + convention | 319/324 live |
| **License** | MIT | GPL v3 | MIT | MIT | MIT | MIT |
| **Python** | 3.10-3.12 | 3.9-3.12 | 3.10+ | 3.10+ | 3.11+ | 3.10-3.12 |
| **Resolve Version** | 18.5+ | Through v20 | Studio 18+ | Studio 18+ | 20.3 | 18.5+ |
| **Resources** | No | 7 | 7 | 6 | 5 | No |
| **Skills/Agents** | No | No | No | No | 15 skills, 26 agents | No |
| **Dependencies** | mcp[cli] only | 6 core + 15 optional | mcp, pydantic, typing-ext | mcp, pydantic | fastmcp, google-genai, OTIO, pydantic | mcp[cli] only |
| **LOC (core)** | ~9,500 | ~2,000 | ~1,300 | ~1,500 | ~7,600 | ~9,300 |

---

## Detailed Analysis Per Repo

### 1. samuelgursky/davinci-resolve-mcp (The Gold Standard)

**Architecture:** Dual-mode server — compound (27 action-dispatched tools) or granular (342 individual tools). Built on FastMCP with stdio transport. Lazy connection pattern with auto-launch of Resolve.

**Strengths:**
- 100% API coverage (324/324 methods)
- 98.5% live-tested (319/319 pass rate)
- Dual mode gives flexibility (low-context compound vs power-user granular)
- Auto-launches Resolve if not running (60s timeout with retry)
- Cross-platform (macOS, Windows, Linux)
- Universal installer supporting 10 MCP clients
- Security: path traversal protection on layout presets
- Sandbox path redirection for temp directories Resolve can't access
- Gallery stills grab_and_export with auto-cleanup
- Comprehensive Fusion comp control (20 actions in v2.1.0)
- Minimal dependencies (just mcp[cli])

**Weaknesses:**
- No MCP Resources (read-only data endpoints)
- No AI-powered tools (no Gemini/LLM integration)
- No OTIO/interchange format support
- No skills or agents for Claude Code
- No background job management
- server.py is monolithic (~2,400 lines)

**Key Patterns to Adopt:**
- Action-based dispatch pattern (group related ops under one tool)
- `_err()` / `_ok()` response helpers
- Lazy connection with auto-launch
- Platform-specific path detection
- Path traversal protection

---

### 2. Positronikal/davinci-mcp-professional (Enterprise Structure, Limited Scope)

**Architecture:** Clean separation of concerns — MCP server layer, Resolve client abstraction, tools module, resources module, types module. Two execution modes: interactive CLI (verbose) and silent MCP server. Uses raw MCP Server class (not FastMCP).

**Strengths:**
- Excellent code architecture (best separation of concerns)
- Protocol-based type safety (Python Protocols for duck typing)
- DXT (Desktop Extension) for one-click Claude Desktop install
- Custom exception hierarchy (DaVinciResolveError, NotRunning, ConnectionError)
- Security audit tooling integrated (Bandit, Safety, Semgrep)
- Comprehensive dev tooling (black, mypy, ruff, isort)
- 7 MCP Resources for passive data access
- Interactive CLI mode with colored output

**Weaknesses:**
- Only 13 tools (very limited API coverage ~5%)
- No Fusion, Color, Fairlight, or Render tools
- No auto-reconnection
- GPL v3 license (restrictive for our use)
- No tests for individual methods (only integration + security)

**Key Patterns to Adopt:**
- Separation: server.py / resolve_client.py / tools/ / resources/ / types.py
- Custom exception hierarchy
- Protocol-based type definitions
- Two execution modes (interactive CLI vs silent server)
- MCP Resources for read-only data

---

### 3. apvlv/davinci-resolve-mcp (Fusion Focus + Raw Scripting)

**Architecture:** Monolithic FastMCP server (server.py 741 LOC) with thin ResolveAPI wrapper (556 LOC). Connection at import time (eager, not lazy).

**Strengths:**
- Fusion node chain creation (automatic input-output connections)
- `execute_python` — arbitrary Python code execution with Resolve API access
- `execute_lua` — Lua script execution in Fusion environment
- 7 MCP Resources (system status, project, timelines, media pool, storage)
- Clean, readable codebase
- PyPI-publishable (uv build system, semantic release)
- CI/CD pipeline (GitHub Actions)

**Weaknesses:**
- Only 14 tools (~5% API coverage)
- Eager connection (fails fast if Resolve not running)
- No reconnection logic
- Import-only tests (no functional tests)
- `create_fusion_node` has indentation bug (standalone function, not class method)

**Key Patterns to Adopt:**
- `execute_python` / `execute_lua` for raw scripting escape hatch
- Fusion node chain builder (connect output N to input N+1)
- Recursive media pool folder enumeration
- MCP Resources for passive inspection

---

### 4. Tooflex/davinci-resolve-mcp (Balanced Feature Set)

**Architecture:** FastMCP server (432 LOC) + ResolveAPI class (1,080 LOC). Standard two-file architecture.

**Strengths:**
- 33 tools — good balance of coverage vs complexity
- Clip property control (Pan, Zoom, transform)
- Track management (add, name, enable/disable, volume)
- Color nodes + gallery stills (save/apply grades)
- Playback control (play, stop, set playhead)
- Render automation with presets
- Project import/export
- Timeline marker support with colors/notes
- 6 MCP Resources
- `RESOLVE_SCRIPT_PATH` env var override
- `refresh()` tool for reconnection

**Weaknesses:**
- No tests at all
- No CI/CD pipeline
- 33 tools still only ~12% API coverage
- Basic Fusion support (node creation + Lua only)
- No error recovery beyond refresh()
- No installer / auto-configuration

**Key Patterns to Adopt:**
- Track management tools (add, name, enable, volume)
- Playback control (play/stop/set position)
- `refresh()` tool for manual reconnection
- Clip property manipulation (Pan, Zoom, etc.)

---

### 5. jenkinsm13/resolve-mcp (The Innovation Leader)

**Architecture:** FastMCP 3.0+ with modular tool files (37+ modules, 80 files). Cascading import chain. Includes Claude Code skills, agents, and safety hooks. Optional Gemini AI integration.

**Strengths:**
- 282+ tools (~99% API coverage)
- AI-powered color grading (Gemini visual analysis -> CDL recommendations)
- Gemini agent loop (interactive timeline building, 50-turn limit)
- OTIO integration (AAF, EDL, XML, FCPXML import/export)
- 15 Claude Code skills (/deliver, /preflight, /color-assist, etc.)
- 26 domain-specific agents (colorist, editor, VFX, music video, etc.)
- Safety hooks (block deletes, protect .env, auto-lint)
- Background job polling (async render/ingest with status handles)
- Sidecar metadata system (JSON sidecars for clips)
- MCP Resources (5 resolve:// URIs)
- `@safe_resolve_call` decorator (all exceptions -> user-friendly strings)
- Custom exception hierarchy (ResolveNotRunning, ProjectNotOpen, etc.)
- Claude Code plugin install (`claude plugin add`)
- Proxy transcoding for Gemini (ffprobe + ffmpeg)
- Multi-variant editing (N versions with different approaches)
- Studio vs Free detection

**Weaknesses:**
- Requires ffprobe/ffmpeg on PATH for AI tools
- Gemini API key required for AI features (crashes on bad key at import time)
- FastMCP 3.0+ only (newer, less battle-tested)
- No auto-launch of Resolve
- Network scripting must be enabled in Resolve preferences
- Tests are import/convention only (no live API tests)
- No universal installer for MCP clients

**Key Patterns to Adopt:**
- Modular tool files (one file per category, cascading imports)
- `@safe_resolve_call` decorator pattern
- Custom exception hierarchy
- MCP Resources for passive data
- Skills + Agents architecture for Claude Code
- Safety hooks (PreToolUse/PostToolUse)
- Background job management with status polling
- OTIO interchange format support
- AI bridge tools pattern
- `_boilerplate()` helper for (resolve, project, media_pool) tuple
- Constants file for marker colors, clip colors, track types
- Sidecar metadata for clip inspection

---

### 6. jerelleco/davinci-resolve-mcp2.1.0 (Fork of samuelgursky)

**Architecture:** Direct fork of samuelgursky v2.1.0. Identical codebase.

**Verdict:** No unique contributions. Use samuelgursky directly.

---

## Best-of-Breed Features for Celavii-Resolve

### From samuelgursky (Foundation):
- 100% API coverage (324 methods)
- Dual-mode architecture (compound + granular)
- Lazy connection with auto-launch
- Action-based dispatch pattern
- Platform detection + path resolution
- Sandbox path redirection
- Path traversal protection
- Gallery stills grab_and_export

### From Positronikal (Architecture):
- Clean separation: server / client / tools / resources / types
- Custom exception hierarchy
- Protocol-based type safety
- Two execution modes (CLI + server)
- Security audit tooling

### From apvlv (Escape Hatches):
- `execute_python` for arbitrary scripting
- `execute_lua` for Fusion scripting
- Fusion node chain builder
- MCP Resources for passive inspection

### From Tooflex (Editing Controls):
- Track management (add, name, enable, volume)
- Playback control (play, stop, seek)
- Clip property manipulation
- `refresh()` reconnection tool

### From jenkinsm13 (Innovation):
- AI color grading (Gemini integration)
- OTIO interchange (AAF, EDL, XML, FCPXML)
- Claude Code skills + agents
- Safety hooks
- Background job polling
- `@safe_resolve_call` decorator
- Sidecar metadata system
- MCP Resources
- Modular tool file organization
- Constants/enums for API values

---

## Architecture Recommendations for Celavii-Resolve

### Core Architecture
```
cutmaster-ai/
  src/
    server.py              # FastMCP entry point, mode selection
    config.py              # Constants, enums, MCP instance
    resolve.py             # Connection management, lazy init, auto-launch
    errors.py              # Exception hierarchy + @safe_resolve_call
    resources.py           # MCP Resources (resolve://, project://, timeline://)
    types.py               # Protocol definitions, type safety
    tools/
      project.py           # Project management (CRUD, settings, DB, cloud)
      media_pool.py        # Media pool operations
      media_storage.py     # Volume and file browsing
      timeline.py          # Timeline management
      timeline_items.py    # Clip properties, transform, composite
      markers.py           # Timeline + clip markers
      color.py             # Color grading, nodes, CDL, grades
      fusion.py            # Fusion comp + node graph
      fairlight.py         # Audio tools
      render.py            # Render pipeline
      gallery.py           # Stills and albums
      scripting.py         # execute_python, execute_lua escape hatches
      ai.py                # AI-powered tools (optional, Gemini/other)
    utils/
      platform.py          # OS detection, path resolution
      connection.py        # Resolve connection, auto-launch
      interchange.py       # OTIO, EDL, XML, FCPXML
      metadata.py          # Sidecar metadata system
  skills/                  # Claude Code skills (/deliver, /color-assist, etc.)
  agents/                  # Domain-specific agents (colorist, editor, VFX)
  hooks/                   # Safety hooks (delete protection, lint)
  tests/
    test_imports.py        # Smoke tests
    test_conventions.py    # Naming, docstrings
    test_live_api.py       # Live Resolve tests
  install.py               # Universal installer
  CLAUDE.md                # Developer guide
```

### Key Design Decisions
1. **FastMCP 3.0+** — Latest framework, best tool registration
2. **Dual-mode** — Compound (low context) + Granular (max control)
3. **Modular tools** — One file per domain (not monolithic)
4. **Lazy connection + auto-launch** — Best UX
5. **MCP Resources** — Passive data for LLM context
6. **Exception hierarchy + decorator** — Clean error handling
7. **Skills + Agents** — First-class Claude Code integration
8. **Safety hooks** — Protect destructive operations
9. **OTIO interchange** — Professional workflow integration
10. **AI bridge** — Optional Gemini/other LLM integration

### API Coverage Target
- **Phase 1**: 100% of Resolve Scripting API (324 methods) — match samuelgursky
- **Phase 2**: AI-powered tools (color assist, timeline critique) — from jenkinsm13
- **Phase 3**: Skills + Agents for Claude Code workflows — from jenkinsm13
- **Phase 4**: OTIO interchange + sidecar metadata — from jenkinsm13

---

## DaVinci Resolve Scripting API Quick Reference

### Object Hierarchy
```
Resolve (root)
├── GetProjectManager() -> ProjectManager
│   ├── GetCurrentProject() -> Project
│   ├── CreateProject(name) -> Project
│   ├── LoadProject(name) -> bool
│   └── GetProjectListInCurrentFolder() -> [str]
├── GetMediaStorage() -> MediaStorage
│   ├── GetMountedVolumeList() -> [str]
│   └── GetSubFolderList(path) -> [str]
├── OpenPage(page) -> bool
├── GetCurrentPage() -> str
└── Fusion() -> Fusion

Project
├── GetMediaPool() -> MediaPool
│   ├── GetRootFolder() -> Folder
│   ├── ImportMedia([paths]) -> [MediaPoolItem]
│   └── CreateEmptyTimeline(name) -> Timeline
├── GetCurrentTimeline() -> Timeline
├── GetTimelineByIndex(idx) -> Timeline  (1-based!)
├── GetGallery() -> Gallery
└── GetSetting(key) / SetSetting(key, value)

Timeline
├── GetItemListInTrack(type, idx) -> [TimelineItem]
├── AddMarker(frame, color, name, note, duration)
├── Export(path, type, subtype) -> bool
└── GetTrackCount(type) -> int

TimelineItem
├── GetProperty(key) / SetProperty(key, value)
├── AddFusionComp() -> FusionComp
├── GetFusionCompByIndex(idx) -> FusionComp
├── AddMarker(...)
└── GetMediaPoolItem() -> MediaPoolItem
```

### Critical API Gotchas
- **All indices are 1-based** (not Python's 0-based)
- **Resolve returns None for empty lists** (not []) — always guard with `or []`
- **AddMarker returns False silently** on duplicate frame (no exception)
- **Marker APIs are asymmetric**: clip markers use frame offsets, timeline markers use seconds
- **Free edition has NO scripting API** — Studio only
- **Python 3.13+ may have ABI issues** with Resolve's fusionscript
- **Network scripting must be enabled** in Resolve Preferences for some connection methods
