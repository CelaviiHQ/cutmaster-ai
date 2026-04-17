"""Content-type preset bundles (spec §3.1).

Each preset bundles:
  - a role the Director agent adopts
  - the hook rule (what constitutes the opening beat)
  - pacing guidance
  - scrubber parameter overrides
  - theme axes to probe during analysis
  - cue vocabulary the Marker agent looks for
  - marker vocabulary (phrasing template for suggested B-Roll inserts)

Presets are *recommendations* the user can override in the Configure screen.
The pipeline does not hard-code them — every parameter flows through
``UserSettings`` after the HIL step.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


Preset = Literal[
    "vlog",
    "product_demo",
    "wedding",
    "interview",
    "tutorial",
    "podcast",
    "reaction",
    "auto",
]


class PresetBundle(BaseModel):
    key: Preset
    label: str
    role: str
    hook_rule: str
    pacing: str
    cue_vocabulary: list[str]
    marker_vocabulary: list[str]
    theme_axes: list[str]
    scrub_defaults: dict = Field(default_factory=dict)


VLOG = PresetBundle(
    key="vlog",
    label="Vlog",
    role="YouTube retention expert and documentary editor",
    hook_rule="the single highest-energy summary statement in the first 20% of the runtime",
    pacing="retention curve — front-load the payoff, keep beats tight",
    cue_vocabulary=[
        "as you can see", "look at this", "check this out", "so here's",
        "when I went to", "over here", "right here", "this is where",
    ],
    marker_vocabulary=["B-Roll: {subject}", "Cutaway: {subject}"],
    theme_axes=["locations", "activities", "reactions", "key takeaways"],
    scrub_defaults={
        "remove_fillers": True,
        "remove_dead_air": True,
        "collapse_restarts": True,
        "dead_air_threshold_s": 0.6,
    },
)

PRODUCT_DEMO = PresetBundle(
    key="product_demo",
    label="Product Demo",
    role="senior product marketing editor",
    hook_rule="the problem/benefit framing that earns the viewer's attention in the first 15 seconds",
    pacing="one beat per feature, no rambling, demo-first",
    cue_vocabulary=[
        "look at", "notice the", "here's the", "as you can see",
        "one of the features", "the difference is",
    ],
    marker_vocabulary=["Insert product shot: {feature}", "Zoom: {feature}"],
    theme_axes=["features", "specs", "use cases", "comparisons"],
    scrub_defaults={
        "remove_fillers": True,
        "remove_dead_air": True,
        "collapse_restarts": True,
        "dead_air_threshold_s": 0.8,
    },
)

WEDDING = PresetBundle(
    key="wedding",
    label="Wedding",
    role="wedding cinema editor",
    hook_rule="the emotional peak (first kiss, vows highlight, or key family moment)",
    pacing="breathing room — let ambient silence and music-led beats land",
    cue_vocabulary=[
        "walking down", "first kiss", "our vows", "when we met",
        "the day we", "dancing", "speech", "toast",
    ],
    marker_vocabulary=["Cutaway: {moment}", "B-Roll: {moment}"],
    theme_axes=["ceremony", "reception", "toasts", "first dance", "family"],
    scrub_defaults={
        "remove_fillers": False,       # preserve authentic pauses
        "remove_dead_air": False,
        "collapse_restarts": True,
        "dead_air_threshold_s": 1.5,
    },
)

INTERVIEW = PresetBundle(
    key="interview",
    label="Interview",
    role="documentary interview editor",
    hook_rule="the strongest quote in the transcript, regardless of chronological position",
    pacing="preserve conversational cadence; don't rush the subject's pauses",
    cue_vocabulary=[
        "I remember when", "the first time", "what happened was",
        "I'll never forget", "the thing is",
    ],
    marker_vocabulary=["B-Roll to cover cut: {topic}", "Archive insert: {topic}"],
    theme_axes=["named entities", "turning points", "topics", "opinions"],
    scrub_defaults={
        "remove_fillers": True,
        "remove_dead_air": False,
        "collapse_restarts": True,
        "dead_air_threshold_s": 1.2,
    },
)

TUTORIAL = PresetBundle(
    key="tutorial",
    label="Tutorial",
    role="educational content editor",
    hook_rule="an outcome or result preview — what the viewer will be able to do by the end",
    pacing="aggressive on intro/preamble; never rush during actual steps or demos",
    cue_vocabulary=[
        "step one", "first", "next", "then", "finally",
        "click", "select", "drag", "type",
    ],
    marker_vocabulary=["Screen recording: {step}", "Zoom: {UI element}"],
    theme_axes=["steps", "tools", "gotchas", "results"],
    scrub_defaults={
        "remove_fillers": True,
        "remove_dead_air": True,
        "collapse_restarts": True,
        "dead_air_threshold_s": 0.8,
    },
)

PODCAST = PresetBundle(
    key="podcast",
    label="Podcast",
    role="podcast-to-video editor",
    hook_rule="the strongest exchange in the first third of the runtime",
    pacing="conversation-paced — do not fragment question/answer pairs",
    cue_vocabulary=[
        "that reminds me", "speaking of", "on that note",
        "let's talk about", "moving on to",
    ],
    marker_vocabulary=["Chapter: {topic shift}", "Pull quote: {line}"],
    theme_axes=["topics", "speaker turns", "guest bio beats"],
    scrub_defaults={
        "remove_fillers": True,
        "remove_dead_air": True,
        "collapse_restarts": False,
        "dead_air_threshold_s": 1.0,
    },
)

REACTION = PresetBundle(
    key="reaction",
    label="Reaction",
    role="reaction-content editor",
    hook_rule="the biggest genuine reaction or laugh in the clip",
    pacing="light scrub — let reactions and pauses breathe; don't sterilize",
    cue_vocabulary=[
        "wait", "what", "no way", "oh my", "hold on",
        "did you see that", "I can't believe",
    ],
    marker_vocabulary=["Show source: {moment}", "Split screen: {moment}"],
    theme_axes=["reaction peaks", "commentary threads"],
    scrub_defaults={
        "remove_fillers": False,
        "remove_dead_air": False,
        "collapse_restarts": False,
        "dead_air_threshold_s": 2.0,
    },
)


PRESETS: dict[str, PresetBundle] = {
    p.key: p
    for p in (VLOG, PRODUCT_DEMO, WEDDING, INTERVIEW, TUTORIAL, PODCAST, REACTION)
}


def get_preset(key: str) -> PresetBundle:
    """Return the preset bundle for ``key``. Raises :class:`KeyError` for unknown."""
    if key not in PRESETS:
        raise KeyError(f"Unknown preset '{key}'. Valid: {sorted(PRESETS)}")
    return PRESETS[key]


def all_presets() -> list[PresetBundle]:
    """Return the preset list in UI-display order."""
    return list(PRESETS.values())
