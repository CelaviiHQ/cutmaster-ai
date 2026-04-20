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

from .excludes import ExcludeCategory

Preset = Literal[
    "vlog",
    "product_demo",
    "wedding",
    "interview",
    "tutorial",
    "podcast",
    "reaction",
    "tightener",
    "clip_hunter",
    "short_generator",
    "auto",
]


class PresetBundle(BaseModel):
    key: Preset
    label: str
    role: str
    hook_rule: str
    pacing: str
    # Structured pacing — the Director prompt renders min/max/target as
    # explicit bounds and validate_plan rejects segments outside
    # [min_segment_s, max_segment_s]. ``target_segment_s`` also drives the
    # target-length recipe (N segments × target = total), replacing the
    # formerly-magic "22s" default. Presets that bypass the standard
    # Director path (tightener, clip_hunter, short_generator) still carry
    # values for schema symmetry; they're unused on those paths.
    min_segment_s: float = Field(
        default=3.0,
        description="Lower bound on individual segment duration (seconds).",
    )
    target_segment_s: float = Field(
        default=18.0,
        description="Preferred per-segment duration. Drives the target-length recipe arithmetic.",
    )
    max_segment_s: float = Field(
        default=40.0,
        description="Upper bound on individual segment duration (seconds).",
    )
    # Narrative reorder policy (batch 7):
    #   - "free": Director picks any order that serves the cut.
    #   - "preserve_macro": segments stay inside their source chapter; the
    #     Director can reorder within a chapter but not across chapters.
    #   - "locked": segments play in source-time order (hook excepted —
    #     still floated to position 0 in the output).
    # The validator enforces "locked" strictly and "preserve_macro" when
    # chapter data is plumbed through; the prompt always surfaces the
    # stance so the Director writes to it even when enforcement is soft.
    reorder_mode: Literal["free", "preserve_macro", "locked"] = Field(
        default="free",
        description="How much the Director may reorder segments from their source-time sequence.",
    )
    cue_vocabulary: list[str]
    marker_vocabulary: list[str]
    theme_axes: list[str]
    scrub_defaults: dict = Field(default_factory=dict)
    exclude_categories: list[ExcludeCategory] = Field(
        default_factory=list,
        description=(
            "Content-category exclusions the user can toggle on the "
            "Configure screen. Populated per-preset in v2-1; empty for v2-0."
        ),
    )
    default_custom_focus_placeholder: str = Field(
        default="",
        description=(
            "Placeholder text for the Configure screen's 'Custom focus' "
            "input. Wired into the Director prompt in v2-1."
        ),
    )
    speaker_awareness: str = Field(
        default="",
        description=(
            "v2-5: per-preset instruction fragment rendered as the SPEAKER "
            "GUIDANCE block in the Director prompt. Empty = preset is not "
            "speaker-aware and the block is omitted. Populated for Interview "
            "and Podcast."
        ),
    )


VLOG = PresetBundle(
    key="vlog",
    label="Vlog",
    role="YouTube retention expert and documentary editor",
    hook_rule="the single highest-energy summary statement in the first 20% of the runtime",
    pacing="retention curve — front-load the payoff, keep beats tight",
    reorder_mode="preserve_macro",
    cue_vocabulary=[
        "as you can see",
        "look at this",
        "check this out",
        "so here's",
        "when I went to",
        "over here",
        "right here",
        "this is where",
    ],
    marker_vocabulary=["B-Roll: {subject}", "Cutaway: {subject}"],
    theme_axes=["locations", "activities", "reactions", "key takeaways"],
    scrub_defaults={
        "remove_fillers": True,
        "remove_dead_air": True,
        "collapse_restarts": True,
        "dead_air_threshold_s": 0.6,
    },
    exclude_categories=[
        ExcludeCategory(
            key="sponsor_reads",
            label="Sponsor reads",
            description="Paid promotional segments and ad reads (e.g. 'today's video is brought to you by…').",
            checked_by_default=True,
        ),
        ExcludeCategory(
            key="subscribe_boilerplate",
            label="Subscribe / like reminders",
            description="Channel-plug boilerplate ('smash that like button', 'don't forget to subscribe').",
            checked_by_default=True,
        ),
        ExcludeCategory(
            key="intro_outro_templates",
            label="Intro / outro templates",
            description="Reusable channel intros and outros unrelated to the specific episode content.",
            checked_by_default=False,
        ),
        ExcludeCategory(
            key="legal_disclaimers",
            label="Legal disclaimers",
            description="Affiliate / FTC disclosures and generic legal boilerplate.",
            checked_by_default=True,
        ),
        ExcludeCategory(
            key="channel_housekeeping",
            label="Channel housekeeping",
            description="'Last week I posted…', Patreon plugs, merch mentions, community tabs.",
            checked_by_default=False,
        ),
    ],
    default_custom_focus_placeholder="e.g. emphasise the drone shots over Lisbon",
)

