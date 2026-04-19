import { useEffect, useState } from "react";
import {
    HelpCircle,
    RotateCcw,
    Save,
    Scissors,
} from "lucide-react";
import type { PresetKey, SttProviderKey, UserSettings } from "./types";
import PresetPickScreen from "./screens/PresetPickScreen";
import AnalyzeScreen from "./screens/AnalyzeScreen";
import ConfigureScreen from "./screens/ConfigureScreen";
import ReviewScreen from "./screens/ReviewScreen";
import TokensGate from "./screens/TokensGate";
import { api } from "./api";
import {
    clearSession,
    formatRelativeTime,
    loadRunId,
    loadSavedAt,
    saveRunPointer,
    touchSavedAt,
} from "./persist";

// v3-0 gate page — bypass the main flow when `?gate=tokens` is in the URL.
const GATE_MODE =
    typeof window !== "undefined" &&
    new URLSearchParams(window.location.search).get("gate") === "tokens";

type Step = "preset" | "analyze" | "configure" | "review";

const STEPS: Step[] = ["preset", "analyze", "configure", "review"];

// Base step labels — context suffix (e.g. "clip_hunter", "3 clips") is appended
// at render time based on the current state machine.
const STEP_BASE: Record<Step, string> = {
    preset: "Preset",
    analyze: "Analyze",
    configure: "Configure",
    review: "Review",
};

interface ResumeInfo {
    runId: string;
    preset: PresetKey;
    timelineName: string;
    resumeAt: Step;
    status: string;
    hasPlan: boolean;
    hasExecute: boolean;
}

