"""Convention tests — verify all tool modules follow project standards.

These tests run without DaVinci Resolve and validate structural quality:
naming, docstrings, registration, and no duplicates.
"""

import ast
import importlib
from pathlib import Path

import pytest

PACKAGE_DIR = Path(__file__).resolve().parent.parent / "src" / "celavii_resolve"
TOOLS_DIR = PACKAGE_DIR / "tools"
WORKFLOWS_DIR = PACKAGE_DIR / "workflows"
AI_DIR = PACKAGE_DIR / "ai"
CUTMASTER_DIR = PACKAGE_DIR / "cutmaster"
TOOL_MODULES = [
    "celavii_resolve.tools.project",
    "celavii_resolve.tools.media_storage",
    "celavii_resolve.tools.media_pool",
    "celavii_resolve.tools.timeline_mgmt",
    "celavii_resolve.tools.timeline_edit",
    "celavii_resolve.tools.timeline_items",
    "celavii_resolve.tools.markers",
    "celavii_resolve.tools.color",
    "celavii_resolve.tools.fusion",
    "celavii_resolve.tools.render",
    "celavii_resolve.tools.gallery",
    "celavii_resolve.tools.fairlight",
    "celavii_resolve.tools.layout",
    "celavii_resolve.tools.graph",
    "celavii_resolve.tools.scripting",
    "celavii_resolve.tools.interchange",
    "celavii_resolve.workflows.ingest",
    "celavii_resolve.workflows.assembly",
    "celavii_resolve.workflows.delivery",
    "celavii_resolve.workflows.conform",
    "celavii_resolve.workflows.grade",
    "celavii_resolve.workflows.chroma_key",
    "celavii_resolve.ai.vision",
    "celavii_resolve.ai.color_assist",
    "celavii_resolve.ai.timeline_critique",
    "celavii_resolve.cutmaster.frame_math",
    "celavii_resolve.cutmaster.source_mapper",
    "celavii_resolve.cutmaster.subclips",
    "celavii_resolve.cutmaster.ffmpeg_audio",
    "celavii_resolve.cutmaster.vfr",
    "celavii_resolve.cutmaster.snapshot",
    "celavii_resolve.cutmaster.state",
    "celavii_resolve.cutmaster.stt",
    "celavii_resolve.cutmaster.stt_gemini",
    "celavii_resolve.cutmaster.stt_deepgram",
    "celavii_resolve.cutmaster.scrubber",
    "celavii_resolve.cutmaster.pipeline",
    "celavii_resolve.cutmaster.llm",
    "celavii_resolve.cutmaster.presets",
    "celavii_resolve.cutmaster.excludes",
    "celavii_resolve.cutmaster.formats",
    "celavii_resolve.cutmaster.captions",
    "celavii_resolve.cutmaster.time_mapping",
    "celavii_resolve.cutmaster.assembled",
    "celavii_resolve.cutmaster.tightener",
    "celavii_resolve.cutmaster.director",
    "celavii_resolve.cutmaster.marker_agent",
    "celavii_resolve.cutmaster.speakers",
    "celavii_resolve.cutmaster.per_clip_stt",
    "celavii_resolve.cutmaster.speaker_reconcile",
    "celavii_resolve.cutmaster.auto_detect",
    "celavii_resolve.cutmaster.themes",
    "celavii_resolve.cutmaster.resolve_segments",
    "celavii_resolve.cutmaster.execute",
]


def _collect_tool_functions() -> list[tuple[str, str]]:
    """Walk tool and workflow module ASTs and return (module, func_name) for every
    function decorated with @mcp.tool."""
    results = []
    scan_dirs = [
        (TOOLS_DIR, "celavii_resolve.tools"),
        (WORKFLOWS_DIR, "celavii_resolve.workflows"),
        (AI_DIR, "celavii_resolve.ai"),
        (CUTMASTER_DIR, "celavii_resolve.cutmaster"),
    ]
    for scan_dir, module_prefix in scan_dirs:
        for py_file in scan_dir.glob("*.py"):
            if py_file.name == "__init__.py":
                continue
            module_name = f"{module_prefix}.{py_file.stem}"
            tree = ast.parse(py_file.read_text())
            for node in ast.walk(tree):
                if not isinstance(node, ast.FunctionDef):
                    continue
                for dec in node.decorator_list:
                    # Match @mcp.tool
                    if (
                        isinstance(dec, ast.Attribute)
                        and isinstance(dec.value, ast.Name)
                        and dec.value.id == "mcp"
                        and dec.attr == "tool"
                    ):
                        results.append((module_name, node.name))
                        break
    return results


