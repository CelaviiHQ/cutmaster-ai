import { useMemo } from "react";
import { useSSE } from "../useSSE";

interface Props {
    runId: string;
    onDone: () => void;
    onReset: () => void;
}

const STAGES = [
    { key: "vfr_check", label: "VFR check" },
    { key: "audio_extract", label: "Extract audio (ffmpeg)" },
    { key: "stt", label: "Transcribe (Gemini)" },
    { key: "speakers", label: "Reconcile speakers", optional: true },
    { key: "scrub", label: "Scrub fillers / restarts" },
] as const;

export default function AnalyzeScreen({ runId, onDone, onReset }: Props) {
    const { events, terminal } = useSSE(runId);

    const byStage = useMemo(() => {
        const m: Record<string, { status: string; message: string; data?: unknown }> = {};
        for (const e of events) {
            m[e.stage] = { status: e.status, message: e.message, data: e.data };
        }
        return m;
    }, [events]);

    const failed = terminal === "error" || Object.values(byStage).some((s) => s.status === "failed");
    const done = terminal === "done" && !failed;

    const vfrFail = byStage["vfr_check"]?.status === "failed";

    return (
        <div>
            <div className="card">
                <h2>Running analyze</h2>
                <p className="muted">
                    Run <code>{runId}</code>
                </p>

                {STAGES.map((s) => {
                    const state = byStage[s.key];
                    // Optional stages (e.g. speaker reconciliation) only
                    // render if they actually emitted an event — otherwise
                    // they'd mislead as perpetually "pending".
                    const isOptional = "optional" in s && s.optional;
                    if (isOptional && !state) return null;
                    const icon = state
                        ? state.status === "complete"
                            ? "✓"
                            : state.status === "failed"
                                ? "✕"
                                : "…"
                        : " ";
                    const cls = state
                        ? state.status === "complete"
                            ? "complete"
                            : state.status === "failed"
                                ? "failed"
                                : "started"
                        : "pending";
                    return (
                        <div key={s.key} className="stage-row">
                            <span className={`stage-icon ${cls}`}>{icon}</span>
                            <span className="stage-name">{s.label}</span>
                            <span className="stage-msg">{state?.message ?? "…"}</span>
                        </div>
                    );
                })}
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

            <div className="row between">
                <button className="secondary" onClick={onReset}>← Start over</button>
                <button disabled={!done} onClick={onDone}>
                    {done ? "Configure →" : "Waiting…"}
                </button>
            </div>
        </div>
    );
}
