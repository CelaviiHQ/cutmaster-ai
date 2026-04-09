"""Tests for the @safe_resolve_call decorator and exception hierarchy."""

from celavii_resolve.errors import (
    BinNotFound,
    ClipNotFound,
    ItemNotFound,
    ProjectNotOpen,
    RenderError,
    ResolveError,
    ResolveNotRunning,
    StudioRequired,
    TimelineNotFound,
    safe_resolve_call,
)


class TestExceptionHierarchy:
    """All custom exceptions should inherit from ResolveError."""

    def test_resolve_not_running(self):
        assert issubclass(ResolveNotRunning, ResolveError)

    def test_project_not_open(self):
        assert issubclass(ProjectNotOpen, ResolveError)

    def test_timeline_not_found(self):
        assert issubclass(TimelineNotFound, ResolveError)

    def test_bin_not_found(self):
        assert issubclass(BinNotFound, ResolveError)

    def test_clip_not_found(self):
        assert issubclass(ClipNotFound, ResolveError)

    def test_item_not_found(self):
        assert issubclass(ItemNotFound, ResolveError)

    def test_studio_required(self):
        assert issubclass(StudioRequired, ResolveError)

    def test_render_error(self):
        assert issubclass(RenderError, ResolveError)


class TestSafeResolveCall:
    """The decorator should catch exceptions and return error strings."""

    def test_passes_through_normal_return(self):
        @safe_resolve_call
        def fn():
            return "hello"

        assert fn() == "hello"

    def test_catches_value_error_from_boilerplate(self):
        @safe_resolve_call
        def fn():
            raise ValueError("Error: No project open")

        result = fn()
        assert result == "Error: No project open"

    def test_catches_resolve_error(self):
        @safe_resolve_call
        def fn():
            raise ResolveNotRunning("DaVinci Resolve is not running")

        result = fn()
        assert "Error:" in result
        assert "not running" in result

    def test_catches_attribute_error(self):
        @safe_resolve_call
        def fn():
            raise AttributeError("'NoneType' has no attribute 'GetName'")

        result = fn()
        assert "Error:" in result
        assert "API" in result
        assert "fn" in result  # function name included

    def test_catches_type_error(self):
        @safe_resolve_call
        def fn():
            raise TypeError("expected str, got int")

        result = fn()
        assert "Error:" in result
        assert "fn" in result

    def test_catches_generic_exception(self):
        @safe_resolve_call
        def fn():
            raise RuntimeError("something broke")

        result = fn()
        assert "Error:" in result
        assert "Unexpected" in result
        assert "something broke" in result

    def test_preserves_function_name(self):
        @safe_resolve_call
        def my_specific_tool():
            raise RuntimeError("fail")

        result = my_specific_tool()
        assert "my_specific_tool" in result

    def test_preserves_docstring(self):
        @safe_resolve_call
        def fn():
            """This is a documented tool."""
            return "ok"

        assert fn.__doc__ == "This is a documented tool."

    def test_passes_args_and_kwargs(self):
        @safe_resolve_call
        def fn(a, b, c=10):
            return f"{a}-{b}-{c}"

        assert fn(1, 2, c=3) == "1-2-3"

    def test_returns_string_not_exception(self):
        @safe_resolve_call
        def fn():
            raise ResolveError("test")

        result = fn()
        assert isinstance(result, str)
        assert not isinstance(result, Exception)
