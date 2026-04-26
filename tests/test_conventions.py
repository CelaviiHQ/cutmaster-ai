"""Convention tests — verify all tool modules follow project standards.

These tests run without DaVinci Resolve and validate structural quality:
naming, docstrings, registration, and no duplicates.
"""

import ast
import importlib
from pathlib import Path

import pytest

PACKAGE_DIR = Path(__file__).resolve().parent.parent / "src" / "cutmaster_ai"
TOOLS_DIR = PACKAGE_DIR / "tools"
WORKFLOWS_DIR = PACKAGE_DIR / "workflows"
INTEL_DIR = PACKAGE_DIR / "intelligence"
CUTMASTER_DIR = PACKAGE_DIR / "cutmaster"
TOOL_MODULES = [
    "cutmaster_ai.tools.project",
    "cutmaster_ai.tools.media_storage",
    "cutmaster_ai.tools.media_pool",
    "cutmaster_ai.tools.timeline_mgmt",
    "cutmaster_ai.tools.timeline_edit",
    "cutmaster_ai.tools.timeline_items",
    "cutmaster_ai.tools.markers",
    "cutmaster_ai.tools.color",
    "cutmaster_ai.tools.fusion",
    "cutmaster_ai.tools.render",
    "cutmaster_ai.tools.gallery",
    "cutmaster_ai.tools.fairlight",
    "cutmaster_ai.tools.layout",
    "cutmaster_ai.tools.graph",
    "cutmaster_ai.tools.scripting",
    "cutmaster_ai.tools.interchange",
    "cutmaster_ai.tools.lut_registry",
    "cutmaster_ai.workflows.ingest",
    "cutmaster_ai.workflows.assembly",
    "cutmaster_ai.workflows.delivery",
    "cutmaster_ai.workflows.conform",
    "cutmaster_ai.workflows.grade",
    "cutmaster_ai.workflows.chroma_key",
    "cutmaster_ai.intelligence.vision",
    "cutmaster_ai.intelligence.color_assist",
    "cutmaster_ai.intelligence.timeline_critique",
    "cutmaster_ai.intelligence.story_critic",
    "cutmaster_ai.intelligence.llm",
    "cutmaster_ai.cutmaster.media.frame_math",
    "cutmaster_ai.cutmaster.resolve_ops.source_mapper",
    "cutmaster_ai.cutmaster.resolve_ops.subclips",
    "cutmaster_ai.cutmaster.media.ffmpeg_audio",
    "cutmaster_ai.cutmaster.media.vfr",
    "cutmaster_ai.cutmaster.core.snapshot",
    "cutmaster_ai.cutmaster.core.state",
    "cutmaster_ai.cutmaster.stt",
    "cutmaster_ai.cutmaster.stt.base",
    "cutmaster_ai.cutmaster.stt.gemini",
    "cutmaster_ai.cutmaster.stt.deepgram",
    "cutmaster_ai.cutmaster.analysis.scrubber",
    "cutmaster_ai.cutmaster.core.pipeline",
    "cutmaster_ai.cutmaster.data.presets",
    "cutmaster_ai.cutmaster.data.excludes",
    "cutmaster_ai.cutmaster.data.content_profiles",
    "cutmaster_ai.cutmaster.data.cut_intents",
    "cutmaster_ai.cutmaster.data.axis_compat",
    "cutmaster_ai.cutmaster.data.axis_resolution",
    "cutmaster_ai.cutmaster.media.formats",
    "cutmaster_ai.cutmaster.media.source_resolver",
    "cutmaster_ai.cutmaster.analysis.captions",
    "cutmaster_ai.cutmaster.analysis.take_dedup",
    "cutmaster_ai.cutmaster.analysis._sentences",
    "cutmaster_ai.cutmaster.media.time_mapping",
    "cutmaster_ai.cutmaster.resolve_ops.assembled",
    "cutmaster_ai.cutmaster.resolve_ops.groups",
    "cutmaster_ai.cutmaster.analysis.tightener",
    "cutmaster_ai.cutmaster.core.director",
    "cutmaster_ai.cutmaster.analysis.marker_agent",
    "cutmaster_ai.cutmaster.stt.speakers",
    "cutmaster_ai.cutmaster.stt.per_clip",
    "cutmaster_ai.cutmaster.stt.reconcile",
    "cutmaster_ai.cutmaster.analysis.auto_detect",
    "cutmaster_ai.cutmaster.analysis.auto_detect.cue_vocab",
    "cutmaster_ai.cutmaster.analysis.auto_detect.metadata",
    "cutmaster_ai.cutmaster.analysis.auto_detect.opening",
    "cutmaster_ai.cutmaster.analysis.auto_detect.scoring",
    "cutmaster_ai.cutmaster.analysis.auto_detect.structure",
    "cutmaster_ai.cutmaster.analysis.themes",
    "cutmaster_ai.cutmaster.resolve_ops.segments",
    "cutmaster_ai.cutmaster.core.execute",
    "cutmaster_ai.cutmaster.core.timeouts",
    "cutmaster_ai.cutmaster.media.ffmpeg_frames",
    "cutmaster_ai.cutmaster.analysis.shot_tagger",
    "cutmaster_ai.cutmaster.analysis.shot_color_painter",
    "cutmaster_ai.cutmaster.analysis.shot_metadata_stamper",
    "cutmaster_ai.cutmaster.analysis.boundary_validator",
    "cutmaster_ai.cutmaster.core.validator_loop",
    "cutmaster_ai.cutmaster.analysis.audio_cues",
    "cutmaster_ai.cutmaster.analysis._sanitize",
    "cutmaster_ai.cutmaster.resolve_ops.track_picker",
]

