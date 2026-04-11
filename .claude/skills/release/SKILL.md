---
name: release
description: "Bump version, update changelog, build plugin, tag, and push a new release."
---

# Release a New Version

## Steps

1. **Determine version bump** — ask the user or infer from recent commits:
   - `patch` (0.1.0 → 0.1.1) — bug fixes only
   - `minor` (0.1.0 → 0.2.0) — new features, backward compatible
   - `major` (0.1.0 → 1.0.0) — breaking changes

2. **Update version in:**
   - `pyproject.toml` → `version = "X.Y.Z"`
   - `.claude-plugin/plugin.json` → `"version": "X.Y.Z"`

3. **Run the full test suite:**
```bash
uv run ruff check src/ --fix
uv run ruff format src/
uv run pytest tests/ -v
```

4. **Build the plugin ZIP:**
```bash
bash build-plugin.sh --skills-only
```

5. **Commit and tag:**
```bash
git add -A
git commit -m "release: vX.Y.Z"
git tag -a vX.Y.Z -m "Release vX.Y.Z"
```

6. **Push:**
```bash
git push origin main --tags
```

## Version locations

| File | Field |
|------|-------|
| `pyproject.toml` | `version = "..."` |
| `.claude-plugin/plugin.json` | `"version": "..."` |

Both must match.
