# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.0] — 2026-04-18

Major restructuring pass to prepare the repo for open-source release. No behaviour changes — all ~240 tools behave identically, but many import paths have changed.

### Changed

- **`ai/` → `intelligence/`**: the old `ai/` subpackage is renamed to `intelligence/` to make room for a broader distinction between *stateless LLM tools* (single MCP call → single LLM roundtrip) and *stateful AI products* (CutMaster and future siblings).
- **`cutmaster/llm.py` promoted to `intelligence/llm.py`**: the shared LLM dispatch layer now lives under `intelligence/` so future products can reuse it without cross-imports from CutMaster.
- **`cutmaster/` split into 6 subpackages** (`core/`, `stt/`, `analysis/`, `media/`, `resolve_ops/`, `data/`) — see [docs/CUTMASTER_ARCHITECTURE.md](docs/CUTMASTER_ARCHITECTURE.md).
- **`http/routes/cutmaster.py` (1,020 LOC) → `http/routes/cutmaster/` package** with feature-split modules (`analyze`, `presets`, `info`, `build`, `execute`). URL prefix `/cutmaster/*` preserved — no panel client changes.
- **`panel/` → `apps/panel/`** and **`panel/resolve-plugin/` → `apps/resolve-plugin/`**. Non-Python deliverables now live under `apps/`.
- **`install.py` / `build-plugin.sh` → `scripts/`**. Top-level is cleaner.
- **`src/celavii_resolve/lut_registry.py` → `src/celavii_resolve/tools/lut_registry.py`** — it's a tool module, belongs with its siblings.
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