# Route modules aren't tool modules — the TOOL_MODULES guard doesn't cover
# them. The route submodule `runs.py` is imported by the cutmaster router
# __init__ so tests exercise it via TestClient in test_runs_endpoints.


def _collect_tool_functions() -> list[tuple[str, str]]:
    """Walk tool and workflow module ASTs and return (module, func_name) for every
    function decorated with @mcp.tool."""
    results = []
    scan_dirs = [
        (TOOLS_DIR, "cutmaster_ai.tools"),
        (WORKFLOWS_DIR, "cutmaster_ai.workflows"),
        (INTEL_DIR, "cutmaster_ai.intelligence"),
        (CUTMASTER_DIR, "cutmaster_ai.cutmaster"),
    ]
    for scan_dir, module_prefix in scan_dirs:
        for py_file in scan_dir.rglob("*.py"):
            if py_file.name == "__init__.py":
                continue
            rel = py_file.relative_to(scan_dir).with_suffix("")
            module_name = f"{module_prefix}." + ".".join(rel.parts)
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
    """Every tool must start with the cutmaster_ prefix."""

    def test_all_tools_have_prefix(self):
        missing = [(mod, name) for mod, name in TOOLS if not name.startswith("cutmaster_")]
        assert not missing, f"Tools without cutmaster_ prefix: {missing}"

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
            if not name.startswith("cutmaster_"):
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

        import cutmaster_ai  # noqa: F401 — triggers tool registration
        from cutmaster_ai.config import mcp

        tools = asyncio.run(mcp.list_tools())
        count = len(tools)
        assert count >= 190, f"Only {count} tools registered — expected 190+"


class TestModuleCoverage:
    """Every .py file in tools/, workflows/, and ai/ should be in our import list."""

    def test_all_tool_files_imported(self):
        tool_files = {
            f"cutmaster_ai.tools.{f.stem}"
            for f in TOOLS_DIR.glob("*.py")
            if f.name != "__init__.py"
        }
        workflow_files = {
            f"cutmaster_ai.workflows.{f.stem}"
            for f in WORKFLOWS_DIR.glob("*.py")
            if f.name != "__init__.py"
        }
        intel_files = {
            f"cutmaster_ai.intelligence.{f.stem}"
            for f in INTEL_DIR.glob("*.py")
            if f.name != "__init__.py"
        }
        cutmaster_files = {
            "cutmaster_ai.cutmaster." + ".".join(f.relative_to(CUTMASTER_DIR).with_suffix("").parts)
            for f in CUTMASTER_DIR.rglob("*.py")
            if f.name != "__init__.py"
        }
        all_files = tool_files | workflow_files | intel_files | cutmaster_files
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
            (INTEL_DIR, "from .intelligence import"),
        ]:
            py_files = {f.stem for f in scan_dir.glob("*.py") if f.name != "__init__.py"}
            missing = {f for f in py_files if f"{import_prefix} {f}" not in init_text}
            assert not missing, f"Modules not in __init__.py ({import_prefix}): {missing}"
