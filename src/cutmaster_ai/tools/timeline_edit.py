"""Timeline editing tools — clip properties, transform, composite, speed, generators."""

import json

from ..config import mcp
from ..constants import COMPOSITE_MODES, TRACK_TYPES
from ..errors import safe_resolve_call
from ..resolve import _boilerplate, _ser


def _get_timeline_item(project, track_type: str, track_index: int, item_index: int):
    """Retrieve a specific timeline item. Returns (timeline, item) or raises ValueError."""
    tl = project.GetCurrentTimeline()
    if not tl:
        raise ValueError("No current timeline.")
    items = tl.GetItemListInTrack(track_type, track_index) or []
    if not items:
        raise ValueError(f"No items on {track_type} track {track_index}.")
    if item_index < 0 or item_index >= len(items):
        raise ValueError(
            f"Item index {item_index} out of range (0-{len(items) - 1}) "
            f"on {track_type} track {track_index}."
        )
    return tl, items[item_index]


# ---------------------------------------------------------------------------
# Listing items
# ---------------------------------------------------------------------------


@mcp.tool
@safe_resolve_call
def celavii_list_timeline_items(
    track_type: str = "video",
    track_index: int = 1,
) -> str:
    """List all items on a timeline track.

    Args:
        track_type: 'video', 'audio', or 'subtitle'.
        track_index: 1-based track index.
    """
    if track_type not in TRACK_TYPES:
        return f"Invalid track type. Valid: {', '.join(sorted(TRACK_TYPES))}"
    _, project, _ = _boilerplate()
    tl = project.GetCurrentTimeline()
    if not tl:
        return "No current timeline."
    items = tl.GetItemListInTrack(track_type, track_index) or []
    if not items:
        return f"No items on {track_type} track {track_index}."
    result = []
    for idx, item in enumerate(items):
        info = {"index": idx, "name": item.GetName(), "duration": item.GetDuration()}
        try:
            info["start"] = item.GetProperty("Start")
            info["end"] = item.GetEnd()
        except (AttributeError, TypeError):
            pass
        result.append(info)
    return json.dumps({"track": f"{track_type} {track_index}", "items": result}, indent=2)


# ---------------------------------------------------------------------------
# Item properties
# ---------------------------------------------------------------------------


@mcp.tool
@safe_resolve_call
def celavii_get_item_property(
    track_type: str = "video",
    track_index: int = 1,
    item_index: int = 0,
    key: str = "",
) -> str:
    """Get a property from a timeline item.

    Args:
        track_type: 'video', 'audio', or 'subtitle'.
        track_index: 1-based track index.
        item_index: 0-based item index on the track.
        key: Property key, or empty for all properties.
    """
    _, project, _ = _boilerplate()
    _, item = _get_timeline_item(project, track_type, track_index, item_index)
    if key:
        value = item.GetProperty(key)
        return json.dumps({key: _ser(value)}, indent=2)
    props = item.GetProperty() or {}
    return json.dumps(_ser(props), indent=2)


@mcp.tool
@safe_resolve_call
def celavii_set_item_property(
    key: str,
    value: str,
    track_type: str = "video",
    track_index: int = 1,
    item_index: int = 0,
) -> str:
    """Set a property on a timeline item.

    Common keys: Pan, Tilt, ZoomX, ZoomY, ZoomGang, RotationAngle,
    AnchorPointX, AnchorPointY, Pitch, Yaw, FlipX, FlipY,
    CropLeft, CropRight, CropTop, CropBottom, CropSoftness, CropRetain,
    CompositeMode, Opacity, RetimeProcess, SpeedFactor,
    ScalingPreset, ResizeFilter

    Args:
        key: Property key.
        value: Property value.
        track_type: 'video', 'audio', or 'subtitle'.
        track_index: 1-based track index.
        item_index: 0-based item index.
    """
    _, project, _ = _boilerplate()
    _, item = _get_timeline_item(project, track_type, track_index, item_index)
    # Try numeric conversion for common numeric properties
    try:
        numeric = float(value)
        if numeric == int(numeric):
            numeric = int(numeric)
        result = item.SetProperty(key, numeric)
    except ValueError:
        result = item.SetProperty(key, value)
    return f"Set {key} = {value}." if result else f"Failed to set {key}. It may be read-only."


@mcp.tool
@safe_resolve_call
def celavii_set_composite_mode(
    mode: str,
    track_type: str = "video",
    track_index: int = 1,
    item_index: int = 0,
) -> str:
    """Set the composite/blend mode on a timeline item.

    Args:
        mode: Blend mode (Normal, Add, Multiply, Screen, Overlay, etc.).
        track_type: 'video', 'audio', or 'subtitle'.
        track_index: 1-based track index.
        item_index: 0-based item index.
    """
    if mode not in COMPOSITE_MODES:
        return f"Invalid mode '{mode}'. Valid: {', '.join(sorted(COMPOSITE_MODES))}"
    _, project, _ = _boilerplate()
    _, item = _get_timeline_item(project, track_type, track_index, item_index)
    result = item.SetProperty("CompositeMode", mode)
    return f"Composite mode set to {mode}." if result else "Failed to set composite mode."


