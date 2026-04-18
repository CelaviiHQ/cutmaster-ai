import { useEffect, useState } from "react";
import { api } from "../api";
import type { ExecuteResult } from "../api";
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
}

export default function ReviewScreen({
    runId,
    preset,
    settings,
    onBack,
    onReset,
}: Props) {
    const [plan, setPlan] = useState<BuildPlanResult | null>(null);
    const [bundle, setBundle] = useState<PresetBundle | null>(null);
    const [loading, setLoading] = useState(true);
    const [err, setErr] = useState<string | null>(null);
    const [building, setBuilding] = useState(false);
    const [buildResult, setBuildResult] = useState<ExecuteResult | null>(null);
    const [buildErr, setBuildErr] = useState<string | null>(null);
    const [deleting, setDeleting] = useState(false);
    const [selectedCandidate, setSelectedCandidate] = useState(0);

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
            <div className="card">
                <p className="muted">
                    Running Director + Marker agents… this usually takes 5–15 s.
                </p>
            </div>
        );
    }

    if (err) {
        return (
            <div>
                <div className="error-box">{err}</div>
                <div className="row between">
                    <button className="secondary" onClick={onBack}>← Back</button>
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
                    <h2>Clip candidates — pick one to build</h2>
                    <div className="row" style={{ flexWrap: "wrap" }}>
                        {clipHunter.candidates.map((c, i) => (
                            <button
                                key={i}
                                className={i === selectedCandidate ? "" : "secondary"}
                                onClick={() => setSelectedCandidate(i)}
                            >
                                #{i + 1} · {(c.engagement_score * 100).toFixed(0)}%
                                &nbsp;· {(c.end_s - c.start_s).toFixed(0)}s
                            </button>
                        ))}
                    </div>
                    {selectedClip && (
                        <div style={{ marginTop: 10 }}>
                            <p>
                                <strong>&ldquo;{selectedClip.quote}&rdquo;</strong>
                            </p>
                            <p className="muted">{selectedClip.reasoning}</p>
                            {selectedClip.suggested_caption && (
                                <p className="muted">
                                    <strong>Caption:</strong>{" "}
                                    {selectedClip.suggested_caption}
                                </p>
                            )}
                            <p className="muted">
                                Source: <code>{selectedClip.start_s.toFixed(2)}s</code> →{" "}
                                <code>{selectedClip.end_s.toFixed(2)}s</code>
                            </p>
                        </div>
                    )}
                </div>
            )}

            <div className="card">
                <h2>Selected segments</h2>
                <div className="seg-list">
                    {plan.director.selected_clips.map((c, i) => {
                        const isHook = i === plan.director.hook_index;
                        return (
                            <div key={i} className={`seg ${isHook ? "hook" : ""}`}>
                                <span className="seg-time">
                                    {c.start_s.toFixed(2)}s
                                </span>
                                <span className={isHook ? "seg-hook" : "seg-time"}>
                                    {isHook ? "HOOK" : `${(c.end_s - c.start_s).toFixed(1)}s`}
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
                    {plan.resolved_segments.slice(0, 10).map((r, i) => (
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
                    {plan.resolved_segments.length > 10 && (
                        <div className="muted" style={{ padding: 8 }}>
                            …and {plan.resolved_segments.length - 10} more
                        </div>
                    )}
                </div>
            </div>

            {buildResult && (
                <div className="card" style={{ borderColor: "var(--ok)" }}>
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

            {!buildResult && (
                <div className="row between">
                    <button className="secondary" onClick={onBack} disabled={building}>
                        ← Back
                    </button>
                    <button
                        disabled={building}
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
                        {building ? "Building…" : "Build Timeline →"}
                    </button>
                </div>
            )}
        </div>
    );
}