# ---- Fixture: collect once, reuse across tests ----

TOOLS = _collect_tool_functions()


class TestToolNaming:
    """Every tool must start with the celavii_ prefix."""

    def test_all_tools_have_prefix(self):
        missing = [(mod, name) for mod, name in TOOLS if not name.startswith("celavii_")]
        assert not missing, f"Tools without celavii_ prefix: {missing}"

    def test_tool_count_minimum(self):
        """Sanity check: we expect at least 190 tools."""
        assert len(TOOLS) >= 190, f"Only {len(TOOLS)} tools found — expected 190+"

    def test_no_duplicate_tool_names(self):
        """Tool names must be unique across all modules."""
        names = [name for _, name in TOOLS]
        duplicates = [n for n in names if names.count(n) > 1]
        assert not duplicates, f"Duplicate tool names: {set(duplicates)}"


class TestToolDocstrings:
    """Every tool function must have a docstring."""

    @pytest.fixture(params=TOOL_MODULES, ids=lambda m: m.split(".")[-1])
    def module(self, request):
        return importlib.import_module(request.param)

    def test_all_public_functions_have_docstrings(self, module):
        missing = []
        for name in dir(module):
            if not name.startswith("celavii_"):
                continue
            obj = getattr(module, name)
            if callable(obj) and not getattr(obj, "__doc__", None):
                missing.append(name)
        assert not missing, f"Tools without docstrings in {module.__name__}: {missing}"


class TestToolModuleImports:
    """Every tool module must import cleanly."""

    @pytest.mark.parametrize("module_name", TOOL_MODULES, ids=lambda m: m.split(".")[-1])
    def test_module_imports(self, module_name):
        mod = importlib.import_module(module_name)
        assert mod is not None


class TestToolRegistration:
    """Tools must actually be registered on the FastMCP instance."""

    def test_tools_registered_on_mcp(self):
        """At least 190 tools should be registered after importing all modules."""
        import asyncio

        import celavii_resolve  # noqa: F401 — triggers tool registration
        from celavii_resolve.config import mcp

        tools = asyncio.run(mcp.list_tools())
        count = len(tools)
        assert count >= 190, f"Only {count} tools registered — expected 190+"


class TestModuleCoverage:
    """Every .py file in tools/, workflows/, and ai/ should be in our import list."""

    def test_all_tool_files_imported(self):
        tool_files = {
            f"celavii_resolve.tools.{f.stem}"
            for f in TOOLS_DIR.glob("*.py")
            if f.name != "__init__.py"
        }
        workflow_files = {
            f"celavii_resolve.workflows.{f.stem}"
            for f in WORKFLOWS_DIR.glob("*.py")
            if f.name != "__init__.py"
        }
        ai_files = {
            f"celavii_resolve.ai.{f.stem}" for f in AI_DIR.glob("*.py") if f.name != "__init__.py"
        }
        cutmaster_files = {
            f"celavii_resolve.cutmaster.{f.stem}"
            for f in CUTMASTER_DIR.glob("*.py")
            if f.name != "__init__.py"
        }
        all_files = tool_files | workflow_files | ai_files | cutmaster_files
        imported = set(TOOL_MODULES)
        missing = all_files - imported
        assert not missing, f"Module files not imported in tests: {missing}"

    def test_all_tool_files_in_init(self):
        """All tool, workflow, and AI modules should be imported in __init__.py."""
        init_path = PACKAGE_DIR / "__init__.py"
        init_text = init_path.read_text()

        for scan_dir, import_prefix in [
            (TOOLS_DIR, "from .tools import"),
            (WORKFLOWS_DIR, "from .workflows import"),
            (AI_DIR, "from .ai import"),
        ]:
            py_files = {f.stem for f in scan_dir.glob("*.py") if f.name != "__init__.py"}
            missing = {f for f in py_files if f"{import_prefix} {f}" not in init_text}
            assert not missing, f"Modules not in __init__.py ({import_prefix}): {missing}"
