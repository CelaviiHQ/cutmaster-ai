import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { SourceAspectInfo } from "../api";
import MascotLoading from "./MascotLoading";
import {
    SENSORY_SUBTITLES,
    resolveSensoryLayers,
    sensoryModeKey,
} from "../sensory";
import {
    CUT_INTENTS,
    cutIntentModeIncompatibilityReason,
    getCutIntent,
    isUnusualCombination,
    resolveCutIntent,
} from "../axes";
import type {
    ContentType,
    CutIntent,
    FormatKey,
    FormatSpec,
    PresetBundle,
    PresetKey,
    PresetRecommendation,
    SpeakerRosterEntry,
    StoryAnalysis,
    TimelineMode,
    UserSettings,
} from "../types";

// Content-type presets recognised by the three-axis model. Used to guard
// the resolver / picker from running on legacy cut-intent preset keys.
const CONTENT_TYPE_PRESETS: ReadonlySet<string> = new Set([
    "vlog",
    "product_demo",
    "wedding",
    "interview",
    "tutorial",
    "podcast",
    "presentation",
    "reaction",
]);

const SPEAKER_AWARE_PRESETS: ReadonlySet<PresetKey> = new Set([
    "interview",
    "podcast",
    "presentation",
]);

// Length presets — mirror ReviewScreen's Tune-the-cut chips. Anchors the
// brief in human-named durations instead of "180s".
const LENGTH_PRESETS: { label: string; seconds: number }[] = [
    { label: "TikTok", seconds: 30 },
    { label: "Reel", seconds: 60 },
    { label: "Default", seconds: 180 },
    { label: "Long", seconds: 300 },
];

// Stepper chips for num_clips — direct manipulation beats a 1-px slider
// thumb for small integers.
const NUM_CLIP_STEPS = [1, 2, 3, 5, 8, 10];

// Content-type icon + label for the resolved Recipe chip. Falls back to
// a generic glyph if the preset isn't in the map.
const CONTENT_TYPE_META: Record<string, { icon: string; label: string }> = {
    vlog:            { icon: "🎬", label: "Vlog" },
    product_demo:    { icon: "📦", label: "Product demo" },
    wedding:         { icon: "💍", label: "Wedding" },
    interview:       { icon: "🎙", label: "Interview" },
    tutorial:        { icon: "🛠", label: "Tutorial" },
    podcast:         { icon: "🎧", label: "Podcast" },
    presentation:    { icon: "📽", label: "Presentation" },
    reaction:        { icon: "🎭", label: "Reaction" },
    tightener:       { icon: "✂",  label: "Tightener" },
    clip_hunter:     { icon: "🔍", label: "Clip Hunter" },
    short_generator: { icon: "⚡", label: "Short Generator" },
};

const fmtTc = (s: number): string => {
    const total = Math.max(0, Math.round(s));
    const m = Math.floor(total / 60);
    const sec = total % 60;
    return `${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}`;
};

// Smart speaker-label suggestion. Two-speaker shoots: highest word count
// is the Guest (interviewer asks, guest answers). Three+ speakers: anonymous
// "Speaker N" — the user knows the room.
const suggestSpeakerLabel = (
    speakerId: string,
    roster: SpeakerRosterEntry[],
): string => {
    if (roster.length === 2) {
        const sorted = [...roster].sort((a, b) => b.word_count - a.word_count);
        return speakerId === sorted[0].speaker_id ? "Guest" : "Host";
    }
    const idx = roster.findIndex((s) => s.speaker_id === speakerId);
    return idx >= 0 ? `Speaker ${idx + 1}` : speakerId;
};

interface Props {
    runId: string;
    preset: PresetKey;
    onPresetChange: (p: PresetKey) => void;
    settings: UserSettings;
    onSettingsChange: (s: UserSettings) => void;
    onBack: () => void;
    onNext: () => void;
}

function defaultExcludeKeys(bundle: PresetBundle | undefined): string[] {
    if (!bundle) return [];
    return bundle.exclude_categories
        .filter((c) => c.checked_by_default)
        .map((c) => c.key);
}