export default function App() {
    if (GATE_MODE) return <TokensGate />;

    const [step, setStep] = useState<Step>("preset");
    const [timelineName, setTimelineName] = useState("Timeline 1");
    const [preset, setPreset] = useState<PresetKey>("auto");
    const [runId, setRunId] = useState<string | null>(null);
    const [userSettings, setUserSettings] = useState<UserSettings>({
        target_length_s: null,
        themes: [],
        exclude_categories: [],
        custom_focus: null,
        format: "horizontal",
        captions_enabled: false,
        safe_zones_enabled: false,
        timeline_mode: "raw_dump",
        reorder_allowed: true,
        takes_already_scrubbed: false,
        num_clips: 3,
    });
    const [backendOk, setBackendOk] = useState<boolean | null>(null);
    const [resume, setResume] = useState<ResumeInfo | null>(null);
    const [resumeChecked, setResumeChecked] = useState(false);
    const [perClipStt, setPerClipStt] = useState(false);
    const [expectedSpeakers, setExpectedSpeakers] = useState<number | null>(null);
    const [sttProvider, setSttProvider] = useState<SttProviderKey | null>(null);
    const [showShortcuts, setShowShortcuts] = useState(false);
    // Live per-stage context for the step indicator.
    const [analyzeDurationS, setAnalyzeDurationS] = useState<number | null>(null);
    const [reviewClipCount, setReviewClipCount] = useState<number | null>(null);
    // v3-5.3 Saved chip — relative time, refreshed every 30s.
    const [savedAt, setSavedAt] = useState<number | null>(null);
    const [tick, setTick] = useState(0);
    // User-picked name for the built timeline — lives in the header so it's
    // editable from any step once a run exists. Passed into ReviewScreen at
    // build time.
    const [cutName, setCutName] = useState("");

    // Ping + resume check on mount
    useEffect(() => {
        (async () => {
            try {
                await api.ping();
                setBackendOk(true);
            } catch {
                setBackendOk(false);
                setResumeChecked(true);
                return;
            }

            const storedRunId = loadRunId();
            if (!storedRunId) {
                setResumeChecked(true);
                return;
            }
            setSavedAt(loadSavedAt());
            try {
                // Hydrate preset + timeline_name from the server — the
                // browser only stores a run_id pointer. Anything derivable
                // from /state stays on the server so the two can't diverge.
                const state = await api.getState(storedRunId);
                const resumeAt: Step = state.plan
                    ? "review"
                    : (state.scrubbed && state.scrubbed.length > 0)
                        ? "configure"
                        : "analyze";
                setResume({
                    runId: storedRunId,
                    preset: state.preset as PresetKey,
                    timelineName: state.timeline_name,
                    resumeAt,
                    status: state.status,
                    hasPlan: Boolean(state.plan),
                    hasExecute: Boolean((state as unknown as { execute?: unknown }).execute),
                });
            } catch {
                clearSession();
            } finally {
                setResumeChecked(true);
            }
        })();
    }, []);

    // Auto-save: whenever userSettings or cutName change on an active run,
    // debounce-bump savedAt so the header chip reflects that the in-memory
    // state matches the run_id pointer in localStorage. The server-side
    // state is the source of truth for those settings (persisted at
    // /build-plan time), so this is just a UX signal.
    useEffect(() => {
        if (!runId) return;
        const t = window.setTimeout(() => {
            touchSavedAt();
            setSavedAt(Date.now());
        }, 750);
        return () => window.clearTimeout(t);
    }, [runId, userSettings, cutName]);

    // v3-5.3 — refresh relative time every 30s; pause when tab hidden.
    useEffect(() => {
        const onVis = () => {
            if (document.visibilityState === "visible") setTick((t) => t + 1);
        };
        document.addEventListener("visibilitychange", onVis);
        const id = window.setInterval(() => {
            if (document.visibilityState === "visible") setTick((t) => t + 1);
        }, 30_000);
        return () => {
            window.clearInterval(id);
            document.removeEventListener("visibilitychange", onVis);
        };
    }, []);

    // v3-5.2 — global keyboard shortcuts.
    // Cmd/Ctrl+Enter → click the "primary" button (marked data-hotkey="primary").
    // Cmd/Ctrl+Backspace → "back" (data-hotkey="back").
    // Esc → dismiss: close the shortcuts popover first, else close the error box.
    useEffect(() => {
        const onKey = (e: KeyboardEvent) => {
            const mod = e.metaKey || e.ctrlKey;
            if (mod && e.key === "Enter") {
                const btn = document.querySelector<HTMLButtonElement>(
                    'button[data-hotkey="primary"]:not(:disabled)',
                );
                if (btn) {
                    e.preventDefault();
                    btn.click();
                }
            } else if (mod && e.key === "Backspace") {
                const btn = document.querySelector<HTMLButtonElement>(
                    'button[data-hotkey="back"]:not(:disabled)',
                );
                if (btn) {
                    e.preventDefault();
                    btn.click();
                }
            } else if (e.key === "Escape") {
                if (showShortcuts) {
                    setShowShortcuts(false);
                    return;
                }
                const errBox = document.querySelector<HTMLElement>(".error-box");
                if (errBox) {
                    errBox.style.display = "none";
                }
            } else if (e.key === "?" && !mod && !isInputTarget(e.target)) {
                setShowShortcuts((v) => !v);
            }
        };
        document.addEventListener("keydown", onKey);
        return () => document.removeEventListener("keydown", onKey);
    }, [showShortcuts]);

    const reset = () => {
        clearSession();
        setStep("preset");
        setRunId(null);
        setResume(null);
        setSavedAt(null);
        setAnalyzeDurationS(null);
        setReviewClipCount(null);
        setCutName("");
        setUserSettings({
            target_length_s: null,
            themes: [],
            exclude_categories: [],
            custom_focus: null,
            format: "horizontal",
            captions_enabled: false,
            safe_zones_enabled: false,
            timeline_mode: "raw_dump",
            reorder_allowed: true,
            takes_already_scrubbed: false,
            num_clips: 3,
        });
    };

    // Save & Exit — persist the current session and return to the home
    // (preset) screen. Reloading the panel later will surface the resume
    // banner so the user can pick up exactly where they left off.
    const saveAndExit = () => {
        if (runId) {
            saveRunPointer(runId);
            setSavedAt(Date.now());
        }
        window.location.reload();
    };

    // Restart — clear persisted state and drop back to the home screen.
    // Destructive, so confirm first.
    const restart = () => {
        const ok = window.confirm(
            "Restart? This clears the saved session and drops you back at the preset screen.",
        );
        if (!ok) return;
        reset();
    };

    const hasActiveRun = runId !== null || step !== "preset";

    const acceptResume = () => {
        if (!resume) return;
        setRunId(resume.runId);
        setPreset(resume.preset);
        setTimelineName(resume.timelineName);
        setStep(resume.resumeAt);
        setResume(null);
    };

    /**
     * Reopen a run picked from the RunsDrawer. Hydrates from /state so
     * preset, timeline_name, user_settings, review_state all survive a
     * browser reload. Persists the new pointer to localStorage so a
     * subsequent reload stays on this run.
     */
    const reopenRun = async (targetRunId: string) => {
        try {
            const state = await api.getState(targetRunId);
            const resumeAt: Step = state.plan
                ? "review"
                : (state.scrubbed && state.scrubbed.length > 0)
                    ? "configure"
                    : "analyze";
            setRunId(targetRunId);
            setPreset(state.preset as PresetKey);
            setTimelineName(state.timeline_name);
            // Hydrate Configure choices if the run was persisted after
            // /build-plan. Older runs without this top-level mirror just
            // keep the panel defaults.
            if (state.user_settings) {
                setUserSettings((prev) => ({ ...prev, ...state.user_settings! }));
            }
            // Restore cut-name from the last /execute if we have one; the
            // Review screen surfaces execute_history as its own panel.
            setCutName(state.review_state?.custom_name ?? "");
            saveRunPointer(targetRunId);
            setSavedAt(Date.now());
            setResume(null);
            setStep(resumeAt);
        } catch (e) {
            window.alert(`Couldn't reopen run: ${String(e)}`);
        }
    };

    const currentIndex = STEPS.indexOf(step);

    // v3-5.4 — per-step context labels.
    const stepLabel = (s: Step, idx: number): string => {
        const base = `${idx + 1}. ${STEP_BASE[s]}`;
        switch (s) {
            case "preset":
                return preset !== "auto" && idx <= currentIndex
                    ? `${idx + 1}. ${preset}`
                    : base;
            case "analyze":
                return analyzeDurationS !== null && idx < currentIndex
                    ? `${idx + 1}. Transcribed · ${Math.round(analyzeDurationS)}s`
                    : base;
            case "review":
                return reviewClipCount !== null && s === step
                    ? `${idx + 1}. Reviewing · ${reviewClipCount} clip${reviewClipCount === 1 ? "" : "s"}`
                    : base;
            default:
                return base;
        }
    };

    // Suppress unused-var warnings while keeping tick in the dep graph.
    void tick;

    return (
        <div className="app">
            <header className="hdr">
                <div className="hdr-brand">
                    <h1 className="hdr-title">
                        CutMaster <span className="hdr-title-ai">AI</span>
                    </h1>
                    <span className="hdr-version" title={`v${__APP_VERSION__}`}>
                        v{__APP_VERSION__}
                    </span>
                </div>

                {hasActiveRun && (
                    <label className="hdr-cut" title="Name for the new timeline (blank = auto)">
                        <Scissors size={14} className="hdr-cut-icon" aria-hidden />
                        <input
                            type="text"
                            className="hdr-cut-input"
                            value={cutName}
                            placeholder={`${timelineName}_AI_Cut`}
                            onChange={(e) => setCutName(e.target.value)}
                            aria-label="Cut name"
                        />
                    </label>
                )}

                <div className="hdr-status">
                    {savedAt !== null && (
                        <span
                            className="hdr-saved"
                            title={`Session saved ${new Date(savedAt).toLocaleString()}`}
                        >
                            Saved {formatRelativeTime(savedAt)}
                        </span>
                    )}
                    <span
                        className={`hdr-dot ${
                            backendOk === false
                                ? "err"
                                : backendOk
                                    ? "ok"
                                    : "pending"
                        }`}
                        role="status"
                        aria-label={
                            backendOk === null
                                ? "backend: pinging"
                                : backendOk
                                    ? "backend: connected"
                                    : "backend: unreachable"
                        }
                        title={
                            backendOk === null
                                ? "pinging backend…"
                                : backendOk
                                    ? "backend: connected"
                                    : "backend: unreachable — start celavii-resolve-panel"
                        }
                    />
                </div>

                <div className="hdr-actions">
                    {hasActiveRun && (
                        <>
                            <button
                                className="btn-ghost hdr-action"
                                onClick={saveAndExit}
                                title="Save session and return to the home screen"
                            >
                                <Save size={14} aria-hidden />
                                <span>Save</span>
                            </button>
                            <button
                                className="btn-ghost hdr-action hdr-action--danger"
                                onClick={restart}
                                title="Clear session and return to the home screen"
                            >
                                <RotateCcw size={14} aria-hidden />
                                <span>Restart</span>
                            </button>
                        </>
                    )}
                    <button
                        className="btn-ghost hdr-icon-btn"
                        onClick={() => setShowShortcuts((v) => !v)}
                        aria-label="Keyboard shortcuts"
                        title="Keyboard shortcuts"
                    >
                        <HelpCircle size={16} aria-hidden />
                    </button>
                </div>
            </header>

            {showShortcuts && (
                <div className="shortcuts-popover" role="dialog" aria-label="Keyboard shortcuts">
                    <h3>Keyboard shortcuts</h3>
                    <dl>
                        <dt>
                            <kbd>⌘</kbd> <kbd>Enter</kbd>
                        </dt>
                        <dd>Continue / Build (primary action)</dd>
                        <dt>
                            <kbd>⌘</kbd> <kbd>Backspace</kbd>
                        </dt>
                        <dd>Back to previous step</dd>
                        <dt>
                            <kbd>Esc</kbd>
                        </dt>
                        <dd>Close this popover or dismiss an error</dd>
                        <dt>
                            <kbd>?</kbd>
                        </dt>
                        <dd>Toggle this popover</dd>
                    </dl>
                    <p className="muted" style={{ fontSize: "var(--fs-2)", marginTop: "var(--s-3)" }}>
                        On Windows / Linux use <kbd>Ctrl</kbd> instead of <kbd>⌘</kbd>.
                    </p>
                </div>
            )}

            {resumeChecked && resume && step === "preset" && (
                <div className="card" style={{ borderColor: "var(--accent-blue)" }}>
                    <h2>Resume last run?</h2>
                    <p>
                        Timeline <code>{resume.timelineName}</code>,
                        preset <code>{resume.preset}</code>,
                        status <code>{resume.status}</code>
                        {resume.hasPlan && " · plan ready"}
                        {resume.hasExecute && " · cut already built"}
                    </p>
                    <p className="muted">
                        Skips re-analyze + re-Director. Jumps straight to{" "}
                        <strong>{STEP_BASE[resume.resumeAt]}</strong>.
                    </p>
                    <div className="row">
                        <button onClick={acceptResume} data-hotkey="primary">Resume →</button>
                        <button className="secondary" onClick={() => { clearSession(); setResume(null); }}>
                            Start fresh
                        </button>
                    </div>
                </div>
            )}

            <div className="steps">
                {STEPS.map((s, i) => {
                    const cls =
                        i < currentIndex ? "done" : i === currentIndex ? "active" : "";
                    return (
                        <div key={s} className={`step ${cls}`}>
                            {stepLabel(s, i)}
                        </div>
                    );
                })}
            </div>

            {step === "preset" && (
                <PresetPickScreen
                    timelineName={timelineName}
                    onTimelineChange={setTimelineName}
                    preset={preset}
                    onPresetChange={(p) => {
                        setPreset(p);
                        // Tightener is Assembled-only (see preset_mode_compatible).
                        // Force the mode when the user picks it so the Build
                        // button doesn't trip the backend compat guard.
                        if (p === "tightener" && userSettings.timeline_mode !== "assembled") {
                            setUserSettings({ ...userSettings, timeline_mode: "assembled" });
                        }
                    }}
                    timelineMode={userSettings.timeline_mode ?? "raw_dump"}
                    onTimelineModeChange={(m) =>
                        setUserSettings({ ...userSettings, timeline_mode: m })
                    }
                    perClipStt={perClipStt}
                    onPerClipSttChange={setPerClipStt}
                    expectedSpeakers={expectedSpeakers}
                    onExpectedSpeakersChange={setExpectedSpeakers}
                    sttProvider={sttProvider}
                    onSttProviderChange={setSttProvider}
                    onNext={async () => {
                        const r = await api.analyze(timelineName, preset, {
                            perClipStt,
                            expectedSpeakers,
                            sttProvider,
                        });
                        setRunId(r.run_id);
                        saveRunPointer(r.run_id);
                        setSavedAt(Date.now());
                        setStep("analyze");
                    }}
                    onReopenRun={reopenRun}
                />
            )}

            {step === "analyze" && runId && (
                <AnalyzeScreen
                    runId={runId}
                    onDone={(durationS) => {
                        if (typeof durationS === "number") setAnalyzeDurationS(durationS);
                        setStep("configure");
                    }}
                    onReset={reset}
                    onComplete={() => {
                        touchSavedAt();
                        setSavedAt(Date.now());
                    }}
                />
            )}

            {step === "configure" && runId && (
                <ConfigureScreen
                    runId={runId}
                    preset={preset}
                    onPresetChange={(p) => {
                        setPreset(p);
                        // Preset is served by /state — just touch savedAt.
                        touchSavedAt();
                        setSavedAt(Date.now());
                    }}
                    settings={userSettings}
                    onSettingsChange={setUserSettings}
                    onBack={() => setStep("analyze")}
                    onNext={() => setStep("review")}
                />
            )}

            {step === "review" && runId && (
                <ReviewScreen
                    runId={runId}
                    preset={preset}
                    settings={userSettings}
                    onSettingsChange={setUserSettings}
                    onBack={() => setStep("configure")}
                    onReset={reset}
                    onClipCount={setReviewClipCount}
                    timelineName={timelineName}
                    cutName={cutName}
                    onBuildSuccess={() => {
                        touchSavedAt();
                        setSavedAt(Date.now());
                    }}
                    onCutNameChange={setCutName}
                />
            )}
        </div>
    );
}

function isInputTarget(target: EventTarget | null): boolean {
    const el = target as HTMLElement | null;
    if (!el) return false;
    if (el.isContentEditable) return true;
    const tag = el.tagName;
    return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT";
}