PRODUCT_DEMO = PresetBundle(
    key="product_demo",
    label="Product Demo",
    role="senior product marketing editor",
    hook_rule="the problem/benefit framing that earns the viewer's attention in the first 15 seconds",
    pacing="one beat per feature, no rambling, demo-first",
    reorder_mode="preserve_macro",
    min_segment_s=4.0,
    target_segment_s=12.0,
    max_segment_s=30.0,
    cue_vocabulary=[
        "look at",
        "notice the",
        "here's the",
        "as you can see",
        "one of the features",
        "the difference is",
    ],
    marker_vocabulary=["Insert product shot: {feature}", "Zoom: {feature}"],
    theme_axes=["features", "specs", "use cases", "comparisons"],
    scrub_defaults={
        "remove_fillers": True,
        "remove_dead_air": True,
        "collapse_restarts": True,
        "dead_air_threshold_s": 0.8,
    },
    exclude_categories=[
        ExcludeCategory(
            key="legal_disclaimers",
            label="Legal disclaimers",
            description="Warranty, safety, and generic legal language a marketing cut can omit.",
            checked_by_default=True,
        ),
        ExcludeCategory(
            key="price_caveats",
            label="Price / availability caveats",
            description="'Prices vary by region', 'subject to change', 'while supplies last'.",
            checked_by_default=True,
        ),
        ExcludeCategory(
            key="regional_caveats",
            label="Regional availability caveats",
            description="Market-specific callouts that don't apply to the general audience.",
            checked_by_default=False,
        ),
        ExcludeCategory(
            key="specs_deep_dive",
            label="Deep spec tangents",
            description="Extended spec-sheet recitation without a user-facing benefit.",
            checked_by_default=False,
        ),
        ExcludeCategory(
            key="off_message_anecdotes",
            label="Off-message anecdotes",
            description="Personal stories that wander away from the product's benefit framing.",
            checked_by_default=False,
        ),
    ],
    default_custom_focus_placeholder="e.g. emphasise battery life and the fast-charge feature",
)

WEDDING = PresetBundle(
    key="wedding",
    label="Wedding",
    role="wedding cinema editor",
    hook_rule="the emotional peak (first kiss, vows highlight, or key family moment)",
    pacing="breathing room — let ambient silence and music-led beats land",
    reorder_mode="preserve_macro",
    min_segment_s=5.0,
    target_segment_s=25.0,
    max_segment_s=60.0,
    cue_vocabulary=[
        "walking down",
        "first kiss",
        "our vows",
        "when we met",
        "the day we",
        "dancing",
        "speech",
        "toast",
    ],
    marker_vocabulary=["Cutaway: {moment}", "B-Roll: {moment}"],
    theme_axes=["ceremony", "reception", "toasts", "first dance", "family"],
    scrub_defaults={
        "remove_fillers": False,  # preserve authentic pauses
        "remove_dead_air": False,
        "collapse_restarts": True,
        "dead_air_threshold_s": 1.5,
    },
    exclude_categories=[
        ExcludeCategory(
            key="legal_formalities",
            label="Legal formalities",
            description="Officiant's legal recitation (licensing, witness declarations, civic-code language).",
            checked_by_default=True,
        ),
        ExcludeCategory(
            key="mc_talking",
            label="MC / DJ housekeeping",
            description="Crowd-management announcements, food cues, emcee banter between the real moments.",
            checked_by_default=True,
        ),
        ExcludeCategory(
            key="vendor_mentions",
            label="Vendor mentions",
            description="Thank-yous to caterers, florists, venues, planners — usually cut from highlight reels.",
            checked_by_default=True,
        ),
        ExcludeCategory(
            key="repeat_after_me",
            label="Repeat-after-me vows",
            description="Officiant prompting phrases the couple then repeats — keep the repeated line, drop the prompt.",
            checked_by_default=True,
        ),
        ExcludeCategory(
            key="thank_you_speeches",
            label="Extended thank-you speeches",
            description="Long thank-yous to family / guests beyond the emotional peak.",
            checked_by_default=False,
        ),
        ExcludeCategory(
            key="administrative_announcements",
            label="Administrative announcements",
            description="Seating, schedule, timing, and logistics announcements.",
            checked_by_default=True,
        ),
    ],
    default_custom_focus_placeholder="e.g. emphasise the grandparents' toast and the first dance",
)

