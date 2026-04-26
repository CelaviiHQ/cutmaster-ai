import { useEffect, useState } from "react";
import { api } from "../api";
import type { ExecuteResult } from "../api";
import MascotLoading from "./MascotLoading";
import CutHealthCard from "../components/CutHealthCard";
import { formatRelativeTime } from "../persist";
import type {
    BuildPlanResult,
    CoherenceReport,
    ExecuteHistoryEntry,
    PresetBundle,
    PresetKey,
    StoryAnalysis,
    UserSettings,
} from "../types";

// Format seconds as HH:MM:SS — designers think in timecode, not floats.
const tc = (s: number): string => {
    const total = Math.max(0, Math.round(s));
    const h = Math.floor(total / 3600);
    const m = Math.floor((total % 3600) / 60);
    const sec = total % 60;
    const pad = (n: number) => String(n).padStart(2, "0");
    return h > 0 ? `${h}:${pad(m)}:${pad(sec)}` : `${pad(m)}:${pad(sec)}`;
};

// Visual band per arc role on the proportional plan bar.
const ROLE_BAND: Record<string, string> = {
    hook: "#4a9eff",
    setup: "#5ac8fa",
    reinforce: "#64d2c4",
    escalate: "#ffb454",
    resolve: "#67d97a",
    cta: "#bf5af2",
};
const roleColor = (role: string | null | undefined, isHook: boolean) =>
    isHook ? ROLE_BAND.hook : (role && ROLE_BAND[role]) || "#7a7a85";

// Resolve marker palette → CSS color for the on-bar pin + list dot.
const MARKER_HEX: Record<string, string> = {
    blue: "#4a9eff",
    cyan: "#5ac8fa",
    green: "#67d97a",
    yellow: "#ffd60a",
    red: "#ff453a",
    pink: "#ff6b9d",
    purple: "#bf5af2",
    fuchsia: "#ff2d92",
    rose: "#ff6482",
    lavender: "#a78bfa",
    sky: "#7dd3fc",
    mint: "#5eead4",
    lemon: "#fde047",
    sand: "#d4b483",
    cocoa: "#8b6f47",
    cream: "#f5e6c5",
};
const markerColor = (name: string): string =>
    MARKER_HEX[name.trim().toLowerCase()] || "#4a9eff";

// Map an engagement score (0..1) to a human strength label + segment count
// for the visual bar. Designers don't read "0.82" as "good".
const STRENGTH_TIERS = [
    { min: 0.85, label: "very strong", segs: 5 },
    { min: 0.7,  label: "strong",      segs: 4 },
    { min: 0.55, label: "ok",          segs: 3 },
    { min: 0.4,  label: "weak",        segs: 2 },
    { min: 0,    label: "fragile",     segs: 1 },
];
const strengthOf = (score: number) =>
    STRENGTH_TIERS.find((t) => score >= t.min) ?? STRENGTH_TIERS[STRENGTH_TIERS.length - 1];

// Quick-target presets shown as chips next to the slider.
const TARGET_PRESETS: { label: string; seconds: number }[] = [
    { label: "TikTok",  seconds: 30  },
    { label: "Reel",    seconds: 60  },
    { label: "Default", seconds: 180 },
    { label: "Long",    seconds: 300 },
];

// Short-form label so always-visible marker pins don't overflow the bar.
const shortMarkerLabel = (name: string): string => {
    // Strip leading "B-Roll to cover cut:" / "Archive insert:" prefixes.
    const cleaned = name.replace(/^(b-roll[^:]*:|archive[^:]*:)\s*/i, "");
    return cleaned.length > 22 ? cleaned.slice(0, 21) + "…" : cleaned;
};

// Phase 5.8 — content-type preset whitelist for the build-plan request
// remapper. Mirrors ``axes.ts::CONTENT_TYPE_PRESETS`` but scoped to this
// screen so the Review refactor stays local.
const CONTENT_TYPE_PRESETS_REVIEW: ReadonlySet<string> = new Set([
    "vlog",
    "product_demo",
    "wedding",
    "interview",
    "tutorial",
    "podcast",
    "presentation",
    "reaction",
]);

interface Props {
    runId: string;
    preset: PresetKey;
    settings: UserSettings;
    onSettingsChange?: (s: UserSettings) => void;
    onBack: () => void;
    onReset: () => void;
    // v3-5.4 — let the app header show the current clip count.
    onClipCount?: (n: number | null) => void;
    // Source timeline name — used to compute the default "Cut name" input.
    timelineName: string;
    // User-picked cut name (lives in the app header).
    cutName: string;
    // Optional: fired once per successful build so the app shell can
    // refresh its Saved chip without waiting on the settings debounce.
    onBuildSuccess?: () => void;
    // Updates the cutName in the app header — used by the Rebuild action
    // in the execute_history panel to prefill a unique name.
    onCutNameChange?: (name: string) => void;
    // Updates the App-level preset when Review has to resolve "auto" itself
    // (resume-direct-to-Review skips Configure's detect step). Optional so
    // the screen still works in isolation.
    onPresetChange?: (p: PresetKey) => void;
}

