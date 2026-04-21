# Contributing to cutmaster-ai

Thanks for helping! This file covers the minimum you need to open a PR. Deeper architectural context — including the gotchas around DaVinci Resolve's scripting API — lives in [CLAUDE.md](CLAUDE.md); this doc is the on-ramp.

## Setup

Prerequisites:

- Python **3.11** or **3.12** — not 3.13 (ABI issues with `fusionscript`).
- [uv](https://docs.astral.sh/uv/) for dependency management.
- DaVinci Resolve **Studio** — only required for tools that touch a live Resolve session. The smoke and convention test suites run without it, so most contributions don't need Resolve installed at all.

```bash
git clone https://github.com/CelaviiHQ/cutmaster-ai
cd cutmaster-ai
uv sync --extra dev
uv run pre-commit install
```

## The local gate

Before opening a PR, run the same checks CI will run:

```bash
uv run pre-commit run --all-files    # ruff, format, secrets, hygiene
uv run pytest                         # smoke + convention + unit tests
```

If your PR touches a `@mcp.tool`, also reproduce the surface check:

```bash
uv run python scripts/dump_surface.py > /tmp/surface.json
diff tests/surface_snapshot.json /tmp/surface.json
```

Any diff requires a `### BREAKING` or `### Added` bullet under `## [Unreleased]` in [CHANGELOG.md](CHANGELOG.md) *and* a regenerated snapshot (`... > tests/surface_snapshot.json`). See [SURFACE.md § 7](SURFACE.md) for the full policy.

## The four-bucket model

Every new feature fits into exactly one of these. If it doesn't, it's probably two features.

| Bucket | Rule | Location |
|---|---|---|
| **Atomic Resolve op** | One function = one Resolve SDK call = one `@mcp.tool`. No logic. | `src/cutmaster_ai/tools/` |
| **Deterministic compound** | Chains multiple `tools/` ops. No LLM. | `src/cutmaster_ai/workflows/` |
| **Stateless LLM tool** | One MCP call → one LLM roundtrip → one answer. | `src/cutmaster_ai/intelligence/` |
| **Stateful AI product** | Owns state, multi-stage pipeline, optionally its own transport. | `src/cutmaster_ai/cutmaster/` |

[CLAUDE.md](CLAUDE.md) has the full model plus the Resolve API gotchas you'll need when writing tools (1-based indices, `or []` on every `GetSomething()` call, silent `AddMarker` failures, etc.).

## Commit messages

Conventional format:

- `feat:` — new feature or tool
- `fix:` — bug fix
- `docs:` — documentation only
- `refactor:` — code restructure, no behaviour change
- `test:` — add or update tests
- `chore:` — maintenance, CI, dependency updates

Example: `feat(tools): cutmaster_list_power_grade_albums`.

## Changelog

If your PR changes anything under `src/`, add a bullet under `## [Unreleased]` in [CHANGELOG.md](CHANGELOG.md). The [`changelog-check`](.github/workflows/changelog-check.yml) CI job enforces this on every PR. Tool-surface changes additionally must go under `### BREAKING` or `### Added` — enforced by the [`surface`](.github/workflows/ci.yml) job.

## Questions

Open an issue. Security reports go to the contact in [SECURITY.md](SECURITY.md) rather than a public issue.