INTERVIEW = PresetBundle(
    key="interview",
    label="Interview",
    role="documentary interview editor",
    hook_rule="the strongest quote in the transcript, regardless of chronological position",
    pacing="preserve conversational cadence; don't rush the subject's pauses",
    reorder_mode="locked",
    min_segment_s=6.0,
    target_segment_s=22.0,
    max_segment_s=50.0,
    cue_vocabulary=[
        "I remember when",
        "the first time",
        "what happened was",
        "I'll never forget",
        "the thing is",
    ],
    marker_vocabulary=["B-Roll to cover cut: {topic}", "Archive insert: {topic}"],
    theme_axes=["named entities", "turning points", "topics", "opinions"],
    scrub_defaults={
        "remove_fillers": True,
        "remove_dead_air": False,
        "collapse_restarts": True,
        "dead_air_threshold_s": 1.2,
    },
    exclude_categories=[
        ExcludeCategory(
            key="housekeeping_chitchat",
            label="Housekeeping chitchat",
            description="Pre-interview small talk, comfort checks, 'are we rolling?' moments.",
            checked_by_default=True,
        ),
        ExcludeCategory(
            key="mic_checks",
            label="Mic / audio checks",
            description="'Say something', 'count to ten', levels and sound-check chatter.",
            checked_by_default=True,
        ),
        ExcludeCategory(
            key="off_topic_small_talk",
            label="Off-topic small talk",
            description="Tangents about weather, traffic, or personal chat unrelated to the interview subject.",
            checked_by_default=False,
        ),
        ExcludeCategory(
            key="interviewer_verbose_setups",
            label="Interviewer verbose setups",
            description="Long multi-sentence interviewer questions; keep the crisp core, drop the preamble.",
            checked_by_default=False,
        ),
        ExcludeCategory(
            key="repeated_content",
            label="Repeated / duplicated content",
            description="Material the subject re-states verbatim in a later, stronger take.",
            checked_by_default=True,
        ),
    ],
    default_custom_focus_placeholder="e.g. keep the story about their first job",
    speaker_awareness=(
        "This is a two-speaker interview. Preserve the informative side of "
        "each exchange — keep the guest's full answers verbatim, and keep "
        "only the crisp core of each interviewer question (the 1–2 sentences "
        "that actually set up the answer). Drop the interviewer's verbose "
        'multi-sentence setups, re-framings, and agreement noises ("right", '
        '"yeah, that makes sense") unless they\'re the hook. If the '
        "interviewer paraphrases the guest's answer back, drop the paraphrase "
        "and keep the original answer. Never drop a guest answer to tighten "
        "length — tighten interviewer material instead."
    ),
)