export default function ConfigureScreen({
    runId,
    preset,
    onPresetChange,
    settings,
    onSettingsChange,
    onBack,
    onNext,
}: Props) {
    const [rec, setRec] = useState<PresetRecommendation | null>(null);
    const [analysis, setAnalysis] = useState<StoryAnalysis | null>(null);
    const [bundles, setBundles] = useState<PresetBundle[] | null>(null);
    const [formats, setFormats] = useState<FormatSpec[] | null>(null);
    const [source, setSource] = useState<SourceAspectInfo | null>(null);
    const [speakerRoster, setSpeakerRoster] = useState<SpeakerRosterEntry[] | null>(null);
    const [formatAutoSelected, setFormatAutoSelected] = useState(false);
    const [loading, setLoading] = useState(true);
    const [err, setErr] = useState<string | null>(null);

    // One-time exclude-defaults init per preset. Resume-flows (Back from
    // Review, then return) must NOT clobber the user's manual edits, so we
    // remember the last preset key we initialized for.
    const initializedFor = useRef<string | null>(null);

    // Fetch preset bundles + format specs + source-aspect once.
    useEffect(() => {
        let cancelled = false;
        api.listPresets()
            .then((r) => !cancelled && setBundles(r.presets))
            .catch(() => !cancelled && setBundles([]));
        api.listFormats()
            .then((r) => !cancelled && setFormats(r.formats))
            .catch(() => !cancelled && setFormats([]));
        api.speakers(runId)
            .then((r) => !cancelled && setSpeakerRoster(r.speakers))
            .catch(() => !cancelled && setSpeakerRoster([]));
        api.sourceAspect(runId)
            .then((info) => {
                if (cancelled) return;
                setSource(info);
                // Auto-select format when the source already matches a non-horizontal
                // target — e.g. a 9:16 phone shoot should default to Short.
                if (
                    info.recommended_format !== "horizontal" &&
                    !formatAutoSelected
                ) {
                    onSettingsChange({
                        ...settings,
                        format: info.recommended_format,
                    });
                    setFormatAutoSelected(true);
                }
            })
            .catch(() => {
                // Non-fatal — aspect detection is best-effort.
            });
        return () => {
            cancelled = true;
        };
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [runId]);

    // If preset is 'auto', run detect first.
    useEffect(() => {
        let cancelled = false;
        (async () => {
            setLoading(true);
            setErr(null);
            try {
                let effective = preset;
                let suggestedTargetFromAutodetect: number | null = null;
                if (preset === "auto") {
                    const r = await api.detectPreset(runId);
                    if (cancelled) return;
                    setRec(r);
                    effective = r.preset;
                    onPresetChange(r.preset);
                    suggestedTargetFromAutodetect = r.suggested_target_length_s ?? null;
                }
                const a = await api.analyzeThemes(runId, effective);
                if (cancelled) return;
                setAnalysis(a);
                // Pre-check all theme candidates by default + auto-pick the
                // top-engagement hook when the user hasn't chosen one yet.
                // Skip when selected_hook_s is already set (resume-from-Back
                // must preserve prior intent).
                const topHook = a.hook_candidates.reduce<typeof a.hook_candidates[number] | null>(
                    (best, h) => (best == null || h.engagement_score > best.engagement_score ? h : best),
                    null,
                );
                onSettingsChange({
                    ...settings,
                    themes: a.theme_candidates,
                    selected_hook_s:
                        settings.selected_hook_s ?? (topHook ? topHook.start_s : null),
                    // Prefill target_length_s from the autodetect suggestion
                    // so editors aren't left with a blank field that makes
                    // the Director satisfice on a tiny cut. Preserve any
                    // value the editor already set (resume-from-Back, manual
                    // input on a prior pass).
                    target_length_s:
                        settings.target_length_s ?? suggestedTargetFromAutodetect,
                });
            } catch (e) {
                if (!cancelled) setErr(String(e));
            } finally {
                if (!cancelled) setLoading(false);
            }
        })();
        return () => {
            cancelled = true;
        };
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [runId]);

    const currentBundle = bundles?.find((b) => b.key === preset);

    // Seed exclude-category defaults when the preset is first known.
    // custom_focus is deliberately not touched here — see handlePresetChange.
    useEffect(() => {
        if (!currentBundle) return;
        if (initializedFor.current === preset) return;
        onSettingsChange({
            ...settings,
            exclude_categories: defaultExcludeKeys(currentBundle),
        });
        initializedFor.current = preset;
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [currentBundle?.key]);

    const toggleTheme = (t: string) => {
        const has = settings.themes.includes(t);
        onSettingsChange({
            ...settings,
            themes: has
                ? settings.themes.filter((x) => x !== t)
                : [...settings.themes, t],
        });
    };

    const toggleExclude = (key: string) => {
        const current = settings.exclude_categories ?? [];
        const has = current.includes(key);
        onSettingsChange({
            ...settings,
            exclude_categories: has
                ? current.filter((x) => x !== key)
                : [...current, key],
        });
    };

    const handlePresetChange = (next: PresetKey) => {
        // Swap exclude list to the new preset's defaults; preserve custom_focus
        // verbatim (the string may apply equally well to any preset).
        onPresetChange(next);
        const nextBundle = bundles?.find((b) => b.key === next);
        if (nextBundle) {
            onSettingsChange({
                ...settings,
                exclude_categories: defaultExcludeKeys(nextBundle),
            });
            initializedFor.current = next;
        }
    };

    if (loading) {
        return (
            <MascotLoading
                label="Analysing themes"
                hint="Reading the transcript and clustering the strongest narrative threads. Usually ~5–10 s."
                stages={[
                    { label: "Cluster transcript into themes", status: "started" },
                    { label: "Rank themes by salience", status: "pending" },
                ]}
            />
        );
    }

    const excludeCats = currentBundle?.exclude_categories ?? [];
    const selectedExcludes = settings.exclude_categories ?? [];
    const focusPlaceholder =
        currentBundle?.default_custom_focus_placeholder ??
        "e.g. emphasise the key moment";

    const currentFormat: FormatKey = settings.format ?? "horizontal";
    const currentFormatSpec = formats?.find((f) => f.key === currentFormat);
    const lengthCap = currentFormatSpec?.max_duration_s ?? null;
    const sourceMatchesTarget =
        source && source.recommended_format === currentFormat;

    const isTightener = preset === "tightener";
    const isClipHunter = preset === "clip_hunter";
    const isShortGenerator = preset === "short_generator";

    // Phase 5.3 + 5.6 — three-axis derivations.
    // ``cut_intent === null`` means "Auto"; the resolver picks by
    // duration / num_clips / content-type exception. Panel-side mirror
    // of ``resolve_cut_intent`` stays in sync with the backend so the
    // chip preview matches what the server will decide. Only content-
    // type presets resolve; legacy cut-intent presets keep the old
    // num_clips-slider path until they're pruned in Phase 7.
    const timelineModeNow = (settings.timeline_mode ?? "raw_dump") as TimelineMode;
    const explicitCutIntent = settings.cut_intent ?? null;
    const canResolveAxes = CONTENT_TYPE_PRESETS.has(preset);
    const resolvedCutIntentPreview =
        canResolveAxes && !explicitCutIntent
            ? resolveCutIntent(
                  preset as ContentType,
                  settings.target_length_s ?? 60,
                  settings.num_clips ?? 1,
                  timelineModeNow,
                  !!settings.takes_already_scrubbed,
              )
            : null;
    const effectiveCutIntent: CutIntent | null =
        explicitCutIntent ?? resolvedCutIntentPreview?.intent ?? null;
    const effectiveCutIntentInfo =
        effectiveCutIntent ? getCutIntent(effectiveCutIntent) : null;

    // Phase 6.7 — hover-tooltip text for the resolved chip. Built
    // client-side from what the panel already has so there's no extra
    // round-trip; lines:
    //   1) provenance label (auto-resolved / user-supplied / forced)
    //   2) the resolver's reason string (auto / forced paths only)
    //   3) the cut intent's catalogue description
    //   4) "unusual combination" warning when applicable
    const chipTooltip = (() => {
        if (!effectiveCutIntent || !effectiveCutIntentInfo) return undefined;
        const lines: string[] = [];
        const source = resolvedCutIntentPreview?.source;
        if (explicitCutIntent !== null) {
            lines.push("Source: user-supplied");
        } else if (source === "forced") {
            lines.push("Source: forced override (num_clips or assembled+scrubbed)");
        } else if (source === "auto") {
            lines.push("Source: auto-resolved from duration band");
        }
        if (resolvedCutIntentPreview && explicitCutIntent === null) {
            lines.push(`Why: ${resolvedCutIntentPreview.reason}`);
        }
        lines.push(`${effectiveCutIntentInfo.label}: ${effectiveCutIntentInfo.description}`);
        if (isUnusualCombination(preset as ContentType, effectiveCutIntent)) {
            lines.push(
                "⚠ Unusual combination — confirm this is what you want for this content type.",
            );
        }
        return lines.join("\n");
    })();

    // Multi-candidate = num_clips slider is relevant. New-API path: the
    // Axis 2 cut_intent is (or auto-resolves to) multi_clip. Legacy
    // presets + multi_clip cover the same UI need.
    const isMultiClipIntent = effectiveCutIntent === "multi_clip";
    const isAssembledShortIntent = effectiveCutIntent === "assembled_short";
    // Short Generator + Clip Hunter share the "N candidates + target length"
    // shape, so UI rules that hide sequencing-preset cards apply to both
    // legacy presets AND the axis-resolved cut intents.
    const isMultiCandidate =
        isClipHunter || isShortGenerator || isMultiClipIntent || isAssembledShortIntent;

    const showSpeakerCard =
        SPEAKER_AWARE_PRESETS.has(preset) &&
        speakerRoster != null &&
        speakerRoster.length >= 2;
    const speakerLabels = settings.speaker_labels ?? {};
    const updateSpeakerLabel = (speakerId: string, label: string) => {
        const next = { ...speakerLabels };
        const trimmed = label.trim();
        if (trimmed) next[speakerId] = trimmed;
        else delete next[speakerId];
        onSettingsChange({
            ...settings,
            speaker_labels: Object.keys(next).length > 0 ? next : null,
        });
    };
    const timelineMode = settings.timeline_mode ?? "raw_dump";
    const assembledMode =
        !isTightener && !isMultiCandidate && timelineMode === "assembled";
    const curatedMode =
        !isTightener && !isMultiCandidate && timelineMode === "curated";
    const roughCutMode =
        !isTightener && !isMultiCandidate && timelineMode === "rough_cut";
    const takeAwareMode = assembledMode || curatedMode || roughCutMode;

    const scrubParams = (settings.scrub_params ?? {}) as Record<string, unknown>;
    const updateScrub = (patch: Record<string, unknown>) => {
        onSettingsChange({
            ...settings,
            scrub_params: { ...scrubParams, ...patch },
        });
    };

    // -------- Recipe-card derivations (axes + downstream effects) --------
    const contentMeta = CONTENT_TYPE_META[preset] ?? {
        icon: "🎬",
        label: preset,
    };
    const intentLabel = effectiveCutIntentInfo?.label ?? "—";
    const intentProvenance: "auto" | "user" | "forced" =
        explicitCutIntent !== null
            ? "user"
            : (resolvedCutIntentPreview?.source ?? "auto");
    const provenanceIcon =
        intentProvenance === "user" ? "✓" : intentProvenance === "forced" ? "!" : "✨";
    const provenanceLabel =
        intentProvenance === "user"
            ? "you set this"
            : intentProvenance === "forced"
              ? "forced by num_clips / mode"
              : "auto-resolved from duration band";

    // Downstream-effects sentence — surfaces what the recipe will produce.
    const recipeEffects: string[] = [];
    if (isMultiCandidate) {
        const n = settings.num_clips ?? 3;
        const noun = isShortGenerator || isAssembledShortIntent ? "short" : "clip";
        recipeEffects.push(`${n} ${noun}${n === 1 ? "" : "s"}`);
    } else if (settings.target_length_s) {
        recipeEffects.push(`~${fmtTc(settings.target_length_s)} cut`);
    }
    recipeEffects.push(
        currentFormat === "horizontal"
            ? "horizontal 16:9"
            : currentFormat === "vertical_short"
              ? "vertical 9:16"
              : "square 1:1",
    );
    if (settings.captions_enabled) recipeEffects.push("with captions");
    if (settings.sensory_master_enabled) recipeEffects.push("shot-aware");
    else recipeEffects.push("transcript-only");

    const isUnusual =
        canResolveAxes &&
        !!effectiveCutIntent &&
        isUnusualCombination(preset as ContentType, effectiveCutIntent);

    return (
        <div>
            {/* ── Recipe card — what the analyser decided + override path ── */}
            <div className={`card recipe-card ${isUnusual ? "recipe-card--unusual" : ""}`}>
                <div className="recipe-chips">
                    <div className="recipe-chip" title="Detected content type">
                        <span className="recipe-chip-icon">{contentMeta.icon}</span>
                        <span className="recipe-chip-label">{contentMeta.label}</span>
                        <span className="recipe-chip-prov muted">
                            {rec ? `· ${Math.round(rec.confidence * 100)}% confidence` : "· detected"}
                        </span>
                    </div>
                    <span className="recipe-arrow">→</span>
                    <div className="recipe-chip" title={chipTooltip}>
                        <span className="recipe-chip-icon">✂</span>
                        <span className="recipe-chip-label">{intentLabel}</span>
                        <span className="recipe-chip-prov muted" title={provenanceLabel}>
                            · {provenanceIcon} {intentProvenance}
                        </span>
                    </div>
                    <span className="recipe-arrow">→</span>
                    <div className="recipe-chip">
                        <span className="recipe-chip-icon">
                            {isMultiCandidate ? "🎞" : "⏱"}
                        </span>
                        <span className="recipe-chip-label">
                            {isMultiCandidate
                                ? `${settings.num_clips ?? 3} ${isShortGenerator || isAssembledShortIntent ? "shorts" : "clips"}`
                                : settings.target_length_s
                                  ? fmtTc(settings.target_length_s)
                                  : "no length"}
                        </span>
                        <span className="recipe-chip-prov muted">
                            · {isMultiCandidate ? "set below" : "target"}
                        </span>
                    </div>
                </div>
                <p className="recipe-effects">
                    Will produce <strong>{recipeEffects.join(" · ")}</strong>
                </p>
                {isUnusual && (
                    <p className="recipe-warn">
                        ⚠ {contentMeta.label} × {intentLabel} is unusual — confirm
                        this is what you want for this content type.
                    </p>
                )}
                <details className="recipe-why">
                    <summary>
                        <span className="muted">Why these picks · how to override</span>
                    </summary>
                    <div className="recipe-why-body">
                        {rec && (
                            <p className="muted">
                                <strong>Content type:</strong> {rec.reasoning}
                            </p>
                        )}
                        {resolvedCutIntentPreview && explicitCutIntent === null && (
                            <p className="muted">
                                <strong>Cut intent:</strong>{" "}
                                {resolvedCutIntentPreview.reason}
                            </p>
                        )}
                        <div className="recipe-override">
                            <label htmlFor="content-type-select">Override content type</label>
                            <select
                                id="content-type-select"
                                value={preset}
                                onChange={(e) =>
                                    handlePresetChange(e.target.value as PresetKey)
                                }
                            >
                                {[
                                    "vlog",
                                    "product_demo",
                                    "wedding",
                                    "interview",
                                    "tutorial",
                                    "podcast",
                                    "presentation",
                                    "reaction",
                                ].map((p) => (
                                    <option key={p} value={p}>
                                        {(CONTENT_TYPE_META[p]?.icon ?? "") + " " + (CONTENT_TYPE_META[p]?.label ?? p)}
                                    </option>
                                ))}
                                {["tightener", "clip_hunter", "short_generator"].includes(preset) && (
                                    <option value={preset} disabled>
                                        {preset} (legacy — pick a content type above)
                                    </option>
                                )}
                            </select>
                        </div>
                        {rec && rec.confidence < 0.5 && (rec.alternatives ?? []).length > 0 && (
                            <div className="recipe-alts">
                                <span className="muted">Low confidence — try:</span>
                                {(rec.alternatives ?? []).map((alt) => (
                                    <button
                                        key={alt}
                                        type="button"
                                        className="secondary"
                                        onClick={() => handlePresetChange(alt)}
                                    >
                                        {CONTENT_TYPE_META[alt]?.icon ?? ""} {CONTENT_TYPE_META[alt]?.label ?? alt}
                                    </button>
                                ))}
                            </div>
                        )}
                        {canResolveAxes && !isTightener && (
                            <div className="recipe-override">
                                <label>Override cut intent</label>
                                <div className="cut-intent-radios">
                                    <label className="cut-intent-radio">
                                        <input
                                            type="radio"
                                            name="cut-intent"
                                            checked={explicitCutIntent === null}
                                            onChange={() =>
                                                onSettingsChange({ ...settings, cut_intent: null })
                                            }
                                        />
                                        <span><strong>Auto</strong> {resolvedCutIntentPreview && (
                                            <span className="muted">
                                                — picks {getCutIntent(resolvedCutIntentPreview.intent)?.label}
                                            </span>
                                        )}</span>
                                    </label>
                                    {CUT_INTENTS.map((ci) => {
                                        const blockedReason = cutIntentModeIncompatibilityReason(
                                            ci.key, timelineModeNow,
                                        );
                                        const disabled = blockedReason !== null;
                                        const unusual = isUnusualCombination(preset as ContentType, ci.key);
                                        return (
                                            <label
                                                key={ci.key}
                                                className="cut-intent-radio"
                                                title={blockedReason ?? ci.description}
                                                style={{
                                                    opacity: disabled ? 0.4 : 1,
                                                    cursor: disabled ? "not-allowed" : "pointer",
                                                }}
                                            >
                                                <input
                                                    type="radio"
                                                    name="cut-intent"
                                                    checked={explicitCutIntent === ci.key}
                                                    disabled={disabled}
                                                    onChange={() =>
                                                        onSettingsChange({ ...settings, cut_intent: ci.key })
                                                    }
                                                />
                                                <span>
                                                    {ci.label}
                                                    {unusual && (
                                                        <span className="muted"> · unusual for {preset}</span>
                                                    )}
                                                </span>
                                            </label>
                                        );
                                    })}
                                </div>
                            </div>
                        )}
                    </div>
                </details>
            </div>

            {/* ── What you're making — length / count / format / captions ── */}
            {!isTightener && (
                <div className="card">
                    <h2>What you're making</h2>

                    {/* Length: chips + slider. Hidden for multi-candidate
                        flows (Clip Hunter / Short Generator) where the
                        Number-of-clips stepper drives the brief instead. */}
                    {!isMultiCandidate && (
                        <div className="wym-row">
                            <div className="wym-label">Length</div>
                            <div className="wym-control">
                                <div className="tune-presets" style={{ marginBottom: 6 }}>
                                    {LENGTH_PRESETS.map((p) => {
                                        const cap = lengthCap;
                                        const tooLong = cap != null && p.seconds > cap;
                                        const active = settings.target_length_s === p.seconds;
                                        return (
                                            <button
                                                key={p.label}
                                                type="button"
                                                className={`chip ${active ? "on" : ""}`}
                                                disabled={tooLong}
                                                onClick={() =>
                                                    onSettingsChange({
                                                        ...settings,
                                                        target_length_s: p.seconds,
                                                    })
                                                }
                                            >
                                                {p.label}
                                                <span className="muted"> · {fmtTc(p.seconds)}</span>
                                            </button>
                                        );
                                    })}
                                    <button
                                        type="button"
                                        className={`chip ${settings.target_length_s == null ? "on" : ""}`}
                                        onClick={() =>
                                            onSettingsChange({ ...settings, target_length_s: null })
                                        }
                                    >
                                        None
                                    </button>
                                </div>
                                <div className="wym-slider-row">
                                    <input
                                        type="range"
                                        min={15}
                                        max={Math.min(600, lengthCap ?? 600)}
                                        step={5}
                                        value={settings.target_length_s ?? 180}
                                        className="tune-slider"
                                        onChange={(e) =>
                                            onSettingsChange({
                                                ...settings,
                                                target_length_s: Number(e.target.value),
                                            })
                                        }
                                    />
                                    <span className="tune-chip">
                                        {settings.target_length_s != null
                                            ? fmtTc(settings.target_length_s)
                                            : "—"}
                                    </span>
                                </div>
                                {(() => {
                                    const tgt = settings.target_length_s;
                                    if (tgt == null || !currentBundle) return null;
                                    if (tgt <= 90) {
                                        return (
                                            <p className="wym-hint warn">
                                                ⚠ {currentBundle.label} is tuned for longer cuts;
                                                Short Generator produces tighter results under 90 s.
                                            </p>
                                        );
                                    }
                                    const expected = tgt / currentBundle.target_segment_s;
                                    if (expected < 2 || expected > 15) {
                                        return (
                                            <p className="wym-hint warn">
                                                ⚠ {Math.round(tgt)} s ÷ {currentBundle.target_segment_s}s/beat
                                                ≈ {expected.toFixed(1)} segments — outside this preset's
                                                comfort zone (2–15).
                                            </p>
                                        );
                                    }
                                    return null;
                                })()}
                            </div>
                        </div>
                    )}

                    {/* Stepper chips for num_clips when multi-candidate */}
                    {isMultiCandidate && (
                        <div className="wym-row">
                            <div className="wym-label">
                                {isClipHunter || isMultiClipIntent ? "Number of clips" : "Number of shorts"}
                            </div>
                            <div className="wym-control">
                                <div className="stepper-chips">
                                    {NUM_CLIP_STEPS.map((n) => {
                                        const active = (settings.num_clips ?? 3) === n;
                                        return (
                                            <button
                                                key={n}
                                                type="button"
                                                className={`stepper-chip ${active ? "on" : ""}`}
                                                onClick={() =>
                                                    onSettingsChange({ ...settings, num_clips: n })
                                                }
                                            >
                                                {n}
                                            </button>
                                        );
                                    })}
                                </div>
                                <p className="wym-hint muted">
                                    {isClipHunter || isMultiClipIntent
                                        ? "Each clip is a single contiguous span. Build one or all from Review."
                                        : "Each short is 3–8 jump-cut spans stitched around one through-line."}
                                </p>
                            </div>
                        </div>
                    )}

                    {/* Output format — quiet radio row, default doesn't shout */}
                    {formats && formats.length > 0 && (
                        <div className="wym-row">
                            <div className="wym-label">Output format</div>
                            <div className="wym-control">
                                <div className="format-radios">
                                    {formats.map((f) => {
                                        const selected = currentFormat === f.key;
                                        return (
                                            <label
                                                key={f.key}
                                                className={`format-radio ${selected ? "is-on" : ""}`}
                                            >
                                                <input
                                                    type="radio"
                                                    name="format"
                                                    checked={selected}
                                                    onChange={() => {
                                                        const cap = f.max_duration_s;
                                                        const clampedLen =
                                                            cap != null &&
                                                            settings.target_length_s != null &&
                                                            settings.target_length_s > cap
                                                                ? Math.round(cap)
                                                                : settings.target_length_s;
                                                        onSettingsChange({
                                                            ...settings,
                                                            format: f.key,
                                                            target_length_s: clampedLen,
                                                        });
                                                    }}
                                                />
                                                <span>{f.label}</span>
                                            </label>
                                        );
                                    })}
                                </div>
                                {source && (
                                    <p className="wym-hint muted">
                                        Source is <code>{source.width}×{source.height}</code>
                                        {currentFormat !== "horizontal" && sourceMatchesTarget && (
                                            <> — matches the selected format, no reframing needed.</>
                                        )}
                                        {currentFormat !== "horizontal" && !sourceMatchesTarget && (
                                            <> — will reframe.</>
                                        )}
                                    </p>
                                )}
                            </div>
                        </div>
                    )}

                    {/* Captions + safe-zones — quiet checkboxes */}
                    <div className="wym-row wym-row--inline">
                        <label className="wym-check">
                            <input
                                type="checkbox"
                                checked={settings.captions_enabled ?? false}
                                onChange={(e) =>
                                    onSettingsChange({
                                        ...settings,
                                        captions_enabled: e.target.checked,
                                    })
                                }
                            />
                            Generate captions <span className="muted">(SRT + subtitle track)</span>
                        </label>
                        {currentFormat !== "horizontal" && (
                            <label className="wym-check">
                                <input
                                    type="checkbox"
                                    checked={settings.safe_zones_enabled ?? false}
                                    onChange={(e) =>
                                        onSettingsChange({
                                            ...settings,
                                            safe_zones_enabled: e.target.checked,
                                        })
                                    }
                                />
                                Show safe-zone guides
                            </label>
                        )}
                    </div>
                </div>
            )}

            {/* ── Hook + Chapters + Themes priority — narrative-cut flows ── */}
            {analysis && !isTightener && !isMultiCandidate && (
                <>
                    <div className="card">
                        <h2>Hook candidates</h2>
                        <p className="muted" style={{ fontSize: "var(--fs-2)" }}>
                            Click one to lock the opening beat — leave all unselected to let the Director pick.
                        </p>
                        {analysis.hook_candidates.map((h, i) => {
                            const selected =
                                settings.selected_hook_s != null &&
                                Math.abs(settings.selected_hook_s - h.start_s) < 0.01;
                            return (
                                <div
                                    key={i}
                                    className={`seg hook-row ${selected ? "hook-row--selected" : ""}`}
                                    title={h.text}
                                    role="button"
                                    tabIndex={0}
                                    onClick={() =>
                                        onSettingsChange({
                                            ...settings,
                                            selected_hook_s: selected ? null : h.start_s,
                                        })
                                    }
                                    onKeyDown={(e) => {
                                        if (e.key === "Enter" || e.key === " ") {
                                            e.preventDefault();
                                            onSettingsChange({
                                                ...settings,
                                                selected_hook_s: selected ? null : h.start_s,
                                            });
                                        }
                                    }}
                                >
                                    <span className="seg-time">{fmtTc(h.start_s)}</span>
                                    <span className="seg-time">
                                        {(h.engagement_score * 100).toFixed(0)}%
                                    </span>
                                    <span className="seg-reason">
                                        {selected ? "● " : ""}{h.text}
                                    </span>
                                </div>
                            );
                        })}
                    </div>

                    <div className="card">
                        <h2>Chapters detected</h2>
                        <p className="muted" style={{ fontSize: "var(--fs-2)" }}>
                            {settings.selected_hook_s != null
                                ? "Chapters before your hook are dimmed."
                                : "The Director will bias toward covering each chapter."}
                        </p>
                        {analysis.chapters.map((c, i) => {
                            const hookAt = settings.selected_hook_s;
                            const preHook = hookAt != null && c.end_s <= hookAt;
                            const containsHook =
                                hookAt != null && c.start_s <= hookAt && hookAt < c.end_s;
                            return (
                                <div
                                    key={i}
                                    className={`seg chapter-row ${preHook ? "chapter-row--pre-hook" : ""} ${containsHook ? "chapter-row--contains-hook" : ""}`}
                                >
                                    <span className="seg-time">{fmtTc(c.start_s)}</span>
                                    <span className="seg-time">{(c.end_s - c.start_s).toFixed(1)}s</span>
                                    <span>{containsHook ? "● " : ""}{c.title}</span>
                                </div>
                            );
                        })}
                    </div>
                </>
            )}

            {/* ── What goes in / out — topics + skips + focus ── */}
            {!isTightener && (
                <div className="card">
                    <h2>What goes in / out</h2>

                    {/* Topics — when present (multi-candidate or narrative
                        with theme axes both surface them). */}
                    {analysis && analysis.theme_candidates.length > 0 && (
                        <div className="topics-block">
                            <div className="topics-head">
                                <span>
                                    <strong>Topics</strong>
                                    <span className="muted">
                                        {" "}· {analysis.theme_candidates.length} detected · {settings.themes.length} kept
                                    </span>
                                </span>
                                <div className="topics-actions">
                                    <button
                                        type="button"
                                        className="link-button"
                                        onClick={() =>
                                            onSettingsChange({
                                                ...settings,
                                                themes: [...analysis.theme_candidates],
                                            })
                                        }
                                    >
                                        select all
                                    </button>
                                    <span className="muted">·</span>
                                    <button
                                        type="button"
                                        className="link-button"
                                        onClick={() => onSettingsChange({ ...settings, themes: [] })}
                                    >
                                        clear
                                    </button>
                                </div>
                            </div>
                            <p className="muted topics-help">
                                {isMultiCandidate
                                    ? "Anchor candidates to the selected themes."
                                    : "Untick anything you consider noise — the Director down-weights unticked themes."}
                            </p>
                            <div className="topic-pills">
                                {analysis.theme_candidates.map((t) => {
                                    const on = settings.themes.includes(t);
                                    return (
                                        <button
                                            key={t}
                                            type="button"
                                            className={`topic-pill ${on ? "on" : ""}`}
                                            onClick={() => toggleTheme(t)}
                                        >
                                            <span className="topic-pill-glyph">{on ? "✓" : "+"}</span>
                                            {t}
                                        </button>
                                    );
                                })}
                            </div>
                        </div>
                    )}

                    {/* Skip-these — exclude categories with full-width 2-col grid */}
                    {excludeCats.length > 0 && (
                        <div className="skip-block">
                            <div className="topics-head">
                                <span>
                                    <strong>Skip these</strong>
                                    <span className="muted">
                                        {" "}· {selectedExcludes.length} of {excludeCats.length} from {currentBundle?.label}
                                    </span>
                                </span>
                            </div>
                            <div className="skip-grid">
                                {excludeCats.map((c) => {
                                    const checked = selectedExcludes.includes(c.key);
                                    return (
                                        <label key={c.key} className="skip-item" title={c.description}>
                                            <input
                                                type="checkbox"
                                                checked={checked}
                                                onChange={() => toggleExclude(c.key)}
                                            />
                                            <span>{c.label}</span>
                                        </label>
                                    );
                                })}
                            </div>
                        </div>
                    )}

                    {/* Focus — collapsed reveal (used by ~10% of cuts) */}
                    <div className="focus-block">
                        {settings.custom_focus ? (
                            <>
                                <label htmlFor="focus-input" className="topics-head">
                                    <span><strong>Focus</strong> <span className="muted">· soft priority</span></span>
                                </label>
                                <input
                                    id="focus-input"
                                    type="text"
                                    placeholder={focusPlaceholder}
                                    value={settings.custom_focus ?? ""}
                                    onChange={(e) =>
                                        onSettingsChange({
                                            ...settings,
                                            custom_focus: e.target.value || null,
                                        })
                                    }
                                />
                            </>
                        ) : (
                            <button
                                type="button"
                                className="link-button focus-add"
                                onClick={() =>
                                    onSettingsChange({ ...settings, custom_focus: " " })
                                }
                            >
                                + add focus instruction
                            </button>
                        )}
                    </div>
                </div>
            )}

            {/* ── Take-aware mode (Assembled / Curated / Rough cut) ── */}
            {takeAwareMode && (
                <div className="card">
                    <h2>
                        {assembledMode && "Assembled mode"}
                        {curatedMode && "Curated mode"}
                        {roughCutMode && "Rough cut mode"}
                    </h2>
                    <p className="muted" style={{ fontSize: "var(--fs-2)" }}>
                        {assembledMode && "Director will never cross take boundaries."}
                        {curatedMode && "Every selected take appears in the output, arranged for narrative."}
                        {roughCutMode && "Adjacent takes cluster into groups; one winner per group."}
                    </p>
                    {assembledMode && (
                        <label className="wym-check" style={{ marginTop: 6 }}>
                            <input
                                type="checkbox"
                                checked={settings.reorder_allowed ?? true}
                                onChange={(e) =>
                                    onSettingsChange({
                                        ...settings,
                                        reorder_allowed: e.target.checked,
                                    })
                                }
                            />
                            Let the AI reorder takes for narrative flow
                        </label>
                    )}
                    <label className="wym-check" style={{ marginTop: 6 }}>
                        <input
                            type="checkbox"
                            checked={settings.takes_already_scrubbed ?? false}
                            onChange={(e) =>
                                onSettingsChange({
                                    ...settings,
                                    takes_already_scrubbed: e.target.checked,
                                })
                            }
                        />
                        Takes are already scrubbed — skip cleanup
                    </label>
                </div>
            )}

            {/* ── Tightener cleanup — only for the Tightener preset ── */}
            {isTightener && (
                <div className="card">
                    <h2>Tightener cleanup</h2>
                    <p className="muted" style={{ fontSize: "var(--fs-2)" }}>
                        Drops filler words and dead-air gaps inside each take, then
                        plays takes in their original order. Deterministic — no LLM.
                    </p>
                    <div style={{ marginTop: 10 }}>
                        <label className="wym-check" style={{ marginBottom: 8 }}>
                            <input
                                type="checkbox"
                                checked={scrubParams.remove_fillers !== false}
                                onChange={(e) =>
                                    updateScrub({ remove_fillers: e.target.checked })
                                }
                            />
                            Remove filler words (um, uh, ah…)
                        </label>
                        <label className="wym-check" style={{ marginBottom: 8 }}>
                            <input
                                type="checkbox"
                                checked={scrubParams.remove_dead_air !== false}
                                onChange={(e) =>
                                    updateScrub({ remove_dead_air: e.target.checked })
                                }
                            />
                            Remove dead-air words
                        </label>
                        <label className="wym-check" style={{ marginBottom: 8 }}>
                            <input
                                type="checkbox"
                                checked={scrubParams.collapse_restarts !== false}
                                onChange={(e) =>
                                    updateScrub({ collapse_restarts: e.target.checked })
                                }
                            />
                            Collapse restarts
                        </label>
                        <label style={{ display: "block", marginTop: 12 }}>
                            Dead-air gap threshold:{" "}
                            <code>
                                {((scrubParams.dead_air_threshold_s as number | undefined) ?? 0.3).toFixed(2)}s
                            </code>
                        </label>
                        <input
                            type="range"
                            min={0.1}
                            max={1.5}
                            step={0.05}
                            className="tune-slider"
                            value={(scrubParams.dead_air_threshold_s as number | undefined) ?? 0.3}
                            onChange={(e) =>
                                updateScrub({ dead_air_threshold_s: Number(e.target.value) })
                            }
                        />
                    </div>
                </div>
            )}

            {/* ── Speakers — pre-filled smart defaults + edit affordance ── */}
            {showSpeakerCard && speakerRoster && (
                <div className="card">
                    <h2>Speakers <span className="muted" style={{ fontSize: "var(--fs-2)" }}>· {speakerRoster.length} detected</span></h2>
                    <p className="muted" style={{ fontSize: "var(--fs-2)" }}>
                        Suggestions are based on word count — higher count is usually the guest. Tap to override.
                    </p>
                    {speakerRoster.map((s) => {
                        const suggested = suggestSpeakerLabel(s.speaker_id, speakerRoster);
                        const current = speakerLabels[s.speaker_id] ?? "";
                        return (
                            <div key={s.speaker_id} className="speaker-row">
                                <code className="speaker-id">{s.speaker_id}</code>
                                <span className="muted speaker-words">
                                    {s.word_count.toLocaleString()} words
                                </span>
                                <span className="speaker-arrow muted">→</span>
                                <input
                                    type="text"
                                    className="speaker-input"
                                    placeholder={suggested}
                                    value={current}
                                    onChange={(e) =>
                                        updateSpeakerLabel(s.speaker_id, e.target.value)
                                    }
                                />
                                {!current && (
                                    <button
                                        type="button"
                                        className="link-button speaker-accept"
                                        title={`Use suggested label "${suggested}"`}
                                        onClick={() => updateSpeakerLabel(s.speaker_id, suggested)}
                                    >
                                        ✓ use {suggested}
                                    </button>
                                )}
                            </div>
                        );
                    })}
                </div>
            )}

            {/* ── Advanced — shot-aware editing + per-layer overrides ── */}
            {!isTightener && (
                <details className="card card--advanced">
                    <summary>
                        <span>Advanced</span>
                        <span className="muted" style={{ marginLeft: 8, fontSize: "var(--fs-2)" }}>
                            — shot-aware editing, per-layer overrides
                        </span>
                    </summary>
                    <div className="card-body">
                        <SensoryCard
                            settings={settings}
                            preset={preset}
                            timelineMode={timelineMode}
                            onSettingsChange={onSettingsChange}
                        />
                    </div>
                </details>
            )}

            {err && <div className="error-box">{err}</div>}

            <div className="row between">
                <button className="secondary" onClick={onBack} data-hotkey="back">← Back</button>
                <button onClick={onNext} data-hotkey="primary">Review the cut →</button>
            </div>
        </div>
    );
}


// v4 Phase 4.4 — Shot-aware editing card. Master toggle + dynamic subtitle +
// Advanced expand with per-layer overrides. Keeps the per-layer fields as
// tri-state (null/true/false) so the resolver can distinguish "follow the
// matrix" from "explicit on / off".
interface SensoryCardProps {
    settings: UserSettings;
    preset: PresetKey;
    timelineMode: "raw_dump" | "rough_cut" | "curated" | "assembled";
    onSettingsChange: (s: UserSettings) => void;
}

function SensoryCard({
    settings,
    preset,
    timelineMode,
    onSettingsChange,
}: SensoryCardProps) {
    const master = !!settings.sensory_master_enabled;
    const key = sensoryModeKey(preset, timelineMode);
    const subtitle =
        SENSORY_SUBTITLES[key] ?? SENSORY_SUBTITLES.raw_dump;
    const resolved = resolveSensoryLayers(settings, preset);

    // Tri-state per-layer override. Checked reflects the effective
    // resolution (matrix or explicit). Clicking toggles to the opposite
    // explicit value; a second click returns to matrix-defer (null).
    const nextOverride = (
        current: boolean | null | undefined,
        effective: boolean,
    ): boolean | null => {
        if (current === true) return false;
        if (current === false) return null;
        // current is null/undefined — flip to the opposite of the
        // current effective value so the checkbox click feels natural.
        return !effective;
    };

    const toggleLayer = (layer: "c" | "a" | "audio") => {
        if (layer === "c") {
            onSettingsChange({
                ...settings,
                layer_c_enabled: nextOverride(
                    settings.layer_c_enabled,
                    resolved.c,
                ),
            });
        } else if (layer === "a") {
            onSettingsChange({
                ...settings,
                layer_a_enabled: nextOverride(
                    settings.layer_a_enabled,
                    resolved.a,
                ),
            });
        } else {
            onSettingsChange({
                ...settings,
                layer_audio_enabled: nextOverride(
                    settings.layer_audio_enabled,
                    resolved.audio,
                ),
            });
        }
    };

    const toggleMaster = (on: boolean) => {
        // Turning master on/off clears any per-layer overrides so the
        // matrix governs the new state cleanly. Power users can re-flip
        // overrides under Advanced afterwards.
        onSettingsChange({
            ...settings,
            sensory_master_enabled: on,
            layer_c_enabled: null,
            layer_a_enabled: null,
            layer_audio_enabled: null,
        });
    };

    const overrideLabel = (override: boolean | null | undefined) => {
        if (override === true) return " (forced on)";
        if (override === false) return " (forced off)";
        return "";
    };

    return (
        <div className="card">
            <h2>Shot-aware editing</h2>
            <label
                style={{
                    display: "flex",
                    gap: 6,
                    alignItems: "center",
                    margin: 0,
                    marginTop: 4,
                }}
            >
                <input
                    type="checkbox"
                    checked={master}
                    onChange={(e) => toggleMaster(e.target.checked)}
                />
                Enable
            </label>
            <p className="muted" style={{ marginTop: 8 }}>
                {master
                    ? subtitle
                    : "Off — transcript-only cuts, matches v3 behaviour."}
            </p>

            <details className="card card--advanced" style={{ marginTop: 12 }}>
                <summary>
                    <span>
                        Advanced · per-layer overrides
                        {!master && (
                            <>
                                {" "}
                                <span
                                    className="muted"
                                    style={{ fontSize: "var(--fs-2)" }}
                                >
                                    · master off
                                </span>
                            </>
                        )}
                    </span>
                </summary>
                <div className="card-body">
                    <p className="muted" style={{ marginBottom: 8 }}>
                        The master switch auto-picks layers for this
                        preset+mode. Tick to force a layer on, tick again
                        to force off, a third time to return to matrix
                        defaults.
                    </p>
                    <label
                        style={{
                            display: "flex",
                            gap: 6,
                            alignItems: "center",
                            margin: 0,
                            marginBottom: 6,
                        }}
                    >
                        <input
                            type="checkbox"
                            checked={resolved.c}
                            onChange={() => toggleLayer("c")}
                        />
                        Layer C — Shot tagging (Gemini vision)
                        <span className="muted" style={{ fontSize: "var(--fs-2)" }}>
                            {overrideLabel(settings.layer_c_enabled)}
                        </span>
                    </label>
                    <label
                        style={{
                            display: "flex",
                            gap: 6,
                            alignItems: "center",
                            margin: 0,
                            marginBottom: 6,
                        }}
                    >
                        <input
                            type="checkbox"
                            checked={resolved.a}
                            onChange={() => toggleLayer("a")}
                        />
                        Layer A — Boundary validator (post-plan retry)
                        <span className="muted" style={{ fontSize: "var(--fs-2)" }}>
                            {overrideLabel(settings.layer_a_enabled)}
                        </span>
                    </label>
                    <label
                        style={{
                            display: "flex",
                            gap: 6,
                            alignItems: "center",
                            margin: 0,
                        }}
                    >
                        <input
                            type="checkbox"
                            checked={resolved.audio}
                            onChange={() => toggleLayer("audio")}
                        />
                        Layer Audio — Pause / silence / RMS cues (DSP)
                        <span className="muted" style={{ fontSize: "var(--fs-2)" }}>
                            {overrideLabel(settings.layer_audio_enabled)}
                        </span>
                    </label>
                    <p className="muted" style={{ marginTop: 10, fontSize: "var(--fs-2)" }}>
                        Layers C and Audio run during analyze — toggle on the
                        preset screen to apply on the first run, or re-analyze
                        to pick them up later. Layer A runs at build-plan time,
                        so changes here apply to the next plan you build.
                    </p>
                </div>
            </details>
        </div>
    );
}
