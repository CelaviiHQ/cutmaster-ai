import type {
    CoherenceCategory,
    CoherenceIssue,
    CoherenceReport,
    CoherenceSeverity,
    Verdict,
} from "../types";

// Verdict copy is the editor-facing label; the tone is set by the badge
// background colour (CSS), not the word.
const VERDICT_LABEL: Record<Verdict, string> = {
    ship: "Ship",
    review: "Review",
    rework: "Rework",
};

const SEVERITY_LABEL: Record<CoherenceSeverity, string> = {
    info: "Note",
    warning: "Warn",
    error: "Block",
};

// Editor-readable category names. The Pydantic Literal uses snake_case
// for stability; this map is the only place the human form lives.
const CATEGORY_LABEL: Record<CoherenceCategory, string> = {
    non_sequitur: "Non-sequitur",
    weak_hook: "Weak hook",
    missing_setup: "Missing setup",
    abrupt_transition: "Abrupt transition",
    redundancy: "Redundancy",
    unresolved_thread: "Unresolved thread",
    inverted_arc: "Inverted arc",
    weak_resolution: "Weak resolution",
    buried_lede: "Buried lede",
};

interface SubScoreProps {
    label: string;
    value: number | null;
}

function SubScore({ label, value }: SubScoreProps) {
    if (value === null) {
        return (
            <div className="coherence-subscore coherence-subscore--na">
                <span className="coherence-subscore-label">{label}</span>
                <span className="coherence-subscore-value">—</span>
                <span className="coherence-subscore-bar" aria-hidden />
            </div>
        );
    }
    const pct = Math.max(0, Math.min(100, value));
    return (
        <div className="coherence-subscore">
            <span className="coherence-subscore-label">{label}</span>
            <span className="coherence-subscore-value">{pct}</span>
            <span className="coherence-subscore-bar" aria-hidden>
                <span
                    className="coherence-subscore-bar-fill"
                    style={{ width: `${pct}%` }}
                />
            </span>
        </div>
    );
}

interface IssueRowProps {
    issue: CoherenceIssue;
    onClick: (segmentIndex: number) => void;
}

function IssueRow({ issue, onClick }: IssueRowProps) {
    const isWholeCut = issue.segment_index < 0;
    const segLabel = isWholeCut ? "whole cut" : `seg ${issue.segment_index + 1}`;
    const pairSuffix =
        issue.pair_index !== null && issue.pair_index !== undefined
            ? ` → ${issue.pair_index + 2}`
            : "";
    return (
        <button
            type="button"
            className={`coherence-issue coherence-issue--${issue.severity}`}
            onClick={() => onClick(issue.segment_index)}
            disabled={isWholeCut}
            title={
                isWholeCut
                    ? "Whole-cut observation — no segment to scroll to"
                    : `Jump to segment ${issue.segment_index + 1}`
            }
        >
            <span
                className={`coherence-issue-dot coherence-issue-dot--${issue.severity}`}
                aria-hidden
            />
            <span className="coherence-issue-severity">
                {SEVERITY_LABEL[issue.severity]}
            </span>
            <span className="coherence-issue-category">
                {CATEGORY_LABEL[issue.category] ?? issue.category}
            </span>
            <span className="coherence-issue-target">
                {segLabel}
                {pairSuffix}
            </span>
            <span className="coherence-issue-message">{issue.message}</span>
            {issue.suggestion && (
                <span className="coherence-issue-suggestion">
                    → {issue.suggestion}
                </span>
            )}
        </button>
    );
}

interface Props {
    report: CoherenceReport;
    /**
     * Called when an issue is clicked — receives the issue's
     * `segment_index`. The host wires this to its expanded-segment state
     * + scroll-into-view of `seg-${i}`.
     */
    onIssueClick: (segmentIndex: number) => void;
    /**
     * Optional context label for per-candidate reports — e.g. "Candidate
     * 2 of 5". Renders next to the verdict badge.
     */
    contextLabel?: string;
    /**
     * Optional re-critique handler. When provided, renders a "Re-critique"
     * button that fires the retroactive endpoint. Host owns the debounce
     * + busy state so multiple cards can share it.
     */
    onRecritique?: () => void;
    /** Disables the Re-critique button while a request is in flight. */
    recritiqueBusy?: boolean;
    /** Surfaced under the issue list when the last re-critique failed. */
    recritiqueError?: string | null;
}

/**
 * Story-critic verdict card. Renders above the segments list on the
 * Review screen so editors can scan the cut's coherence at a glance and
 * jump to specific issues.
 *
 * Empty-state (when no report is available — flag off, LLM failed, no
 * resolved_axes) is handled by the host, not this component. The card
 * always renders a real report.
 */
export default function CoherenceReportCard({
    report,
    onIssueClick,
    contextLabel,
    onRecritique,
    recritiqueBusy = false,
    recritiqueError = null,
}: Props) {
    return (
        <div className="card coherence-card">
            <div className="coherence-head">
                <div className="coherence-score-block">
                    <span
                        className={`coherence-score coherence-score--${report.verdict}`}
                    >
                        {report.score}
                    </span>
                    <span className="coherence-score-suffix">/ 100</span>
                </div>
                <div className="coherence-verdict-block">
                    <span
                        className={`coherence-verdict coherence-verdict--${report.verdict}`}
                    >
                        {VERDICT_LABEL[report.verdict]}
                    </span>
                    {contextLabel && (
                        <span className="coherence-context muted">
                            {contextLabel}
                        </span>
                    )}
                </div>
                {onRecritique && (
                    <button
                        type="button"
                        className="link-button coherence-recritique"
                        onClick={onRecritique}
                        disabled={recritiqueBusy}
                        title="Re-run the story-critic against this plan"
                    >
                        {recritiqueBusy ? "Re-critiquing…" : "Re-critique"}
                    </button>
                )}
            </div>

            <div className="coherence-subscores">
                <SubScore label="Hook" value={report.hook_strength} />
                <SubScore label="Arc" value={report.arc_clarity} />
                <SubScore label="Transitions" value={report.transitions} />
                <SubScore label="Resolution" value={report.resolution} />
            </div>

            {report.summary && (
                <p className="coherence-summary">{report.summary}</p>
            )}

            {report.issues.length > 0 ? (
                <ul className="coherence-issue-list">
                    {report.issues.map((iss, i) => (
                        <li key={i}>
                            <IssueRow issue={iss} onClick={onIssueClick} />
                        </li>
                    ))}
                </ul>
            ) : (
                <p className="coherence-issue-list-empty muted">
                    No issues flagged.
                </p>
            )}

            {recritiqueError && (
                <p className="coherence-recritique-err">
                    Re-critique failed: {recritiqueError}
                </p>
            )}
        </div>
    );
}
