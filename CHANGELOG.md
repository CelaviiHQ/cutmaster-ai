# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### BREAKING

- **Renamed Python distribution from `celavii-resolve` to `cutmaster-ai`.**
  Users must `pip uninstall celavii-resolve && pip install cutmaster-ai`.
  Imports change from `celavii_resolve` to `cutmaster_ai`.
  Entry-point groups for third-party plugins renamed to
  `cutmaster_ai.tools` and `cutmaster_ai.panel_routes`. Console scripts
  are `cutmaster-ai` and `cutmaster-ai-panel`.
- **MCP tool names renamed** from `celavii_*` prefix to `cutmaster_*`
  prefix (~280 tools). Downstream skills, agents, and hook matchers
  using `mcp__celavii-resolve__celavii_*` must update to
  `mcp__cutmaster-ai__cutmaster_*`.
- **Environment variables renamed** `CELAVII_PANEL_HOST` / `_PORT` / `_DB`
  → `CUTMASTER_PANEL_HOST` / `_PORT` / `_DB`. Same for all other
  `CELAVII_*` config (`_LOG_FORMAT`, `_STT_PROVIDER`, `_DEEPGRAM_*`,
  `_VISION_CONCURRENCY`, `_<AGENT>_MODEL`, etc.).
- **Default filesystem paths renamed** `~/.celavii/panel/state.db` →
  `~/.cutmaster/panel/state.db`; `~/.celavii/cutmaster/` cache roots
  → `~/.cutmaster/cutmaster/`; `~/Documents/celavii-*` → `~/Documents/cutmaster-*`.
- **launchd Label + plist filename** `com.celavii.resolve-mcp` →
  `ai.cutmaster.mcp`; plist file renamed accordingly. Existing users
  need `launchctl unload` the old plist and re-install the new one.
- **LUT vendor directory** renamed `~/Library/.../LUT/Celavii/` →
  `~/Library/.../LUT/CutMaster/`.
- **Claude Code plugin name** in `.claude-plugin/plugin.json` renamed
  to `cutmaster-ai` — users who installed the old plugin will see two
  entries until they uninstall the old one.

The Celavii (company / parent org) brand is retained for: author field,
contact emails (`engineering@celavii.com`, `security@celavii.com`),
GitHub organisation `CelaviiHQ`, and the SQLite table prefix `studio_`
reserved for the closed-source Studio bundle. See `docs/naming.md` in
the private `cutmaster-studio` repo for the full brand hierarchy.

### Added

- Plugin discovery via two entry-point groups: `cutmaster_ai.tools` (FastMCP)
  and `cutmaster_ai.panel_routes` (FastAPI). Third-party packages can
  register capabilities on either surface without touching OSS code. See
  [SURFACE.md](SURFACE.md) and `src/cutmaster_ai/plugins.py`.
- `GET /pro/status` endpoint on the Panel HTTP server reporting
  `{tier, plugins: {tools, panel_routes}}`.
- `cutmaster_ai.licensing.current_tier()` — returns `"oss"` or
  `"standard"` based on whether any plugin has registered.
- `cutmaster-ai-panel` emits `PANEL_READY http://host:port` as its
  first stdout line so supervisors can discover a randomly assigned port
  (`CUTMASTER_PANEL_PORT=0` picks a free port).
- Idempotent SQLite migration runner at
  `cutmaster_ai.migrations.runner.apply_migrations(db_path)` plus
  `0001_init.sql` creating the initial panel state tables
  (`recent_projects`, `custom_presets`, `cutmaster_sessions`,
  `panel_state`). Runs at Panel boot; path via `CUTMASTER_PANEL_DB`.
- Stable Pydantic model re-exports at `cutmaster_ai.http.models` —
  plugins should import from here instead of the private
  `http.routes.*._models`.
- `SURFACE.md` documenting the versioned consumption contract for
  plugin authors and embedders.
- GitHub Actions workflows: `publish.yml` (tag → PyPI via Trusted
  Publishing) and `changelog-check.yml` (PRs touching `src/` must add a
  bullet under `## [Unreleased]`).

### Changed

- README and CLAUDE.md updated from "two consumers" to "three consumers"
  — MCP, Panel, and CutMaster Studio (the paid macOS app built on top of
  this package).

## [0.2.0] — 2026-04-18

Major restructuring pass to prepare the repo for open-source release. No behaviour changes — all ~240 tools behave identically, but many import paths have changed.

### Changed

- **`ai/` → `intelligence/`**: the old `ai/` subpackage is renamed to `intelligence/` to make room for a broader distinction between *stateless LLM tools* (single MCP call → single LLM roundtrip) and *stateful AI products* (CutMaster and future siblings).
- **`cutmaster/llm.py` promoted to `intelligence/llm.py`**: the shared LLM dispatch layer now lives under `intelligence/` so future products can reuse it without cross-imports from CutMaster.
- **`cutmaster/` split into 6 subpackages** (`core/`, `stt/`, `analysis/`, `media/`, `resolve_ops/`, `data/`) — see [docs/CUTMASTER_ARCHITECTURE.md](docs/CUTMASTER_ARCHITECTURE.md).
- **`http/routes/cutmaster.py` (1,020 LOC) → `http/routes/cutmaster/` package** with feature-split modules (`analyze`, `presets`, `info`, `build`, `execute`). URL prefix `/cutmaster/*` preserved — no panel client changes.
- **`panel/` → `apps/panel/`** and **`panel/resolve-plugin/` → `apps/resolve-plugin/`**. Non-Python deliverables now live under `apps/`.
- **`install.py` / `build-plugin.sh` → `scripts/`**. Top-level is cleaner.
- **`src/cutmaster_ai/lut_registry.py` → `src/cutmaster_ai/tools/lut_registry.py`** — it's a tool module, belongs with its siblings.
- **`launchd/` → `scripts/launchd/`**.

### Added

- `intelligence/` subpackage as a named home for single-shot LLM tools.
- `.pre-commit-config.yaml` with hooks that block `/Users/`, `/home/`, and common API-key patterns.
- [SECURITY.md](SECURITY.md) — responsible disclosure policy.
- [.github/CODEOWNERS](.github/CODEOWNERS) — auto-assigned review on sensitive paths.
- [docs/CONTRIBUTING.md](docs/CONTRIBUTING.md) — first-time setup + responsibility model.
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — public-facing layer model.
- [docs/MCP_TOOLS.md](docs/MCP_TOOLS.md) — auto-generated tool catalogue.
- [apps/README.md](apps/README.md) — describes the non-Python deliverables.

### Removed

- Duplicate `.claude/agents/` (canonical copies live in top-level `agents/` for plugin distribution).
- `.mcp.json.backup`.

### Security

- Repository history audited with trufflehog 3.94.3 and gitleaks 8.30.1 — 0 secrets found.
- Pre-commit hooks now block accidental commit of hardcoded local paths.

## [0.1.0] — 2026-04-10

Initial pre-release with ~240 tools, CutMaster v2-8 (per-clip STT, speakers, clip hunter), and React Workflow Integration panel.
