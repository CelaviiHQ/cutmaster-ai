"""Smoke tests — verify all modules import without error (no Resolve needed)."""

import importlib


def test_package_imports():
    """The top-level package should import without DaVinci Resolve running."""
    mod = importlib.import_module("celavii_resolve")
    assert hasattr(mod, "__version__")
    assert hasattr(mod, "mcp")
    assert hasattr(mod, "main")


def test_config_imports():
    from celavii_resolve.config import mcp
    assert mcp.name == "celavii-resolve"


def test_errors_imports():
    from celavii_resolve.errors import (
        ResolveError,
        ResolveNotRunning,
        safe_resolve_call,
    )
    assert issubclass(ResolveNotRunning, ResolveError)
    assert callable(safe_resolve_call)


def test_constants_imports():
    from celavii_resolve.constants import (
        COMPOSITE_MODES,
        MARKER_COLORS,
        PAGES,
        TRACK_TYPES,
    )
    assert "Blue" in MARKER_COLORS
    assert "video" in TRACK_TYPES
    assert "edit" in PAGES
    assert len(COMPOSITE_MODES) > 10


def test_resolve_helpers_import():
    from celavii_resolve.resolve import (
        _boilerplate,
        get_resolve,
    )
    assert callable(get_resolve)
    assert callable(_boilerplate)


def test_resources_import():
    from celavii_resolve import resources  # noqa: F401


def test_project_tools_import():
    from celavii_resolve.tools import project  # noqa: F401


def test_tool_naming_convention():
    """All registered tools should start with 'celavii_'."""
    from celavii_resolve.config import mcp

    # FastMCP 3.0+ stores tools — access may vary by version
    tools = getattr(mcp, "_tools", None) or getattr(mcp, "tools", {})
    if hasattr(tools, "values"):
        for tool in tools.values():
            name = getattr(tool, "name", str(tool))
            assert name.startswith("celavii_"), f"Tool '{name}' missing celavii_ prefix"
