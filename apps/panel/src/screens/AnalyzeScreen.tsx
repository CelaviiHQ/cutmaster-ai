import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import { useSSE } from "../useSSE";

interface Props {
    runId: string;
    // Caller receives the total transcribed audio duration (seconds) on success,
    // so the step indicator can show "2. Transcribed · 441s".
    onDone: (durationS?: number) => void;
    onReset: () => void;
    // Optional fire-once callback when the SSE stream closes with a
    // terminal 'done' status. Used by the app shell to update the Saved
    // chip as soon as the run completes, before the user clicks Configure.
    onComplete?: () => void;
    // Phase 5.9 — content-type label shown under the run id. When
    // ``auto``, the cascade resolves after transcribe; the label
    // updates via the detect-preset call on the Configure screen.
    presetLabel?: string;
}

const STAGES = [
    { key: "vfr_check", label: "VFR check" },
    { key: "audio_extract", label: "Extract audio (ffmpeg)" },
    { key: "stt", label: "Transcribe" },
    { key: "speakers", label: "Reconcile speakers", optional: true },
    { key: "scrub", label: "Scrub fillers / restarts" },
    { key: "shot_tag", label: "Shot tagging (vision)", optional: true },
    { key: "audio_cues", label: "Audio cues (DSP)", optional: true },
] as const;

const PROVIDER_LABELS: Record<string, string> = {
    gemini: "Gemini",
    deepgram: "Deepgram",
};

/** Format a duration in seconds as a compact human string. */
function formatElapsed(s: number): string {
    if (s < 1) return "<1s";
    if (s < 60) return `${s.toFixed(s < 10 ? 1 : 0)}s`;
    const mins = Math.floor(s / 60);
    const secs = Math.round(s - mins * 60);
    return `${mins}m ${secs.toString().padStart(2, "0")}s`;
}

/**
 * Strip the parenthesised filename out of a stage message and re-render
 * it as `… · DJI_…0018.MP4` so long source names don't push the row
 * onto two lines. Returns the short form + the full original for tooltip.
 */
function shortenMessage(msg: string): { short: string; full: string } {
    const m = msg.match(/^(.*?)\s*\(([^()]+\.[A-Za-z0-9]+)\)\s*$/);
    if (!m) return { short: msg, full: msg };
    const head = m[1];
    const fname = m[2];
    const dot = fname.lastIndexOf(".");
    const stem = dot > 0 ? fname.slice(0, dot) : fname;
    const ext = dot > 0 ? fname.slice(dot) : "";
    const tail = stem.length > 8 ? `${stem.slice(0, 4)}…${stem.slice(-4)}${ext}` : fname;
    return { short: `${head} · ${tail}`, full: msg };
}

