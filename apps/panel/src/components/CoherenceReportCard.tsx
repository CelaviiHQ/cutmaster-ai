import { useState } from "react";
import type {
    CoherenceCategory,
    CoherenceIssue,
    CoherenceReport,
    CoherenceSeverity,
    Verdict,
} from "../types";

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
    fixed: boolean;
    onToggleFixed: () => void;
    onJump: (segmentIndex: number) => void;
}

/**
 * Stacked, three-line issue row. Each row owns its own grid so long
 * messages can never push siblings out of place. Mark-fixed is a local
 * triage tool — strikes through and dims the row but doesn't persist
 * server-side (the plan still surfaces the issue on rebuild).
 */
function IssueRow({ issue, fixed, onToggleFixed, onJump }: IssueRowProps) {
    const isWholeCut = issue.segment_index < 0;
    const segLabel = isWholeCut ? "whole cut" : `seg ${issue.segment_index + 1}`;
    const pairSuffix =
        issue.pair_index !== null && issue.pair_index !== undefined
            ? ` → ${issue.pair_index + 2}`
            : "";
    return (
        <div
            className={`coherence-issue coherence-issue--${issue.severity}${
                fixed ? " coherence-issue--fixed" : ""
            }`}
        >
            <div className="coherence-issue-meta">
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
                <button
                    type="button"
                    className="coherence-issue-target"
                    onClick={() =>
                        !isWholeCut && onJump(issue.segment_index)
                    }
                    disabled={isWholeCut}
                    title={
                        isWholeCut
                            ? "Whole-cut observation — no segment to scroll to"
                            : `Jump to segment ${issue.segment_index + 1}`
                    }
                >
                    {segLabel}
                    {pairSuffix}
                </button>
                <button
                    type="button"
                    className={`coherence-issue-fix${
                        fixed ? " coherence-issue-fix--on" : ""
                    }`}
                    onClick={onToggleFixed}
                    title={
                        fixed
                            ? "Mark as not fixed"
                            : "Mark as fixed (does not persist)"
                    }
                    aria-pressed={fixed}
                >
                    {fixed ? "✓ Fixed" : "Mark fixed"}
                </button>
            </div>
            <p className="coherence-issue-message">{issue.message}</p>
            {issue.suggestion && (
                <p className="coherence-issue-suggestion">
                    → {issue.suggestion}
                </p>
            )}
        </div>
    );
}

interface LadderStep {
    score: number;
    verdict: Verdict;
}

interface LiftLadderProps {
    steps: LadderStep[];
    shippedIndex: number;
    onPassClick?: (passIndex: number) => void;
}

function LiftLadder({ steps, shippedIndex, onPassClick }: LiftLadderProps) {
    // Glyph between consecutive chips reflects the delta direction.
    // Threshold matches the backend's MIN_DELTA default (3); we don't
    // import it at runtime since the backend is the source of truth and
    // this is purely cosmetic.
    const MIN_DELTA = 3;
    const arrow = (delta: number): { glyph: string; cls: string } => {
        if (delta <= -MIN_DELTA) return { glyph: "↘", cls: "down" };
        if (delta >= MIN_DELTA) return { glyph: "↗", cls: "up" };
        return { glyph: "→", cls: "flat" };
    };
    return (
        <span
            className="coherence-lift-ladder"
            title="Critic score per iteration. ↗ improvement, → plateau, ↘ regression. * marks the shipped pass."
        >
            {steps.map((step, i) => {
                const isShipped = i === shippedIndex;
                const passLabel = `Pass ${i + 1}`;
                const stepCls = `coherence-lift-step coherence-lift-step--${step.verdict}${
                    isShipped ? " coherence-lift-step--ship" : ""
                }`;
                const Inner = onPassClick ? (
                    <button
                        type="button"
                        className={stepCls}
                        onClick={() => onPassClick(i)}
                        title={`${passLabel} (${VERDICT_LABEL[step.verdict]}) — click to view this iteration's prompt`}
                    >
                        {step.score}
                        {isShipped && (
                            <span
                                className="coherence-lift-ship-marker"
                                aria-label="shipped"
                            >
                                *
                            </span>
                        )}
                    </button>
                ) : (
                    <span
                        className={stepCls}
                        title={`${passLabel} (${VERDICT_LABEL[step.verdict]})`}
                    >
                        {step.score}
                        {isShipped && (
                            <span
                                className="coherence-lift-ship-marker"
                                aria-label="shipped"
                            >
                                *
                            </span>
                        )}
                    </span>
                );
                if (i === 0) return <span key={i}>{Inner}</span>;
                const prev = steps[i - 1];
                const a = arrow(step.score - prev.score);
                return (
                    <span key={i}>
                        <span
                            className={`coherence-lift-arrow coherence-lift-arrow--${a.cls}`}
                            aria-hidden
                        >
                            {a.glyph}
                        </span>
                        {Inner}
                    </span>
                );
            })}
        </span>
    );
}

interface Props {
    report: CoherenceReport;
    onIssueClick: (segmentIndex: number) => void;
    contextLabel?: string;
    onRecritique?: () => void;
    recritiqueBusy?: boolean;
    recritiqueError?: string | null;
    /**
     * Pre-rework single-pass report. Used by the legacy two-pass lift
     * chip when no ``ladderSteps`` is provided. Ignored when ``ladderSteps``
     * has ≥ 2 entries — the stepped ladder subsumes it.
     */
    previousReport?: CoherenceReport | null;
    onViewReworkPrompt?: () => void;
    /**
     * Stepped lift ladder: one chip per critic iteration. When provided
     * and length ≥ 2, the card renders the ladder instead of the
     * legacy two-pass lift chip. ``shippedPassIndex`` (0-based) marks
     * which step won the regression-guard. ``onPassClick`` opens that
     * pass's Director prompt; falls back to ``onViewReworkPrompt`` when
     * unset.
     */
    ladderSteps?: { score: number; verdict: Verdict }[];
    shippedPassIndex?: number;
    onPassClick?: (passIndex: number) => void;
    /**
     * When ``true``, the Re-critique button is disabled with a tooltip
     * explaining why. Host owns the gate so different builds (assembled,
     * raw_dump, etc.) can apply different rules.
     */
    recritiqueDisabled?: boolean;
    recritiqueDisabledReason?: string;
    /** Optional prefix shown at the top of the card (e.g. "Story coherence"). */
    sectionLabel?: string;
}

