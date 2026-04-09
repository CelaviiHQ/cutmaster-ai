"""Tests for Phase 4 — skills, agents, and safety hooks.

Validates that all skill/agent markdown files exist, have correct frontmatter,
and that the hooks.json is valid.
"""

import json
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR = PROJECT_ROOT / "skills"
AGENTS_DIR = PROJECT_ROOT / "agents"
HOOKS_FILE = PROJECT_ROOT / "hooks" / "hooks.json"

EXPECTED_SKILLS = [
    "deliver",
    "preflight",
    "color-assist",
    "conform",
    "ingest",
    "assembly",
    "review",
    "export-stills",
]

EXPECTED_AGENTS = [
    "editor",
    "colorist",
    "vfx",
    "sound",
    "conform",
    "delivery",
    "producer",
]


class TestSkills:
    """Validate all skill files exist and have correct structure."""

    def test_all_skills_exist(self):
        for skill in EXPECTED_SKILLS:
            skill_file = SKILLS_DIR / skill / "SKILL.md"
            assert skill_file.is_file(), f"Skill file missing: {skill_file}"

    @pytest.mark.parametrize("skill", EXPECTED_SKILLS)
    def test_skill_has_frontmatter(self, skill):
        content = (SKILLS_DIR / skill / "SKILL.md").read_text()
        assert content.startswith("---"), f"Skill '{skill}' missing YAML frontmatter"
        # Find closing ---
        second_fence = content.index("---", 3)
        frontmatter = content[3:second_fence].strip()
        assert "name:" in frontmatter, f"Skill '{skill}' missing 'name:' in frontmatter"
        assert "description:" in frontmatter, f"Skill '{skill}' missing 'description:'"

    @pytest.mark.parametrize("skill", EXPECTED_SKILLS)
    def test_skill_has_content(self, skill):
        content = (SKILLS_DIR / skill / "SKILL.md").read_text()
        # Content after frontmatter should be non-trivial
        second_fence = content.index("---", 3) + 3
        body = content[second_fence:].strip()
        assert len(body) > 50, f"Skill '{skill}' has too little content ({len(body)} chars)"

    @pytest.mark.parametrize("skill", EXPECTED_SKILLS)
    def test_skill_name_matches_directory(self, skill):
        content = (SKILLS_DIR / skill / "SKILL.md").read_text()
        second_fence = content.index("---", 3)
        frontmatter = content[3:second_fence]
        # Extract name value
        for line in frontmatter.splitlines():
            if line.strip().startswith("name:"):
                name_val = line.split(":", 1)[1].strip()
                assert name_val == skill, (
                    f"Skill name '{name_val}' doesn't match directory '{skill}'"
                )
                break


class TestAgents:
    """Validate all agent files exist and have correct structure."""

    def test_all_agents_exist(self):
        for agent in EXPECTED_AGENTS:
            agent_file = AGENTS_DIR / f"{agent}.md"
            assert agent_file.is_file(), f"Agent file missing: {agent_file}"

    @pytest.mark.parametrize("agent", EXPECTED_AGENTS)
    def test_agent_has_frontmatter(self, agent):
        content = (AGENTS_DIR / f"{agent}.md").read_text()
        assert content.startswith("---"), f"Agent '{agent}' missing YAML frontmatter"
        second_fence = content.index("---", 3)
        frontmatter = content[3:second_fence].strip()
        assert "name:" in frontmatter, f"Agent '{agent}' missing 'name:'"
        assert "description:" in frontmatter, f"Agent '{agent}' missing 'description:'"
        assert "when_to_use:" in frontmatter, f"Agent '{agent}' missing 'when_to_use:'"
        assert "tools:" in frontmatter, f"Agent '{agent}' missing 'tools:'"

    @pytest.mark.parametrize("agent", EXPECTED_AGENTS)
    def test_agent_tools_reference_celavii(self, agent):
        content = (AGENTS_DIR / f"{agent}.md").read_text()
        # All tool references should use celavii-resolve server name
        assert "mcp__celavii-resolve__celavii_" in content, (
            f"Agent '{agent}' tools don't reference celavii-resolve MCP server"
        )

    @pytest.mark.parametrize("agent", EXPECTED_AGENTS)
    def test_agent_has_body_content(self, agent):
        content = (AGENTS_DIR / f"{agent}.md").read_text()
        second_fence = content.index("---", 3) + 3
        body = content[second_fence:].strip()
        assert len(body) > 100, f"Agent '{agent}' has too little body content"

    @pytest.mark.parametrize("agent", EXPECTED_AGENTS)
    def test_agent_name_matches_file(self, agent):
        content = (AGENTS_DIR / f"{agent}.md").read_text()
        second_fence = content.index("---", 3)
        frontmatter = content[3:second_fence]
        for line in frontmatter.splitlines():
            if line.strip().startswith("name:"):
                name_val = line.split(":", 1)[1].strip()
                assert name_val == agent, (
                    f"Agent name '{name_val}' doesn't match file '{agent}.md'"
                )
                break


class TestHooks:
    """Validate the safety hooks configuration."""

    def test_hooks_file_exists(self):
        assert HOOKS_FILE.is_file(), "hooks/hooks.json not found"

    def test_hooks_is_valid_json(self):
        content = HOOKS_FILE.read_text()
        data = json.loads(content)
        assert "hooks" in data

    def test_hooks_has_pre_tool_use(self):
        data = json.loads(HOOKS_FILE.read_text())
        assert "PreToolUse" in data["hooks"]
        pre_hooks = data["hooks"]["PreToolUse"]
        assert len(pre_hooks) >= 2, "Expected at least 2 PreToolUse hooks"

    def test_hooks_has_post_tool_use(self):
        data = json.loads(HOOKS_FILE.read_text())
        assert "PostToolUse" in data["hooks"]
        post_hooks = data["hooks"]["PostToolUse"]
        assert len(post_hooks) >= 1, "Expected at least 1 PostToolUse hook"

    def test_hooks_block_env_editing(self):
        data = json.loads(HOOKS_FILE.read_text())
        pre_hooks = data["hooks"]["PreToolUse"]
        env_hook = next(
            (h for h in pre_hooks if h.get("matcher") == "Edit|Write"),
            None,
        )
        assert env_hook is not None, "Missing .env protection hook"
        command = env_hook["hooks"][0]["command"]
        assert ".env" in command

    def test_hooks_warn_on_destructive_ops(self):
        data = json.loads(HOOKS_FILE.read_text())
        pre_hooks = data["hooks"]["PreToolUse"]
        destructive_hook = next(
            (h for h in pre_hooks if "delete" in h.get("matcher", "").lower()),
            None,
        )
        assert destructive_hook is not None, "Missing destructive operation warning hook"

    def test_hooks_warn_on_scripting(self):
        data = json.loads(HOOKS_FILE.read_text())
        pre_hooks = data["hooks"]["PreToolUse"]
        script_hook = next(
            (h for h in pre_hooks if "execute" in h.get("matcher", "").lower()),
            None,
        )
        assert script_hook is not None, "Missing scripting caution hook"

    def test_hooks_auto_lint_python(self):
        data = json.loads(HOOKS_FILE.read_text())
        post_hooks = data["hooks"]["PostToolUse"]
        lint_hook = next(
            (h for h in post_hooks if "ruff" in h["hooks"][0].get("command", "")),
            None,
        )
        assert lint_hook is not None, "Missing ruff auto-lint hook"
