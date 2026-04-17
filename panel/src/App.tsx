import { useEffect, useState } from "react";
import type { PresetKey, UserSettings } from "./types";
import PresetPickScreen from "./screens/PresetPickScreen";
import AnalyzeScreen from "./screens/AnalyzeScreen";
import ConfigureScreen from "./screens/ConfigureScreen";
import ReviewScreen from "./screens/ReviewScreen";
import { api } from "./api";
import { clearSession, loadSession, saveSession } from "./persist";

type Step = "preset" | "analyze" | "configure" | "review";

const STEP_LABEL: Record<Step, string> = {
    preset: "Preset",
    analyze: "Analyze",
    configure: "Configure",
    review: "Review",
};

const STEPS: Step[] = ["preset", "analyze", "configure", "review"];

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
    const [step, setStep] = useState<Step>("preset");
    const [timelineName, setTimelineName] = useState("Timeline 1");
    const [preset, setPreset] = useState<PresetKey>("auto");
    const [runId, setRunId] = useState<string | null>(null);
    const [userSettings, setUserSettings] = useState<UserSettings>({
        target_length_s: null,
        themes: [],
    });
    const [backendOk, setBackendOk] = useState<boolean | null>(null);
    const [resume, setResume] = useState<ResumeInfo | null>(null);
    const [resumeChecked, setResumeChecked] = useState(false);

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

            const session = loadSession();
            if (!session) {
                setResumeChecked(true);
                return;
            }
            try {
                const state = await api.getState(session.runId);
                const resumeAt: Step = state.plan
                    ? "review"
                    : (state.scrubbed && state.scrubbed.length > 0)
                        ? "configure"
                        : "analyze";
                setResume({
                    runId: session.runId,
                    preset: session.preset as PresetKey,
                    timelineName: session.timelineName,
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

    const reset = () => {
        clearSession();
        setStep("preset");
        setRunId(null);
        setResume(null);
        setUserSettings({ target_length_s: null, themes: [] });
    };

    const acceptResume = () => {
        if (!resume) return;
        setRunId(resume.runId);
        setPreset(resume.preset);
        setTimelineName(resume.timelineName);
        setStep(resume.resumeAt);
        setResume(null);
    };

    const currentIndex = STEPS.indexOf(step);

    return (
        <div className="app">
            <header className="hdr">
                <h1>
                    CutMaster AI <span className="sub">— phase 6</span>
                </h1>
                <div className="sub">
                    {backendOk === null
                        ? "…"
                        : backendOk
                            ? "backend: connected"
                            : "backend: unreachable — start celavii-resolve-panel"}
                </div>
            </header>

            {resumeChecked && resume && step === "preset" && (
                <div className="card" style={{ borderColor: "var(--accent)" }}>
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
                        <strong>{STEP_LABEL[resume.resumeAt]}</strong>.
                    </p>
                    <div className="row">
                        <button onClick={acceptResume}>Resume →</button>
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
                            {i + 1}. {STEP_LABEL[s]}
                        </div>
                    );
                })}
            </div>

            {step === "preset" && (
                <PresetPickScreen
                    timelineName={timelineName}
                    onTimelineChange={setTimelineName}
                    preset={preset}
                    onPresetChange={setPreset}
                    onNext={async () => {
                        const r = await api.analyze(timelineName, preset);
                        setRunId(r.run_id);
                        saveSession({
                            runId: r.run_id,
                            preset,
                            timelineName,
                        });
                        setStep("analyze");
                    }}
                />
            )}

            {step === "analyze" && runId && (
                <AnalyzeScreen
                    runId={runId}
                    onDone={() => setStep("configure")}
                    onReset={reset}
                />
            )}

            {step === "configure" && runId && (
                <ConfigureScreen
                    runId={runId}
                    preset={preset}
                    onPresetChange={(p) => {
                        setPreset(p);
                        saveSession({ runId, preset: p, timelineName });
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
                    onBack={() => setStep("configure")}
                    onReset={reset}
                />
            )}
        </div>
    );
}