export default function CoherenceReportCard({
    report,
    onIssueClick,
    contextLabel,
    onRecritique,
    recritiqueBusy = false,
    recritiqueError = null,
    previousReport = null,
    onViewReworkPrompt,
    ladderSteps,
    shippedPassIndex,
    onPassClick,
    recritiqueDisabled = false,
    recritiqueDisabledReason,
    sectionLabel,
}: Props) {
    const [fixedSet, setFixedSet] = useState<Set<number>>(new Set());
    const toggleFixed = (i: number) => {
        setFixedSet((prev) => {
            const next = new Set(prev);
            if (next.has(i)) next.delete(i);
            else next.add(i);
            return next;
        });
    };

    const lift =
        previousReport !== null ? report.score - previousReport.score : null;
    // When the regression-guard kept pass 1, ``report`` IS pass 1 and
    // ``previousReport`` is the lower-scoring rework. The chip flips:
    // negative-direction means the loop tried and failed.
    const liftDirection: "up" | "down" | "flat" =
        lift === null
            ? "flat"
            : lift > 0
              ? "up"
              : lift < 0
                ? "down"
                : "flat";

    // Phase 5 stepped ladder. Renders one chip per iteration plus the
    // delta glyph between consecutive chips so the editor can read the
    // loop's shape at a glance:
    //   ↗ (up) — improvement worth iterating for
    //   → (flat) — plateau, no movement
    //   ↘ (down) — regression, the new pass got worse
    //   * — terminal marker on the shipped step (latest-wins on ties)
    const showLadder = !!ladderSteps && ladderSteps.length >= 2;

    return (
        <div className="coherence-card">
            {sectionLabel && (
                <div className="coherence-section-label muted">
                    {sectionLabel}
                </div>
            )}
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
                    {showLadder ? (
                        <LiftLadder
                            steps={ladderSteps!}
                            shippedIndex={shippedPassIndex ?? ladderSteps!.length - 1}
                            onPassClick={onPassClick}
                        />
                    ) : (
                        previousReport !== null &&
                        lift !== null && (
                            <span
                                className={`coherence-lift coherence-lift--${liftDirection}`}
                                title={
                                    liftDirection === "down"
                                        ? `Auto-rework regressed: pass 1 scored ${previousReport.score} (${VERDICT_LABEL[previousReport.verdict]}); pass 2 scored ${report.score}. The regression-guard kept whichever score was higher.`
                                        : `Auto-rework: pass 1 scored ${previousReport.score} (${VERDICT_LABEL[previousReport.verdict]}); pass 2 scored ${report.score}.`
                                }
                            >
                                {liftDirection === "down" ? "Regressed " : "Lift "}
                                {previousReport.score} → {report.score}
                                <span className="coherence-lift-delta">
                                    {" "}
                                    ({lift >= 0 ? "+" : ""}
                                    {lift})
                                </span>
                            </span>
                        )
                    )}
                    {contextLabel && (
                        <span className="coherence-context muted">
                            {contextLabel}
                        </span>
                    )}
                </div>
                <div className="coherence-actions">
                    {onRecritique && (
                        <button
                            type="button"
                            className="link-button coherence-recritique"
                            onClick={onRecritique}
                            disabled={recritiqueBusy || recritiqueDisabled}
                            title={
                                recritiqueDisabled
                                    ? (recritiqueDisabledReason ??
                                      "Re-critique unavailable")
                                    : "Re-run the story-critic against this plan"
                            }
                        >
                            {recritiqueBusy ? "Re-critiquing…" : "Re-critique"}
                        </button>
                    )}
                    {onViewReworkPrompt && previousReport !== null && (
                        <button
                            type="button"
                            className="link-button coherence-rework-prompt"
                            onClick={onViewReworkPrompt}
                            title="Open the prompt the Director was given on the rework pass"
                        >
                            View rework prompt
                        </button>
                    )}
                </div>
            </div>

            {report.summary && (
                <p className="coherence-summary">{report.summary}</p>
            )}

            {report.issues.length > 0 ? (
                <div className="coherence-issue-list">
                    {report.issues.map((iss, i) => (
                        <IssueRow
                            key={i}
                            issue={iss}
                            fixed={fixedSet.has(i)}
                            onToggleFixed={() => toggleFixed(i)}
                            onJump={onIssueClick}
                        />
                    ))}
                </div>
            ) : (
                <p className="coherence-issue-list-empty muted">
                    No issues flagged.
                </p>
            )}

            <details className="coherence-subscores-details">
                <summary>Sub-scores</summary>
                <div className="coherence-subscores">
                    <SubScore label="Hook" value={report.hook_strength} />
                    <SubScore label="Arc" value={report.arc_clarity} />
                    <SubScore label="Transitions" value={report.transitions} />
                    <SubScore label="Resolution" value={report.resolution} />
                </div>
            </details>

            {recritiqueError && (
                <p className="coherence-recritique-err">
                    Re-critique failed: {recritiqueError}
                </p>
            )}
        </div>
    );
}
