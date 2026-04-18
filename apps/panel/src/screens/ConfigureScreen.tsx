import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { SourceAspectInfo } from "../api";
import type {
    FormatKey,
    FormatSpec,
    PresetBundle,
    PresetKey,
    PresetRecommendation,
    SpeakerRosterEntry,
    StoryAnalysis,
    UserSettings,
} from "../types";

const SPEAKER_AWARE_PRESETS: ReadonlySet<PresetKey> = new Set([
    "interview",
    "podcast",
]);

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
    const [speakerRoster, setSpeakerRoster] = useState<SpeakerRosterEntry[] | null>(null);
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
        api.speakers(runId)
            .then((r) => !cancelled && setSpeakerRoster(r.speakers))
            .catch(() => !cancelled && setSpeakerRoster([]));
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

    const isTightener = preset === "tightener";
    const isClipHunter = preset === "clip_hunter";
    const isShortGenerator = preset === "short_generator";
    // Short Generator + Clip Hunter share the "N candidates + target length"
    // shape, so UI rules that hide sequencing-preset cards apply to both.
    const isMultiCandidate = isClipHunter || isShortGenerator;
    const showSpeakerCard =
        SPEAKER_AWARE_PRESETS.has(preset) &&
        speakerRoster != null &&
        speakerRoster.length >= 2;
    const speakerLabels = settings.speaker_labels ?? {};
    const updateSpeakerLabel = (speakerId: string, label: string) => {
        const next = { ...speakerLabels };
        const trimmed = label.trim();
        if (trimmed) next[speakerId] = trimmed;
        else delete next[speakerId];
        onSettingsChange({
            ...settings,
            speaker_labels: Object.keys(next).length > 0 ? next : null,
        });
    };
    const timelineMode = settings.timeline_mode ?? "raw_dump";
    const assembledMode =
        !isTightener && !isMultiCandidate && timelineMode === "assembled";
    const curatedMode =
        !isTightener && !isMultiCandidate && timelineMode === "curated";
    const roughCutMode =
        !isTightener && !isMultiCandidate && timelineMode === "rough_cut";
    const takeAwareMode = assembledMode || curatedMode || roughCutMode;

    const scrubParams = (settings.scrub_params ?? {}) as Record<string, unknown>;
    const updateScrub = (patch: Record<string, unknown>) => {
        onSettingsChange({
            ...settings,
            scrub_params: { ...scrubParams, ...patch },
        });
    };

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
                        "tightener",
                        "clip_hunter",
                        "short_generator",
                    ].map((p) => (
                        <option key={p} value={p}>
                            {p}
                        </option>
                    ))}
                </select>
            </div>

            {isMultiCandidate && (
                <div className="card">
                    <h2>
                        {isClipHunter ? "Clip Hunter" : "Short Generator"}
                    </h2>
                    <p className="muted">
                        {isClipHunter ? (
                            <>
                                Surfaces {settings.num_clips ?? 3} short,
                                self-contained moments ranked by engagement.
                                Each candidate is a single contiguous span —
                                picked whole, no reassembly. Build one or all
                                from the Review screen; multiple builds land
                                on <code>_AI_Clip_1</code>,{" "}
                                <code>_AI_Clip_2</code>, etc.
                            </>
                        ) : (
                            <>
                                Composes {settings.num_clips ?? 3} assembled
                                shorts — each one is 3–8 jump-cut spans
                                stitched around a single through-line. More
                                editorial work than Clip Hunter, more
                                TikTok-coded output. Builds land on{" "}
                                <code>_AI_Short_1</code>,{" "}
                                <code>_AI_Short_2</code>, etc.
                            </>
                        )}
                    </p>
                    <label style={{ display: "block", marginTop: 8 }}>
                        {isClipHunter ? "Number of clips:" : "Number of shorts:"}{" "}
                        <code>{settings.num_clips ?? 3}</code>
                    </label>
                    <input
                        type="range"
                        min={1}
                        max={5}
                        step={1}
                        style={{ width: "100%" }}
                        value={settings.num_clips ?? 3}
                        onChange={(e) =>
                            onSettingsChange({
                                ...settings,
                                num_clips: Number(e.target.value),
                            })
                        }
                    />
                </div>
            )}

            {isMultiCandidate &&
                analysis &&
                analysis.theme_candidates.length > 0 && (
                    <div className="card">
                        <h2>Detected topics</h2>
                        <p className="muted">
                            Themes the analyser surfaced from the episode.
                            {isClipHunter
                                ? " Clip Hunter prefers candidates that touch the selected themes."
                                : " Short Generator builds shorts anchored to the selected themes."}{" "}
                            Untick anything you consider noise.
                        </p>
                        <div style={{ marginTop: 6 }}>
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
                    </div>
                )}

            {analysis && !isTightener && !isMultiCandidate && (
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

            {takeAwareMode && (
                <div className="card">
                    <h2>
                        {assembledMode && "Assembled mode"}
                        {curatedMode && "Curated mode"}
                        {roughCutMode && "Rough cut mode"}
                    </h2>
                    <p className="muted">
                        {assembledMode &&
                            "Director will never cross take boundaries. Scrubbing happens inside each take; reordering whole takes is a separate switch."}
                        {curatedMode &&
                            "Every take you selected will appear in the output. The Director arranges them into the strongest narrative and may split takes into multiple spans for callbacks."}
                        {roughCutMode &&
                            "Adjacent takes are clustered into groups (by clip color, flags, or transcript similarity). The Director picks one winner per group — alternates that don't win get dropped."}
                    </p>
                    {assembledMode && (
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
                                    checked={settings.reorder_allowed ?? true}
                                    onChange={(e) =>
                                        onSettingsChange({
                                            ...settings,
                                            reorder_allowed: e.target.checked,
                                        })
                                    }
                                />
                                Let the AI reorder takes for narrative flow
                            </label>
                        </div>
                    )}
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
                                checked={settings.takes_already_scrubbed ?? false}
                                onChange={(e) =>
                                    onSettingsChange({
                                        ...settings,
                                        takes_already_scrubbed: e.target.checked,
                                    })
                                }
                            />
                            Takes are already scrubbed — skip cleanup (use raw transcript)
                        </label>
                    </div>
                </div>
            )}

            {isTightener && (
                <div className="card">
                    <h2>Tightener cleanup</h2>
                    <p className="muted">
                        Tightener drops filler words and dead-air gaps inside
                        each take, then plays the takes in their original
                        order. No Director LLM runs — this is a deterministic
                        pass. Adjust the thresholds below if the default cut
                        is too loose or too aggressive.
                    </p>
                    <div style={{ marginTop: 10 }}>
                        <label
                            style={{
                                display: "flex",
                                gap: 6,
                                alignItems: "center",
                                margin: 0,
                                marginBottom: 8,
                            }}
                        >
                            <input
                                type="checkbox"
                                checked={scrubParams.remove_fillers !== false}
                                onChange={(e) =>
                                    updateScrub({ remove_fillers: e.target.checked })
                                }
                            />
                            Remove filler words (um, uh, ah…)
                        </label>
                        <label
                            style={{
                                display: "flex",
                                gap: 6,
                                alignItems: "center",
                                margin: 0,
                                marginBottom: 8,
                            }}
                        >
                            <input
                                type="checkbox"
                                checked={scrubParams.remove_dead_air !== false}
                                onChange={(e) =>
                                    updateScrub({ remove_dead_air: e.target.checked })
                                }
                            />
                            Remove dead-air words (fillers inside long gaps)
                        </label>
                        <label
                            style={{
                                display: "flex",
                                gap: 6,
                                alignItems: "center",
                                margin: 0,
                                marginBottom: 8,
                            }}
                        >
                            <input
                                type="checkbox"
                                checked={scrubParams.collapse_restarts !== false}
                                onChange={(e) =>
                                    updateScrub({ collapse_restarts: e.target.checked })
                                }
                            />
                            Collapse restarts (drop the earlier attempt)
                        </label>
                        <label
                            style={{ display: "block", marginTop: 12 }}
                        >
                            Dead-air gap threshold:{" "}
                            <code>
                                {(
                                    (scrubParams.dead_air_threshold_s as number | undefined) ??
                                    0.3
                                ).toFixed(2)}
                                s
                            </code>
                        </label>
                        <input
                            type="range"
                            min={0.1}
                            max={1.5}
                            step={0.05}
                            style={{ width: "100%" }}
                            value={
                                (scrubParams.dead_air_threshold_s as
                                    | number
                                    | undefined) ?? 0.3
                            }
                            onChange={(e) =>
                                updateScrub({
                                    dead_air_threshold_s: Number(e.target.value),
                                })
                            }
                        />
                    </div>
                </div>
            )}

            {excludeCats.length > 0 && !isTightener && (
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

            {showSpeakerCard && (
                <div className="card">
                    <h2>Speaker labels</h2>
                    <p className="muted">
                        Rename speakers so the Director reasons about roles,
                        not raw STT ids. The higher word count is usually the
                        host.
                    </p>
                    {speakerRoster?.map((s) => (
                        <div
                            key={s.speaker_id}
                            className="row"
                            style={{ alignItems: "center", marginTop: 6 }}
                        >
                            <code style={{ minWidth: 48 }}>{s.speaker_id}</code>
                            <span className="muted" style={{ minWidth: 90 }}>
                                {s.word_count} words
                            </span>
                            <input
                                type="text"
                                placeholder={
                                    s.speaker_id === "S1" ? "Host" : "Guest"
                                }
                                value={speakerLabels[s.speaker_id] ?? ""}
                                onChange={(e) =>
                                    updateSpeakerLabel(
                                        s.speaker_id,
                                        e.target.value,
                                    )
                                }
                                style={{ flex: 1 }}
                            />
                        </div>
                    ))}
                </div>
            )}

            {!isTightener && (
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
            )}

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

            {!isTightener && (
                <div className="card">
                    <h2>
                        {isClipHunter
                            ? "Target clip length"
                            : isShortGenerator
                              ? "Target short length"
                              : "Target length (optional)"}
                    </h2>
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
            )}

            {err && <div className="error-box">{err}</div>}

            <div className="row between">
                <button className="secondary" onClick={onBack} data-hotkey="back">← Back</button>
                <button onClick={onNext} data-hotkey="primary">Build plan →</button>
            </div>
        </div>
    );
}
