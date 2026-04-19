import { useEffect, useState } from "react";
import { api } from "../api";
import { formatRelativeTime } from "../persist";
import type { RunSummary } from "../types";

interface Props {
    /** Called with the run_id the user picked — parent hydrates from /state. */
    onReopen: (runId: string) => void;
}

/**
 * Collapsible drawer listing prior runs. Each row exposes Reopen, Clone,
 * and Delete. Clone reopens the new run automatically so the editor
 * lands at Configure with the same transcript.
 *
 * Scope: drawer only — everything else (resumeAt logic, state hydration)
 * lives in the app shell so this component stays presentational.
 */
export default function RunsDrawer({ onReopen }: Props) {
    const [open, setOpen] = useState(false);
    const [runs, setRuns] = useState<RunSummary[]>([]);
    const [total, setTotal] = useState(0);
    const [truncated, setTruncated] = useState(false);
    const [loading, setLoading] = useState(false);
    const [err, setErr] = useState<string | null>(null);
    const [busyId, setBusyId] = useState<string | null>(null);

    const refresh = async () => {
        setLoading(true);
        setErr(null);
        try {
            const r = await api.listRuns({ limit: 20 });
            setRuns(r.runs);
            setTotal(r.total);
            setTruncated(r.truncated);
        } catch (e) {
            setErr(String(e));
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        if (open) refresh();
    }, [open]);

    const onDelete = async (run: RunSummary) => {
        const cuts = run.execute_history.filter((h) => !h.aborted).length;
        const extra = cuts > 0 ? ` (${cuts} cut timeline${cuts === 1 ? "" : "s"} stays in Resolve)` : "";
        if (!window.confirm(`Delete run "${run.timeline_name}"?${extra}`)) return;
        setBusyId(run.run_id);
        try {
            await api.deleteRun(run.run_id);
            await refresh();
        } catch (e) {
            setErr(String(e));
        } finally {
            setBusyId(null);
        }
    };

    const onClone = async (run: RunSummary) => {
        setBusyId(run.run_id);
        try {
            const cloned = await api.cloneRun(run.run_id);
            onReopen(cloned.run_id);
        } catch (e) {
            setErr(String(e));
        } finally {
            setBusyId(null);
        }
    };

    return (
        <details
            className="card"
            open={open}
            onToggle={(e) => setOpen((e.target as HTMLDetailsElement).open)}
        >
            <summary>
                <span>
                    Previous runs{" "}
                    <span className="muted" style={{ fontSize: "var(--fs-2)" }}>
                        {total > 0 ? `· ${total}` : ""}
                    </span>
                </span>
            </summary>
            <div className="card-body">
                {loading && <p className="muted">Loading…</p>}
                {err && (
                    <p className="muted" style={{ color: "var(--err)" }}>
                        {err}
                    </p>
                )}
                {!loading && runs.length === 0 && !err && (
                    <p className="muted">No runs yet. Analyze a timeline to create one.</p>
                )}
                {runs.length > 0 && (
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
                        {runs.map((r) => {
                            const cuts = r.execute_history.filter((h) => !h.aborted).length;
                            const isBusy = busyId === r.run_id;
                            const age = formatRelativeTime(r.last_modified * 1000);
                            return (
                                <li
                                    key={r.run_id}
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
                                            {r.timeline_name || <em className="muted">(unnamed)</em>}
                                        </div>
                                        <div
                                            className="muted"
                                            style={{ fontSize: "var(--fs-2)" }}
                                        >
                                            <code>{r.preset}</code> · {r.status} · {age}
                                            {r.has_plan && " · plan"}
                                            {cuts > 0 && ` · ${cuts} cut${cuts === 1 ? "" : "s"}`}
                                        </div>
                                    </div>
                                    <div
                                        className="row"
                                        style={{ gap: "var(--s-2)", margin: 0 }}
                                    >
                                        <button
                                            disabled={isBusy}
                                            onClick={() => onReopen(r.run_id)}
                                            title="Reopen this run at the step it left off"
                                        >
                                            Reopen
                                        </button>
                                        <button
                                            className="secondary"
                                            disabled={isBusy || !r.has_transcript}
                                            onClick={() => onClone(r)}
                                            title={
                                                r.has_transcript
                                                    ? "Start a new run with this transcript (fresh plan)"
                                                    : "Clone needs a transcript — this run has none yet"
                                            }
                                        >
                                            Clone
                                        </button>
                                        <button
                                            className="btn-ghost"
                                            disabled={isBusy}
                                            onClick={() => onDelete(r)}
                                            title="Delete run state + cached audio (Resolve timelines untouched)"
                                        >
                                            {isBusy ? "…" : "Delete"}
                                        </button>
                                    </div>
                                </li>
                            );
                        })}
                    </ul>
                )}
                {truncated && (
                    <p className="muted" style={{ marginTop: "var(--s-3)", fontSize: "var(--fs-2)" }}>
                        Showing the 20 most recent — older runs are on disk but hidden here.
                    </p>
                )}
                {runs.length > 0 && !loading && (
                    <div className="row" style={{ marginTop: "var(--s-3)" }}>
                        <button className="btn-ghost" onClick={refresh}>
                            ↻ Refresh
                        </button>
                    </div>
                )}
            </div>
        </details>
    );
}
