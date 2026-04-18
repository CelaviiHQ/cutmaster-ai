---
name: update-skills
description: "Edit user-facing SKILL.md files in skills/ — add tools, update parameters, fix docs."
---

# Update Skills

Skills live in two places:
- `skills/` (root) — **user-facing**, bundled in the plugin ZIP
- `.claude/skills/` — **developer-facing**, for working on this repo

This skill is for editing the **user-facing** skills in `skills/`.

## Skill Format

Each skill is a directory containing a `SKILL.md` with YAML frontmatter:

```markdown
---
name: skill-name
description: "One-line description of what this skill does."
---

# Skill Title

Instructions for Claude Code when this skill is invoked...
```

## Current User-Facing Skills

| Skill | File |
|-------|------|
| assembly | `skills/assembly/SKILL.md` |
| color-assist | `skills/color-assist/SKILL.md` |
| conform | `skills/conform/SKILL.md` |
| deliver | `skills/deliver/SKILL.md` |
| export-stills | `skills/export-stills/SKILL.md` |
| grade-log | `skills/grade-log/SKILL.md` |
| ingest | `skills/ingest/SKILL.md` |
| preflight | `skills/preflight/SKILL.md` |
| review | `skills/review/SKILL.md` |

## When updating a skill

1. Read the current SKILL.md
2. Check which MCP tools it references — verify they still exist in `src/celavii_resolve/`
3. Update tool names, parameters, or workflows as needed
4. Keep the YAML frontmatter intact
5. Rebuild the plugin if distributing: `bash scripts/build-plugin.sh`

## Adding a new user-facing skill

```bash
mkdir -p skills/new-skill-name
# Create skills/new-skill-name/SKILL.md with frontmatter
```

Then update `README.md` skill count and skill table.