@mcp.tool
@safe_resolve_call
def celavii_set_opacity(
    opacity: float,
    track_type: str = "video",
    track_index: int = 1,
    item_index: int = 0,
) -> str:
    """Set the opacity of a timeline item (0.0 to 100.0).

    Args:
        opacity: Opacity percentage.
        track_type: 'video', 'audio', or 'subtitle'.
        track_index: 1-based track index.
        item_index: 0-based item index.
    """
    _, project, _ = _boilerplate()
    _, item = _get_timeline_item(project, track_type, track_index, item_index)
    result = item.SetProperty("Opacity", opacity)
    return f"Opacity set to {opacity}%." if result else "Failed to set opacity."


@mcp.tool
@safe_resolve_call
def celavii_set_transform(
    track_type: str = "video",
    track_index: int = 1,
    item_index: int = 0,
    zoom_x: float | None = None,
    zoom_y: float | None = None,
    pan: float | None = None,
    tilt: float | None = None,
    rotation: float | None = None,
    anchor_x: float | None = None,
    anchor_y: float | None = None,
) -> str:
    """Set transform properties on a timeline item in one call.

    All parameters are optional — only provided values are applied.

    Args:
        track_type: 'video', 'audio', or 'subtitle'.
        track_index: 1-based track index.
        item_index: 0-based item index.
        zoom_x: Horizontal zoom factor.
        zoom_y: Vertical zoom factor.
        pan: Horizontal position.
        tilt: Vertical position.
        rotation: Rotation angle in degrees.
        anchor_x: Anchor point X.
        anchor_y: Anchor point Y.
    """
    _, project, _ = _boilerplate()
    _, item = _get_timeline_item(project, track_type, track_index, item_index)
    props = {}
    if zoom_x is not None:
        props["ZoomX"] = zoom_x
    if zoom_y is not None:
        props["ZoomY"] = zoom_y
    if pan is not None:
        props["Pan"] = pan
    if tilt is not None:
        props["Tilt"] = tilt
    if rotation is not None:
        props["RotationAngle"] = rotation
    if anchor_x is not None:
        props["AnchorPointX"] = anchor_x
    if anchor_y is not None:
        props["AnchorPointY"] = anchor_y
    if not props:
        return "No transform values provided."
    applied = []
    failed = []
    for k, v in props.items():
        if item.SetProperty(k, v):
            applied.append(f"{k}={v}")
        else:
            failed.append(k)
    msg = f"Applied: {', '.join(applied)}." if applied else ""
    if failed:
        msg += f" Failed: {', '.join(failed)} (may be read-only)."
    return msg.strip()


@mcp.tool
@safe_resolve_call
def celavii_set_crop(
    track_type: str = "video",
    track_index: int = 1,
    item_index: int = 0,
    left: float | None = None,
    right: float | None = None,
    top: float | None = None,
    bottom: float | None = None,
    softness: float | None = None,
) -> str:
    """Set crop values on a timeline item.

    Args:
        track_type: 'video', 'audio', or 'subtitle'.
        track_index: 1-based track index.
        item_index: 0-based item index.
        left: Left crop.
        right: Right crop.
        top: Top crop.
        bottom: Bottom crop.
        softness: Crop softness.
    """
    _, project, _ = _boilerplate()
    _, item = _get_timeline_item(project, track_type, track_index, item_index)
    props = {}
    if left is not None:
        props["CropLeft"] = left
    if right is not None:
        props["CropRight"] = right
    if top is not None:
        props["CropTop"] = top
    if bottom is not None:
        props["CropBottom"] = bottom
    if softness is not None:
        props["CropSoftness"] = softness
    if not props:
        return "No crop values provided."
    applied = []
    for k, v in props.items():
        if item.SetProperty(k, v):
            applied.append(f"{k}={v}")
    return f"Crop set: {', '.join(applied)}." if applied else "Failed to set crop values."


@mcp.tool
@safe_resolve_call
def celavii_set_speed(
    speed: float,
    track_type: str = "video",
    track_index: int = 1,
    item_index: int = 0,
    retime_process: str = "NearestFrame",
) -> str:
    """Set the playback speed of a timeline item.

    Args:
        speed: Speed factor (1.0 = normal, 0.5 = half speed, 2.0 = double).
        track_type: 'video', 'audio', or 'subtitle'.
        track_index: 1-based track index.
        item_index: 0-based item index.
        retime_process: 'NearestFrame', 'FrameBlend', or 'OpticalFlow'.
    """
    from ..constants import RETIME_PROCESSES

    if retime_process not in RETIME_PROCESSES:
        return f"Invalid retime process. Valid: {', '.join(sorted(RETIME_PROCESSES))}"
    _, project, _ = _boilerplate()
    _, item = _get_timeline_item(project, track_type, track_index, item_index)
    item.SetProperty("RetimeProcess", retime_process)
    result = item.SetProperty("SpeedFactor", speed)
    return f"Speed set to {speed}x ({retime_process})." if result else "Failed to set speed."