export default function AnalyzeScreen({
    runId,
    onDone,
    onReset,
    onComplete,
    presetLabel,
}: Props) {
    const { events, terminal } = useSSE(runId);
    const [cancelling, setCancelling] = useState(false);
    const [cancelled, setCancelled] = useState(false);

    // Per-stage rollup: latest message/data plus the start + last timestamps
    // so the row can render elapsed time and (for shot_tag) an ETA derived
    // from items_done / items_total.
    const byStage = useMemo(() => {
        const m: Record<
            string,
            {
                status: string;
                message: string;
                data?: unknown;
                tsStart?: number;
                tsLast?: number;
                tsEnd?: number;
            }
        > = {};
        for (const e of events) {
            const prev = m[e.stage];
            const next = {
                status: e.status,
                message: e.message,
                data: e.data,
                tsStart: prev?.tsStart,
                tsLast: e.ts,
                tsEnd: prev?.tsEnd,
            };
            if (e.status === "started" && next.tsStart === undefined) {
                next.tsStart = e.ts;
            }
            if (e.status === "complete" || e.status === "failed") {
                next.tsEnd = e.ts;
            }
            m[e.stage] = next;
        }
        return m;
    }, [events]);

    // 1Hz tick so elapsed / ETA on a running stage advances live.
    const [now, setNow] = useState(() => Date.now() / 1000);
    useEffect(() => {
        const id = window.setInterval(() => setNow(Date.now() / 1000), 1000);
        return () => window.clearInterval(id);
    }, []);

    const failed = terminal === "error" || Object.values(byStage).some((s) => s.status === "failed");
    const done = terminal === "done" && !failed;
    const running = !done && !failed && !cancelled;

    // Celebrate once when analyze lands cleanly. Mascot unmounts after the
    // video ends so the card isn't dominated by a frozen elephant.
    const [showCelebrate, setShowCelebrate] = useState(false);
    useEffect(() => {
        if (done) {
            setShowCelebrate(true);
            onComplete?.();
        }
    }, [done, onComplete]);

    const vfrFail = byStage["vfr_check"]?.status === "failed";

    const onCancel = async () => {
        if (!window.confirm("Cancel this run? In-flight LLM calls keep running but the result is orphaned.")) return;
        setCancelling(true);
        try {
            await api.cancel(runId);
            setCancelled(true);
        } catch {
            // User can always fall back to Start over
        } finally {
            setCancelling(false);
        }
    };

    return (
        <div>
            <div className="card">
                <div
                    style={{
                        display: "flex",
                        gap: "var(--s-5)",
                        alignItems: "center",
                        justifyContent: "space-between",
                    }}
                >
                    <div style={{ flex: 1, minWidth: 0 }}>
                        <h2>Running analyze</h2>
                        <p className="muted">
                            Run <code>{runId}</code>
                            {presetLabel && presetLabel !== "auto" && (
                                <> · <code>{presetLabel}</code></>
                            )}
                            {presetLabel === "auto" && (
                                <> · auto-detecting content type</>
                            )}
                        </p>

                        {STAGES.map((s) => {
                            const state = byStage[s.key];
                            // Optional stages (e.g. speaker reconciliation) only
                            // render if they actually emitted an event.
                            const isOptional = "optional" in s && s.optional;
                            if (isOptional && !state) return null;
                            let label = s.label as string;
                            if (s.key === "stt" && state?.data) {
                                const provider = (state.data as { provider?: string })?.provider;
                                if (provider) {
                                    const pretty = PROVIDER_LABELS[provider] ?? provider;
                                    label = `Transcribe (${pretty})`;
                                }
                            }
                            const icon = state
                                ? state.status === "complete"
                                    ? "✓"
                                    : state.status === "failed"
                                        ? "✕"
                                        : "●"
                                : " ";
                            const cls = state
                                ? state.status === "complete"
                                    ? "complete"
                                    : state.status === "failed"
                                        ? "failed"
                                        : "started"
                                : "pending";
                            const isRunningStage = cls === "started";
                            const isFailedStage = cls === "failed";

                            // Per-row elapsed time. While running, count from
                            // tsStart → now; on terminal events the diff is
                            // tsEnd - tsStart. Pre-start rows show nothing.
                            let elapsedLabel: string | null = null;
                            if (state?.tsStart) {
                                const end = state.tsEnd ?? (isRunningStage ? now : state.tsLast);
                                if (end && end >= state.tsStart) {
                                    elapsedLabel = formatElapsed(end - state.tsStart);
                                }
                            }

                            // Shot tagging emits items_done / items_total in
                            // the data payload — turn it into a determinate
                            // progress bar + ETA derived from average per-item
                            // wall time so far.
                            const data = state?.data as
                                | { items_done?: number; items_total?: number }
                                | undefined;
                            const itemsDone = data?.items_done;
                            const itemsTotal = data?.items_total;
                            const showProgress =
                                isRunningStage &&
                                typeof itemsDone === "number" &&
                                typeof itemsTotal === "number" &&
                                itemsTotal > 0;
                            let progressPct = 0;
                            let etaLabel: string | null = null;
                            if (showProgress) {
                                progressPct = Math.min(100, Math.round((itemsDone / itemsTotal) * 100));
                                if (state?.tsStart && itemsDone > 0 && itemsDone < itemsTotal) {
                                    const elapsedSoFar = now - state.tsStart;
                                    const perItem = elapsedSoFar / itemsDone;
                                    const eta = perItem * (itemsTotal - itemsDone);
                                    if (eta > 0 && Number.isFinite(eta)) {
                                        etaLabel = `~${formatElapsed(eta)} left`;
                                    }
                                }
                            }

                            const rawMsg = state?.message ?? "…";
                            const { short: msgShort, full: msgFull } = shortenMessage(rawMsg);

                            return (
                                <div
                                    key={s.key}
                                    className={`stage-row ${isFailedStage ? "stage-row--failed" : ""} ${showProgress ? "stage-row--has-progress" : ""}`}
                                >
                                    <div className="stage-row-main">
                                        <span className={`stage-icon ${cls}`}>{icon}</span>
                                        <span className="stage-name">{label}</span>
                                        <span className="stage-msg" title={msgFull !== msgShort ? msgFull : undefined}>
                                            {msgShort}
                                            {isRunningStage && !showProgress && (
                                                <span className="dots" aria-hidden="true" />
                                            )}
                                            {etaLabel && <span className="stage-eta"> · {etaLabel}</span>}
                                        </span>
                                        <span className="stage-elapsed">{elapsedLabel ?? ""}</span>
                                    </div>
                                    {showProgress && (
                                        <div
                                            className="stage-progress"
                                            role="progressbar"
                                            aria-valuemin={0}
                                            aria-valuemax={100}
                                            aria-valuenow={progressPct}
                                        >
                                            <div className="stage-progress-bar" style={{ width: `${progressPct}%` }} />
                                        </div>
                                    )}
                                </div>
                            );
                        })}

                        {/* Total elapsed across all stages — earliest start to
                            latest event timestamp. Sticks below the list so
                            the editor sees the run-level cost at a glance. */}
                        {(() => {
                            const starts = Object.values(byStage)
                                .map((v) => v.tsStart)
                                .filter((v): v is number => typeof v === "number");
                            const lasts = Object.values(byStage)
                                .map((v) => v.tsEnd ?? v.tsLast)
                                .filter((v): v is number => typeof v === "number");
                            if (!starts.length) return null;
                            const earliest = Math.min(...starts);
                            const latest = running
                                ? now
                                : lasts.length
                                    ? Math.max(...lasts)
                                    : now;
                            const totalS = Math.max(0, latest - earliest);
                            return (
                                <div className="stage-total">
                                    <span className="stage-total-label">Total</span>
                                    <span className="stage-total-value">{formatElapsed(totalS)}</span>
                                </div>
                            );
                        })()}
                    </div>

                    {/* v3-2 mascot — discovery elephant loops while running;
                        brief celebration plays once on clean completion then
                        unmounts so the card doesn't get frozen on the last
                        frame. CSS respects prefers-reduced-motion. */}
                    {running && (
                        <video
                            className="mascot mascot--analyze"
                            src="/mascot/discovery-480.webm"
                            autoPlay
                            loop
                            muted
                            playsInline
                            aria-hidden="true"
                        />
                    )}
                    {showCelebrate && (
                        <video
                            className="mascot mascot--analyze"
                            src="/mascot/celebration-960.webm"
                            autoPlay
                            muted
                            playsInline
                            aria-hidden="true"
                        />
                    )}
                </div>
            </div>

            {vfrFail && (
                <div className="error-box">
                    Variable-frame-rate media detected. CutMaster refuses to proceed because AI timestamps will drift from video frames.
                    <br /><br />
                    <strong>How to fix:</strong> transcode the source to CFR (same fps as the timeline) and relink. iPhone / screen recordings are the usual culprits.
                </div>
            )}

            {failed && !vfrFail && (
                <div className="error-box">
                    The analyze pipeline failed. Check the backend log and reset.
                </div>
            )}

            {cancelled && (
                <div className="error-box" style={{ borderColor: "var(--warn)", color: "var(--warn)" }}>
                    Run cancelled. In-flight LLM calls may still be running — they'll finish and be orphaned.
                </div>
            )}

            <div className="row between">
                <button className="secondary" onClick={onReset}>← Start over</button>
                <div className="row" style={{ gap: "var(--s-2)", marginTop: 0 }}>
                    {running && (
                        <button
                            className="secondary"
                            onClick={onCancel}
                            disabled={cancelling}
                            title="Mark this run as cancelled and return to Preset"
                        >
                            {cancelling ? "Cancelling…" : "Cancel"}
                        </button>
                    )}
                    <button
                        disabled={!done}
                        data-hotkey="primary"
                        onClick={() => {
                            const sttData = byStage["stt"]?.data as
                                | { duration_s?: number; durationSeconds?: number }
                                | undefined;
                            const duration =
                                sttData?.duration_s ??
                                sttData?.durationSeconds ??
                                undefined;
                            onDone(duration);
                        }}
                    >
                        {done ? "Configure →" : "Analyzing…"}
                    </button>
                </div>
            </div>
        </div>
    );
}
