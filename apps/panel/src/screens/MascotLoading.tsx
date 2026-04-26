import { useEffect, useState } from "react";

/*
 * Shared loading state for LLM-backed calls that take ~5–15s but have no
 * stage-by-stage SSE progress yet. Mirrors the Analyze screen's pattern:
 * discovery mascot + stage rows + animated dots + an elapsed-time counter
 * so users see *something moving* even during a single long LLM call.
 *
 * v3-8 upgrades the Review-screen variant to real SSE stage events with
 * per-stage elapsed counters + retry indicators. Until then this is the
 * baseline — the counter + indeterminate bar are what kill the "is it
 * hung?" perception.
 */

interface Stage {
    label: string;
    status?: "pending" | "started" | "complete" | "failed";
    /** Optional message — overrides the default "in progress" / "done" copy. */
    message?: string;
    /** Wall-clock seconds since started (running) or total duration (complete). */
    elapsedS?: number;
    /** LLM retry attempts the agent burned (Director / Marker only). */
    attempts?: number;
    /** Residual validator errors after best-effort fallback. */
    validationErrors?: number;
}

interface Props {
    /** Headline — e.g. "Building plan", "Analysing themes". */
    label: string;
    /** Optional explainer line beneath the label. */
    hint?: string;
    /** Stage rows rendered below. Defaults to one "started" row using the label. */
    stages?: Stage[];
}

export default function MascotLoading({ label, hint, stages }: Props) {
    const rows: Stage[] = stages ?? [{ label, status: "started" }];
    const [elapsedS, setElapsedS] = useState(0);

    useEffect(() => {
        const started = Date.now();
        const id = window.setInterval(() => {
            setElapsedS((Date.now() - started) / 1000);
        }, 100);
        return () => window.clearInterval(id);
    }, []);

    return (
        <div className="card mascot-loading-card">
            <div className="mascot-loading-inner">
                <video
                    className="mascot mascot--loading"
                    src="/mascot/discovery-480.webm"
                    autoPlay
                    loop
                    muted
                    playsInline
                    aria-hidden="true"
                />
                <h2 className="mascot-loading-label">
                    {label}
                    <span className="dots" aria-hidden="true" />
                </h2>
                {hint && <p className="muted mascot-loading-hint">{hint}</p>}

                <div className="mascot-loading-progress" aria-hidden="true">
                    <div className="bar" />
                </div>

                <div className="mascot-loading-stages">
                    {rows.map((s, i) => {
                        const status = s.status ?? "started";
                        const iconChar =
                            status === "complete"
                                ? "✓"
                                : status === "failed"
                                    ? "✕"
                                    : status === "pending"
                                        ? " "
                                        : "●";
                        // Build the right-hand status line. Default copy
                        // ("in progress…", "done", "…") still fires when no
                        // explicit message is provided so existing callers
                        // keep their look. When the build-progress poller
                        // supplies a message + telemetry, render that
                        // instead so the editor can see "Plan built · 5
                        // attempt(s) · 2 unresolved issue(s)".
                        const showAttempts = typeof s.attempts === "number" && s.attempts > 1;
                        const showIssues =
                            typeof s.validationErrors === "number" && s.validationErrors > 0;
                        return (
                            <div
                                key={i}
                                className={`stage-row ${status === "failed" ? "stage-row--failed" : ""}`}
                            >
                                <div className="stage-row-main">
                                    <span className={`stage-icon ${status}`}>{iconChar}</span>
                                    <span className="stage-name">{s.label}</span>
                                    <span className="stage-msg" title={s.message}>
                                        {s.message ? (
                                            s.message
                                        ) : status === "started" ? (
                                            <>
                                                in progress
                                                <span className="dots" aria-hidden="true" />
                                            </>
                                        ) : status === "complete" ? (
                                            "done"
                                        ) : status === "failed" ? (
                                            "failed"
                                        ) : (
                                            "…"
                                        )}
                                        {(showAttempts || showIssues) && (
                                            <span className="stage-eta">
                                                {showAttempts && ` · ${s.attempts} attempt${s.attempts === 1 ? "" : "s"}`}
                                                {showIssues && ` · ${s.validationErrors} issue${s.validationErrors === 1 ? "" : "s"}`}
                                            </span>
                                        )}
                                    </span>
                                    {typeof s.elapsedS === "number" && (
                                        <span className="stage-elapsed">{formatStageElapsed(s.elapsedS)}</span>
                                    )}
                                </div>
                            </div>
                        );
                    })}
                </div>

                <p className="muted mascot-loading-elapsed" aria-live="polite">
                    {formatElapsed(elapsedS)} elapsed
                </p>
            </div>
        </div>
    );
}

function formatElapsed(s: number): string {
    if (s < 60) return `${s.toFixed(1)}s`;
    const min = Math.floor(s / 60);
    const sec = Math.floor(s % 60);
    return `${min}m ${sec.toString().padStart(2, "0")}s`;
}

/** Per-stage elapsed — same shape as Analyze rows (mono, tabular). */
function formatStageElapsed(s: number): string {
    if (s < 1) return "<1s";
    if (s < 60) return `${s.toFixed(s < 10 ? 1 : 0)}s`;
    const mins = Math.floor(s / 60);
    const secs = Math.round(s - mins * 60);
    return `${mins}m ${secs.toString().padStart(2, "0")}s`;
}