@mcp.tool
@safe_resolve_call
def celavii_set_clip_enabled(
    enabled: bool,
    track_type: str = "video",
    track_index: int = 1,
    item_index: int = 0,
) -> str:
    """Enable or disable a timeline item.

    Args:
        enabled: True to enable, False to disable.
        track_type: 'video', 'audio', or 'subtitle'.
        track_index: 1-based track index.
        item_index: 0-based item index.
    """
    _, project, _ = _boilerplate()
    _, item = _get_timeline_item(project, track_type, track_index, item_index)
    result = item.SetClipEnabled(enabled)
    state = "enabled" if enabled else "disabled"
    return f"Clip {state}." if result else f"Failed to {state} clip."


# ---------------------------------------------------------------------------
# Generators & titles
# ---------------------------------------------------------------------------


@mcp.tool
@safe_resolve_call
def celavii_insert_generator(name: str) -> str:
    """Insert a generator into the current timeline at the playhead.

    Args:
        name: Generator name (e.g. 'Solid Color', '10 Point Grid').
    """
    _, project, _ = _boilerplate()
    tl = project.GetCurrentTimeline()
    if not tl:
        return "No current timeline."
    item = tl.InsertGeneratorIntoTimeline(name)
    return f"Generator '{name}' inserted." if item else f"Failed to insert generator '{name}'."


@mcp.tool
@safe_resolve_call
def celavii_insert_title(name: str) -> str:
    """Insert a title into the current timeline at the playhead.

    Args:
        name: Title template name (e.g. 'Text+', 'Scroll', 'Lower Third').
    """
    _, project, _ = _boilerplate()
    tl = project.GetCurrentTimeline()
    if not tl:
        return "No current timeline."
    item = tl.InsertTitleIntoTimeline(name)
    return f"Title '{name}' inserted." if item else f"Failed to insert title '{name}'."


@mcp.tool
@safe_resolve_call
def celavii_insert_fusion_title(name: str) -> str:
    """Insert a Fusion title into the current timeline at the playhead.

    Args:
        name: Fusion title template name.
    """
    _, project, _ = _boilerplate()
    tl = project.GetCurrentTimeline()
    if not tl:
        return "No current timeline."
    item = tl.InsertFusionTitleIntoTimeline(name)
    return (
        f"Fusion title '{name}' inserted." if item else f"Failed to insert Fusion title '{name}'."
    )


@mcp.tool
@safe_resolve_call
def celavii_insert_fusion_generator(name: str) -> str:
    """Insert a Fusion generator into the current timeline at the playhead.

    Args:
        name: Fusion generator name.
    """
    _, project, _ = _boilerplate()
    tl = project.GetCurrentTimeline()
    if not tl:
        return "No current timeline."
    item = tl.InsertFusionGeneratorIntoTimeline(name)
    return f"Fusion generator '{name}' inserted." if item else "Failed to insert."


@mcp.tool
@safe_resolve_call
def celavii_create_compound_clip(
    track_type: str = "video",
    track_index: int = 1,
    item_indices: list[int] | None = None,
) -> str:
    """Create a compound clip from selected timeline items.

    Args:
        track_type: Track type of the items.
        track_index: 1-based track index.
        item_indices: 0-based indices of items to compound.
    """
    _, project, _ = _boilerplate()
    tl = project.GetCurrentTimeline()
    if not tl:
        return "No current timeline."
    items = tl.GetItemListInTrack(track_type, track_index) or []
    idxs = item_indices or []
    selected = [items[i] for i in idxs if 0 <= i < len(items)]
    if not selected:
        return "No valid items selected."
    result = tl.CreateCompoundClip(selected)
    return "Compound clip created." if result else "Failed to create compound clip."


@mcp.tool
@safe_resolve_call
def celavii_create_fusion_clip(
    track_type: str = "video",
    track_index: int = 1,
    item_indices: list[int] | None = None,
) -> str:
    """Create a Fusion clip from selected timeline items.

    Args:
        track_type: Track type of the items.
        track_index: 1-based track index.
        item_indices: 0-based indices of items to fuse.
    """
    _, project, _ = _boilerplate()
    tl = project.GetCurrentTimeline()
    if not tl:
        return "No current timeline."
    items = tl.GetItemListInTrack(track_type, track_index) or []
    idxs = item_indices or []
    selected = [items[i] for i in idxs if 0 <= i < len(items)]
    if not selected:
        return "No valid items selected."
    result = tl.CreateFusionClip(selected)
    return "Fusion clip created." if result else "Failed to create Fusion clip."
