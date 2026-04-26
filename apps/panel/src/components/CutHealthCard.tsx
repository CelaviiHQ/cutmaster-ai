import CoherenceReportCard from "./CoherenceReportCard";
import type {
    CoherenceIssue,
    CoherenceReport,
    PlanWarning,
    PlanWarningActionKind,
    Verdict,
} from "../types";

interface Props {
    /**
     * Director validation residue from a best-effort fallback. Empty /
     * null when the model honoured every constraint. Renders the
     * "couldn't fully honour your plan" strip when populated.
     */
    planWarnings?: PlanWarning[] | null;
    /**
     * Inline-action handler. Fires when the editor clicks a per-warning
     * action button (e.g. "Pick a different hook moment" → host
     * navigates to Configure with the hook field focused). Host owns
     * navigation + regenerate plumbing; the card just emits intent.
     */
    onPlanWarningAction?: (kind: PlanWarningActionKind, warning: PlanWarning) => void;
    /** Story-critic verdict on the built cut. ``null`` hides the section. */
    coherenceReport: CoherenceReport | null;
    /** Pre-rework report when the auto-rework loop fired. */
    previousReport?: CoherenceReport | null;
    /** Stepped lift ladder: one entry per critic iteration. */
    ladderSteps?: { score: number; verdict: Verdict }[];
    shippedPassIndex?: number;
    onPassClick?: (passIndex: number) => void;
    /** Optional context (e.g. "Candidate 2 of 5"). */
    contextLabel?: string;
    /** Empty-state copy when ``coherenceReport`` is null. */
    emptyMessage?: string;
    onIssueClick: (segmentIndex: number) => void;
    onRecritique?: () => void;
    recritiqueBusy?: boolean;
    recritiqueError?: string | null;
    recritiqueDisabled?: boolean;
    recritiqueDisabledReason?: string;
    onViewReworkPrompt?: () => void;
    onRegenerateWithFeedback?: (unfixedIssues: CoherenceIssue[]) => void;
    regenerateWithFeedbackBusy?: boolean;
}

/**
 * Single "Cut health" card. Subsumes the Director validation strip
 * (which used to render inside the cut card) and the story-critic
 * verdict so both surfaces speak with one voice.
 *
 * Renders nothing when there is no signal to show — empty critic +
 * empty plan warnings means the cut is healthy and silence is correct.
 */
export default function CutHealthCard({
    planWarnings,
    onPlanWarningAction,
    coherenceReport,
    previousReport = null,
    ladderSteps,
    shippedPassIndex,
    onPassClick,
    contextLabel,
    emptyMessage,
    onIssueClick,
    onRecritique,
    recritiqueBusy = false,
    recritiqueError = null,
    recritiqueDisabled = false,
    recritiqueDisabledReason,
    onViewReworkPrompt,
    onRegenerateWithFeedback,
    regenerateWithFeedbackBusy = false,
}: Props) {
    const hasWarnings = (planWarnings?.length ?? 0) > 0;
    const hasCritic = coherenceReport !== null;
    if (!hasWarnings && !hasCritic) {
        if (emptyMessage) {
            return (
                <div className="card cut-health-card cut-health-card--empty">
                    <h2 className="cut-health-title">Cut health</h2>
                    <p className="muted">{emptyMessage}</p>
                </div>
            );
        }
        return null;
    }

    return (
        <div className="card cut-health-card">
            <h2 className="cut-health-title">Cut health</h2>

            {hasWarnings && (
                <div className="cut-health-row plan-warning" role="alert">
                    <div className="plan-warning-head">
                        <span aria-hidden>⚠</span>
                        <strong>
                            {planWarnings!.length === 1
                                ? "One thing to know about this cut"
                                : `${planWarnings!.length} things to know about this cut`}
                        </strong>
                    </div>
                    <ul className="plan-warning-list">
                        {planWarnings!.map((w, i) => (
                            <li key={i} className="plan-warning-item">
                                <div className="plan-warning-item-head">
                                    <span className="plan-warning-item-title">
                                        {w.title}
                                    </span>
                                    {w.action && onPlanWarningAction && (
                                        <button
                                            type="button"
                                            className="plan-warning-action"
                                            onClick={() =>
                                                onPlanWarningAction(w.action!.kind, w)
                                            }
                                            title={w.raw}
                                        >
                                            {w.action.label} →
                                        </button>
                                    )}
                                </div>
                                <p className="plan-warning-item-detail">
                                    {w.detail}
                                </p>
                            </li>
                        ))}
                    </ul>
                </div>
            )}

            {hasCritic && (
                <CoherenceReportCard
                    report={coherenceReport!}
                    previousReport={previousReport}
                    ladderSteps={ladderSteps}
                    shippedPassIndex={shippedPassIndex}
                    onPassClick={onPassClick}
                    contextLabel={contextLabel}
                    onIssueClick={onIssueClick}
                    onRecritique={onRecritique}
                    recritiqueBusy={recritiqueBusy}
                    recritiqueError={recritiqueError}
                    recritiqueDisabled={recritiqueDisabled}
                    recritiqueDisabledReason={recritiqueDisabledReason}
                    onViewReworkPrompt={onViewReworkPrompt}
                    onRegenerateWithFeedback={onRegenerateWithFeedback}
                    regenerateWithFeedbackBusy={regenerateWithFeedbackBusy}
                    sectionLabel={hasWarnings ? "Story coherence" : undefined}
                />
            )}
        </div>
    );
}
