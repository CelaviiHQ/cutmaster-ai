import { useMemo } from "react";
import { useSSE } from "../useSSE";

interface Props {
    runId: string;
    // Caller receives the total transcribed audio duration (seconds) on success,
    // so the step indicator can show "2. Transcribed · 441s".
    onDone: (durationS?: number) => void;
    onReset: () => void;
}

const STAGES = [
    { key: "vfr_check", label: "VFR check" },
    { key: "audio_extract", label: "Extract audio (ffmpeg)" },
    { key: "stt", label: "Transcribe" },
    { key: "speakers", label: "Reconcile speakers", optional: true },
    { key: "scrub", label: "Scrub fillers / restarts" },
] as const;

const PROVIDER_LABELS: Record<string, string> = {
    gemini: "Gemini",
    deepgram: "Deepgram",
};

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
                    // Annotate the STT row with the actual provider the
                    // backend used (Gemini / Deepgram) instead of the
                    // hardcoded placeholder.
                    let label = s.label as string;
                    if (s.key === "stt" && state?.data) {
                        const provider = (state.data as { provider?: string })
                            ?.provider;
                        if (provider) {
                            const pretty =
                                PROVIDER_LABELS[provider] ?? provider;
                            label = `Transcribe (${pretty})`;
                        }
                    }
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
                            <span className="stage-name">{label}</span>
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
                <button
                    disabled={!done}
                    data-hotkey="primary"
                    onClick={() => {
                        // Pull audio-duration hint from the STT event's data if present.
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
                    {done ? "Configure →" : "Waiting…"}
                </button>
            </div>
        </div>
    );
}
