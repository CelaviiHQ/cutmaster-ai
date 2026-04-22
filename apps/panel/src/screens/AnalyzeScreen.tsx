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

    const byStage = useMemo(() => {
        const m: Record<string, { status: string; message: string; data?: unknown }> = {};
        for (const e of events) {
            m[e.stage] = { status: e.status, message: e.message, data: e.data };
        }
        return m;
    }, [events]);

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
                            return (
                                <div
                                    key={s.key}
                                    className={`stage-row ${isFailedStage ? "stage-row--failed" : ""}`}
                                >
                                    <span className={`stage-icon ${cls}`}>{icon}</span>
                                    <span className="stage-name">{label}</span>
                                    <span className="stage-msg">
                                        {state?.message ?? "…"}
                                        {isRunningStage && <span className="dots" aria-hidden="true" />}
                                    </span>
                                </div>
                            );
                        })}
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