TUTORIAL = PresetBundle(
    key="tutorial",
    label="Tutorial",
    role="educational content editor",
    hook_rule="an outcome or result preview — what the viewer will be able to do by the end",
    pacing="aggressive on intro/preamble; never rush during actual steps or demos",
    reorder_mode="locked",
    min_segment_s=5.0,
    target_segment_s=20.0,
    max_segment_s=45.0,
    cue_vocabulary=[
        "step one",
        "first",
        "next",
        "then",
        "finally",
        "click",
        "select",
        "drag",
        "type",
    ],
    marker_vocabulary=["Screen recording: {step}", "Zoom: {UI element}"],
    theme_axes=["steps", "tools", "gotchas", "results"],
    scrub_defaults={
        "remove_fillers": True,
        "remove_dead_air": True,
        "collapse_restarts": True,
        "dead_air_threshold_s": 0.8,
    },
    exclude_categories=[
        ExcludeCategory(
            key="app_boot_narration",
            label="App boot / setup narration",
            description="'Let me open the app', 'waiting for it to load' — wastes viewer time.",
            checked_by_default=True,
        ),
        ExcludeCategory(
            key="window_management",
            label="Window / desktop management",
            description="Moving windows, resizing, finding the right monitor; not part of the lesson.",
            checked_by_default=True,
        ),
        ExcludeCategory(
            key="unrelated_notifications",
            label="Unrelated notifications / distractions",
            description="Phone pings, doorbell reactions, 'sorry my cat is here' moments.",
            checked_by_default=True,
        ),
        ExcludeCategory(
            key="personal_preamble",
            label="Personal preamble",
            description="'Hey everyone, welcome back' and life-update small talk before the tutorial starts.",
            checked_by_default=False,
        ),
        ExcludeCategory(
            key="promo_plugs",
            label="Course / promo plugs",
            description="Mid-tutorial promotions for courses, Discord servers, or paid products.",
            checked_by_default=True,
        ),
    ],
    default_custom_focus_placeholder="e.g. emphasise the keyboard shortcut section",
)

PODCAST = PresetBundle(
    key="podcast",
    label="Podcast",
    role="podcast-to-video editor",
    hook_rule="the strongest exchange in the first third of the runtime",
    pacing="conversation-paced — do not fragment question/answer pairs",
    reorder_mode="locked",
    min_segment_s=8.0,
    target_segment_s=35.0,
    max_segment_s=90.0,
    cue_vocabulary=[
        "that reminds me",
        "speaking of",
        "on that note",
        "let's talk about",
        "moving on to",
    ],
    marker_vocabulary=["Chapter: {topic shift}", "Pull quote: {line}"],
    theme_axes=["topics", "speaker turns", "guest bio beats"],
    scrub_defaults={
        "remove_fillers": True,
        "remove_dead_air": True,
        "collapse_restarts": False,
        "dead_air_threshold_s": 1.0,
    },
    exclude_categories=[
        ExcludeCategory(
            key="ad_reads",
            label="Ad / sponsor reads",
            description="Host- or guest-read advertisements embedded in the conversation.",
            checked_by_default=True,
        ),
        ExcludeCategory(
            key="sponsor_tags",
            label="Sponsor tags / transitions",
            description="Short 'thanks to X' tags and transition copy around ad breaks.",
            checked_by_default=True,
        ),
        ExcludeCategory(
            key="housekeeping_plugs",
            label="Housekeeping plugs",
            description="'Rate us five stars', Patreon pushes, merch mentions, newsletter plugs.",
            checked_by_default=True,
        ),
        ExcludeCategory(
            key="self_promo_tangents",
            label="Self-promo tangents",
            description="Extended riffs about the hosts' other shows or business ventures.",
            checked_by_default=False,
        ),
        ExcludeCategory(
            key="off_topic_chat",
            label="Off-topic chat",
            description="Extended tangents unrelated to the episode's stated subject.",
            checked_by_default=False,
        ),
    ],
    default_custom_focus_placeholder="e.g. keep the debate about remote work",
    speaker_awareness=(
        "This is a multi-speaker podcast conversation. Protect question → "
        "answer turns as a unit — never split a speaker's answer across a "
        "cut. When two hosts riff with each other, keep both sides; when a "
        "host asks a guest a question, keep the question crisp and keep the "
        'guest\'s full answer. Drop host agreement interjections ("totally", '
        '"for sure", "right") that interrupt a guest\'s answer. The '
        "speaker with the most words across the transcript is usually the "
        "primary host — treat their housekeeping / show-wrap material as "
        "lower priority than guest content."
    ),
)

