# Plugin & Embedding Surface

This document enumerates the **stable** contract that external consumers — most notably the CutMaster Studio bundle and any third-party plugin author — depend on. Anything listed here is covered by semantic versioning: breaking changes require a **major** version bump and a changelog entry under `## [Unreleased]`. Everything else in `src/cutmaster_ai/` is internal and may be restructured without notice.

> **Scope:** this file describes consumption patterns, not the full tool catalogue. For the MCP tool list see [docs/MCP_TOOLS.md](docs/MCP_TOOLS.md).

---

## 1. Entry-point groups

The package advertises **two** entry-point groups. A plugin may register into one, the other, or both.

### `cutmaster_ai.tools`

Register signature:

```python
def register_tools(mcp: fastmcp.FastMCP) -> None:
    """Decorate plugin functions with @mcp.tool directly."""
```

Declared in `pyproject.toml`:

```toml
[project.entry-points."cutmaster_ai.tools"]
my_plugin = "my_plugin:register_tools"
```

Consumed by: MCP clients (Claude Desktop, Claude Code, Cursor, any FastMCP host). Called once during `cutmaster_ai.__init__` after OSS tools have registered.

### `cutmaster_ai.panel_routes`

Register signature:

```python
def register_routes(app: fastapi.FastAPI) -> None:
    """Include your APIRouter(s) onto the Panel app."""
```

Declared in `pyproject.toml`:

```toml
[project.entry-points."cutmaster_ai.panel_routes"]
my_plugin = "my_plugin:register_routes"
```

Consumed by: the Panel HTTP server (CutMaster Studio bundle, React panel inside Resolve's Workflow Integration webview). Called once inside `cutmaster_ai.http.app.create_app()` **after** OSS routes register, so plugins cannot shadow built-in paths.

### Contract guarantees

- Registration is best-effort. A plugin that raises during load or register is **logged and skipped** — it will never prevent OSS from starting.
- The list of successfully-registered names is exposed at `GET /pro/status` and via `cutmaster_ai.plugins.registered_plugins()`.
- Existence of any registered plugin flips `cutmaster_ai.licensing.current_tier()` from `"oss"` to `"standard"`.
- OSS has **no hardcoded references** to specific plugin module names. Discovery is entry-point-only.

---

## 2. Route conventions

### Prefix your routes under `/plugins/<name>/`

Every plugin-registered router **must** live under a unique prefix to avoid collisions. The recommended convention:

```python
router = APIRouter(prefix="/plugins/my_plugin", tags=["my-plugin"])
```

Plugins shipped by CutMaster Studio itself may use the shorter `/pro/<feature>` prefix — this is reserved for the signed bundle. Third-party plugins should use `/plugins/<your-name>/` so two plugins can coexist without collision.

OSS reserves the following top-level path prefixes and plugins **must not** register there:

- `/ping`
- `/pro/status`
- `/cutmaster/*`
- `/_docs`

### Stable HTTP endpoints

| Method | Path | Contract |
|---|---|---|
| `GET` | `/ping` | `{ok: bool, service: str, version: str}` — health check |
| `GET` | `/pro/status` | `{tier: "oss" \| "standard", plugins: {tools: [str], panel_routes: [str]}}` |
| `GET` / `POST` | `/cutmaster/**` | CutMaster panel API — see `http/routes/cutmaster/` for per-route shapes |

Breaking any of these requires a major bump.

---

## 3. Stdout protocol

`python -m cutmaster_ai.http` (equivalently, the `cutmaster-ai-panel` console script) emits the following **as the first stdout line** at startup:

```
PANEL_READY http://<host>:<port>
```

Supervisors parse this line to discover the bound URL — particularly important when `CUTMASTER_PANEL_PORT=0` is set to request a random free port. No other output (log lines, banners) will precede this line on stdout; logging goes to stderr after the line is flushed.

Environment:

- `CUTMASTER_PANEL_HOST` — bind host, default `127.0.0.1`.
- `CUTMASTER_PANEL_PORT` — bind port; default `8765`. Set to `0` for random.
- `CUTMASTER_PANEL_DB` — SQLite path for the panel state database. Default `~/.cutmaster/panel/state.db`.

---

## 4. Stable Python imports

Third-party code **must** import Pydantic request/response models from `cutmaster_ai.http.models`, not from the private `cutmaster_ai.http.routes.*._models` modules. The re-export list is versioned; additions are non-breaking, removals or shape changes are breaking.

Current re-exports include (non-exhaustive — check `http/models.py` for the full `__all__`):

- `AnalyzeRequest`, `AnalyzeResponse`
- `BuildPlanRequest`, `ExecuteRequest`
- `UserSettings`, `RunListResponse`, `RunSummary`
- `ProjectInfoResponse`, `TimelineInfo`, `SourceAspectResponse`
- `SpeakerRosterResponse`, `SpeakerRosterEntry`
- `DetectPresetRequest`, `AnalyzeThemesRequest`
- `DeleteCutRequest`, `DeleteAllCutsRequest`, `DeleteRunRequest`, `CloneRunRequest`

Other stable top-level imports:

- `cutmaster_ai.__version__` — package version string.
- `cutmaster_ai.plugins.discover_tools(mcp)` / `.discover_panel_routes(app)` — manual re-invocation (rare).
- `cutmaster_ai.plugins.registered_plugins()` — snapshot dict.
- `cutmaster_ai.licensing.current_tier()` — returns `"oss"` or `"standard"`.

---

## 5. Migrations

The Panel boots with an idempotent SQLite migration runner at `cutmaster_ai.migrations.runner.apply_migrations(db_path)`. It applies every file in `cutmaster_ai/migrations/` matching the pattern `NNNN_*.sql`, in numeric order, tracking applied files in `_cutmaster_schema_migrations`.

OSS owns the un-prefixed tables (`recent_projects`, `custom_presets`, `cutmaster_sessions`, `panel_state`, …). The `studio_` table prefix is **reserved for the CutMaster Studio bundle**. Third-party plugins that need their own tables should use a namespaced prefix matching their package, e.g. `myplugin_recent_items`.

Breaking changes to any OSS table require a new forward-only migration and a major bump.

---

## 6. What is **not** in this contract

Anything not listed above is internal and may change between minor versions. Notably:

- Module paths under `cutmaster_ai.cutmaster.*`, `cutmaster_ai.tools.*`, `cutmaster_ai.workflows.*`, `cutmaster_ai.intelligence.*` — all free to move.
- Private request/response models under `http.routes.*._models` — use `cutmaster_ai.http.models` instead.
- FastMCP registry internals — tools are the product, not the specific `_tools` dict shape.
- Filesystem cache locations under `~/.cutmaster/` — treated as implementation detail.
- Logging format and logger names.

If you find yourself importing something that isn't documented here, file an issue requesting promotion to the stable surface rather than depending on it silently.

---

## 7. Snapshot enforcement

The tool surface is machine-verified on every PR. [`tests/surface_snapshot.json`](tests/surface_snapshot.json) is a deterministic dump of every registered `@mcp.tool` — name, description, input parameter schema, and output schema — and the `surface` job in [`.github/workflows/ci.yml`](.github/workflows/ci.yml) fails if the live surface diverges from the snapshot without a matching CHANGELOG entry.

If you intentionally changed a tool signature or added a tool:

```bash
uv run python scripts/dump_surface.py > tests/surface_snapshot.json
```

Then add a bullet under `## [Unreleased]` in [CHANGELOG.md](CHANGELOG.md) — `### BREAKING` for removals, renames, or required-param additions; `### Added` for new tools or new optional parameters. CI accepts either heading; the point is that surface changes are never silent.
