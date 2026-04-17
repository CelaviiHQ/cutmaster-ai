import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { SourceAspectInfo } from "../api";
import type {
    FormatKey,
    FormatSpec,
    PresetBundle,
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

function defaultExcludeKeys(bundle: PresetBundle | undefined): string[] {
    if (!bundle) return [];
    return bundle.exclude_categories
        .filter((c) => c.checked_by_default)
        .map((c) => c.key);
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
    const [bundles, setBundles] = useState<PresetBundle[] | null>(null);
    const [formats, setFormats] = useState<FormatSpec[] | null>(null);
    const [source, setSource] = useState<SourceAspectInfo | null>(null);
    const [formatAutoSelected, setFormatAutoSelected] = useState(false);
    const [loading, setLoading] = useState(true);
    const [err, setErr] = useState<string | null>(null);

    // One-time exclude-defaults init per preset. Resume-flows (Back from
    // Review, then return) must NOT clobber the user's manual edits, so we
    // remember the last preset key we initialized for.
    const initializedFor = useRef<string | null>(null);

    // Fetch preset bundles + format specs + source-aspect once.
    useEffect(() => {
        let cancelled = false;
        api.listPresets()
            .then((r) => !cancelled && setBundles(r.presets))
            .catch(() => !cancelled && setBundles([]));
        api.listFormats()
            .then((r) => !cancelled && setFormats(r.formats))
            .catch(() => !cancelled && setFormats([]));
        api.sourceAspect(runId)
            .then((info) => {
                if (cancelled) return;
                setSource(info);
                // Auto-select format when the source already matches a non-horizontal
                // target — e.g. a 9:16 phone shoot should default to Short.
                if (
                    info.recommended_format !== "horizontal" &&
                    !formatAutoSelected
                ) {
                    onSettingsChange({
                        ...settings,
                        format: info.recommended_format,
                    });
                    setFormatAutoSelected(true);
                }
            })
            .catch(() => {
                // Non-fatal — aspect detection is best-effort.
            });
        return () => {
            cancelled = true;
        };
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [runId]);

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

    const currentBundle = bundles?.find((b) => b.key === preset);

    // Seed exclude-category defaults when the preset is first known.
    // custom_focus is deliberately not touched here — see handlePresetChange.
    useEffect(() => {
        if (!currentBundle) return;
        if (initializedFor.current === preset) return;
        onSettingsChange({
            ...settings,
            exclude_categories: defaultExcludeKeys(currentBundle),
        });
        initializedFor.current = preset;
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [currentBundle?.key]);

    const toggleTheme = (t: string) => {
        const has = settings.themes.includes(t);
        onSettingsChange({
            ...settings,
            themes: has
                ? settings.themes.filter((x) => x !== t)
                : [...settings.themes, t],
        });
    };

    const toggleExclude = (key: string) => {
        const current = settings.exclude_categories ?? [];
        const has = current.includes(key);
        onSettingsChange({
            ...settings,
            exclude_categories: has
                ? current.filter((x) => x !== key)
                : [...current, key],
        });
    };

    const handlePresetChange = (next: PresetKey) => {
        // Swap exclude list to the new preset's defaults; preserve custom_focus
        // verbatim (the string may apply equally well to any preset).
        onPresetChange(next);
        const nextBundle = bundles?.find((b) => b.key === next);
        if (nextBundle) {
            onSettingsChange({
                ...settings,
                exclude_categories: defaultExcludeKeys(nextBundle),
            });
            initializedFor.current = next;
        }
    };

    if (loading) {
        return (
            <div className="card">
                <p className="muted">Analysing themes… (~5–10 s)</p>
            </div>
        );
    }

    const excludeCats = currentBundle?.exclude_categories ?? [];
    const selectedExcludes = settings.exclude_categories ?? [];
    const focusPlaceholder =
        currentBundle?.default_custom_focus_placeholder ??
        "e.g. emphasise the key moment";

    const currentFormat: FormatKey = settings.format ?? "horizontal";
    const currentFormatSpec = formats?.find((f) => f.key === currentFormat);
    const lengthCap = currentFormatSpec?.max_duration_s ?? null;
    const sourceMatchesTarget =
        source && source.recommended_format === currentFormat;

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
                    onChange={(e) => handlePresetChange(e.target.value as PresetKey)}
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

            {excludeCats.length > 0 && (
                <div className="card">
                    <h2>Content to exclude</h2>
                    <p className="muted">
                        Tick categories the Director should drop. Defaults come
                        from the {currentBundle?.label} preset — adjust freely.
                    </p>
                    <div className="exclude-grid">
                        {excludeCats.map((c) => {
                            const checked = selectedExcludes.includes(c.key);
                            return (
                                <label key={c.key} className="exclude-item">
                                    <input
                                        type="checkbox"
                                        checked={checked}
                                        onChange={() => toggleExclude(c.key)}
                                    />
                                    <span className="exclude-body">
                                        <span className="exclude-label">{c.label}</span>
                                        <br />
                                        <span className="exclude-desc">{c.description}</span>
                                    </span>
                                </label>
                            );
                        })}
                    </div>
                </div>
            )}

            <div className="card">
                <h2>Custom focus (optional)</h2>
                <p className="muted">
                    One short instruction the Director treats as a soft
                    priority. Kept across preset changes.
                </p>
                <input
                    type="text"
                    placeholder={focusPlaceholder}
                    value={settings.custom_focus ?? ""}
                    onChange={(e) =>
                        onSettingsChange({
                            ...settings,
                            custom_focus: e.target.value || null,
                        })
                    }
                />
            </div>

            {formats && formats.length > 0 && (
                <div className="card">
                    <h2>Output format</h2>
                    {source && (
                        <p className="muted">
                            Source timeline: <code>{source.width}×{source.height}</code>
                            &nbsp;(aspect {source.aspect.toFixed(2)}).
                            {sourceMatchesTarget && currentFormat !== "horizontal" && (
                                <>
                                    {" "}
                                    Source already matches the selected format —
                                    no reframing needed.
                                </>
                            )}
                        </p>
                    )}
                    <div className="row">
                        {formats.map((f) => {
                            const selected = currentFormat === f.key;
                            return (
                                <button
                                    key={f.key}
                                    className={selected ? "" : "secondary"}
                                    onClick={() => {
                                        const cap = f.max_duration_s;
                                        const clampedLen =
                                            cap != null &&
                                            settings.target_length_s != null &&
                                            settings.target_length_s > cap
                                                ? Math.round(cap)
                                                : settings.target_length_s;
                                        onSettingsChange({
                                            ...settings,
                                            format: f.key,
                                            target_length_s: clampedLen,
                                        });
                                    }}
                                >
                                    {f.label}
                                </button>
                            );
                        })}
                    </div>
                    <div className="row" style={{ marginTop: 10 }}>
                        <label
                            style={{
                                display: "flex",
                                gap: 6,
                                alignItems: "center",
                                margin: 0,
                            }}
                        >
                            <input
                                type="checkbox"
                                checked={settings.captions_enabled ?? false}
                                onChange={(e) =>
                                    onSettingsChange({
                                        ...settings,
                                        captions_enabled: e.target.checked,
                                    })
                                }
                            />
                            Generate captions (SRT + subtitle track)
                        </label>
                    </div>
                    {currentFormat !== "horizontal" && (
                        <div className="row" style={{ marginTop: 6 }}>
                            <label
                                style={{
                                    display: "flex",
                                    gap: 6,
                                    alignItems: "center",
                                    margin: 0,
                                }}
                            >
                                <input
                                    type="checkbox"
                                    checked={settings.safe_zones_enabled ?? false}
                                    onChange={(e) =>
                                        onSettingsChange({
                                            ...settings,
                                            safe_zones_enabled: e.target.checked,
                                        })
                                    }
                                />
                                Show platform-UI safe-zone guides
                            </label>
                        </div>
                    )}
                </div>
            )}

            <div className="card">
                <h2>Target length (optional)</h2>
                <input
                    type="number"
                    min={15}
                    step={15}
                    max={lengthCap ?? undefined}
                    placeholder={
                        lengthCap
                            ? `max ${Math.round(lengthCap)}s for this format`
                            : "e.g. 90 for a 90-second cut — leave blank to keep all good takes"
                    }
                    value={settings.target_length_s ?? ""}
                    onChange={(e) => {
                        const raw = e.target.value ? Number(e.target.value) : null;
                        const clamped =
                            raw != null && lengthCap != null && raw > lengthCap
                                ? Math.round(lengthCap)
                                : raw;
                        onSettingsChange({
                            ...settings,
                            target_length_s: clamped,
                        });
                    }}
                />
                {lengthCap && (
                    <p className="muted">
                        Capped at {Math.round(lengthCap)} s for the selected format.
                    </p>
                )}
            </div>

            {err && <div className="error-box">{err}</div>}

            <div className="row between">
                <button className="secondary" onClick={onBack}>← Back</button>
                <button onClick={onNext}>Build plan →</button>
            </div>
        </div>
    );
}