REACTION = PresetBundle(
    key="reaction",
    label="Reaction",
    role="reaction-content editor",
    hook_rule="the biggest genuine reaction or laugh in the clip",
    pacing="light scrub — let reactions and pauses breathe; don't sterilize",
    reorder_mode="free",
    min_segment_s=4.0,
    target_segment_s=15.0,
    max_segment_s=35.0,
    cue_vocabulary=[
        "wait",
        "what",
        "no way",
        "oh my",
        "hold on",
        "did you see that",
        "I can't believe",
    ],
    marker_vocabulary=["Show source: {moment}", "Split screen: {moment}"],
    theme_axes=["reaction peaks", "commentary threads"],
    scrub_defaults={
        "remove_fillers": False,
        "remove_dead_air": False,
        "collapse_restarts": False,
        "dead_air_threshold_s": 2.0,
    },
    exclude_categories=[
        ExcludeCategory(
            key="pre_clip_setup",
            label="Pre-clip setup",
            description="Long ramp-up before the source content starts playing; keep the payoff.",
            checked_by_default=True,
        ),
        ExcludeCategory(
            key="unrelated_context",
            label="Unrelated channel context",
            description="Viewer-of-the-week shout-outs, channel updates, tangents away from the source clip.",
            checked_by_default=True,
        ),
        ExcludeCategory(
            key="over_explanation",
            label="Over-explanation",
            description="Explaining the source material in detail after the moment already landed.",
            checked_by_default=False,
        ),
        ExcludeCategory(
            key="subscribe_boilerplate",
            label="Subscribe / like reminders",
            description="Channel-plug boilerplate interrupting the reaction flow.",
            checked_by_default=True,
        ),
    ],
    default_custom_focus_placeholder="e.g. emphasise the real laugh at the punchline",
)


TIGHTENER = PresetBundle(
    key="tightener",
    label="Tightener (surgical)",
    role="no-LLM tightener — skips the Director and relies on per-take word-block segmentation",
    hook_rule="preserve the original opening of each take; no narrative reordering",
    pacing="surgical — drop filler + dead air inside each take; keep the story order",
    cue_vocabulary=[],  # no Marker LLM runs for tightener
    marker_vocabulary=[],  # keep empty — tightener output is take-level only
    theme_axes=[],  # no theme selection in the UI
    scrub_defaults={
        "remove_fillers": True,
        "remove_dead_air": True,
        "collapse_restarts": True,
        "dead_air_threshold_s": 0.3,  # more aggressive than vlog's 0.6
    },
    exclude_categories=[],  # category exclusion is a Director concept
    default_custom_focus_placeholder="",  # no Director to read focus
)


CLIP_HUNTER = PresetBundle(
    key="clip_hunter",
    label="Clip Hunter (long-form → short-form)",
    role="viral-moments editor — finds quotable, self-contained exchanges in a long-form recording",
    hook_rule="the single most quotable, tension-rich, or emotionally clear moment in the window",
    pacing="each candidate must be self-contained — a viewer with zero prior context should get the moment",
    cue_vocabulary=[
        "the thing is",
        "here's the problem",
        "what blew my mind",
        "you won't believe",
        "nobody talks about",
        "the truth is",
        "wait, so",
        "hold on",
        "imagine",
        "picture this",
    ],
    marker_vocabulary=["Clip: {topic}", "Hook: {line}"],
    theme_axes=["punchlines", "revelations", "disagreements", "emotional peaks", "quotable lines"],
    scrub_defaults={
        "remove_fillers": True,
        "remove_dead_air": True,
        "collapse_restarts": True,
        "dead_air_threshold_s": 0.8,
    },
    exclude_categories=[
        ExcludeCategory(
            key="ad_reads",
            label="Ad / sponsor reads",
            description="Paid promotional segments; almost never survive as viral clips.",
            checked_by_default=True,
        ),
        ExcludeCategory(
            key="housekeeping_plugs",
            label="Housekeeping / channel plugs",
            description="'Rate us five stars', Patreon pushes, subscribe boilerplate.",
            checked_by_default=True,
        ),
        ExcludeCategory(
            key="intro_outro_templates",
            label="Intro / outro templates",
            description="Generic show openers / closers with no episode-specific content.",
            checked_by_default=True,
        ),
        ExcludeCategory(
            key="off_topic_chat",
            label="Extended off-topic chat",
            description="Tangents unrelated to the episode's core subject.",
            checked_by_default=False,
        ),
    ],
    default_custom_focus_placeholder="e.g. emphasise the debate about AI",
)


