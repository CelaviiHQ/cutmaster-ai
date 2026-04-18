import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import type {
    PresetBundle,
    PresetKey,
    ProjectInfo,
    TimelineMode,
} from "../types";

interface Props {
    timelineName: string;
    onTimelineChange: (n: string) => void;
    preset: PresetKey;
    onPresetChange: (p: PresetKey) => void;
    timelineMode: TimelineMode;
    onTimelineModeChange: (m: TimelineMode) => void;
    perClipStt: boolean;
    onPerClipSttChange: (v: boolean) => void;
    expectedSpeakers: number | null;
    onExpectedSpeakersChange: (v: number | null) => void;
    onNext: () => void | Promise<void>;
}

export default function PresetPickScreen({
    timelineName,
    onTimelineChange,
    preset,
    onPresetChange,
    timelineMode,
    onTimelineModeChange,
    perClipStt,
    onPerClipSttChange,
    expectedSpeakers,
    onExpectedSpeakersChange,
    onNext,
}: Props) {
    const [presets, setPresets] = useState<PresetBundle[]>([]);
    const [loading, setLoading] = useState(false);
    const [err, setErr] = useState<string | null>(null);
    const [projectInfo, setProjectInfo] = useState<ProjectInfo | null>(null);
    const [projectErr, setProjectErr] = useState<string | null>(null);
    const [projectLoading, setProjectLoading] = useState(false);
    const [fallbackToText, setFallbackToText] = useState(false);
    // Auto-select the active timeline only the first time project info loads —
    // don't clobber a name the user typed manually.
    const hasAutoSelected = useRef(false);

    useEffect(() => {
        api.listPresets()
            .then((r) => setPresets(r.presets))
            .catch((e) => setErr(String(e)));
    }, []);

    const loadProjectInfo = async () => {
        setProjectLoading(true);
        setProjectErr(null);
        try {
            const info = await api.projectInfo();
            setProjectInfo(info);
            setFallbackToText(info.timelines.length === 0);
            if (!hasAutoSelected.current) {
                const current = info.timelines.find((t) => t.is_current);
                if (current) {
                    onTimelineChange(current.name);
                }
                hasAutoSelected.current = true;
            }
        } catch (e) {
            setProjectErr(String(e));
            setFallbackToText(true);
        } finally {
            setProjectLoading(false);
        }
    };

    useEffect(() => {
        loadProjectInfo();
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    const submit = async () => {
        setLoading(true);
        setErr(null);
        try {
            await onNext();
        } catch (e) {
            setErr(String(e));
        } finally {
            setLoading(false);
        }
    };

    return (
        <div>
            <div className="card">
                <div
                    className="row between"
                    style={{ alignItems: "baseline", marginBottom: 6 }}
                >
                    <label htmlFor="tl" style={{ margin: 0 }}>
                        Source timeline
                        {projectInfo && (
                            <>
                                {" "}
                                <span className="muted">
                                    · {projectInfo.project_name}
                                </span>
                            </>
                        )}
                    </label>
                    <button
                        className="secondary"
                        onClick={loadProjectInfo}
                        disabled={projectLoading}
                        title="Re-read timelines from Resolve"
                        style={{ padding: "4px 10px", fontSize: 12 }}
                    >
                        {projectLoading ? "…" : "↻ Refresh"}
                    </button>
                </div>

                {projectErr && (
                    <p className="muted" style={{ color: "var(--err, #e88)" }}>
                        Couldn't reach Resolve — type the timeline name below.
                    </p>
                )}

                {!fallbackToText && projectInfo ? (
                    <select
                        id="tl"
                        value={timelineName}
                        onChange={(e) => onTimelineChange(e.target.value)}
                    >
                        {!projectInfo.timelines.some(
                            (t) => t.name === timelineName,
                        ) && (
                            <option value={timelineName}>
                                {timelineName || "(pick a timeline)"}
                            </option>
                        )}
                        {projectInfo.timelines.map((t) => (
                            <option key={t.name} value={t.name}>
                                {t.name}
                                {t.is_current ? "  · currently open" : ""}
                                {t.item_count
                                    ? `  · ${t.item_count} item${t.item_count === 1 ? "" : "s"}`
                                    : ""}
                            </option>
                        ))}
                    </select>
                ) : (
                    <input
                        id="tl"
                        type="text"
                        value={timelineName}
                        onChange={(e) => onTimelineChange(e.target.value)}
                        placeholder="Timeline 1"
                    />
                )}

                {projectInfo && projectInfo.timelines.length === 0 && (
                    <p className="muted" style={{ marginTop: 6 }}>
                        The open project has no timelines — create one in Resolve first.
                    </p>
                )}
            </div>

            <div className="card">
                <h2>Is this timeline already edited?</h2>
                <p className="muted">
                    Raw dump = one or more source clips, no cuts yet. Assembled =
                    you've already picked takes / laid them out; CutMaster should
                    respect those boundaries.
                </p>
                <div className="row">
                    <button
                        className={timelineMode === "raw_dump" ? "" : "secondary"}
                        onClick={() => onTimelineModeChange("raw_dump")}
                        disabled={preset === "tightener"}
                    >
                        Raw dump (v1 default)
                    </button>
                    <button
                        className={timelineMode === "assembled" ? "" : "secondary"}
                        onClick={() => onTimelineModeChange("assembled")}
                        disabled={preset === "tightener"}
                    >
                        Assembled — takes picked
                    </button>
                </div>
                {preset === "tightener" && (
                    <p className="muted" style={{ marginTop: 8 }}>
                        Tightener always runs in assembled mode.
                    </p>
                )}
            </div>

            <div className="card">
                <h2>Content type</h2>
                <p>Pick a preset — or let Auto-detect classify the content from the transcript.</p>
                <div className="grid-presets">
                    <div
                        className={`preset-card auto ${preset === "auto" ? "selected" : ""}`}
                        onClick={() => onPresetChange("auto")}
                    >
                        <h3>✨ Auto-detect</h3>
                        <p>Let the agent classify this clip after transcription. You can override in the next step.</p>
                    </div>
                    {presets.map((p) => (
                        <div
                            key={p.key}
                            className={`preset-card ${preset === p.key ? "selected" : ""}`}
                            onClick={() => onPresetChange(p.key)}
                        >
                            <h3>{p.label}</h3>
                            <p>{p.hook_rule}</p>
                        </div>
                    ))}
                </div>
            </div>

            <div className="card">
                <h2>Transcription mode (advanced)</h2>
                <label style={{ display: "flex", gap: 6, alignItems: "center", margin: 0 }}>
                    <input
                        type="checkbox"
                        checked={perClipStt}
                        onChange={(e) => onPerClipSttChange(e.target.checked)}
                    />
                    Per-clip STT — transcribe each timeline item separately
                </label>
                <p className="muted" style={{ marginTop: 6 }}>
                    Slower on the first run but richer context: the Director
                    sees per-clip metadata (file, duration) and per-clip
                    results cache so re-analyzing a trimmed timeline only
                    re-transcribes the changed takes.
                </p>

                {perClipStt && (
                    <div style={{ marginTop: 12 }}>
                        <label
                            htmlFor="expected-speakers"
                            style={{ display: "block", marginBottom: 4 }}
                        >
                            Speakers in shots (optional)
                        </label>
                        <input
                            id="expected-speakers"
                            type="number"
                            min={1}
                            max={10}
                            step={1}
                            placeholder="leave blank if unsure"
                            value={expectedSpeakers ?? ""}
                            onChange={(e) => {
                                const raw = e.target.value;
                                onExpectedSpeakersChange(
                                    raw ? Math.max(1, Math.min(10, Number(raw))) : null,
                                );
                            }}
                            style={{ maxWidth: 180 }}
                        />
                        <p className="muted" style={{ marginTop: 6 }}>
                            Per-clip STT assigns speaker IDs locally, so clip 0's
                            "S1" isn't necessarily clip 1's "S1". Give us the
                            count and we'll reconcile: 1 = trivial collapse to a
                            single speaker (no extra LLM call), 2+ = one cheap
                            Gemini reconciliation pass. Leave blank to keep raw
                            per-clip IDs.
                        </p>
                    </div>
                )}
            </div>

            {err && <div className="error-box">{err}</div>}

            <div className="row between">
                <span className="muted">
                    Preset: <code>{preset}</code> &nbsp;·&nbsp; Timeline: <code>{timelineName}</code>
                </span>
                <button disabled={loading || !timelineName.trim()} onClick={submit}>
                    {loading ? "Starting…" : "Analyze →"}
                </button>
            </div>
        </div>
    );
}
