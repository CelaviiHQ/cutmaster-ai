# Plugin & Embedding Surface

This document enumerates the **stable** contract that external consumers — most notably the Celavii Studio bundle and any third-party plugin author — depend on. Anything listed here is covered by semantic versioning: breaking changes require a **major** version bump and a changelog entry under `## [Unreleased]`. Everything else in `src/celavii_resolve/` is internal and may be restructured without notice.

> **Scope:** this file describes consumption patterns, not the full tool catalogue. For the MCP tool list see [docs/MCP_TOOLS.md](docs/MCP_TOOLS.md).

---

## 1. Entry-point groups

The package advertises **two** entry-point groups. A plugin may register into one, the other, or both.

### `celavii_resolve.tools`

Register signature:

```python
def register_tools(mcp: fastmcp.FastMCP) -> None:
    """Decorate plugin functions with @mcp.tool directly."""
```

Declared in `pyproject.toml`:

```toml
[project.entry-points."celavii_resolve.tools"]
my_plugin = "my_plugin:register_tools"
```

Consumed by: MCP clients (Claude Desktop, Claude Code, Cursor, any FastMCP host). Called once during `celavii_resolve.__init__` after OSS tools have registered.

### `celavii_resolve.panel_routes`

Register signature:

```python
def register_routes(app: fastapi.FastAPI) -> None:
    """Include your APIRouter(s) onto the Panel app."""
```

Declared in `pyproject.toml`:

```toml
[project.entry-points."celavii_resolve.panel_routes"]
my_plugin = "my_plugin:register_routes"
```

Consumed by: the Panel HTTP server (Celavii Studio bundle, React panel inside Resolve's Workflow Integration webview). Called once inside `celavii_resolve.http.app.create_app()` **after** OSS routes register, so plugins cannot shadow built-in paths.

### Contract guarantees

- Registration is best-effort. A plugin that raises during load or register is **logged and skipped** — it will never prevent OSS from starting.
- The list of successfully-registered names is exposed at `GET /pro/status` and via `celavii_resolve.plugins.registered_plugins()`.
- Existence of any registered plugin flips `celavii_resolve.licensing.current_tier()` from `"oss"` to `"standard"`.
- OSS has **no hardcoded references** to specific plugin module names. Discovery is entry-point-only.

---

## 2. Route conventions

### Prefix your routes under `/plugins/<name>/`

Every plugin-registered router **must** live under a unique prefix to avoid collisions. The recommended convention:

```python
router = APIRouter(prefix="/plugins/my_plugin", tags=["my-plugin"])
```

Plugins shipped by Celavii Studio itself may use the shorter `/pro/<feature>` prefix — this is reserved for the signed bundle. Third-party plugins should use `/plugins/<your-name>/` so two plugins can coexist without collision.

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

`python -m celavii_resolve.http` (equivalently, the `celavii-resolve-panel` console script) emits the following **as the first stdout line** at startup:

```
PANEL_READY http://<host>:<port>
```

Supervisors parse this line to discover the bound URL — particularly important when `CELAVII_PANEL_PORT=0` is set to request a random free port. No other output (log lines, banners) will precede this line on stdout; logging goes to stderr after the line is flushed.

Environment:

- `CELAVII_PANEL_HOST` — bind host, default `127.0.0.1`.
- `CELAVII_PANEL_PORT` — bind port; default `8765`. Set to `0` for random.
- `CELAVII_PANEL_DB` — SQLite path for the panel state database. Default `~/.celavii/panel/state.db`.

---

## 4. Stable Python imports

Third-party code **must** import Pydantic request/response models from `celavii_resolve.http.models`, not from the private `celavii_resolve.http.routes.*._models` modules. The re-export list is versioned; additions are non-breaking, removals or shape changes are breaking.

Current re-exports include (non-exhaustive — check `http/models.py` for the full `__all__`):

- `AnalyzeRequest`, `AnalyzeResponse`
- `BuildPlanRequest`, `ExecuteRequest`
- `UserSettings`, `RunListResponse`, `RunSummary`
- `ProjectInfoResponse`, `TimelineInfo`, `SourceAspectResponse`
- `SpeakerRosterResponse`, `SpeakerRosterEntry`
- `DetectPresetRequest`, `AnalyzeThemesRequest`
- `DeleteCutRequest`, `DeleteAllCutsRequest`, `DeleteRunRequest`, `CloneRunRequest`

Other stable top-level imports:

- `celavii_resolve.__version__` — package version string.
- `celavii_resolve.plugins.discover_tools(mcp)` / `.discover_panel_routes(app)` — manual re-invocation (rare).
- `celavii_resolve.plugins.registered_plugins()` — snapshot dict.
- `celavii_resolve.licensing.current_tier()` — returns `"oss"` or `"standard"`.

---

## 5. Migrations

The Panel boots with an idempotent SQLite migration runner at `celavii_resolve.migrations.runner.apply_migrations(db_path)`. It applies every file in `celavii_resolve/migrations/` matching the pattern `NNNN_*.sql`, in numeric order, tracking applied files in `_celavii_schema_migrations`.

OSS owns the un-prefixed tables (`recent_projects`, `custom_presets`, `cutmaster_sessions`, `panel_state`, …). The `studio_` table prefix is **reserved for the Celavii Studio bundle**. Third-party plugins that need their own tables should use a namespaced prefix matching their package, e.g. `myplugin_recent_items`.

Breaking changes to any OSS table require a new forward-only migration and a major bump.

---

## 6. What is **not** in this contract

Anything not listed above is internal and may change between minor versions. Notably:

- Module paths under `celavii_resolve.cutmaster.*`, `celavii_resolve.tools.*`, `celavii_resolve.workflows.*`, `celavii_resolve.intelligence.*` — all free to move.
- Private request/response models under `http.routes.*._models` — use `celavii_resolve.http.models` instead.
- FastMCP registry internals — tools are the product, not the specific `_tools` dict shape.
- Filesystem cache locations under `~/.celavii/` — treated as implementation detail.
- Logging format and logger names.

If you find yourself importing something that isn't documented here, file an issue requesting promotion to the stable surface rather than depending on it silently.
