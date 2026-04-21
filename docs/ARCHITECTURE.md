# Architecture Overview

CutMaster AI is a **maximum-control MCP server for DaVinci Resolve Studio**. ~280 tools covering the full Resolve Scripting API, compound workflow tools, AI-enhanced features, and an optional React panel that ships alongside the Python package.

## Three deliverables, one repo

| Deliverable | What | Consumer |
|---|---|---|
| **`cutmaster-ai`** | MCP stdio server | Claude Code / Desktop / Cursor / any MCP client |
| **`cutmaster-ai-panel`** | HTTP/SSE server on `127.0.0.1:8765` | React panel inside DaVinci Resolve |
| **Resolve plugin** | Workflow Integration panel wrapper | DaVinci Resolve Studio 18.5+ |

The MCP server and panel backend **share the same Python package** — adding a new feature makes it available via both surfaces.

## Layer model

```
┌─────────────────────────────────────────────────────────────────┐
│  Consumers                                                       │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────────┐  │
│  │ Claude Code  │    │ React Panel  │    │ Resolve Plugin   │  │
│  │ (stdio MCP)  │    │ (HTTP+SSE)   │    │ (wraps panel)    │  │
│  └──────┬───────┘    └──────┬───────┘    └────────┬─────────┘  │
└─────────┼───────────────────┼─────────────────────┼────────────┘
          │                   │                     │
          ▼                   ▼                     ▼
┌─────────────────────────────────────────────────────────────────┐
│  src/cutmaster_ai/                                            │
│                                                                  │
│  Kernel:   config.py  resolve.py  errors.py  constants.py       │
│                                                                  │
│  ┌─────────────┐ ┌──────────────┐ ┌──────────────┐             │
│  │  tools/     │ │  workflows/  │ │ intelligence/│             │
│  │  (atomic    │ │  (compound,  │ │  (stateless  │             │
│  │   Resolve   │ │   no LLM)    │ │   LLM tools) │             │
│  │   SDK ops)  │ │              │ │              │             │
│  └─────────────┘ └──────────────┘ └──────┬───────┘             │
│                                          │                      │
│         ┌────────────────────────────────┤                      │
│         ▼                                │                      │
│  ┌────────────────────────────────┐     │                      │
│  │  cutmaster/  (AI product)      │◄────┘                      │
│  │  core/ stt/ analysis/          │   shared llm.py            │
│  │  media/ resolve_ops/ data/     │                            │
│  └────────────┬───────────────────┘                            │
│               │                                                 │
│               ▼                                                 │
│  ┌──────────────────────────────┐                              │
│  │  http/  (FastAPI backend)    │                              │
│  │  routes/cutmaster/ (6 files) │                              │
│  └──────────────────────────────┘                              │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
                                │
                                ▼
                     ┌──────────────────────┐
                     │ DaVinci Resolve API  │
                     └──────────────────────┘
```

## Four-bucket responsibility model

Every feature fits exactly one bucket. This is the rule we use to decide where new code goes.

| Bucket | Rule | Location | Example |
|---|---|---|---|
| **Atomic Resolve op** | One function = one Resolve SDK call = one MCP tool. No logic. | `tools/` | `cutmaster_add_node`, `cutmaster_set_cdl` |
| **Deterministic compound** | Chains `tools/` ops. No LLM. | `workflows/` | `cutmaster_quick_assembly` |
| **Stateless LLM tool** | One MCP call → one LLM roundtrip → one answer. | `intelligence/` | `cutmaster_analyze_frame`, `cutmaster_color_assist` |
| **Stateful AI product** | Owns state, multi-stage pipeline, optionally its own transport. | `cutmaster/` | Entire CutMaster workflow |

## Package layout

```
src/cutmaster_ai/
├── config.py           FastMCP singleton, Gemini client factory, constants
├── resolve.py          Connection management, _boilerplate() helper
├── errors.py           Exception hierarchy + @safe_resolve_call decorator
├── constants.py        API constants (colours, composite modes, track types)
├── resources.py        MCP Resources (resolve://, project://, timeline://)
│
├── tools/              17 modules — atomic wrappers over Resolve Scripting API
│   ├── color.py        CDL, nodes, LUTs
│   ├── fairlight.py    audio tracks, volume, voice isolation
│   ├── fusion.py       compositing node graph
│   ├── gallery.py      stills export/import
│   ├── graph.py        group pre/post graph + group LUTs
│   ├── interchange.py  EDL / XML / AAF / FCPXML / OTIO
│   ├── layout.py       UI layout presets
│   ├── lut_registry.py LUT library + camera-to-LUT map
│   ├── markers.py      clip + timeline markers
│   ├── media_pool.py   bins, clips, search
│   ├── media_storage.py volume browsing + import
│   ├── project.py      project CRUD, database, archive
│   ├── render.py       format/codec/preset + deliver jobs
│   ├── scripting.py    execute_python / execute_lua escape hatches
│   ├── timeline_edit.py append, insert titles/generators, compound clips
│   ├── timeline_items.py per-item properties, transforms, speed
│   └── timeline_mgmt.py timeline CRUD, tracks
│
├── workflows/          6 compound tools — high-level multi-step actions
│   ├── assembly.py     quick assembly / assembly from bin
│   ├── chroma_key.py   auto chroma-key node graph
│   ├── conform.py      import timeline + relink + verify
│   ├── delivery.py     one-command render (format presets)
│   ├── grade.py        log-grade node tree + CST + LUT
│   └── ingest.py       import + bin-organise + metadata
│
├── intelligence/       Stateless LLM tools + shared dispatch
│   ├── llm.py          Single LLM chokepoint (call_structured + validate)
│   ├── vision.py       Gemini frame analysis (describe, OCR, compare)
│   ├── color_assist.py AI CDL suggestion + application
│   └── timeline_critique.py editorial feedback on pacing/structure
│
├── cutmaster/          Stateful AI product — 6 subpackages
│   ├── core/           pipeline, director, execute, state, snapshot
│   ├── stt/            base + gemini/deepgram providers, speakers, reconcile
│   ├── analysis/       auto_detect (cascade), marker_agent, scrubber, tightener, captions, themes
│   ├── media/          frame_math, time_mapping, vfr, ffmpeg_audio, formats
│   ├── resolve_ops/    segments, source_mapper, subclips, assembled
│   └── data/           presets (9 bundles), excludes
│
├── http/               FastAPI backend for the panel
│   ├── app.py          app factory + static mount
│   └── routes/cutmaster/   6 feature modules (analyze, presets, info, build, execute)
│
└── utils/              Cross-cutting helpers (platform detection, media io)
```

## Dual-consumer pattern

Every cutmaster feature has **two entry points** backed by one implementation:

```python
# cutmaster/core/execute.py
def execute_plan(run: dict, name_suffix: str) -> dict:
    """Plain Python function — all the real work lives here."""
    ...

# http/routes/cutmaster/execute.py
@router.post("/execute")
async def execute(body: ExecuteRequest) -> dict:
    """HTTP wrapper — thin."""
    result = await asyncio.to_thread(execute_plan, run, name_suffix)
    return result
```

The MCP server exposes the same function via `@mcp.tool`-decorated adapters. Adding a new feature means writing the plain function once.

## Where to go next

- **New feature?** → [CONTRIBUTING.md](CONTRIBUTING.md) → pick a bucket
- **CutMaster internals?** → [CUTMASTER_ARCHITECTURE.md](CUTMASTER_ARCHITECTURE.md)
- **Full tool catalog?** → [MCP_TOOLS.md](MCP_TOOLS.md)
- **Setup for new dev machine?** → [SETUP.md](SETUP.md)