SHORT_GENERATOR = PresetBundle(
    key="short_generator",
    label="Short Generator (assembled reels)",
    role="TikTok / Reels editor specialising in punchy, jump-cut shorts assembled from scattered moments",
    hook_rule="the strongest opening statement that earns the next five seconds of attention",
    pacing="3–8 spans assembled into one 45–90 s short; jump cuts welcome; no dead air over 0.5 s; each cut should feel like it adds, not interrupts",
    cue_vocabulary=[
        "the thing is",
        "here's the problem",
        "what blew my mind",
        "you won't believe",
        "the truth is",
        "hold on",
        "imagine",
    ],
    marker_vocabulary=["Hook: {line}", "Beat: {topic}"],
    theme_axes=[
        "central claims",
        "callbacks",
        "setup-and-punchline pairs",
        "contrasting opinions",
        "escalating arguments",
    ],
    scrub_defaults={
        "remove_fillers": True,
        "remove_dead_air": True,
        "collapse_restarts": True,
        "dead_air_threshold_s": 0.5,
    },
    exclude_categories=[
        ExcludeCategory(
            key="ad_reads",
            label="Ad / sponsor reads",
            description="Paid promotional segments; almost never survive as viral reels.",
            checked_by_default=True,
        ),
        ExcludeCategory(
            key="housekeeping_plugs",
            label="Housekeeping / channel plugs",
            description="'Rate us five stars', Patreon pushes, subscribe boilerplate.",
            checked_by_default=True,
        ),
        ExcludeCategory(
            key="intro_outro_templates",
            label="Intro / outro templates",
            description="Generic show openers / closers with no episode-specific content.",
            checked_by_default=True,
        ),
        ExcludeCategory(
            key="off_topic_chat",
            label="Extended off-topic chat",
            description="Tangents unrelated to the episode's core subject.",
            checked_by_default=False,
        ),
    ],
    default_custom_focus_placeholder="e.g. build a reel around the loneliness debate",
)


PRESETS: dict[str, PresetBundle] = {
    p.key: p
    for p in (
        VLOG,
        PRODUCT_DEMO,
        WEDDING,
        INTERVIEW,
        TUTORIAL,
        PODCAST,
        REACTION,
        TIGHTENER,
        CLIP_HUNTER,
        SHORT_GENERATOR,
    )
}


def get_preset(key: str) -> PresetBundle:
    """Return the preset bundle for ``key``. Raises :class:`KeyError` for unknown."""
    if key not in PRESETS:
        raise KeyError(f"Unknown preset '{key}'. Valid: {sorted(PRESETS)}")
    return PRESETS[key]


def all_presets() -> list[PresetBundle]:
    """Return the preset list in UI-display order."""
    return list(PRESETS.values())


# ---------------------------------------------------------------------------
# v2-11: Preset × Mode compatibility matrix
# ---------------------------------------------------------------------------
#
# Only Tightener has mode restrictions — it encodes a state-level contract
# ("preserve order, just tighten") disguised as a content preset, so it only
# makes sense paired with Assembled. Every other preset (including Clip
# Hunter) is orthogonal to mode; the snapshot/copy guarantee means the
# source timeline is never modified regardless of combo.

TimelineMode = Literal["raw_dump", "rough_cut", "curated", "assembled"]

_INCOMPATIBLE: dict[tuple[str, str], str] = {
    ("tightener", "raw_dump"): (
        "Tightener preserves take order — the source timeline must already "
        "be assembled. Pick Assembled instead."
    ),
    ("tightener", "rough_cut"): (
        "Tightener preserves take order — it can't pick between A/B "
        "alternates. Pick Assembled instead."
    ),
    ("tightener", "curated"): (
        "Tightener preserves take order — Curated hasn't committed to one "
        "yet. Pick Assembled instead."
    ),
}


def preset_mode_compatible(preset: str, mode: str) -> bool:
    """True when the preset × mode combination is supported."""
    return (preset, mode) not in _INCOMPATIBLE


def preset_mode_incompatibility_reason(preset: str, mode: str) -> str | None:
    """Return a human-readable reason when a combo is blocked, else ``None``."""
    return _INCOMPATIBLE.get((preset, mode))


