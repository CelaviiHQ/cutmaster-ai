## Summary

<!-- 1–2 sentences on what this PR changes and why. -->

## Bucket

Which of the four responsibility buckets does this land in? (See [CLAUDE.md § Responsibility model](../CLAUDE.md).)

- [ ] `tools/` — atomic Resolve op (one SDK call = one `@mcp.tool`)
- [ ] `workflows/` — deterministic compound (chains `tools/` ops, no LLM)
- [ ] `intelligence/` — stateless LLM tool
- [ ] `cutmaster/` — stateful AI product (owns state, multi-stage pipeline)
- [ ] None of the above — infra, docs, tests, or CI

## Checklist

- [ ] Local gate passes: `uv run pre-commit run --all-files && uv run pytest`
- [ ] `src/` change → bullet added under `## [Unreleased]` in [CHANGELOG.md](../CHANGELOG.md)
- [ ] `@mcp.tool` signature change → `tests/surface_snapshot.json` regenerated with `uv run python scripts/dump_surface.py > tests/surface_snapshot.json` and a `### BREAKING` or `### Added` CHANGELOG bullet
- [ ] New or changed tools have docstrings (FastMCP surfaces them to clients)
- [ ] Tests added or updated for new behaviour
