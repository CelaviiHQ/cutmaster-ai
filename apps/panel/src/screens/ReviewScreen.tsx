import { useEffect, useState } from "react";
import { api } from "../api";
import type { ExecuteResult } from "../api";
import MascotLoading from "./MascotLoading";
import { formatRelativeTime } from "../persist";
import type {
    BuildPlanResult,
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
}: Props) {
    const [analysis, setAnalysis] = useState<StoryAnalysis | null>(null);
    const [regenerating, setRegenerating] = useState(false);
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

    const openPrompt = async () => {
        setPromptOpen(true);
        if (promptText !== null) return;
        setPromptLoading(true);
        setPromptErr(null);
        try {
            const res = await fetch(`/cutmaster/debug/prompt/${runId}`);
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
                // Phase 5.8 — send ``content_type`` when the preset is a
                // content-type key; legacy cut-intent presets (tightener
                // / clip_hunter / short_generator) still rely on the
                // backend's auto-remapping.
                const contentType = CONTENT_TYPE_PRESETS_REVIEW.has(preset)
                    ? preset
                    : null;
                const [p, presetList, cachedThemes] = await Promise.all([
                    api.buildPlan(runId, preset, settings, contentType),
                    api.listPresets().catch(() => ({ presets: [] })),
                    api.themesCache(runId).catch(() => null),
                ]);
                if (cancelled) return;
                setPlan(p);
                setBundle(
                    presetList.presets.find((b) => b.key === preset) ?? null,
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

    const regenerate = async (next: UserSettings) => {
        setRegenerating(true);
        setErr(null);
        try {
            const contentType = CONTENT_TYPE_PRESETS_REVIEW.has(preset)
                ? preset
                : null;
            const p = await api.buildPlan(runId, preset, next, contentType);
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

    const totalS = plan.director.selected_clips.reduce(
        (s, c) => s + (c.end_s - c.start_s),
        0,
    );

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
            <div className="card">
                <h2>Plan summary</h2>
                <p>
                    <strong>{plan.director.selected_clips.length}</strong> segments
                    &nbsp;·&nbsp; total <strong>{tc(totalS)}</strong>
                    <span className="muted"> ({totalS.toFixed(1)}s)</span>
                    &nbsp;·&nbsp; {plan.markers.markers.length} markers
                </p>
                {plan.director.reasoning && (
                    <p className="muted">{plan.director.reasoning}</p>
                )}
                <p className="muted">
                    <button
                        type="button"
                        className="link-button"
                        onClick={openPrompt}
                        title="Show the prompt that was sent to the Director model for this run"
                    >
                        View Director prompt
                    </button>
                </p>
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
                                    <>
                                        {" "}No alternates detected — treated as
                                        Curated.
                                    </>
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
                    <p className="muted" style={{ marginTop: 8 }}>
                        {excludeLabels.length > 0 && (
                            <>
                                Applied exclusions ({excludeLabels.length}):{" "}
                                {excludeLabels.join(", ")}
                            </>
                        )}
                        {excludeLabels.length > 0 && appliedFocus && " · "}
                        {appliedFocus && (
                            <>Focus: &ldquo;{appliedFocus}&rdquo;</>
                        )}
                    </p>
                )}
            </div>

            {!clipHunter && analysis && (
                <details className="card card--advanced">
                    <summary>
                        <span>Regenerate plan</span>
                        <span className="muted" style={{ marginLeft: 8, fontSize: "var(--fs-2)" }}>
                            — nudge the hook or target length, get a fresh cut in ~5s
                        </span>
                    </summary>

                    <div style={{ marginTop: 12 }}>
                        <label style={{ display: "block", marginBottom: 6 }}>
                            Target length (seconds)
                        </label>
                        <input
                            type="number"
                            min={15}
                            step={5}
                            defaultValue={settings.target_length_s ?? 180}
                            style={{ width: 120 }}
                            onBlur={(e) => {
                                const next = Number(e.target.value) || null;
                                if (next === settings.target_length_s) return;
                                onSettingsChange?.({
                                    ...settings,
                                    target_length_s: next,
                                });
                            }}
                        />
                        <p className="muted" style={{ marginTop: 4 }}>
                            Director enforces a 75–125 % window around this.
                        </p>
                    </div>

                    <div style={{ marginTop: 12 }}>
                        <h3 style={{ margin: "0 0 6px" }}>Hook</h3>
                        <p className="muted" style={{ marginTop: 0 }}>
                            Click to swap the opening beat. Clear to let the Director pick.
                        </p>
                        {analysis.hook_candidates.map((h, i) => {
                            const selected =
                                settings.selected_hook_s != null &&
                                Math.abs(settings.selected_hook_s - h.start_s) < 0.01;
                            return (
                                <div
                                    key={i}
                                    className={`seg hook-row ${selected ? "hook-row--selected" : ""}`}
                                    role="button"
                                    tabIndex={0}
                                    onClick={() =>
                                        onSettingsChange?.({
                                            ...settings,
                                            selected_hook_s: selected ? null : h.start_s,
                                        })
                                    }
                                >
                                    <span className="seg-time">{h.start_s.toFixed(1)}s</span>
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

                    <div className="row" style={{ marginTop: 12 }}>
                        <button
                            disabled={regenerating}
                            onClick={() => regenerate(settings)}
                        >
                            {regenerating ? "Regenerating…" : "Regenerate plan"}
                        </button>
                        <span className="muted" style={{ fontSize: "var(--fs-2)" }}>
                            Reuses the scrubbed transcript — no re-transcription.
                        </span>
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
                // Map a marker (in source seconds) onto its position along the
                // assembled cut by walking the segment list.
                const markerCutOffset = (atS: number): number | null => {
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
                return (
                    <div className="card">
                        <h2>The cut</h2>
                        <div className="plan-bar" role="img" aria-label={`Proportional view of ${segs.length} segments`}>
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
                                        onClick={() => setExpandedSegment(expandedSegment === i ? null : i)}
                                    >
                                        <span className="plan-bar-label">{label}</span>
                                        <span className="plan-bar-dur">{dur.toFixed(0)}s</span>
                                    </div>
                                );
                            })}
                            {markers.map((m, i) => {
                                const off = markerCutOffset(m.at_s);
                                if (off === null || total === 0) return null;
                                const pct = (off / total) * 100;
                                return (
                                    <div
                                        key={i}
                                        className="plan-bar-pin"
                                        style={{ left: `${pct}%` }}
                                        title={`📌 ${m.name} — ${m.note}`}
                                    >
                                        <span className="plan-bar-pin-dot" />
                                        <span className="plan-bar-pin-label">📌 {m.name}</span>
                                    </div>
                                );
                            })}
                        </div>
                        <p className="muted" style={{ marginTop: 8, fontSize: "var(--fs-2)" }}>
                            {segs.length} segments · {tc(total)} total
                            {markers.length > 0 && <> · {markers.length} marker{markers.length === 1 ? "" : "s"} pinned</>}
                        </p>
                    </div>
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
                        return (
                            <div
                                key={i}
                                className={`seg-card ${isHook ? "seg-card--hook" : ""}`}
                            >
                                <div
                                    className="seg-card-stripe"
                                    style={{ background: roleColor(role, isHook) }}
                                />
                                <div className="seg-card-body">
                                    <div className="seg-card-head">
                                        {badge && (
                                            <span
                                                className={`seg-badge ${isHook ? "seg-badge--hook" : ""}`}
                                                style={{
                                                    borderColor: roleColor(role, isHook),
                                                    color: roleColor(role, isHook),
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
                                        {canExpand && (
                                            <button
                                                className="btn-ghost seg-card-toggle"
                                                onClick={() =>
                                                    setExpandedSegment(isExpanded ? null : i)
                                                }
                                            >
                                                {isExpanded ? "Hide transcript ▾" : "Show transcript ▸"}
                                            </button>
                                        )}
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
                            <li key={i} className="marker-row">
                                <span
                                    className="marker-dot"
                                    style={{ background: m.color.toLowerCase() }}
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
                return (
                    <>
                    {willCollide && (
                        <div
                            className="card"
                            style={{
                                borderColor: "var(--warn)",
                                background: "rgba(255, 159, 10, 0.08)",
                            }}
                        >
                            <p style={{ margin: 0 }}>
                                ⚠ Timeline <code>{projectedBase}</code> already exists in this project.
                            </p>
                            <p className="muted" style={{ marginTop: 6 }}>
                                {replaceExisting
                                    ? "The existing timeline will be deleted after the new build succeeds. Snapshot is preserved."
                                    : <>A suffix (e.g. <code>{projectedBase}_2</code>) will be appended so nothing is overwritten.</>}
                            </p>
                            <label
                                style={{
                                    display: "flex",
                                    alignItems: "center",
                                    gap: 8,
                                    marginTop: 8,
                                    cursor: "pointer",
                                }}
                            >
                                <input
                                    type="checkbox"
                                    checked={replaceExisting}
                                    onChange={(e) => setReplaceExisting(e.target.checked)}
                                />
                                <span>Replace the existing timeline</span>
                            </label>
                        </div>
                    )}
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
                            {building && !buildProgress
                                ? "Building…"
                                : clipHunter
                                  ? `Build ${clipHunter.mode === "short_generator" ? "short" : "clip"} #${selectedCandidate + 1} →`
                                  : "Build Timeline →"}
                        </button>
                    </div>
                </div>
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
