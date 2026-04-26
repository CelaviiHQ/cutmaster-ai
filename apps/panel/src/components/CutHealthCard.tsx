import CoherenceReportCard from "./CoherenceReportCard";
import type { CoherenceReport } from "../types";

interface Props {
    /**
     * Director validation residue from a best-effort fallback. Empty /
     * null when the model honoured every constraint. Renders the
     * "couldn't fully honour your plan" strip when populated.
     */
    planWarnings?: string[] | null;
    /** Story-critic verdict on the built cut. ``null`` hides the section. */
    coherenceReport: CoherenceReport | null;
    /** Pre-rework report when the auto-rework loop fired. */
    previousReport?: CoherenceReport | null;
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
    coherenceReport,
    previousReport = null,
    contextLabel,
    emptyMessage,
    onIssueClick,
    onRecritique,
    recritiqueBusy = false,
    recritiqueError = null,
    recritiqueDisabled = false,
    recritiqueDisabledReason,
    onViewReworkPrompt,
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
                            The Director couldn't fully honour your plan
                        </strong>
                        <span className="muted">
                            · best-effort fallback after retry exhaustion
                        </span>
                    </div>
                    <ul className="plan-warning-list">
                        {planWarnings!.map((w, i) => (
                            <li key={i}>{w}</li>
                        ))}
                    </ul>
                    <p className="muted plan-warning-foot">
                        Try Regenerate (the model is non-deterministic),
                        pick a longer or clearer hook quote, or relax the
                        target length.
                    </p>
                </div>
            )}

            {hasCritic && (
                <CoherenceReportCard
                    report={coherenceReport!}
                    previousReport={previousReport}
                    contextLabel={contextLabel}
                    onIssueClick={onIssueClick}
                    onRecritique={onRecritique}
                    recritiqueBusy={recritiqueBusy}
                    recritiqueError={recritiqueError}
                    recritiqueDisabled={recritiqueDisabled}
                    recritiqueDisabledReason={recritiqueDisabledReason}
                    onViewReworkPrompt={onViewReworkPrompt}
                    sectionLabel={hasWarnings ? "Story coherence" : undefined}
                />
            )}
        </div>
    );
}
