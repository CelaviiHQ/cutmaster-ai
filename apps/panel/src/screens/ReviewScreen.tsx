import { useEffect, useState } from "react";
import { api } from "../api";
import type { ExecuteResult } from "../api";
import MascotLoading from "./MascotLoading";
import type {
    BuildPlanResult,
    PresetBundle,
    PresetKey,
    UserSettings,
} from "../types";

interface Props {
    runId: string;
    preset: PresetKey;
    settings: UserSettings;
    onBack: () => void;
    onReset: () => void;
    // v3-5.4 — let the app header show the current clip count.
    onClipCount?: (n: number | null) => void;
}

export default function ReviewScreen({
    runId,
    preset,
    settings,
    onBack,
    onReset,
    onClipCount,
}: Props) {
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
                const [p, presetList] = await Promise.all([
                    api.buildPlan(runId, preset, settings),
                    api.listPresets().catch(() => ({ presets: [] })),
                ]);
                if (cancelled) return;
                setPlan(p);
                setBundle(
                    presetList.presets.find((b) => b.key === preset) ?? null,
                );
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
                    &nbsp;·&nbsp; total <strong>{totalS.toFixed(1)}s</strong>
                    &nbsp;·&nbsp; {plan.markers.markers.length} markers
                </p>
                {plan.director.reasoning && (
                    <p className="muted">{plan.director.reasoning}</p>
                )}
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

            <div className="card">
                <h2>Selected segments</h2>
                <div className="seg-list">
                    {(clipHunter
                        ? (clipHunter.candidates[selectedCandidate]
                              ?.resolved_segments ?? []
                          ).map((s) => ({
                              start_s: s.start_s,
                              end_s: s.end_s,
                              reason: s.reason,
                          }))
                        : plan.director.selected_clips
                    ).map((c, i) => {
                        const isHook = !clipHunter && i === plan.director.hook_index;
                        return (
                            <div key={i} className={`seg ${isHook ? "hook" : ""}`}>
                                <span className="seg-time">
                                    {c.start_s.toFixed(2)}s
                                </span>
                                <span className={isHook ? "seg-hook" : "seg-time"}>
                                    {isHook
                                        ? "HOOK"
                                        : `${(c.end_s - c.start_s).toFixed(1)}s`}
                                </span>
                                <span className="seg-reason" title={c.reason}>
                                    {c.reason}
                                </span>
                            </div>
                        );
                    })}
                </div>
            </div>

            <div className="card">
                <h2>Markers</h2>
                {plan.markers.markers.length === 0 && (
                    <p className="muted">No B-Roll markers suggested for this cut.</p>
                )}
                {plan.markers.markers.map((m, i) => (
                    <div key={i} className="seg">
                        <span className="seg-time">@{m.at_s.toFixed(2)}s</span>
                        <span className="seg-time">{m.color}</span>
                        <span className="seg-reason" title={m.note}>
                            {m.name}
                        </span>
                    </div>
                ))}
            </div>

            <div className="card">
                <h2>Resolved source frames</h2>
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
                                } catch (e) {
                                    setBuildErr(String(e));
                                } finally {
                                    setDeleting(false);
                                }
                            }}
                        >
                            {deleting ? "Deleting…" : "Delete this cut"}
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

            {!buildResult && buildAllResults.length === 0 && (
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
                                            const res = await api.execute(runId, i);
                                            results.push(res);
                                        }
                                        setBuildAllResults(results);
                                    } catch (e) {
                                        setBuildErr(String(e));
                                        if (results.length > 0) {
                                            setBuildAllResults(results);
                                        }
                                    } finally {
                                        setBuilding(false);
                                        setBuildProgress(null);
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
                                    );
                                    setBuildResult(res);
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
            )}
        </div>
    );
}
