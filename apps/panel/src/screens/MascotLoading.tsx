/*
 * Shared loading state for LLM-backed calls that take ~5–15s but have no
 * stage-by-stage SSE progress yet. Mirrors the Analyze screen's pattern:
 * discovery mascot + stage rows + animated dots so users see *what* is
 * running, not just "wait".
 *
 * v3-8 upgrades the Review-screen variant to real SSE stage events with
 * elapsed counters + retry indicators. Until then this is the baseline.
 */

interface Stage {
    label: string;
    status?: "pending" | "started" | "complete";
}

interface Props {
    /** Headline — e.g. "Building plan", "Analysing themes". */
    label: string;
    /** Optional explainer line beneath the label. */
    hint?: string;
    /** Stage rows rendered below the mascot. Defaults to one "started" row
     *  using the label itself. */
    stages?: Stage[];
}

export default function MascotLoading({ label, hint, stages }: Props) {
    const rows: Stage[] = stages ?? [{ label, status: "started" }];
    return (
        <div
            className="card"
            style={{
                textAlign: "center",
                paddingTop: "var(--s-5)",
                paddingBottom: "var(--s-5)",
            }}
        >
            <video
                className="mascot mascot--loading"
                src="/mascot/discovery-480.webm"
                autoPlay
                loop
                muted
                playsInline
                aria-hidden="true"
            />
            <p
                style={{
                    color: "var(--text-primary)",
                    fontWeight: 500,
                    marginTop: "var(--s-2)",
                    marginBottom: hint ? 0 : "var(--s-3)",
                }}
            >
                {label}
                <span className="dots" aria-hidden="true" />
            </p>
            {hint && (
                <p
                    className="muted"
                    style={{
                        fontSize: "var(--fs-2)",
                        marginTop: 0,
                        marginBottom: "var(--s-3)",
                    }}
                >
                    {hint}
                </p>
            )}
            <div
                style={{
                    maxWidth: 420,
                    margin: "0 auto",
                    textAlign: "left",
                }}
            >
                {rows.map((s, i) => {
                    const status = s.status ?? "started";
                    const iconChar =
                        status === "complete" ? "✓" : status === "pending" ? " " : "●";
                    return (
                        <div key={i} className="stage-row">
                            <span className={`stage-icon ${status}`}>{iconChar}</span>
                            <span className="stage-name">{s.label}</span>
                            <span className="stage-msg">
                                {status === "started" ? (
                                    <>
                                        in progress
                                        <span className="dots" aria-hidden="true" />
                                    </>
                                ) : status === "complete" ? (
                                    "done"
                                ) : (
                                    "…"
                                )}
                            </span>
                        </div>
                    );
                })}
            </div>
        </div>
    );
}
