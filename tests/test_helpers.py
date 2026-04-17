"""Tests for helper functions — path safety, serialization, constants.

These tests run without DaVinci Resolve.
"""

import os
import platform

import pytest


class TestResolveSafeDir:
    """_resolve_safe_dir should redirect sandbox paths."""

    def test_normal_path_unchanged(self):
        from celavii_resolve.resolve import _resolve_safe_dir

        result = _resolve_safe_dir("/Users/someone/Documents/exports")
        assert result == "/Users/someone/Documents/exports"

    @pytest.mark.skipif(platform.system() != "Darwin", reason="macOS only")
    def test_var_folders_redirected_macos(self):
        from celavii_resolve.resolve import _resolve_safe_dir

        result = _resolve_safe_dir("/var/folders/ab/xyz/T/resolve-out")
        assert "resolve-exports" in result
        assert not result.startswith("/var/")

    @pytest.mark.skipif(platform.system() != "Darwin", reason="macOS only")
    def test_private_var_redirected_macos(self):
        from celavii_resolve.resolve import _resolve_safe_dir

        result = _resolve_safe_dir("/private/var/folders/ab/xyz/T/out")
        assert "resolve-exports" in result

    def test_home_path_unchanged(self):
        from celavii_resolve.resolve import _resolve_safe_dir

        home = os.path.expanduser("~")
        result = _resolve_safe_dir(os.path.join(home, "Desktop", "output"))
        assert result == os.path.join(home, "Desktop", "output")


class TestValidatePathWithin:
    """_validate_path_within should detect path traversal."""

    def test_valid_path_within(self):
        from celavii_resolve.resolve import _validate_path_within

        assert _validate_path_within("/opt/presets/my_preset.xml", "/opt/presets") is True

    def test_path_traversal_blocked(self):
        from celavii_resolve.resolve import _validate_path_within

        assert _validate_path_within("/opt/presets/../../etc/passwd", "/opt/presets") is False

    def test_exact_root_is_valid(self):
        from celavii_resolve.resolve import _validate_path_within

        assert _validate_path_within("/opt/presets", "/opt/presets") is True

    def test_sibling_directory_blocked(self):
        from celavii_resolve.resolve import _validate_path_within

        assert _validate_path_within("/opt/other/file.xml", "/opt/presets") is False


class TestSerializer:
    """_ser should safely convert various types to JSON-safe values."""

    def test_none(self):
        from celavii_resolve.resolve import _ser

        assert _ser(None) is None

    def test_primitives(self):
        from celavii_resolve.resolve import _ser

        assert _ser("hello") == "hello"
        assert _ser(42) == 42
        assert _ser(3.14) == 3.14
        assert _ser(True) is True

    def test_dict(self):
        from celavii_resolve.resolve import _ser

        result = _ser({"key": "value", "num": 1})
        assert result == {"key": "value", "num": 1}

    def test_list(self):
        from celavii_resolve.resolve import _ser

        result = _ser([1, "two", 3.0])
        assert result == [1, "two", 3.0]

    def test_nested_structure(self):
        from celavii_resolve.resolve import _ser

        result = _ser({"items": [{"name": "clip1"}, {"name": "clip2"}]})
        assert result == {"items": [{"name": "clip1"}, {"name": "clip2"}]}

    def test_unknown_object_becomes_string(self):
        from celavii_resolve.resolve import _ser

        class FakeResolveObj:
            def __repr__(self):
                return "<FakeResolveObj>"

        result = _ser(FakeResolveObj())
        assert result == "<FakeResolveObj>"

    def test_tuple_becomes_list(self):
        from celavii_resolve.resolve import _ser

        result = _ser((1, 2, 3))
        assert result == [1, 2, 3]


class TestConstants:
    """Constants should have expected values and sizes."""

    def test_marker_colors_count(self):
        from celavii_resolve.constants import MARKER_COLORS

        assert len(MARKER_COLORS) == 16

    def test_clip_colors_count(self):
        from celavii_resolve.constants import CLIP_COLORS

        assert len(CLIP_COLORS) == 16

    def test_track_types(self):
        from celavii_resolve.constants import TRACK_TYPES

        assert {"video", "audio", "subtitle"} == TRACK_TYPES

    def test_pages(self):
        from celavii_resolve.constants import PAGES

        assert "edit" in PAGES
        assert "color" in PAGES
        assert "deliver" in PAGES
        assert len(PAGES) == 7

    def test_composite_modes_has_normal(self):
        from celavii_resolve.constants import COMPOSITE_MODES

        assert "Normal" in COMPOSITE_MODES
        assert "Multiply" in COMPOSITE_MODES
        assert "Screen" in COMPOSITE_MODES

    def test_export_types_mapping(self):
        from celavii_resolve.constants import EXPORT_TYPES

        assert EXPORT_TYPES["EDL"] == 2
        assert EXPORT_TYPES["FCPXML"] == 4
        assert EXPORT_TYPES["OTIO"] == 8

    def test_render_presets(self):
        from celavii_resolve.constants import RENDER_PRESETS

        assert "h264" in RENDER_PRESETS
        assert RENDER_PRESETS["h264"]["format"] == "mp4"
        assert RENDER_PRESETS["prores422hq"]["codec"] == "ProRes422HQ"

    def test_studio_only_features(self):
        from celavii_resolve.constants import STUDIO_ONLY_FEATURES

        assert "TranscribeAudio" in STUDIO_ONLY_FEATURES
        assert "DetectSceneCuts" in STUDIO_ONLY_FEATURES
        assert len(STUDIO_ONLY_FEATURES) >= 10

    def test_keyframe_modes(self):
        from celavii_resolve.constants import KEYFRAME_MODES

        assert KEYFRAME_MODES["All"] == 0
        assert KEYFRAME_MODES["Color"] == 1

    def test_version_types(self):
        from celavii_resolve.constants import VERSION_TYPES

        assert {"local", "remote"} == VERSION_TYPES

    def test_node_cache_modes(self):
        from celavii_resolve.constants import NODE_CACHE_MODES

        assert NODE_CACHE_MODES["None"] == 0
        assert NODE_CACHE_MODES["Smart"] == 1
        assert NODE_CACHE_MODES["On"] == 2


class TestPlatformDetection:
    """Platform helpers should not crash on any OS."""

    def test_system_returns_string(self):
        from celavii_resolve.resolve import _system

        result = _system()
        assert result in ("darwin", "windows", "linux")

    def test_resolve_module_path_returns_string_or_none(self):
        from celavii_resolve.resolve import _resolve_module_path

        result = _resolve_module_path()
        assert result is None or isinstance(result, str)

    def test_resolve_app_path_returns_string_or_none(self):
        from celavii_resolve.resolve import _resolve_app_path

        result = _resolve_app_path()
        assert result is None or isinstance(result, str)
