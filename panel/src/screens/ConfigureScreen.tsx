import { useEffect, useState } from "react";
import { api } from "../api";
import type {
    PresetKey,
    PresetRecommendation,
    StoryAnalysis,
    UserSettings,
} from "../types";

interface Props {
    runId: string;
    preset: PresetKey;
    onPresetChange: (p: PresetKey) => void;
    settings: UserSettings;
    onSettingsChange: (s: UserSettings) => void;
    onBack: () => void;
    onNext: () => void;
}

export default function ConfigureScreen({
    runId,
    preset,
    onPresetChange,
    settings,
    onSettingsChange,
    onBack,
    onNext,
}: Props) {
    const [rec, setRec] = useState<PresetRecommendation | null>(null);
    const [analysis, setAnalysis] = useState<StoryAnalysis | null>(null);
    const [loading, setLoading] = useState(true);
    const [err, setErr] = useState<string | null>(null);

    // If preset is 'auto', run detect first.
    useEffect(() => {
        let cancelled = false;
        (async () => {
            setLoading(true);
            setErr(null);
            try {
                let effective = preset;
                if (preset === "auto") {
                    const r = await api.detectPreset(runId);
                    if (cancelled) return;
                    setRec(r);
                    effective = r.preset;
                    onPresetChange(r.preset);
                }
                const a = await api.analyzeThemes(runId, effective);
                if (cancelled) return;
                setAnalysis(a);
                // Pre-check all theme candidates by default
                onSettingsChange({ ...settings, themes: a.theme_candidates });
            } catch (e) {
                if (!cancelled) setErr(String(e));
            } finally {
                if (!cancelled) setLoading(false);
            }
        })();
        return () => {
            cancelled = true;
        };
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [runId]);

    const toggleTheme = (t: string) => {
        const has = settings.themes.includes(t);
        onSettingsChange({
            ...settings,
            themes: has
                ? settings.themes.filter((x) => x !== t)
                : [...settings.themes, t],
        });
    };

    if (loading) {
        return (
            <div className="card">
                <p className="muted">Analysing themes… (~5–10 s)</p>
            </div>
        );
    }

    return (
        <div>
            {rec && (
                <div className="card">
                    <h2>Auto-detect result</h2>
                    <p>
                        Recommended preset:&nbsp;
                        <strong>{rec.preset}</strong>
                        &nbsp;(confidence {Math.round(rec.confidence * 100)}%)
                    </p>
                    <p className="muted">{rec.reasoning}</p>
                    <p className="muted" style={{ marginTop: 8 }}>
                        Override below if you disagree.
                    </p>
                </div>
            )}

            <div className="card">
                <h2>Preset</h2>
                <select
                    value={preset}
                    onChange={(e) => onPresetChange(e.target.value as PresetKey)}
                >
                    {[
                        "vlog",
                        "product_demo",
                        "wedding",
                        "interview",
                        "tutorial",
                        "podcast",
                        "reaction",
                    ].map((p) => (
                        <option key={p} value={p}>
                            {p}
                        </option>
                    ))}
                </select>
            </div>

            {analysis && (
                <>
                    <div className="card">
                        <h2>Chapters detected</h2>
                        {analysis.chapters.map((c, i) => (
                            <div key={i} className="seg">
                                <span className="seg-time">
                                    {c.start_s.toFixed(1)}s
                                </span>
                                <span className="seg-time">
                                    {(c.end_s - c.start_s).toFixed(1)}s
                                </span>
                                <span>{c.title}</span>
                            </div>
                        ))}
                    </div>

                    <div className="card">
                        <h2>Hook candidates</h2>
                        <p className="muted">The Director will pick one of these (or something close to it) as the opening beat.</p>
                        {analysis.hook_candidates.map((h, i) => (
                            <div key={i} className="seg" title={h.text}>
                                <span className="seg-time">
                                    {h.start_s.toFixed(1)}s
                                </span>
                                <span className="seg-time">
                                    {(h.engagement_score * 100).toFixed(0)}%
                                </span>
                                <span className="seg-reason">{h.text}</span>
                            </div>
                        ))}
                    </div>

                    <div className="card">
                        <h2>Prioritize themes</h2>
                        <p className="muted">Unchecked themes are less likely to be included.</p>
                        {analysis.theme_candidates.map((t) => (
                            <span
                                key={t}
                                className={`chip ${settings.themes.includes(t) ? "on" : ""}`}
                                onClick={() => toggleTheme(t)}
                            >
                                {t}
                            </span>
                        ))}
                    </div>
                </>
            )}

            <div className="card">
                <h2>Target length (optional)</h2>
                <input
                    type="number"
                    min={15}
                    step={15}
                    placeholder="e.g. 90 for a 90-second cut — leave blank to keep all good takes"
                    value={settings.target_length_s ?? ""}
                    onChange={(e) =>
                        onSettingsChange({
                            ...settings,
                            target_length_s: e.target.value
                                ? Number(e.target.value)
                                : null,
                        })
                    }
                />
            </div>

            {err && <div className="error-box">{err}</div>}

            <div className="row between">
                <button className="secondary" onClick={onBack}>← Back</button>
                <button onClick={onNext}>Build plan →</button>
            </div>
        </div>
    );
}
