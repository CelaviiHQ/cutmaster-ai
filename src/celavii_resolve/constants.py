"""Canonical constants for the DaVinci Resolve Scripting API.

All colour/type values accepted by the API are defined here so that tools can
validate input without guessing and provide helpful error messages.
"""

# ---------------------------------------------------------------------------
# Marker colours  (Timeline.AddMarker / TimelineItem.AddMarker / MPI.AddMarker)
# ---------------------------------------------------------------------------
MARKER_COLORS = frozenset(
    {
        "Blue",
        "Cyan",
        "Green",
        "Yellow",
        "Red",
        "Pink",
        "Purple",
        "Fuchsia",
        "Rose",
        "Lavender",
        "Sky",
        "Mint",
        "Lemon",
        "Sand",
        "Cocoa",
        "Cream",
    }
)

# ---------------------------------------------------------------------------
# Clip colours  (MediaPoolItem.SetClipColor / TimelineItem.SetClipColor)
# ---------------------------------------------------------------------------
CLIP_COLORS = frozenset(
    {
        "Orange",
        "Apricot",
        "Yellow",
        "Lime",
        "Olive",
        "Green",
        "Teal",
        "Navy",
        "Blue",
        "Purple",
        "Violet",
        "Pink",
        "Tan",
        "Beige",
        "Brown",
        "Chocolate",
    }
)

# ---------------------------------------------------------------------------
# Track types
# ---------------------------------------------------------------------------
TRACK_TYPES = frozenset({"video", "audio", "subtitle"})

# ---------------------------------------------------------------------------
# Resolve pages (OpenPage / GetCurrentPage)
# ---------------------------------------------------------------------------
PAGES = frozenset(
    {
        "media",
        "cut",
        "edit",
        "fusion",
        "color",
        "fairlight",
        "deliver",
    }
)

# ---------------------------------------------------------------------------
# Composite / blend modes  (TimelineItem.SetProperty("CompositeMode", ...))
# ---------------------------------------------------------------------------
COMPOSITE_MODES = frozenset(
    {
        "Normal",
        "Add",
        "Subtract",
        "Difference",
        "Multiply",
        "Screen",
        "Overlay",
        "Hardlight",
        "Softlight",
        "Darken",
        "Lighten",
        "Color Dodge",
        "Color Burn",
        "Linear Dodge",
        "Linear Burn",
        "Linear Light",
        "Vivid Light",
        "Pin Light",
        "Hard Mix",
        "Exclusion",
        "Hue",
        "Saturation",
        "Color",
        "Luminosity",
    }
)

# ---------------------------------------------------------------------------
# Retime process modes
# ---------------------------------------------------------------------------
RETIME_PROCESSES = frozenset(
    {
        "NearestFrame",
        "FrameBlend",
        "OpticalFlow",
    }
)

# ---------------------------------------------------------------------------
# Timeline export types  (Timeline.Export)
# ---------------------------------------------------------------------------
EXPORT_TYPES = {
    "AAF": 0,  # EXPORT_AAF
    "DRT": 1,  # EXPORT_DRT  (DaVinci Resolve Timeline)
    "EDL": 2,  # EXPORT_EDL
    "FCP7XML": 3,  # EXPORT_FCP_7_XML
    "FCPXML": 4,  # EXPORT_FCPXML_1_8 / 1_9 / 1_10 / 1_11
    "HDR10": 5,  # EXPORT_HDR_10_PROFILE_A / B
    "CSV": 6,  # EXPORT_TEXT_CSV
    "TAB": 7,  # EXPORT_TEXT_TAB
    "OTIO": 8,  # EXPORT_OTIO
}

# ---------------------------------------------------------------------------
# Render format shorthands (commonly used)
# ---------------------------------------------------------------------------
RENDER_PRESETS = {
    "h264": {"format": "mp4", "codec": "H264"},
    "h265": {"format": "mp4", "codec": "H265"},
    "prores422": {"format": "mov", "codec": "ProRes422"},
    "prores422hq": {"format": "mov", "codec": "ProRes422HQ"},
    "prores4444": {"format": "mov", "codec": "ProRes4444"},
    "proxylt": {"format": "mov", "codec": "ProResProxy"},
    "dnxhd": {"format": "mxf", "codec": "DNxHD"},
    "dnxhr": {"format": "mxf", "codec": "DNxHR"},
    "tiff": {"format": "tif", "codec": "RGB16LZW"},
    "dpx": {"format": "dpx", "codec": "RGB10"},
    "exr": {"format": "exr", "codec": "RGB_half"},
}

# ---------------------------------------------------------------------------
# Features that require DaVinci Resolve Studio
# ---------------------------------------------------------------------------
STUDIO_ONLY_FEATURES = frozenset(
    {
        "TranscribeAudio",
        "ClearTranscription",
        "AnalyzeDolbyVision",
        "OptimizeDolbyVision",
        "ConvertTimelineToStereo",
        "VoiceIsolation",
        "SmartReframe",
        "SpeedWarp",
        "SceneCutDetection",
        "DetectSceneCuts",
        "NoiseReduction",
        "MagicMask",
        "AddSubtitlesFromAudio",
        "CreateStereoClip",
        "AutoSyncAudio",
    }
)

# ---------------------------------------------------------------------------
# Keyframe modes  (Resolve.GetKeyframeMode / SetKeyframeMode)
# ---------------------------------------------------------------------------
KEYFRAME_MODES = {
    "All": 0,
    "Color": 1,
    "Sizing": 2,
    "Composite": 3,  # not yet officially documented
}

# ---------------------------------------------------------------------------
# Version types  (TimelineItem.AddVersion / LoadVersionByName)
# ---------------------------------------------------------------------------
VERSION_TYPES = frozenset({"local", "remote"})

# ---------------------------------------------------------------------------
# Node cache modes  (Graph.SetNodeCacheMode)
# ---------------------------------------------------------------------------
NODE_CACHE_MODES = {
    "None": 0,
    "Smart": 1,
    "On": 2,
}
