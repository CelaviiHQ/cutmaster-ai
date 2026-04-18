import { useEffect, useState } from "react";
import { api } from "../api";
import type { PresetBundle, PresetKey, TimelineMode } from "../types";

interface Props {
    timelineName: string;
    onTimelineChange: (n: string) => void;
    preset: PresetKey;
    onPresetChange: (p: PresetKey) => void;
    timelineMode: TimelineMode;
    onTimelineModeChange: (m: TimelineMode) => void;
    onNext: () => void | Promise<void>;
}

export default function PresetPickScreen({
    timelineName,
    onTimelineChange,
    preset,
    onPresetChange,
    timelineMode,
    onTimelineModeChange,
    onNext,
}: Props) {
    const [presets, setPresets] = useState<PresetBundle[]>([]);
    const [loading, setLoading] = useState(false);
    const [err, setErr] = useState<string | null>(null);

    useEffect(() => {
        api.listPresets()
            .then((r) => setPresets(r.presets))
            .catch((e) => setErr(String(e)));
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
                <label htmlFor="tl">Source timeline (must be open in Resolve)</label>
                <input
                    id="tl"
                    type="text"
                    value={timelineName}
                    onChange={(e) => onTimelineChange(e.target.value)}
                    placeholder="Timeline 1"
                />
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