# ---------------------------------------------------------------------------
# v4 Phase 4.4: Per-mode sensory-layer activation matrix
# ---------------------------------------------------------------------------
#
# Proposal §"Per-mode activation matrix" — each (preset, timeline_mode) pair
# gets a different mix of Layer C (shot tags), Layer A (boundary validator),
# and Layer Audio (DSP cues) because each cutting context makes different
# kinds of cuts.
#
#   - "default": active when the master toggle flips on.
#   - "opt_in":  never auto-activates; explicit per-layer override needed.
#   - "off":     cost/no-signal combo; explicit override still respected
#                so power users debugging can force it.

ActivationLevel = Literal["default", "opt_in", "off"]


class SensoryActivation(BaseModel):
    """One row of :data:`SENSORY_MATRIX`. Per-layer activation level."""

    c: ActivationLevel = "default"
    a: ActivationLevel = "default"
    audio: ActivationLevel = "opt_in"


# Keyed by an internal "mode key" that collapses preset + timeline_mode onto
# the proposal's 6-row matrix. Multi-candidate presets (clip_hunter,
# short_generator) ignore timeline_mode; Tightener forces assembled.
SENSORY_MATRIX: dict[str, SensoryActivation] = {
    "raw_dump": SensoryActivation(c="default", a="default", audio="opt_in"),
    "rough_cut": SensoryActivation(c="default", a="default", audio="opt_in"),
    "curated": SensoryActivation(c="default", a="default", audio="opt_in"),
    "assembled": SensoryActivation(c="default", a="off", audio="default"),
    "clip_hunter": SensoryActivation(c="default", a="off", audio="opt_in"),
    "short_generator": SensoryActivation(c="default", a="default", audio="default"),
}


# Human-facing copy for the Configure-screen subtitle. Kept next to the
# matrix so schema drift stays obvious. Picked up by the panel through a
# preset-info endpoint in a follow-up; backend never renders this itself.
SENSORY_MODE_SUBTITLES: dict[str, str] = {
    "raw_dump": "Shot tagging + cut validation. Adds 30–60s on first analyze; cached after.",
    "rough_cut": "Shot tagging helps pick winners between A/B takes; cut validation between takes.",
    "curated": "Shot-variety tagging across takes; cut validation at take boundaries.",
    "assembled": "Gesture-aware filler tightening + pause detection. Within-take cuts only.",
    "clip_hunter": "Visual-energy scoring boosts engagement ranking; clip in/out validated.",
    "short_generator": "Full stack — shot tagging, span boundary validation, beat-aware hook timing.",
}


def sensory_mode_key(preset: str, timeline_mode: str) -> str:
    """Collapse (preset, timeline_mode) to the matrix key.

    - ``short_generator`` / ``clip_hunter`` → preset key (multi-candidate
      presets don't use timeline_mode).
    - ``tightener`` → ``"assembled"`` (preset forces assembled mode; the
      sensory profile tracks the state, not the preset label).
    - anything else → ``timeline_mode`` (raw_dump / rough_cut / curated /
      assembled).
    """
    if preset in ("short_generator", "clip_hunter"):
        return preset
    if preset == "tightener":
        return "assembled"
    if timeline_mode in ("raw_dump", "rough_cut", "curated", "assembled"):
        return timeline_mode
    return "raw_dump"  # unknown mode — safe default


def resolve_sensory_layers(
    *,
    master_enabled: bool,
    c_override: bool | None,
    a_override: bool | None,
    audio_override: bool | None,
    preset: str,
    timeline_mode: str,
) -> tuple[bool, bool, bool]:
    """Resolve effective per-layer enabled flags.

    Precedence: ``*_override is not None`` wins (forces on/off); else fall
    back to the matrix × master toggle: ``"default"`` layers go on when
    master is on, ``"opt_in"`` / ``"off"`` stay off unless overridden.

    Returns ``(layer_c_enabled, layer_a_enabled, layer_audio_enabled)``.
    """
    key = sensory_mode_key(preset, timeline_mode)
    row = SENSORY_MATRIX.get(key, SENSORY_MATRIX["raw_dump"])

    def _pick(level: ActivationLevel, override: bool | None) -> bool:
        if override is not None:
            return override
        if not master_enabled:
            return False
        return level == "default"

    return (
        _pick(row.c, c_override),
        _pick(row.a, a_override),
        _pick(row.audio, audio_override),
    )
