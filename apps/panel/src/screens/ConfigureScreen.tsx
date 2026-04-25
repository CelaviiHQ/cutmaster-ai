import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { SourceAspectInfo } from "../api";
import MascotLoading from "./MascotLoading";
import {
    SENSORY_MATRIX,
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

            {/* ── Advanced — shot-aware editing (single disclosure) ── */}
            {!isTightener && (
                <details className="card card--advanced">
                    <summary>
                        <span>Shot-aware editing</span>
                        <span className="muted" style={{ marginLeft: 8, fontSize: "var(--fs-2)" }}>
                            — vision + audio cue layers · {settings.sensory_master_enabled ? "ON" : "off"}
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

// Layer-level metadata. Codenames (C / A / Audio) live here as `tag`
// so the rare power user can still see them on hover, but the headline
// is the functional name designers think in.
interface LayerMeta {
    key: "c" | "a" | "audio";
    tag: string;            // engineer codename — tooltip only
    name: string;           // headline
    desc: string;           // one-line plain-English summary
    when: "analyze" | "build";
    cost: string;           // wall-clock or LLM cost summary
}

const LAYER_META: LayerMeta[] = [
    {
        key: "c",
        tag: "Layer C",
        name: "Shot tagging",
        desc: "Gemini vision tags each shot's framing, motion and gesture.",
        when: "analyze",
        cost: "+30–60s first run · cached afterwards",
    },
    {
        key: "a",
        tag: "Layer A",
        name: "Boundary validator",
        desc: "Re-runs the Director when a planned cut lands mid-gesture.",
        when: "build",
        cost: "+1 LLM call per plan · no analyze cost",
    },
    {
        key: "audio",
        tag: "Layer Audio",
        name: "Audio cues",
        desc: "Pause / silence / RMS energy. Catches beats and fillers the transcript misses.",
        when: "analyze",
        cost: "+5–15s first run · cached afterwards",
    },
];

// Pretty label for a content-type / cut-intent / mode combo. Used in
// the "Default mix for X" sentence.
function presetCombo(preset: PresetKey, timelineMode: string): string {
    const label = (CONTENT_TYPE_META[preset]?.label ?? preset);
    const mode = timelineMode.replace("_", " ");
    return `${label} × ${mode}`;
}

function SensoryCard({
    settings,
    preset,
    timelineMode,
    onSettingsChange,
}: SensoryCardProps) {
    const master = !!settings.sensory_master_enabled;
    const resolved = resolveSensoryLayers(settings, preset);

    // Read the matrix row directly so each layer card can show "default
    // for this combo: ON/OFF" — the matrix used to be invisible to users.
    const matrixKey = sensoryModeKey(preset, timelineMode);
    const matrixRow = SENSORY_MATRIX[matrixKey] ?? SENSORY_MATRIX.raw_dump;
    const matrixDefault = (k: "c" | "a" | "audio"): boolean =>
        matrixRow[k] === "default";

    // Tri-state state machine, but driven by an explicit segmented control
    // (default / force-on / force-off) instead of click-counting.
    type Override = boolean | null;
    const overrideOf = (k: "c" | "a" | "audio"): Override => {
        if (k === "c") return settings.layer_c_enabled ?? null;
        if (k === "a") return settings.layer_a_enabled ?? null;
        return settings.layer_audio_enabled ?? null;
    };
    const setOverride = (k: "c" | "a" | "audio", v: Override) => {
        if (k === "c") onSettingsChange({ ...settings, layer_c_enabled: v });
        else if (k === "a") onSettingsChange({ ...settings, layer_a_enabled: v });
        else onSettingsChange({ ...settings, layer_audio_enabled: v });
    };

    const toggleMaster = (on: boolean) => {
        // Turning master on/off clears overrides so the matrix governs
        // the new state cleanly. Power users can re-set them after.
        onSettingsChange({
            ...settings,
            sensory_master_enabled: on,
            layer_c_enabled: null,
            layer_a_enabled: null,
            layer_audio_enabled: null,
        });
    };

    // Live "currently active" summary — the names of layers that will
    // actually fire given current settings. Empty when master is off.
    const activeNames = master
        ? LAYER_META.filter((l) => resolved[l.key]).map((l) => l.name)
        : [];

    const defaultMixNames = LAYER_META.filter((l) => matrixDefault(l.key)).map((l) => l.name);

    return (
        <div className="sensory">
            <div className="sensory-head">
                <div>
                    <h2 style={{ margin: 0 }}>Shot-aware editing</h2>
                    <p className="muted sensory-status">
                        {master
                            ? activeNames.length > 0
                                ? <>Currently active: <strong>{activeNames.join(" + ")}</strong></>
                                : <>On — but every layer is forced off.</>
                            : <>Off — transcript-only cuts. Matches v3 behaviour.</>}
                    </p>
                </div>
                <button
                    type="button"
                    role="switch"
                    aria-checked={master}
                    className={`switch ${master ? "is-on" : ""}`}
                    onClick={() => toggleMaster(!master)}
                >
                    <span className="switch-track">
                        <span className="switch-thumb" />
                    </span>
                    <span className="switch-label">{master ? "ON" : "OFF"}</span>
                </button>
            </div>

            <p className="muted sensory-defaultmix">
                Default mix for <strong>{presetCombo(preset, timelineMode)}</strong>
                {defaultMixNames.length > 0
                    ? <> — {defaultMixNames.join(" + ")}.</>
                    : <> — none.</>}
            </p>

            <div className={`sensory-layers ${!master ? "is-disabled" : ""}`}>
                {LAYER_META.map((l) => {
                    const isOn = resolved[l.key];
                    const override = overrideOf(l.key);
                    const def = matrixDefault(l.key);
                    const segState: "default" | "on" | "off" =
                        override === true ? "on" : override === false ? "off" : "default";
                    return (
                        <div
                            key={l.key}
                            className={`sensory-layer sensory-layer--${segState} ${isOn ? "is-on" : "is-off"}`}
                        >
                            <div className="sensory-layer-head">
                                <span className="sensory-layer-glyph" aria-hidden>
                                    {isOn ? "✦" : "○"}
                                </span>
                                <span
                                    className="sensory-layer-name"
                                    title={`${l.tag} — engineer codename`}
                                >
                                    {l.name}
                                </span>
                                <span
                                    className={`sensory-when sensory-when--${l.when}`}
                                    title={
                                        l.when === "analyze"
                                            ? "Runs during analyze — re-analyze on the Preset screen to apply changes."
                                            : "Runs at build time — your next Regenerate will pick up changes."
                                    }
                                >
                                    {l.when === "analyze" ? "analyze" : "build"}
                                </span>
                            </div>
                            <p className="sensory-layer-desc">{l.desc}</p>
                            <p className="sensory-layer-cost muted">{l.cost}</p>
                            <div className="sensory-layer-control">
                                <span className="sensory-layer-default-label muted">
                                    Default for this combo: <strong>{def ? "ON" : "OFF"}</strong>
                                </span>
                                <div
                                    className="seg-control"
                                    role="radiogroup"
                                    aria-label={`${l.name} — override`}
                                >
                                    <button
                                        type="button"
                                        role="radio"
                                        aria-checked={segState === "default"}
                                        className={`seg-btn ${segState === "default" ? "on" : ""}`}
                                        onClick={() => setOverride(l.key, null)}
                                        title="Use the default for this preset / mode combination"
                                    >
                                        default <span className="muted">({def ? "on" : "off"})</span>
                                    </button>
                                    <button
                                        type="button"
                                        role="radio"
                                        aria-checked={segState === "on"}
                                        className={`seg-btn seg-btn--on ${segState === "on" ? "on" : ""}`}
                                        onClick={() => setOverride(l.key, true)}
                                    >
                                        force on
                                    </button>
                                    <button
                                        type="button"
                                        role="radio"
                                        aria-checked={segState === "off"}
                                        className={`seg-btn seg-btn--off ${segState === "off" ? "on" : ""}`}
                                        onClick={() => setOverride(l.key, false)}
                                    >
                                        force off
                                    </button>
                                </div>
                            </div>
                        </div>
                    );
                })}
            </div>

            <p className="muted sensory-foot">
                ⓘ Layers tagged <em>analyze</em> need a re-analyze on the Preset
                screen to apply changes. Layers tagged <em>build</em> apply on
                your next Regenerate.
            </p>
        </div>
    );
}
