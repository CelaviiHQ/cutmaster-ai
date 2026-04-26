import { useEffect, useState } from "react";

/*
 * Shared loading state for LLM-backed pipeline calls (analyze themes,
 * /build-plan). Every long-wait screen funnels through here so we get
 * consistent personality (mascot), consistent telemetry (per-stage
 * elapsed, attempts, validation issues), and consistent escape hatches
 * (Cancel + slow-job notice + next-step preview).
 *
 * The visual goal: turn waiting into watching progress. Editors should
 * see *which* stage is moving, *how far* through the pipeline they are,
 * and what comes next — same trick that makes Vercel/Linear deploy
 * loaders feel fast even when they aren't.
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
    /**
     * When true and 2+ rows share `status === "started"`, surface a
     * "running in parallel" badge so editors don't read it as a UI bug.
     */
    parallel?: boolean;
    /** Static "what comes next" preview — anchors expectation. */
    nextLabel?: string;
    /**
     * Soft upper bound from the hint copy. When elapsed crosses this
     * threshold we inject a calm "taking longer than usual" line so the
     * editor doesn't assume the pipeline stalled.
     */
    expectedMaxS?: number;
    /** Optional cancel handler — renders a ghost "Cancel" button when set. */
    onCancel?: () => void;
}

export default function MascotLoading({
    label,
    hint,
    stages,
    parallel,
    nextLabel,
    expectedMaxS,
    onCancel,
}: Props) {
    const rows: Stage[] = stages ?? [{ label, status: "started" }];
    const [elapsedS, setElapsedS] = useState(0);

    useEffect(() => {
        const started = Date.now();
        const id = window.setInterval(() => {
            setElapsedS((Date.now() - started) / 1000);
        }, 100);
        return () => window.clearInterval(id);
    }, []);

    // Stepped progress fill — count completed stages out of the total.
    // Determinate progress ("3 / 5") feels more honest than an
    // indeterminate bar at 30s+, especially when stages don't run for
    // exactly the same duration.
    const total = rows.length;
    const completedCount = rows.filter((r) => r.status === "complete").length;
    const activeCount = rows.filter((r) => r.status === "started").length;
    // Show a parallel badge when explicitly requested and 2+ rows are
    // simultaneously running — e.g. Director + Marker in the build phase.
    const showParallel = !!parallel && activeCount >= 2;
    const slow = expectedMaxS != null && elapsedS > expectedMaxS;

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

                {/* Headline + inline elapsed — promotes the timer from
                    a footer afterthought to a primary signal. */}
                <h2 className="mascot-loading-label">
                    {label}
                    <span className="mascot-loading-elapsed-inline" aria-live="polite">
                        {" · "}
                        {formatElapsed(elapsedS)}
                    </span>
                </h2>
                {hint && <p className="muted mascot-loading-hint">{hint}</p>}

                {/* Stepped progress bar — one filled chunk per completed
                    stage, half-fill on the active stage so motion is visible
                    even when only one chunk has flipped. */}
                <div
                    className="mascot-loading-progress mascot-loading-progress--stepped"
                    aria-hidden="true"
                    aria-label={`${completedCount} of ${total} stages complete`}
                >
                    {rows.map((r, i) => {
                        const status = r.status ?? "pending";
                        return (
                            <div
                                key={i}
                                className={`step ${status}`}
                            />
                        );
                    })}
                </div>
                <p className="mascot-loading-step-count">
                    {completedCount} / {total} {showParallel && (
                        <span className="mascot-loading-parallel" title="Director + Marker run concurrently">
                            ↔ in parallel
                        </span>
                    )}
                </p>

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
                        const showAttempts = typeof s.attempts === "number" && s.attempts > 1;
                        const showIssues =
                            typeof s.validationErrors === "number" && s.validationErrors > 0;
                        const isActiveParallel = showParallel && status === "started";
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
                                            "—"
                                        )}
                                        {(showAttempts || showIssues) && (
                                            <span className="stage-eta">
                                                {showAttempts && ` · ${s.attempts} attempt${s.attempts === 1 ? "" : "s"}`}
                                                {showIssues && ` · ${s.validationErrors} issue${s.validationErrors === 1 ? "" : "s"}`}
                                            </span>
                                        )}
                                        {isActiveParallel && (
                                            <span className="mascot-loading-parallel mascot-loading-parallel--inline">
                                                ↔
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

                {slow && (
                    <p className="mascot-loading-slow">
                        Taking longer than usual — large transcript? You can keep
                        waiting or come back to it from <em>Previous runs</em>.
                    </p>
                )}

                {(nextLabel || onCancel) && (
                    <div className="mascot-loading-footer">
                        <span className="mascot-loading-next">
                            {nextLabel && <>Next: {nextLabel}</>}
                        </span>
                        {onCancel && (
                            <button
                                type="button"
                                className="btn-ghost"
                                onClick={onCancel}
                            >
                                Cancel
                            </button>
                        )}
                    </div>
                )}
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