export default function ReviewScreen({
    runId,
    preset,
    settings,
    onSettingsChange,
    onBack,
    onReset,
    onClipCount,
    timelineName,
    cutName,
    onBuildSuccess,
    onCutNameChange,
    onPresetChange,
}: Props) {
    const [analysis, setAnalysis] = useState<StoryAnalysis | null>(null);
    const [regenerating, setRegenerating] = useState(false);
    // Tracks "regenerate with recommendations" specifically — the
    // critic-fed-rebuild path is a 20–60s round-trip with the auto-rework
    // loop on top, so we surface the same MascotLoading screen as the
    // initial-mount build instead of leaving the editor staring at a
    // stale plan with only a tiny "Regenerating…" button label changing.
    const [regeneratingWithFeedback, setRegeneratingWithFeedback] = useState(false);
    const [plan, setPlan] = useState<BuildPlanResult | null>(null);
    const [bundle, setBundle] = useState<PresetBundle | null>(null);
    const [loading, setLoading] = useState(true);
    const [err, setErr] = useState<string | null>(null);
    const [building, setBuilding] = useState(false);
    const [buildProgress, setBuildProgress] = useState<string | null>(null);
    const [buildResult, setBuildResult] = useState<ExecuteResult | null>(null);
    const [buildAllResults, setBuildAllResults] = useState<ExecuteResult[]>([]);
    const [buildErr, setBuildErr] = useState<string | null>(null);
    const [deleting, setDeleting] = useState(false);
    const [selectedCandidate, setSelectedCandidate] = useState(0);
    const [replaceExisting, setReplaceExisting] = useState(false);
    const [existingNames, setExistingNames] = useState<Set<string>>(new Set());
    const [executeHistory, setExecuteHistory] = useState<ExecuteHistoryEntry[]>([]);
    // Feature: inline transcript expansion per segment. Fetched once on
    // mount from the run's scrubbed transcript; one row at a time can be
    // expanded to surface the words that fall inside its [start_s, end_s].
    const [transcript, setTranscript] = useState<
        Array<{ word: string; start_time: number; end_time: number }>
    >([]);
    const [expandedSegment, setExpandedSegment] = useState<number | null>(null);
    // Director-prompt viewer. Loaded lazily on first open via
    // GET /cutmaster/debug/prompt/{run_id}.
    const [promptText, setPromptText] = useState<string | null>(null);
    const [promptErr, setPromptErr] = useState<string | null>(null);
    const [promptOpen, setPromptOpen] = useState(false);
    const [promptLoading, setPromptLoading] = useState(false);
    // Plan-bar scrubber state. `playheadPct` is 0..1 along the assembled cut;
    // `hoverPct` is the live mouse-track for the hover tooltip. Both are null
    // when the user hasn't engaged yet.
    const [playheadPct, setPlayheadPct] = useState<number | null>(null);
    const [hoverPct, setHoverPct] = useState<number | null>(null);
    // Story-critic re-critique state. Phase 4.4 — debounced to 1s so
    // impatient clicks don't spam the LLM.
    const [recritiqueBusy, setRecritiqueBusy] = useState(false);
    const [recritiqueErr, setRecritiqueErr] = useState<string | null>(null);
    const [recritiqueLastFiredAt, setRecritiqueLastFiredAt] = useState(0);

    const recritique = async () => {
        if (recritiqueBusy) return;
        const now = Date.now();
        if (now - recritiqueLastFiredAt < 1000) return; // debounce
        setRecritiqueLastFiredAt(now);
        setRecritiqueBusy(true);
        setRecritiqueErr(null);
        try {
            const res = await fetch(`/cutmaster/critique/${runId}`, {
                method: "POST",
            });
            if (!res.ok) {
                const body = await res.text();
                setRecritiqueErr(`${res.status} — ${body || "no detail"}`);
                return;
            }
            // Refresh the persisted plan so the card re-renders with the
            // new envelope.
            const fresh = await api.getState(runId);
            const nextPlan = fresh.plan as BuildPlanResult | undefined;
            if (nextPlan) setPlan(nextPlan);
        } catch (e) {
            setRecritiqueErr(String(e));
        } finally {
            setRecritiqueBusy(false);
        }
    };

    const openPrompt = async (opts?: { pass?: "rework" | number }) => {
        const pass = opts?.pass;
        // Reset modal state for whichever pass we're loading. Both v1
        // and per-iteration dumps share the modal — switching back
        // loads from disk again, simpler than caching every pass.
        setPromptOpen(true);
        setPromptLoading(true);
        setPromptErr(null);
        setPromptText(null);
        const passQuery =
            pass === undefined
                ? null
                : typeof pass === "number"
                  ? String(pass)
                  : pass;
        const url = passQuery
            ? `/cutmaster/debug/prompt/${runId}?pass=${passQuery}`
            : `/cutmaster/debug/prompt/${runId}`;
        try {
            const res = await fetch(url);
            if (!res.ok) {
                const body = await res.text();
                setPromptErr(`${res.status} — ${body || "no prompt cached"}`);
                return;
            }
            setPromptText(await res.text());
        } catch (e) {
            setPromptErr(String(e));
        } finally {
            setPromptLoading(false);
        }
    };

    const refreshHistory = async () => {
        try {
            const state = await api.getState(runId);
            setExecuteHistory(state.execute_history ?? []);
            // Cache the scrubbed transcript so segment rows can expand
            // inline to show their words. Falls back to the raw
            // transcript when scrubbed is empty (analyze not run yet).
            const words =
                state.scrubbed && state.scrubbed.length > 0
                    ? state.scrubbed
                    : (state.transcript ?? []);
            setTranscript(
                words.map((w) => ({
                    word: w.word,
                    start_time: w.start_time,
                    end_time: w.end_time,
                })),
            );
        } catch {
            // /state unreachable — hide the history panel rather than error out.
            setExecuteHistory([]);
        }
    };

    // Refresh the known timeline-name list. Called on mount and after every
    // successful build so the collision warning stays accurate.
    const refreshTimelineNames = async () => {
        try {
            const info = await api.projectInfo();
            setExistingNames(new Set(info.timelines.map((t) => t.name)));
        } catch {
            // Resolve unreachable — leave the set empty; we fall back to the
            // backend's existing _unique_timeline_name safety net.
        }
    };

    useEffect(() => {
        refreshTimelineNames();
        refreshHistory();
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    // v3-5.4 — emit current clip count to the app header for the step indicator.
    useEffect(() => {
        if (!plan) {
            onClipCount?.(null);
            return;
        }
        const ch = plan.clip_hunter;
        const cand = ch?.candidates[selectedCandidate];
        // Short Generator candidate has `spans[]`; Clip Hunter candidate is a
        // single span (resolved_segments[]). Fall back to the director's
        // selected_clips when the candidate shape doesn't carry a count.
        const n = cand?.spans
            ? cand.spans.length
            : cand?.resolved_segments
                ? cand.resolved_segments.length
                : plan.director.selected_clips.length;
        onClipCount?.(n);
    }, [plan, selectedCandidate, onClipCount]);

    useEffect(() => {
        let cancelled = false;
        (async () => {
            setLoading(true);
            setErr(null);
            try {
                // Resume-direct-to-Review can carry preset="auto" (the
                // sentinel for "run the cascade") because Configure's
                // detect step never ran. /build-plan only knows resolved
                // preset keys, so resolve it here first — mirrors the
                // ConfigureScreen mount effect.
                let effectivePreset: PresetKey = preset;
                if (preset === "auto") {
                    const r = await api.detectPreset(runId);
                    if (cancelled) return;
                    effectivePreset = r.preset;
                    onPresetChange?.(r.preset);
                }
                // Phase 5.8 — send ``content_type`` when the preset is a
                // content-type key; legacy cut-intent presets (tightener
                // / clip_hunter / short_generator) still rely on the
                // backend's auto-remapping.
                const contentType = CONTENT_TYPE_PRESETS_REVIEW.has(effectivePreset)
                    ? effectivePreset
                    : null;
                const [p, presetList, cachedThemes] = await Promise.all([
                    api.buildPlan(runId, effectivePreset, settings, contentType),
                    api.listPresets().catch(() => ({ presets: [] })),
                    api.themesCache(runId).catch(() => null),
                ]);
                if (cancelled) return;
                setPlan(p);
                setBundle(
                    presetList.presets.find((b) => b.key === effectivePreset) ?? null,
                );
                if (cachedThemes?.analysis) {
                    setAnalysis(cachedThemes.analysis);
                }
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

    const regenerate = async (
        next: UserSettings,
        opts?: { criticFeedback?: Record<string, unknown> | null },
    ) => {
        setRegenerating(true);
        setErr(null);
        try {
            // Same auto-resolution guard as the mount effect — a regenerate
            // call shouldn't 400 just because the seed preset never got
            // promoted past the "auto" sentinel.
            let effectivePreset: PresetKey = preset;
            if (preset === "auto") {
                const r = await api.detectPreset(runId);
                effectivePreset = r.preset;
                onPresetChange?.(r.preset);
            }
            const contentType = CONTENT_TYPE_PRESETS_REVIEW.has(effectivePreset)
                ? effectivePreset
                : null;
            const p = await api.buildPlan(
                runId,
                effectivePreset,
                next,
                contentType,
                opts?.criticFeedback ?? null,
            );
            setPlan(p);
            onSettingsChange?.(next);
            // Reset any build attempt tied to the prior plan.
            setBuildResult(null);
            setBuildAllResults([]);
        } catch (e) {
            setErr(String(e));
        } finally {
            setRegenerating(false);
        }
    };

    /**
     * "Regenerate with recommendations" — feeds the current build's
     * coherence_report into a fresh /build-plan call as the critic
     * feedback the Director must address. Different from the plain
     * Regenerate button (no feedback, fresh start) and from Re-critique
     * (re-grades the SAME plan).
     *
     * The card filters out any issue the editor marked fixed locally
     * before invoking this — so "I'll add a voiceover myself" issues
     * don't get sent. ``unfixedIssues`` is the post-filter subset.
     */
    const regenerateWithCriticFeedback = async (
        report: CoherenceReport,
        unfixedIssues: typeof report.issues,
    ): Promise<void> => {
        setRegeneratingWithFeedback(true);
        try {
            await regenerate(settings, {
                criticFeedback: {
                    score: report.score,
                    verdict: report.verdict,
                    summary: report.summary,
                    issues: unfixedIssues.map((iss) => ({
                        segment_index: iss.segment_index,
                        severity: iss.severity,
                        category: iss.category,
                        message: iss.message,
                        suggestion: iss.suggestion,
                    })),
                    history: [],
                },
            });
        } finally {
            setRegeneratingWithFeedback(false);
        }
    };

    if (loading) {
        return (
            <MascotLoading
                label="Building plan"
                hint="Director agent composes the cut; Marker agent picks B-roll cues. Usually 5–15 s."
                stages={[
                    { label: "Director agent (plan the cut)", status: "started" },
                    { label: "Marker agent (B-roll cues)", status: "started" },
                    { label: "Resolve source-frame mapping", status: "pending" },
                ]}
            />
        );
    }

    if (regenerating) {
        // Both regenerate paths (Tune-the-cut "Regenerate plan" and
        // Cut-health "Regenerate with recommendations") block on a
        // /build-plan round-trip; surface the same MascotLoading the
        // initial mount uses so the editor sees something is happening
        // instead of a frozen panel. Copy varies by which path fired.
        return regeneratingWithFeedback ? (
            <MascotLoading
                label="Regenerating with recommendations"
                hint="Director rebuilds with the critic's feedback; the iterative loop runs up to 3 reworks. Usually 20–60 s."
                stages={[
                    { label: "Director agent (rework with feedback)", status: "started" },
                    { label: "Story-critic loop (iterate while improving)", status: "started" },
                    { label: "Marker agent (B-roll cues)", status: "pending" },
                ]}
            />
        ) : (
            <MascotLoading
                label="Regenerating plan"
                hint="Director recomposes the cut with your updated settings; Marker agent re-picks B-roll cues. Usually 5–15 s."
                stages={[
                    { label: "Director agent (recompose the cut)", status: "started" },
                    { label: "Marker agent (B-roll cues)", status: "started" },
                    { label: "Resolve source-frame mapping", status: "pending" },
                ]}
            />
        );
    }

    if (err) {
        return (
            <div>
                <div className="error-box">{err}</div>
                <div className="row between">
                    <button className="secondary" onClick={onBack} data-hotkey="back">← Back</button>
                    <button className="secondary" onClick={onReset}>Start over</button>
                </div>
            </div>
        );
    }

    if (!plan) return null;

    const appliedExcludes = plan.user_settings.exclude_categories ?? [];
    const appliedFocus = plan.user_settings.custom_focus ?? null;
    const clipHunter = plan.clip_hunter;
    const selectedClip =
        clipHunter?.candidates?.[selectedCandidate] ?? null;
    const excludeLabels = bundle
        ? appliedExcludes
              .map(
                  (key) =>
                      bundle.exclude_categories.find((c) => c.key === key)?.label ??
                      key,
              )
        : appliedExcludes;

    return (
        <div>
            {/* "Plan details" — kept for clipHunter / timeline_state /
                tightener / exclusions metadata that doesn't belong on the
                primary cut card. Renders only when there's something to show. */}
            {(clipHunter || plan.timeline_state || plan.tightener ||
              excludeLabels.length > 0 || appliedFocus) && (
                <details className="card card--advanced">
                    <summary>
                        <span>Plan details</span>
                        <span className="muted" style={{ marginLeft: 8, fontSize: "var(--fs-2)" }}>
                            — preset settings, exclusions, take selection
                        </span>
                    </summary>
                    <div className="card-body">
                        {clipHunter && (
                            <p className="muted">
                                {clipHunter.candidates.length} clip candidate(s) @ target{" "}
                                <code>{clipHunter.target_clip_length_s.toFixed(0)}s</code>
                                &nbsp;from a {(clipHunter.source_duration_s / 60).toFixed(1)}-min source.
                            </p>
                        )}
                        {clipHunter?.duration_warning && (
                            <p className="muted" style={{ color: "var(--warn)" }}>
                                {clipHunter.duration_warning}
                            </p>
                        )}
                        {plan.timeline_state && (
                            <p className="muted">
                                {plan.timeline_state.mode === "curated" && (
                                    <>
                                        <strong>Curated</strong> — used all{" "}
                                        <code>{plan.timeline_state.total_takes}</code> takes,
                                        arranged in order{" "}
                                        <code>[{plan.timeline_state.takes_used.join(", ")}]</code>.
                                    </>
                                )}
                                {plan.timeline_state.mode === "rough_cut" && (
                                    <>
                                        <strong>Rough cut</strong> — detected{" "}
                                        <code>{plan.timeline_state.groups?.length ?? 0}</code>{" "}
                                        group(s); kept{" "}
                                        <code>{plan.timeline_state.takes_used.length}</code>{" "}
                                        winner(s) from{" "}
                                        <code>{plan.timeline_state.total_takes}</code> candidate
                                        take(s).
                                        {plan.timeline_state.all_singletons && (
                                            <> No alternates detected — treated as Curated.</>
                                        )}
                                    </>
                                )}
                            </p>
                        )}
                        {plan.tightener && (
                            <p className="muted">
                                <strong>
                                    {(plan.tightener.percent_tighter * 100).toFixed(1)}% tighter
                                </strong>
                                &nbsp;— kept <code>{plan.tightener.kept_words}</code> of{" "}
                                <code>{plan.tightener.original_words}</code> words&nbsp;
                                (<code>{plan.tightener.segment_total_s.toFixed(1)}s</code> out of{" "}
                                <code>{plan.tightener.take_total_s.toFixed(1)}s</code> take time).
                            </p>
                        )}
                        {(excludeLabels.length > 0 || appliedFocus) && (
                            <p className="muted">
                                {excludeLabels.length > 0 && (
                                    <>Applied exclusions ({excludeLabels.length}): {excludeLabels.join(", ")}</>
                                )}
                                {excludeLabels.length > 0 && appliedFocus && " · "}
                                {appliedFocus && <>Focus: &ldquo;{appliedFocus}&rdquo;</>}
                            </p>
                        )}
                    </div>
                </details>
            )}

            {clipHunter && clipHunter.candidates.length > 0 && (
                <div className="card">
                    <h2>
                        {clipHunter.mode === "short_generator"
                            ? "Short candidates — pick one to build"
                            : "Clip candidates — pick one to build"}
                    </h2>
                    <div className="row" style={{ flexWrap: "wrap" }}>
                        {clipHunter.candidates.map((c, i) => {
                            const duration =
                                clipHunter.mode === "short_generator"
                                    ? (c.total_s ?? 0)
                                    : (c.end_s ?? 0) - (c.start_s ?? 0);
                            return (
                                <button
                                    key={i}
                                    className={
                                        i === selectedCandidate ? "" : "secondary"
                                    }
                                    onClick={() => {
                                        setSelectedCandidate(i);
                                        if (i !== selectedCandidate) setBuildResult(null);
                                    }}
                                >
                                    #{i + 1} ·{" "}
                                    {(c.engagement_score * 100).toFixed(0)}%
                                    &nbsp;· {duration.toFixed(0)}s
                                </button>
                            );
                        })}
                    </div>
                    {selectedClip && (
                        <div style={{ marginTop: 10 }}>
                            {clipHunter.mode === "short_generator" ? (
                                <>
                                    <p>
                                        <strong>{selectedClip.theme}</strong>
                                    </p>
                                    <p className="muted">{selectedClip.reasoning}</p>
                                    {selectedClip.suggested_caption && (
                                        <p className="muted">
                                            <strong>Caption:</strong>{" "}
                                            {selectedClip.suggested_caption}
                                        </p>
                                    )}
                                    <p className="muted">
                                        {selectedClip.spans?.length ?? 0} spans ·{" "}
                                        total{" "}
                                        <code>
                                            {(selectedClip.total_s ?? 0).toFixed(1)}
                                            s
                                        </code>
                                    </p>
                                    {selectedClip.spans && (
                                        <div
                                            className="seg-list"
                                            style={{ marginTop: 6 }}
                                        >
                                            {selectedClip.spans.map((s, j) => (
                                                <div key={j} className="seg">
                                                    <span className="seg-time">
                                                        {s.start_s.toFixed(2)}s
                                                    </span>
                                                    <span className="seg-time">
                                                        {(
                                                            s.end_s - s.start_s
                                                        ).toFixed(1)}
                                                        s
                                                    </span>
                                                    <span className="seg-reason">
                                                        {s.role || "span"}
                                                    </span>
                                                </div>
                                            ))}
                                        </div>
                                    )}
                                </>
                            ) : (
                                <>
                                    <p>
                                        <strong>
                                            &ldquo;{selectedClip.quote}&rdquo;
                                        </strong>
                                    </p>
                                    <p className="muted">{selectedClip.reasoning}</p>
                                    {selectedClip.suggested_caption && (
                                        <p className="muted">
                                            <strong>Caption:</strong>{" "}
                                            {selectedClip.suggested_caption}
                                        </p>
                                    )}
                                    <p className="muted">
                                        Source:{" "}
                                        <code>
                                            {(selectedClip.start_s ?? 0).toFixed(2)}
                                            s
                                        </code>{" "}
                                        →{" "}
                                        <code>
                                            {(selectedClip.end_s ?? 0).toFixed(2)}
                                            s
                                        </code>
                                    </p>
                                </>
                            )}
                        </div>
                    )}
                </div>
            )}

            {(() => {
                const segs = clipHunter
                    ? (clipHunter.candidates[selectedCandidate]
                          ?.resolved_segments ?? []
                      ).map((s) => ({
                          start_s: s.start_s,
                          end_s: s.end_s,
                          reason: s.reason,
                          arc_role: null as string | null,
                      }))
                    : plan.director.selected_clips;
                const total = segs.reduce((a, c) => a + (c.end_s - c.start_s), 0);
                const markers = plan.markers.markers;
                // Map a source-time second onto its offset along the assembled
                // cut by walking the segment list.
                const sourceToCutOffset = (atS: number): number | null => {
                    let acc = 0;
                    for (const seg of segs) {
                        const dur = seg.end_s - seg.start_s;
                        if (atS >= seg.start_s && atS <= seg.end_s) {
                            return acc + (atS - seg.start_s);
                        }
                        acc += dur;
                    }
                    return null;
                };
                // Inverse — given a cut offset (seconds along assembled), find
                // the source second of that frame. Powers the scrubber tooltip.
                const cutOffsetToSource = (offS: number): { src: number; segIndex: number } | null => {
                    let acc = 0;
                    for (let i = 0; i < segs.length; i++) {
                        const seg = segs[i];
                        const dur = seg.end_s - seg.start_s;
                        if (offS <= acc + dur) {
                            return { src: seg.start_s + (offS - acc), segIndex: i };
                        }
                        acc += dur;
                    }
                    return null;
                };
                const onBarMove = (e: React.MouseEvent<HTMLDivElement>) => {
                    const rect = e.currentTarget.getBoundingClientRect();
                    const pct = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
                    setHoverPct(pct);
                };
                const onBarClick = (e: React.MouseEvent<HTMLDivElement>) => {
                    const rect = e.currentTarget.getBoundingClientRect();
                    const pct = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
                    setPlayheadPct(pct);
                };
                const hoverTip =
                    hoverPct !== null && total > 0 ? cutOffsetToSource(hoverPct * total) : null;
                return (
                    <div className="card card--primary cut-card">
                        <div className="cut-head">
                            <h2 style={{ marginBottom: 0 }}>The cut</h2>
                            <span className="cut-stats">
                                {segs.length} segments · {tc(total)}
                                <span className="muted"> ({total.toFixed(1)}s)</span>
                                {markers.length > 0 && <> · {markers.length} markers</>}
                            </span>
                        </div>
                        {plan.director.reasoning && (
                            <p className="muted cut-reasoning">{plan.director.reasoning}</p>
                        )}
                        <div className="plan-bar-wrap">
                            {/* Always-visible marker labels above the bar.
                                Two-row layout: a label whose centre falls
                                within ~14% of the previous one drops to the
                                lower row so they don't overlap. */}
                            {(() => {
                                const placed: { idx: number; pct: number; row: 0 | 1 }[] = [];
                                let lastRow0Pct = -100;
                                let lastRow1Pct = -100;
                                markers.forEach((m, i) => {
                                    const off = sourceToCutOffset(m.at_s);
                                    if (off === null || total === 0) return;
                                    const pct = (off / total) * 100;
                                    // Drop to row 1 when row 0 is too close.
                                    let row: 0 | 1 = 0;
                                    if (pct - lastRow0Pct < 14) row = 1;
                                    if (row === 0) lastRow0Pct = pct;
                                    else lastRow1Pct = pct;
                                    placed.push({ idx: i, pct, row });
                                });
                                void lastRow1Pct;
                                if (placed.length === 0) {
                                    return null;
                                }
                                return (
                                    <div className="plan-bar-labels">
                                        <span
                                            className="plan-bar-labels-anchor"
                                            aria-hidden
                                        >
                                            Markers
                                        </span>
                                        {placed.map(({ idx, pct, row }) => {
                                            const m = markers[idx];
                                            return (
                                                <button
                                                    key={idx}
                                                    type="button"
                                                    className={`plan-bar-marker-label plan-bar-marker-label--row${row}`}
                                                    style={{ left: `${pct}%`, color: markerColor(m.color) }}
                                                    title={`${m.name} — ${m.note}`}
                                                    onClick={() => {
                                                        const el = document.getElementById(`marker-${idx}`);
                                                        el?.scrollIntoView({ behavior: "smooth", block: "center" });
                                                        el?.classList.add("marker-row--flash");
                                                        setTimeout(
                                                            () => el?.classList.remove("marker-row--flash"),
                                                            1200,
                                                        );
                                                    }}
                                                >
                                                    <span className="plan-bar-marker-text">
                                                        {shortMarkerLabel(m.name)}
                                                    </span>
                                                </button>
                                            );
                                        })}
                                    </div>
                                );
                            })()}

                            <div
                                className="plan-bar"
                                role="slider"
                                aria-label={`Cut scrubber, ${segs.length} segments`}
                                aria-valuemin={0}
                                aria-valuemax={Math.round(total)}
                                aria-valuenow={Math.round((playheadPct ?? 0) * total)}
                                tabIndex={0}
                                onMouseMove={onBarMove}
                                onMouseLeave={() => setHoverPct(null)}
                                onClick={onBarClick}
                            >
                                {segs.map((c, i) => {
                                    const isHook = !clipHunter && i === plan.director.hook_index;
                                    const role = "arc_role" in c ? (c as { arc_role?: string | null }).arc_role : null;
                                    const dur = c.end_s - c.start_s;
                                    const label = isHook ? "HOOK" : (role || "");
                                    return (
                                        <div
                                            key={i}
                                            className="plan-bar-seg"
                                            style={{
                                                flexGrow: dur,
                                                background: roleColor(role, isHook),
                                            }}
                                            title={`${label || `Segment ${i + 1}`} · ${dur.toFixed(1)}s\n${c.reason}`}
                                        >
                                            <span className="plan-bar-label">{label}</span>
                                            <span className="plan-bar-dur">{dur.toFixed(0)}s</span>
                                        </div>
                                    );
                                })}
                                {/* Marker leader-lines — pin dot + drop line through the bar */}
                                {markers.map((m, i) => {
                                    const off = sourceToCutOffset(m.at_s);
                                    if (off === null || total === 0) return null;
                                    const pct = (off / total) * 100;
                                    return (
                                        <span
                                            key={i}
                                            className="plan-bar-pin"
                                            style={{ left: `${pct}%` }}
                                        >
                                            <span
                                                className="plan-bar-pin-dot"
                                                style={{
                                                    background: markerColor(m.color),
                                                    boxShadow: `0 0 0 2px var(--surface-2), 0 0 8px ${markerColor(m.color)}66`,
                                                }}
                                            />
                                        </span>
                                    );
                                })}
                                {/* Hover tooltip — shows source TC under the cursor */}
                                {hoverPct !== null && hoverTip && (
                                    <div
                                        className="plan-bar-hover"
                                        style={{ left: `${hoverPct * 100}%` }}
                                    >
                                        <span className="plan-bar-hover-line" />
                                        <span className="plan-bar-hover-chip">
                                            src {tc(hoverTip.src)} · seg {hoverTip.segIndex + 1}
                                        </span>
                                    </div>
                                )}
                                {/* Persistent playhead from a click */}
                                {playheadPct !== null && (
                                    <div
                                        className="plan-bar-playhead"
                                        style={{ left: `${playheadPct * 100}%` }}
                                    />
                                )}
                            </div>
                        </div>
                        <div className="cut-meta">
                            <button
                                type="button"
                                className="link-button"
                                onClick={() => openPrompt()}
                                title="Show the prompt that was sent to the Director model"
                            >
                                View Director prompt
                            </button>
                            {playheadPct !== null && total > 0 && (() => {
                                const here = cutOffsetToSource(playheadPct * total);
                                return here ? (
                                    <span className="muted" style={{ fontSize: "var(--fs-2)" }}>
                                        · playhead at cut {tc(playheadPct * total)} · source {tc(here.src)}
                                    </span>
                                ) : null;
                            })()}
                        </div>
                    </div>
                );
            })()}

            {/* Combined Cut-health surface. Subsumes the Director
                best-effort warning (formerly inside the cut card) and
                the story-critic verdict so editors see one ready-or-not
                summary. */}
            {(() => {
                const env = plan.coherence_report;
                let report: CoherenceReport | null = null;
                let contextLabel: string | undefined;
                let emptyMessage: string | undefined;
                if (env) {
                    if (env.kind === "per_candidate") {
                        const cands = env.report.candidates;
                        if (cands.length === 0) {
                            emptyMessage =
                                "Story-critic returned no candidates for this build.";
                        } else {
                            const idx = Math.min(
                                selectedCandidate,
                                cands.length - 1,
                            );
                            report = cands[idx];
                            const isBest =
                                idx === env.report.best_candidate_index;
                            contextLabel = `Candidate ${idx + 1} of ${cands.length}${
                                isBest ? " · top pick" : ""
                            }`;
                        }
                    } else {
                        report = env.report;
                    }
                } else {
                    emptyMessage =
                        "Story-critic did not run for this build (flag off, no resolved axes, or the critic call failed).";
                }

                // The iterative critic loop persists every pass to
                // ``coherence_history``. With ≥ 2 entries we render a
                // stepped ladder (one chip per iteration); with exactly
                // 2 entries the legacy two-pass lift chip is also
                // populated for back-compat with the ladder-disabled
                // path. ``shippedPassIndex`` reflects the latest-wins
                // tie-break so the right chip carries the * marker.
                const history = plan.coherence_history ?? [];
                const singleHistory = history.filter(
                    (env): env is { kind: "single"; report: CoherenceReport } =>
                        env.kind === "single",
                );
                const ladderSteps =
                    singleHistory.length >= 2
                        ? singleHistory.map((env) => ({
                              score: env.report.score,
                              verdict: env.report.verdict,
                          }))
                        : undefined;
                let shippedPassIndex: number | undefined;
                if (ladderSteps && report !== null) {
                    // Latest-wins tie-break: scan in reverse for the
                    // first entry whose score equals the shipped report.
                    for (let i = ladderSteps.length - 1; i >= 0; i--) {
                        if (ladderSteps[i].score === report.score) {
                            shippedPassIndex = i;
                            break;
                        }
                    }
                }
                let previousReport: CoherenceReport | null = null;
                if (
                    !ladderSteps &&
                    singleHistory.length === 2 &&
                    report !== null
                ) {
                    const v1 = singleHistory[0].report;
                    const v2 = singleHistory[1].report;
                    if (report.score === v2.score) {
                        previousReport = v1;
                    } else if (report.score === v1.score) {
                        previousReport = v2;
                    }
                }

                const handleIssueClick = (segIdx: number) => {
                    if (segIdx < 0) return;
                    setExpandedSegment(segIdx);
                    requestAnimationFrame(() => {
                        document
                            .getElementById(`seg-${segIdx}`)
                            ?.scrollIntoView({
                                behavior: "smooth",
                                block: "center",
                            });
                    });
                };
                const handleViewReworkPrompt = previousReport
                    ? () => openPrompt({ pass: "rework" })
                    : undefined;
                const handlePassClick = ladderSteps
                    ? (passIndex: number) => {
                          // Pass 0 (first iteration) is the original
                          // dump; passes 1..N are the rework dumps.
                          if (passIndex === 0) {
                              void openPrompt();
                          } else {
                              void openPrompt({ pass: passIndex });
                          }
                      }
                    : undefined;

                // Re-critique is only meaningful when the verdict is
                // mid-band — for "ship" cuts it's redundant, for
                // "rework" cuts the editor should regenerate the plan
                // instead. We keep the button enabled but explain what
                // we'd expect to happen.
                let recritiqueDisabled = false;
                let recritiqueDisabledReason: string | undefined;
                if (report) {
                    if (report.verdict === "ship" && !recritiqueErr) {
                        recritiqueDisabled = true;
                        recritiqueDisabledReason =
                            "Already passing — modify the plan and rebuild to invalidate.";
                    } else if (history.length >= 2 && !recritiqueErr) {
                        recritiqueDisabled = true;
                        recritiqueDisabledReason =
                            "Auto-rework already ran. Rebuild the plan to grade a new cut.";
                    }
                }

                const handleRegenerateWithFeedback =
                    report && report.verdict !== "ship"
                        ? (unfixed: typeof report.issues) =>
                              regenerateWithCriticFeedback(report, unfixed)
                        : undefined;

                return (
                    <CutHealthCard
                        planWarnings={plan.plan_warnings}
                        coherenceReport={report}
                        previousReport={previousReport}
                        ladderSteps={ladderSteps}
                        shippedPassIndex={shippedPassIndex}
                        onPassClick={handlePassClick}
                        contextLabel={contextLabel}
                        emptyMessage={emptyMessage}
                        onIssueClick={handleIssueClick}
                        onRecritique={recritique}
                        recritiqueBusy={recritiqueBusy}
                        recritiqueError={recritiqueErr}
                        recritiqueDisabled={recritiqueDisabled}
                        recritiqueDisabledReason={recritiqueDisabledReason}
                        onViewReworkPrompt={handleViewReworkPrompt}
                        onRegenerateWithFeedback={handleRegenerateWithFeedback}
                        regenerateWithFeedbackBusy={regenerating}
                    />
                );
            })()}

            <div className="card">
                <h2>Segments</h2>
                <div className="seg-cards">
                    {(clipHunter
                        ? (clipHunter.candidates[selectedCandidate]
                              ?.resolved_segments ?? []
                          ).map((s) => ({
                              start_s: s.start_s,
                              end_s: s.end_s,
                              reason: s.reason,
                              arc_role: null,
                          }))
                        : plan.director.selected_clips
                    ).map((c, i) => {
                        const isHook = !clipHunter && i === plan.director.hook_index;
                        const role =
                            !isHook && "arc_role" in c ? c.arc_role : null;
                        const isExpanded = expandedSegment === i;
                        const canExpand = transcript.length > 0;
                        const words = canExpand && isExpanded
                            ? transcript.filter(
                                  (w) =>
                                      w.start_time >= c.start_s - 0.001 &&
                                      w.end_time <= c.end_s + 0.001,
                              )
                            : [];
                        const badge = isHook ? "HOOK" : role;
                        const colour = roleColor(role, isHook);
                        return (
                            <div
                                key={i}
                                id={`seg-${i}`}
                                className={`seg-card ${isHook ? "seg-card--hook" : ""}`}
                            >
                                <div className="seg-card-stripe" style={{ background: colour }} />
                                <div className="seg-card-body">
                                    <div className="seg-card-head">
                                        {canExpand && (
                                            <button
                                                type="button"
                                                aria-label={isExpanded ? "Hide transcript" : "Show transcript"}
                                                aria-expanded={isExpanded}
                                                className={`seg-card-disc ${isExpanded ? "is-open" : ""}`}
                                                onClick={() =>
                                                    setExpandedSegment(isExpanded ? null : i)
                                                }
                                            >
                                                ▸
                                            </button>
                                        )}
                                        {badge && (
                                            <span
                                                className="seg-badge seg-badge--filled"
                                                style={{
                                                    background: colour,
                                                    color: "rgba(0,0,0,0.82)",
                                                }}
                                            >
                                                {badge}
                                            </span>
                                        )}
                                        <span className="seg-card-tc">
                                            {tc(c.start_s)} → {tc(c.end_s)}
                                        </span>
                                        <span className="seg-card-dur">
                                            {(c.end_s - c.start_s).toFixed(1)}s
                                        </span>
                                    </div>
                                    <p className="seg-card-text">{c.reason}</p>
                                    {isExpanded && (
                                        <div className="seg-transcript">
                                            {words.length > 0
                                                ? words.map((w) => w.word).join(" ")
                                                : "(no words in this range)"}
                                        </div>
                                    )}
                                </div>
                            </div>
                        );
                    })}
                </div>
            </div>

            {plan.markers.markers.length > 0 && (
                <div className="card">
                    <h2>Markers</h2>
                    <ul className="marker-list">
                        {plan.markers.markers.map((m, i) => (
                            <li
                                key={i}
                                id={`marker-${i}`}
                                className="marker-row"
                            >
                                <span
                                    className="marker-dot"
                                    style={{
                                        background: markerColor(m.color),
                                        boxShadow: `0 0 0 2px var(--surface-1), 0 0 6px ${markerColor(m.color)}55`,
                                    }}
                                    aria-hidden
                                />
                                <span className="seg-card-tc">{tc(m.at_s)}</span>
                                <span className="marker-name">{m.name}</span>
                                <span className="marker-note muted">{m.note}</span>
                            </li>
                        ))}
                    </ul>
                </div>
            )}

            {/* "Tune the cut" — refinement panel. Surfaces current state,
                shows hook context + a will-change diff before regenerating. */}
            {!clipHunter && analysis && (() => {
                // Anchor: what's currently in the cut.
                const currentTarget = plan.user_settings.target_length_s ?? 180;
                const currentHookSrc =
                    plan.director.selected_clips[plan.director.hook_index]?.start_s ?? null;
                const currentSegCount = plan.director.selected_clips.length;

                // Pending: what the user has dialed in (may equal current).
                const nextTarget = settings.target_length_s ?? currentTarget;
                const nextHookSrc =
                    settings.selected_hook_s ?? currentHookSrc;

                const targetChanged = nextTarget !== currentTarget;
                const hookChanged =
                    nextHookSrc !== null &&
                    currentHookSrc !== null &&
                    Math.abs(nextHookSrc - currentHookSrc) > 0.01;
                const willChange = targetChanged || hookChanged;

                // Estimate new segment count by scaling from the current ratio.
                const currentTotal = plan.director.selected_clips.reduce(
                    (a, c) => a + (c.end_s - c.start_s), 0,
                );
                const avgSegLen = currentTotal > 0 ? currentTotal / currentSegCount : 20;
                const estSegCount = Math.max(2, Math.round(nextTarget / avgSegLen));

                // Pull surrounding transcript words for a hook candidate to give
                // the designer context — 5s before, 5s after the candidate span.
                const hookContext = (h: typeof analysis.hook_candidates[number]) => {
                    if (transcript.length === 0) return null;
                    const words = transcript.filter(
                        (w) => w.start_time >= h.start_s - 5 && w.end_time <= h.end_s + 6,
                    );
                    if (words.length === 0) return null;
                    const text = words.map((w) => w.word).join(" ").trim();
                    // Mark the candidate quote inside the context for emphasis.
                    const inside = words
                        .filter((w) => w.start_time >= h.start_s - 0.001 && w.end_time <= h.end_s + 0.001)
                        .map((w) => w.word)
                        .join(" ")
                        .trim();
                    return { text, inside };
                };

                return (
                <details className="card card--advanced" open={willChange}>
                    <summary>
                        <span>Tune the cut</span>
                        <span className="muted" style={{ marginLeft: 8, fontSize: "var(--fs-2)" }}>
                            {willChange
                                ? "— pending changes, click Regenerate to apply"
                                : "— nudge the hook or target length, get a fresh cut in ~5s"}
                        </span>
                    </summary>
                    <div className="card-body">
                        {/* Anchor — what's currently used */}
                        <div className="tune-anchor">
                            <span className="muted" style={{ fontSize: "var(--fs-2)" }}>
                                Currently using
                            </span>
                            <span className="tune-anchor-chip">
                                HOOK at {currentHookSrc !== null ? tc(currentHookSrc) : "auto"}
                            </span>
                            <span className="muted" style={{ fontSize: "var(--fs-2) " }}>· target</span>
                            <span className="tune-anchor-chip">{tc(currentTarget)}</span>
                        </div>

                        {/* Target length — slider + chip + presets */}
                        <div className="tune-row" style={{ marginTop: 14 }}>
                            <label htmlFor="tune-target" style={{ marginBottom: 0 }}>
                                Target length
                            </label>
                            <span className="tune-chip">
                                {tc(nextTarget)}
                                <span className="muted"> ({nextTarget}s)</span>
                            </span>
                        </div>
                        <input
                            id="tune-target"
                            type="range"
                            min={15}
                            max={600}
                            step={5}
                            value={nextTarget}
                            className="tune-slider"
                            onChange={(e) => {
                                const next = Number(e.target.value);
                                onSettingsChange?.({ ...settings, target_length_s: next });
                            }}
                        />
                        <div className="tune-presets">
                            {TARGET_PRESETS.map((p) => {
                                const active = nextTarget === p.seconds;
                                return (
                                    <button
                                        key={p.label}
                                        type="button"
                                        className={`chip ${active ? "on" : ""}`}
                                        onClick={() =>
                                            onSettingsChange?.({
                                                ...settings,
                                                target_length_s: p.seconds,
                                            })
                                        }
                                    >
                                        {p.label}
                                        <span className="muted"> · {tc(p.seconds)}</span>
                                    </button>
                                );
                            })}
                        </div>
                        <p className="muted" style={{ marginTop: 6, fontSize: "var(--fs-2)" }}>
                            Director enforces a 75–125 % window around this.
                        </p>

                        {/* Hook candidates — radio + strength bar + context */}
                        <h3 style={{ margin: "18px 0 6px" }}>Hook candidates</h3>
                        <p className="muted" style={{ marginTop: 0, marginBottom: 8, fontSize: "var(--fs-2)" }}>
                            Locks the first ~2 seconds of the cut and anchors its topic. The body is built around it; later segments may bridge to other themes.
                        </p>
                        <div className="hook-cards">
                            {analysis.hook_candidates.map((h, i) => {
                                const selected =
                                    settings.selected_hook_s != null &&
                                    Math.abs(settings.selected_hook_s - h.start_s) < 0.01;
                                const isCurrent =
                                    currentHookSrc !== null &&
                                    Math.abs(currentHookSrc - h.start_s) < 0.01;
                                const strength = strengthOf(h.engagement_score);
                                const ctx = hookContext(h);
                                const dur = (h.end_s - h.start_s).toFixed(1);
                                return (
                                    <div
                                        key={i}
                                        className={`hook-card ${selected ? "hook-card--selected" : ""} ${isCurrent ? "hook-card--current" : ""}`}
                                        role="button"
                                        tabIndex={0}
                                        onClick={() =>
                                            onSettingsChange?.({
                                                ...settings,
                                                selected_hook_s: selected ? null : h.start_s,
                                            })
                                        }
                                        onKeyDown={(e) => {
                                            if (e.key === "Enter" || e.key === " ") {
                                                e.preventDefault();
                                                onSettingsChange?.({
                                                    ...settings,
                                                    selected_hook_s: selected ? null : h.start_s,
                                                });
                                            }
                                        }}
                                    >
                                        <div className="hook-card-head">
                                            <span className={`hook-radio ${selected ? "is-on" : ""}`} aria-hidden />
                                            <span className="hook-card-title">{h.text}</span>
                                            {isCurrent && !selected && (
                                                <span className="hook-card-badge">current</span>
                                            )}
                                            <span
                                                className="hook-strength"
                                                title={`Engagement: ${(h.engagement_score * 100).toFixed(0)}%`}
                                            >
                                                <span className="hook-strength-bar">
                                                    {[0, 1, 2, 3, 4].map((idx) => (
                                                        <span
                                                            key={idx}
                                                            className={`hook-strength-tick ${idx < strength.segs ? "is-on" : ""}`}
                                                        />
                                                    ))}
                                                </span>
                                                <span className="hook-strength-label muted">
                                                    {strength.label}
                                                </span>
                                            </span>
                                        </div>
                                        <div className="hook-card-meta">
                                            <span className="seg-card-tc">{tc(h.start_s)} → {tc(h.end_s)}</span>
                                            <span className="muted" style={{ fontSize: "var(--fs-1)" }}>· {dur}s</span>
                                        </div>
                                        {ctx && (
                                            <p className="hook-context">
                                                {ctx.inside ? (() => {
                                                    const idx = ctx.text.indexOf(ctx.inside);
                                                    if (idx === -1) return ctx.text;
                                                    return (
                                                        <>
                                                            <span className="muted">…{ctx.text.slice(0, idx)}</span>
                                                            <strong>{ctx.text.slice(idx, idx + ctx.inside.length)}</strong>
                                                            <span className="muted">{ctx.text.slice(idx + ctx.inside.length)}…</span>
                                                        </>
                                                    );
                                                })() : <span className="muted">{ctx.text}</span>}
                                            </p>
                                        )}
                                    </div>
                                );
                            })}
                        </div>

                        {/* Will-change diff — only when something has been nudged */}
                        {willChange && (
                            <div className="will-change">
                                <div className="will-change-head">
                                    <span>Will change on Regenerate</span>
                                </div>
                                <ul className="will-change-list">
                                    {hookChanged && (
                                        <li>
                                            <span className="will-change-key">hook</span>
                                            <span className="seg-card-tc">{tc(currentHookSrc!)}</span>
                                            <span className="muted">→</span>
                                            <span className="seg-card-tc">{tc(nextHookSrc!)}</span>
                                            <span className="muted">
                                                ({nextHookSrc! < currentHookSrc!
                                                    ? `jumps ${tc(currentHookSrc! - nextHookSrc!)} earlier`
                                                    : `jumps ${tc(nextHookSrc! - currentHookSrc!)} later`})
                                            </span>
                                        </li>
                                    )}
                                    {targetChanged && (
                                        <li>
                                            <span className="will-change-key">total</span>
                                            <span className="seg-card-tc">{tc(currentTotal)}</span>
                                            <span className="muted">→</span>
                                            <span className="seg-card-tc">~{tc(nextTarget)}</span>
                                            <span className="muted">
                                                ({nextTarget > currentTotal ? "+" : ""}
                                                {Math.round(nextTarget - currentTotal)}s,{" "}
                                                ~{estSegCount} segments)
                                            </span>
                                        </li>
                                    )}
                                    <li>
                                        <span className="will-change-key">markers</span>
                                        <span className="muted">
                                            {plan.markers.markers.length} kept (re-evaluated)
                                        </span>
                                    </li>
                                </ul>
                            </div>
                        )}

                        <div className="row" style={{ marginTop: 12 }}>
                            <button
                                disabled={regenerating || !willChange}
                                onClick={() => regenerate(settings)}
                                title={willChange ? "Apply pending changes" : "No pending changes"}
                            >
                                {regenerating ? "Regenerating…" : "Regenerate plan"}
                            </button>
                            {willChange && (
                                <button
                                    type="button"
                                    className="secondary"
                                    disabled={regenerating}
                                    onClick={() =>
                                        onSettingsChange?.({
                                            ...settings,
                                            target_length_s: currentTarget,
                                            selected_hook_s: null,
                                        })
                                    }
                                >
                                    ↶ Revert
                                </button>
                            )}
                            <span className="muted" style={{ fontSize: "var(--fs-2)" }}>
                                ~5s · reuses scrubbed transcript
                            </span>
                        </div>
                    </div>
                </details>
                );
            })()}

            <details className="card card--advanced">
                <summary>
                    <span>Dev details</span>
                    <span className="muted" style={{ marginLeft: 8, fontSize: "var(--fs-2)" }}>
                        — resolved source frames, for round-trip debugging
                    </span>
                </summary>
                <div className="card-body">
                    <div className="seg-list">
                        {(clipHunter
                            ? clipHunter.candidates[selectedCandidate]
                                  ?.resolved_segments ?? []
                            : plan.resolved_segments
                        )
                            .slice(0, 10)
                            .map((r, i) => (
                            <div key={i} className="seg">
                                <span className="seg-time">
                                    tl {r.timeline_start_frame}
                                </span>
                                <span className="seg-time">
                                    src [{r.source_in_frame}..{r.source_out_frame}]
                                </span>
                                <span className="seg-reason">
                                    {r.source_item_name}
                                    {r.speed_ramped && (
                                        <span style={{ color: "var(--warn)" }}>
                                            {" "}
                                            ({r.speed}× speed)
                                        </span>
                                    )}
                                </span>
                            </div>
                        ))}
                        {(() => {
                            const list = clipHunter
                                ? clipHunter.candidates[selectedCandidate]
                                      ?.resolved_segments ?? []
                                : plan.resolved_segments;
                            return list.length > 10 ? (
                                <div className="muted" style={{ padding: 8 }}>
                                    …and {list.length - 10} more
                                </div>
                            ) : null;
                        })()}
                    </div>
                </div>
            </details>

            {buildResult && (
                <div
                    className="card"
                    style={{ borderColor: "var(--ok)", textAlign: "center" }}
                >
                    {/* v3-4.5 preview — celebration mascot plays once on the
                        post-build success card. */}
                    <video
                        className="mascot mascot--celebrate"
                        src="/mascot/celebration-960.webm"
                        autoPlay
                        muted
                        playsInline
                        aria-hidden="true"
                    />
                    <h2>✓ Timeline created</h2>
                    <p>
                        New timeline:&nbsp;<code>{buildResult.new_timeline_name}</code>
                    </p>
                    <p className="muted">
                        {buildResult.appended} segment(s) appended
                        {buildResult.markers_added > 0 && (
                            <> · {buildResult.markers_added} marker(s) placed</>
                        )}
                        {buildResult.markers_skipped.length > 0 && (
                            <> · {buildResult.markers_skipped.length} skipped (cut out)</>
                        )}
                    </p>
                    {buildResult.format && (
                        <p className="muted">
                            Format: <code>{buildResult.format.format}</code>
                            &nbsp;· {buildResult.format.width}×{buildResult.format.height}
                            {buildResult.format.resolution_warning && (
                                <span style={{ color: "var(--warn)" }}>
                                    {" "}
                                    · resolution apply warning: {buildResult.format.resolution_warning}
                                </span>
                            )}
                        </p>
                    )}
                    {buildResult.captions?.enabled && (
                        <p className="muted">
                            Captions: {buildResult.captions.lines ?? 0} line(s)
                            {buildResult.captions.path && (
                                <>
                                    {" "}
                                    · SRT at <code>{buildResult.captions.path}</code>
                                </>
                            )}
                            {buildResult.captions.subtitle_track &&
                                !buildResult.captions.subtitle_track.ok && (
                                    <span style={{ color: "var(--warn)" }}>
                                        {" "}
                                        · subtitle track not populated (
                                        {buildResult.captions.subtitle_track.reason ??
                                            buildResult.captions.subtitle_track.error ??
                                            "unknown"}
                                        )
                                    </span>
                                )}
                        </p>
                    )}
                    {buildResult.safe_zones?.enabled &&
                        buildResult.safe_zones.added === 0 &&
                        buildResult.safe_zones.reason && (
                            <p className="muted" style={{ color: "var(--warn)" }}>
                                Safe-zone guides skipped: {buildResult.safe_zones.reason}
                            </p>
                        )}
                    <p className="muted">
                        Snapshot: <code>{buildResult.snapshot_path}</code>&nbsp;
                        ({buildResult.snapshot_size_kb.toFixed(1)} KB)
                    </p>
                    {buildResult.replaced_timelines && buildResult.replaced_timelines.length > 0 && (
                        <p className="muted">
                            Replaced {buildResult.replaced_timelines.length} prior timeline(s):{" "}
                            {buildResult.replaced_timelines.map((n, i) => (
                                <span key={n}>
                                    {i > 0 && ", "}
                                    <code>{n}</code>
                                </span>
                            ))}
                        </p>
                    )}
                    {buildResult.append_errors.length > 0 && (
                        <p className="muted" style={{ color: "var(--warn)" }}>
                            {buildResult.append_errors.length} append warning(s) — check backend log.
                        </p>
                    )}

                    <div className="row">
                        <button
                            className="secondary"
                            disabled={deleting}
                            onClick={async () => {
                                if (!confirm(
                                    `Delete '${buildResult.new_timeline_name}'?\n` +
                                    `(The .drp snapshot stays on disk.)`,
                                )) return;
                                setDeleting(true);
                                try {
                                    await api.deleteCut(runId);
                                    setBuildResult(null);
                                    refreshTimelineNames();
                                    refreshHistory();
                                } catch (e) {
                                    setBuildErr(String(e));
                                } finally {
                                    setDeleting(false);
                                }
                            }}
                        >
                            {deleting ? "Deleting…" : "Delete this cut"}
                        </button>
                        <button
                            className="secondary"
                            disabled={deleting}
                            onClick={async () => {
                                if (!confirm(
                                    "Delete every timeline this run has built?\n" +
                                    "(Snapshots stay on disk.)",
                                )) return;
                                setDeleting(true);
                                try {
                                    await api.deleteAllCuts(runId);
                                    setBuildResult(null);
                                    setBuildAllResults([]);
                                    refreshTimelineNames();
                                    refreshHistory();
                                } catch (e) {
                                    setBuildErr(String(e));
                                } finally {
                                    setDeleting(false);
                                }
                            }}
                            title="Remove every cut this run has built"
                        >
                            Delete all cuts from this run
                        </button>
                        <button onClick={onReset}>Start a new run →</button>
                    </div>
                </div>
            )}

            {buildErr && <div className="error-box">{buildErr}</div>}

            {buildAllResults.length > 0 && (
                <div className="card" style={{ borderColor: "var(--ok)" }}>
                    <h2>✓ Built {buildAllResults.length} timeline(s)</h2>
                    {buildAllResults.map((r, i) => (
                        <p key={i}>
                            <code>{r.new_timeline_name}</code>
                            <span className="muted">
                                {" "}— {r.appended} segment(s) appended
                                {r.captions?.enabled && r.captions.lines
                                    ? ` · ${r.captions.lines} captions`
                                    : ""}
                            </span>
                        </p>
                    ))}
                    <div className="row" style={{ marginTop: 8 }}>
                        <button onClick={onReset}>Start a new run →</button>
                    </div>
                </div>
            )}

            {executeHistory.length > 0 && !buildResult && buildAllResults.length === 0 && (
                <details className="card">
                    <summary>
                        <span>
                            Previous cuts from this run{" "}
                            <span className="muted" style={{ fontSize: "var(--fs-2)" }}>
                                · {executeHistory.filter((h) => !h.aborted).length}
                            </span>
                        </span>
                    </summary>
                    <div className="card-body">
                        <ul
                            style={{
                                listStyle: "none",
                                padding: 0,
                                margin: 0,
                                display: "flex",
                                flexDirection: "column",
                                gap: "var(--s-2)",
                            }}
                        >
                            {executeHistory.map((h, i) => {
                                const name = h.new_timeline_name ?? "(aborted build)";
                                const age = formatRelativeTime(h.at * 1000);
                                return (
                                    <li
                                        key={`${name}-${h.at}`}
                                        className="row between"
                                        style={{
                                            padding: "var(--s-2) var(--s-3)",
                                            border: "1px solid var(--border)",
                                            borderRadius: "var(--radius-sm)",
                                            gap: "var(--s-3)",
                                            flexWrap: "wrap",
                                        }}
                                    >
                                        <div style={{ minWidth: 0, flex: 1 }}>
                                            <div style={{ fontWeight: 500 }}>
                                                Cut {i + 1}: <code>{name}</code>
                                                {h.aborted && (
                                                    <span
                                                        className="muted"
                                                        style={{
                                                            marginLeft: "var(--s-2)",
                                                            fontSize: "var(--fs-2)",
                                                        }}
                                                    >
                                                        aborted
                                                    </span>
                                                )}
                                            </div>
                                            <div
                                                className="muted"
                                                style={{ fontSize: "var(--fs-2)" }}
                                                title={h.snapshot_path ?? undefined}
                                            >
                                                {age}
                                                {h.snapshot_path && (
                                                    <>
                                                        {" · "}
                                                        snapshot on disk
                                                    </>
                                                )}
                                            </div>
                                        </div>
                                        {!h.aborted && onCutNameChange && (
                                            <button
                                                className="secondary"
                                                disabled={building}
                                                onClick={() => {
                                                    // Seed the header cutName with a
                                                    // unique-ish variant so the next
                                                    // Build creates a sibling timeline
                                                    // rather than colliding. User can
                                                    // tweak before hitting Build.
                                                    const base = h.custom_name ?? name;
                                                    onCutNameChange(`${base}_v${i + 2}`);
                                                }}
                                                title="Fill the Cut name with a fresh variant so the next build is a sibling"
                                            >
                                                Rebuild…
                                            </button>
                                        )}
                                    </li>
                                );
                            })}
                        </ul>
                    </div>
                </details>
            )}

            {!buildResult && buildAllResults.length === 0 && (() => {
                const base = cutName.trim() || `${timelineName}_AI_Cut`;
                const defaultSuffix = clipHunter
                    ? `_${selectedCandidate + 1}`
                    : "";
                // For clip-hunter the backend appends `_{n}` to the custom
                // name; for a single build it uses the name as-is.
                const projectedBase = cutName.trim()
                    ? (clipHunter ? `${base}${defaultSuffix}` : base)
                    : (clipHunter
                          ? `${timelineName}_AI_${clipHunter.mode === "short_generator" ? "Short" : "Clip"}_${selectedCandidate + 1}`
                          : `${timelineName}_AI_Cut`);
                const willCollide = existingNames.has(projectedBase);
                // Pre-compute the friendly label that goes inside the Build
                // button — the collision banner is folded into the action.
                const buildLabel = building && !buildProgress
                    ? "Building…"
                    : clipHunter
                      ? `Build ${clipHunter.mode === "short_generator" ? "short" : "clip"} #${selectedCandidate + 1} →`
                      : willCollide
                        ? (replaceExisting
                              ? `Replace ${projectedBase} →`
                              : `Build as ${projectedBase}_2 →`)
                        : "Build Timeline →";
                return (
                    <>
                <div className="row between">
                    <button className="secondary" onClick={onBack} disabled={building} data-hotkey="back">
                        ← Back
                    </button>
                    <div className="row">
                        {clipHunter && clipHunter.candidates.length > 1 && (
                            <button
                                className="secondary"
                                disabled={building}
                                onClick={async () => {
                                    setBuilding(true);
                                    setBuildErr(null);
                                    const results: ExecuteResult[] = [];
                                    try {
                                        for (
                                            let i = 0;
                                            i < clipHunter.candidates.length;
                                            i++
                                        ) {
                                            setBuildProgress(
                                                `Building clip ${i + 1} of ${clipHunter.candidates.length}…`,
                                            );
                                            const res = await api.execute(
                                                runId,
                                                i,
                                                cutName,
                                                replaceExisting,
                                            );
                                            results.push(res);
                                        }
                                        setBuildAllResults(results);
                                        if (results.length > 0) onBuildSuccess?.();
                                    } catch (e) {
                                        setBuildErr(String(e));
                                        if (results.length > 0) {
                                            setBuildAllResults(results);
                                            onBuildSuccess?.();
                                        }
                                    } finally {
                                        setBuilding(false);
                                        setBuildProgress(null);
                                        refreshTimelineNames();
                                        refreshHistory();
                                    }
                                }}
                                title="Build every candidate into its own timeline"
                            >
                                {building && buildProgress
                                    ? buildProgress
                                    : `Build all ${clipHunter.candidates.length} ${clipHunter.mode === "short_generator" ? "shorts" : "clips"} →`}
                            </button>
                        )}
                        <div className="build-split">
                            <button
                                disabled={building}
                                data-hotkey="primary"
                                onClick={async () => {
                                    setBuilding(true);
                                    setBuildErr(null);
                                    try {
                                        const res = await api.execute(
                                            runId,
                                            clipHunter ? selectedCandidate : undefined,
                                            cutName,
                                            replaceExisting,
                                        );
                                        setBuildResult(res);
                                        refreshTimelineNames();
                                        refreshHistory();
                                        onBuildSuccess?.();
                                    } catch (e) {
                                        setBuildErr(String(e));
                                    } finally {
                                        setBuilding(false);
                                    }
                                }}
                            >
                                {buildLabel}
                            </button>
                            {willCollide && !clipHunter && (
                                <button
                                    type="button"
                                    className="secondary build-split-toggle"
                                    disabled={building}
                                    onClick={() => setReplaceExisting(!replaceExisting)}
                                    title={
                                        replaceExisting
                                            ? `Switch to non-destructive ${projectedBase}_2`
                                            : `Replace existing ${projectedBase} after the build succeeds`
                                    }
                                >
                                    {replaceExisting
                                        ? "↶ Save as new instead"
                                        : "↻ Replace instead"}
                                </button>
                            )}
                        </div>
                    </div>
                </div>
                {willCollide && !clipHunter && (
                    <p className="muted build-collision-hint">
                        {replaceExisting
                            ? <>The existing <code>{projectedBase}</code> will be deleted after the new build succeeds. Snapshot stays on disk.</>
                            : <>A timeline named <code>{projectedBase}</code> already exists — your build will be saved as <code>{projectedBase}_2</code>.</>}
                    </p>
                )}
                </>
                );
            })()}
            {promptOpen && (
                <div
                    className="prompt-overlay"
                    role="dialog"
                    aria-label="Director prompt viewer"
                    onClick={() => setPromptOpen(false)}
                >
                    <div
                        className="prompt-modal"
                        onClick={(e) => e.stopPropagation()}
                    >
                        <div className="prompt-modal-head">
                            <strong>Director prompt</strong>
                            <button
                                type="button"
                                className="link-button"
                                onClick={() => setPromptOpen(false)}
                            >
                                Close
                            </button>
                        </div>
                        {promptLoading && (
                            <p className="muted">Loading…</p>
                        )}
                        {promptErr && (
                            <p className="error-box">{promptErr}</p>
                        )}
                        {!promptLoading && !promptErr && promptText !== null && (
                            <pre className="prompt-pre">{promptText}</pre>
                        )}
                    </div>
                </div>
            )}
        </div>
    );
}
