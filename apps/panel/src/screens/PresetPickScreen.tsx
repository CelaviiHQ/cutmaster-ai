import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import RunsDrawer from "./RunsDrawer";
import TimelineDropdown from "./TimelineDropdown";
import type {
    PresetBundle,
    PresetKey,
    ProjectInfo,
    SttProviderInfo,
    SttProviderKey,
    TimelineMode,
    TrackListResponse,
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
    sttProvider: SttProviderKey | null;
    onSttProviderChange: (v: SttProviderKey | null) => void;
    videoTrack: number | null;
    onVideoTrackChange: (v: number | null) => void;
    audioTrack: number | null;
    onAudioTrackChange: (v: number | null) => void;
    sensoryMasterEnabled: boolean;
    onSensoryMasterChange: (v: boolean) => void;
    onNext: () => void | Promise<void>;
    /** Jump to an existing run — hydrates state + navigates to resumeAt. */
    onReopenRun: (runId: string) => void | Promise<void>;
}

const TIMELINE_MODE_INFO: Record<TimelineMode, { title: string; blurb: string }> = {
    raw_dump: {
        title: "Raw dump",
        blurb: "Pile of content. Agent picks keepers, sequences, and tightens.",
    },
    rough_cut: {
        title: "Rough cut",
        blurb: "Candidates with A/B alternates. Agent picks winners per group and sequences them.",
    },
    curated: {
        title: "Curated",
        blurb: "Final selects, no duplicates. Agent keeps every take and sequences them.",
    },
    assembled: {
        title: "Assembled",
        blurb: "Cut is locked. Agent only tightens within takes — no reordering.",
    },
};

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
    sttProvider,
    onSttProviderChange,
    videoTrack,
    onVideoTrackChange,
    audioTrack,
    onAudioTrackChange,
    sensoryMasterEnabled,
    onSensoryMasterChange,
    onNext,
    onReopenRun,
}: Props) {
    const [presets, setPresets] = useState<PresetBundle[]>([]);
    const [loading, setLoading] = useState(false);
    const [err, setErr] = useState<string | null>(null);
    const [projectInfo, setProjectInfo] = useState<ProjectInfo | null>(null);
    const [projectErr, setProjectErr] = useState<string | null>(null);
    const [projectLoading, setProjectLoading] = useState(false);
    const [fallbackToText, setFallbackToText] = useState(false);
    const [providers, setProviders] = useState<SttProviderInfo[] | null>(null);
    const [defaultProvider, setDefaultProvider] = useState<SttProviderKey>("gemini");
    // v3-1.1: source timeline collapses to breadcrumb after the first auto-pick.
    // User flips to expanded state via the breadcrumb "change" link.
    const [timelineExpanded, setTimelineExpanded] = useState(false);
    const [timelineUserTouched, setTimelineUserTouched] = useState(false);
    const hasAutoSelected = useRef(false);
    // Track roster for the override picker. Fetched on each timeline
    // change; null = "not loaded yet" (UI stays collapsed).
    const [trackList, setTrackList] = useState<TrackListResponse | null>(null);
    const [tracksLoading, setTracksLoading] = useState(false);

    useEffect(() => {
        api.listPresets()
            .then((r) => setPresets(r.presets))
            .catch((e) => setErr(String(e)));
        api.sttProviders()
            .then((r) => {
                setProviders(r.providers);
                setDefaultProvider(r.default);
            })
            .catch(() => setProviders([]));
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

    // Fetch track roster when the timeline name changes. The override
    // card renders auto-picked defaults unless the editor expands.
    useEffect(() => {
        if (!timelineName.trim()) {
            setTrackList(null);
            return;
        }
        let cancelled = false;
        setTracksLoading(true);
        api.tracks(timelineName)
            .then((r) => {
                if (cancelled) return;
                setTrackList(r);
                // If the editor hasn't overridden, reflect the
                // auto-picks back up to App.tsx so analyze carries
                // null (= auto) — only set explicit overrides when the
                // editor deliberately picks a different track.
            })
            .catch(() => {
                if (!cancelled) setTrackList(null);
            })
            .finally(() => {
                if (!cancelled) setTracksLoading(false);
            });
        return () => {
            cancelled = true;
        };
    }, [timelineName]);

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

    const currentTimelineItemCount =
        projectInfo?.timelines.find((t) => t.name === timelineName)?.item_count ?? null;
    const isCurrentOpen =
        projectInfo?.timelines.find((t) => t.name === timelineName)?.is_current ?? false;
    // Collapse the source-timeline card when: we auto-picked the currently-open
    // timeline AND the user hasn't touched anything AND they haven't explicitly
    // expanded via "change".
    const timelineCollapsed =
        !timelineExpanded &&
        !timelineUserTouched &&
        !fallbackToText &&
        !projectErr &&
        projectInfo !== null &&
        isCurrentOpen &&
        timelineName.trim() !== "";

    const handleTimelineChange = (name: string) => {
        setTimelineUserTouched(true);
        onTimelineChange(name);
    };

    // v3-1.3: Tightener locks the timeline mode to assembled. Auto-apply on pick.
    useEffect(() => {
        if (preset === "tightener" && timelineMode !== "assembled") {
            onTimelineModeChange("assembled");
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [preset]);

    const sttConfigured = !!providers && providers.some((p) => p.configured);
    const effectiveProvider = sttProvider ?? defaultProvider;

    return (
        <div>
            {/* v3-1.1 — Source timeline: collapsed breadcrumb after first auto-pick */}
            {timelineCollapsed ? (
                <div className="card" style={{ padding: "var(--s-3) var(--s-4)" }}>
                    <div className="row between" style={{ margin: 0 }}>
                        <span className="muted" style={{ fontSize: "var(--fs-2)" }}>
                            Source:{" "}
                            <code>{timelineName}</code>
                            {currentTimelineItemCount ? (
                                <>
                                    {" "}
                                    ·{" "}
                                    {currentTimelineItemCount} item
                                    {currentTimelineItemCount === 1 ? "" : "s"}
                                </>
                            ) : null}
                            {projectInfo?.project_name ? (
                                <> · {projectInfo.project_name}</>
                            ) : null}
                        </span>
                        <button
                            className="btn-ghost"
                            onClick={() => setTimelineExpanded(true)}
                            style={{ fontSize: "var(--fs-2)" }}
                        >
                            change
                        </button>
                    </div>
                </div>
            ) : (
                <div className="card">
                    <div
                        className="row between"
                        style={{ alignItems: "baseline", marginBottom: "var(--s-2)" }}
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
                            className="btn-ghost"
                            onClick={loadProjectInfo}
                            disabled={projectLoading}
                            title="Re-read timelines from Resolve"
                            aria-label="Refresh timeline list"
                            style={{ fontSize: "var(--fs-2)" }}
                        >
                            {projectLoading ? "…" : "↻ Refresh"}
                        </button>
                    </div>

                    {projectErr && (
                        <p className="muted" style={{ color: "var(--err)" }}>
                            Couldn't reach Resolve — type the timeline name below.
                        </p>
                    )}

                    {!fallbackToText && projectInfo ? (
                        <TimelineDropdown
                            timelines={projectInfo.timelines}
                            value={timelineName}
                            onChange={handleTimelineChange}
                            placeholder="pick a timeline"
                        />
                    ) : (
                        <input
                            id="tl"
                            type="text"
                            value={timelineName}
                            onChange={(e) => handleTimelineChange(e.target.value)}
                            placeholder="Timeline 1"
                        />
                    )}

                    {projectInfo && projectInfo.timelines.length === 0 && (
                        <p className="muted" style={{ marginTop: "var(--s-2)" }}>
                            The open project has no timelines — create one in Resolve first.
                        </p>
                    )}
                </div>
            )}

            {/* Source tracks: auto-picked, collapsible override. */}
            {trackList && (
                <details
                    className="card card--advanced"
                    open={
                        // Auto-expand when auto-pick failed or when the
                        // editor has already chosen an override.
                        trackList.picked_video == null ||
                        trackList.picked_audio == null ||
                        videoTrack != null ||
                        audioTrack != null
                    }
                >
                    <summary>
                        <span>
                            Source tracks
                            {trackList.picked_video != null && (
                                <>
                                    {" "}
                                    <span className="muted" style={{ fontSize: "var(--fs-2)" }}>
                                        · V{videoTrack ?? trackList.picked_video} + A
                                        {audioTrack ?? trackList.picked_audio}
                                    </span>
                                </>
                            )}
                            {tracksLoading && (
                                <>
                                    {" "}
                                    <span className="muted" style={{ fontSize: "var(--fs-2)" }}>
                                        · loading…
                                    </span>
                                </>
                            )}
                        </span>
                    </summary>
                    <div className="card-body">
                        <p className="muted" style={{ marginBottom: "var(--s-3)" }}>
                            CutMaster auto-picks the picture edit + dialogue
                            track. Override below if the defaults miss — e.g.
                            picture on V2 with an empty V1, or a dialogue track
                            stacked above a music bed.
                        </p>

                        <label style={{ display: "block", marginBottom: "var(--s-2)" }}>
                            Video (picture edit)
                        </label>
                        <select
                            value={videoTrack ?? ""}
                            onChange={(e) => {
                                const raw = e.target.value;
                                if (!raw) onVideoTrackChange(null);
                                else onVideoTrackChange(Number(raw));
                            }}
                            style={{ marginBottom: "var(--s-3)" }}
                        >
                            <option value="">
                                {trackList.picked_video != null
                                    ? `Auto (V${trackList.picked_video})`
                                    : "Auto (no pick available)"}
                            </option>
                            {trackList.video_tracks.map((t) => (
                                <option key={t.index} value={t.index}>
                                    V{t.index}
                                    {t.name && t.name !== `V${t.index}` ? ` — ${t.name}` : ""}
                                    {" · "}
                                    {t.item_count} item
                                    {t.item_count === 1 ? "" : "s"}
                                    {t.picked_by_default ? " ✓" : ""}
                                </option>
                            ))}
                        </select>

                        <label style={{ display: "block", marginBottom: "var(--s-2)" }}>
                            Audio (dialogue)
                        </label>
                        <select
                            value={audioTrack ?? ""}
                            onChange={(e) => {
                                const raw = e.target.value;
                                if (!raw) onAudioTrackChange(null);
                                else onAudioTrackChange(Number(raw));
                            }}
                        >
                            <option value="">
                                {trackList.picked_audio != null
                                    ? `Auto (A${trackList.picked_audio})`
                                    : "Auto (no pick available)"}
                            </option>
                            {trackList.audio_tracks.map((t) => (
                                <option key={t.index} value={t.index}>
                                    A{t.index}
                                    {t.name && t.name !== `A${t.index}` ? ` — ${t.name}` : ""}
                                    {" · "}
                                    {t.item_count} item
                                    {t.item_count === 1 ? "" : "s"}
                                    {t.picked_by_default ? " ✓" : ""}
                                </option>
                            ))}
                        </select>
                    </div>
                </details>
            )}

            {/* v3-1.2 — Timeline state as 2×2 grid */}
            <div className="card">
                <h2>What state is this timeline in?</h2>
                <p className="muted">
                    How much editorial work have you already done? That decides which
                    decisions the agent is allowed to make.
                </p>
                {preset === "tightener" ? (
                    // v3-1.3 — Tightener auto-lock: single info line replaces the grid
                    <p
                        className="muted"
                        style={{
                            marginTop: "var(--s-3)",
                            padding: "var(--s-3)",
                            background: "var(--surface-3)",
                            borderRadius: "var(--radius-md)",
                            fontSize: "var(--fs-2)",
                        }}
                    >
                        Tightener runs in <strong>Assembled</strong> mode — no other
                        state supported. Your cut is locked; the agent only tightens
                        within takes.
                    </p>
                ) : (
                    <div
                        style={{
                            display: "grid",
                            gridTemplateColumns: "repeat(2, 1fr)",
                            gap: "var(--s-2)",
                            marginTop: "var(--s-3)",
                        }}
                    >
                        {(
                            ["raw_dump", "rough_cut", "curated", "assembled"] as TimelineMode[]
                        ).map((mode) => {
                            const info = TIMELINE_MODE_INFO[mode];
                            const selected = timelineMode === mode;
                            return (
                                <div
                                    key={mode}
                                    role="button"
                                    tabIndex={0}
                                    className={`preset-card ${selected ? "selected" : ""}`}
                                    onClick={() => onTimelineModeChange(mode)}
                                    onKeyDown={(e) => {
                                        if (e.key === "Enter" || e.key === " ") {
                                            e.preventDefault();
                                            onTimelineModeChange(mode);
                                        }
                                    }}
                                >
                                    <h3>{info.title}</h3>
                                    <p>{info.blurb}</p>
                                </div>
                            );
                        })}
                    </div>
                )}
            </div>

            {/* v3-1.4 — Content type is the primary decision */}
            <div className="card card--primary">
                <h2>Content type</h2>
                <p>
                    Pick a preset — or let Auto-detect classify the content from the transcript.
                </p>
                <div className="grid-presets">
                    <div
                        role="button"
                        tabIndex={0}
                        className={`preset-card auto ${preset === "auto" ? "selected" : ""}`}
                        onClick={() => onPresetChange("auto")}
                        onKeyDown={(e) => {
                            if (e.key === "Enter" || e.key === " ") {
                                e.preventDefault();
                                onPresetChange("auto");
                            }
                        }}
                    >
                        <h3>✨ Auto-detect</h3>
                        <p>
                            Let the agent classify this clip after transcription. You can
                            override in the next step.
                        </p>
                    </div>
                    {/* Phase 5.2 — three-axis split. The legacy cut-intent
                        presets (tightener / clip_hunter / short_generator)
                        are no longer content types; they're Axis 2 cut
                        intents. Filter them out so the grid shows the 8
                        true content types + Auto (9 total), down from the
                        pre-phase-5 12. */}
                    {presets
                        .filter(
                            (p) =>
                                p.key !== "tightener" &&
                                p.key !== "clip_hunter" &&
                                p.key !== "short_generator",
                        )
                        .map((p) => (
                        <div
                            key={p.key}
                            role="button"
                            tabIndex={0}
                            className={`preset-card ${preset === p.key ? "selected" : ""}`}
                            onClick={() => onPresetChange(p.key)}
                            onKeyDown={(e) => {
                                if (e.key === "Enter" || e.key === " ") {
                                    e.preventDefault();
                                    onPresetChange(p.key);
                                }
                            }}
                        >
                            <h3>{p.label}</h3>
                            <p>{p.hook_rule}</p>
                        </div>
                    ))}
                </div>
            </div>

            {/* Speakers on camera — single segmented control reads as
                "pick one". Long rationale collapsed into the title tooltip. */}
            <div className="card">
                <div className="row between" style={{ alignItems: "baseline" }}>
                    <h2 style={{ margin: 0 }}>
                        Speakers on camera{" "}
                        <span
                            className="muted"
                            title="Helps the Director/Marker agents reason about roles and stops Gemini from inventing phantom speakers on solo content."
                            style={{
                                fontSize: "var(--fs-2)",
                                cursor: "help",
                                marginLeft: 4,
                            }}
                        >
                            (?)
                        </span>
                    </h2>
                </div>
                <div
                    className="segmented"
                    role="radiogroup"
                    aria-label="Number of speakers on camera"
                    style={{ marginTop: "var(--s-3)" }}
                >
                    <button
                        className={
                            "seg-opt is-ghost" +
                            (expectedSpeakers == null ? " is-selected" : "")
                        }
                        role="radio"
                        aria-checked={expectedSpeakers == null}
                        onClick={() => onExpectedSpeakersChange(null)}
                    >
                        Unsure
                    </button>
                    {[1, 2, 3, 4].map((n) => (
                        <button
                            key={n}
                            className={
                                "seg-opt" + (expectedSpeakers === n ? " is-selected" : "")
                            }
                            role="radio"
                            aria-checked={expectedSpeakers === n}
                            onClick={() => onExpectedSpeakersChange(n)}
                        >
                            {n === 1 ? "1 (solo)" : `${n}`}
                        </button>
                    ))}
                    <button
                        className={
                            "seg-opt" +
                            (expectedSpeakers != null && expectedSpeakers > 4
                                ? " is-selected"
                                : "")
                        }
                        role="radio"
                        aria-checked={expectedSpeakers != null && expectedSpeakers > 4}
                        onClick={() => onExpectedSpeakersChange(5)}
                        title="5 or more — enter a number below"
                    >
                        5+
                    </button>
                </div>
                {expectedSpeakers != null && expectedSpeakers > 4 && (
                    <div style={{ marginTop: "var(--s-3)" }}>
                        <label
                            htmlFor="expected-speakers"
                            style={{ display: "block", marginBottom: "var(--s-1)" }}
                        >
                            Exact count (5–10)
                        </label>
                        <input
                            id="expected-speakers"
                            type="number"
                            min={5}
                            max={10}
                            step={1}
                            value={expectedSpeakers}
                            onChange={(e) => {
                                const raw = e.target.value;
                                if (!raw) return;
                                onExpectedSpeakersChange(
                                    Math.max(5, Math.min(10, Number(raw))),
                                );
                            }}
                            style={{ maxWidth: 140 }}
                        />
                    </div>
                )}
                <p
                    className="muted"
                    style={{ marginTop: "var(--s-3)", fontSize: "var(--fs-2)" }}
                >
                    {expectedSpeakers === 1 && (
                        <>Every word will be tagged as one speaker (S1).</>
                    )}
                    {expectedSpeakers != null && expectedSpeakers >= 2 && (
                        <>
                            With per-clip STT on, each clip's local IDs are reconciled
                            into a consistent {expectedSpeakers}-speaker roster via one
                            cheap Gemini-Flash-Lite call.
                        </>
                    )}
                    {expectedSpeakers == null && (
                        <>Gemini's raw speaker IDs are used unchanged.</>
                    )}
                </p>
            </div>

            {/* Transcription — primary path, always visible. Segmented
                provider toggle + dynamic caption, per-clip STT tucked
                under an Advanced sub-disclosure. */}
            <div className="card">
                <div className="row between" style={{ alignItems: "baseline" }}>
                    <h2 style={{ margin: 0 }}>
                        Transcription{" "}
                        <span
                            className="muted"
                            title="Gemini is free with a key and fine for ≤ 8 min audio. Deepgram Nova-3 handles long-form and bundles diarization — recommended for interviews."
                            style={{
                                fontSize: "var(--fs-2)",
                                cursor: "help",
                                marginLeft: 4,
                            }}
                        >
                            (?)
                        </span>
                    </h2>
                </div>

                {providers && providers.length > 0 && (
                    <>
                        <div
                            className="segmented segmented--block"
                            role="radiogroup"
                            aria-label="Transcription service"
                            style={{ marginTop: "var(--s-3)" }}
                        >
                            {providers.map((p) => {
                                const selected = effectiveProvider === p.key;
                                const disabled = !p.configured;
                                return (
                                    <button
                                        key={p.key}
                                        className={
                                            "seg-opt" + (selected ? " is-selected" : "")
                                        }
                                        role="radio"
                                        aria-checked={selected}
                                        disabled={disabled}
                                        onClick={() => onSttProviderChange(p.key)}
                                        title={
                                            disabled
                                                ? `${p.key.toUpperCase()}_API_KEY not set in .env`
                                                : p.label
                                        }
                                    >
                                        {/* Short label inside segment; full label
                                            with cost/notes goes in the caption below. */}
                                        {p.key === "gemini"
                                            ? "Gemini Flash-Lite"
                                            : p.key === "deepgram"
                                              ? "Deepgram Nova-3"
                                              : p.label}
                                        {disabled && " · key missing"}
                                    </button>
                                );
                            })}
                        </div>
                        <p className="seg-caption">
                            {effectiveProvider === "deepgram"
                                ? "Long-form, diarized · no word-level cap"
                                : "Free with a Gemini key · ≤ 8 min audio"}
                        </p>
                        {!sttConfigured && (
                            <div className="inline-warn">
                                No STT key configured. Add either{" "}
                                <code>GEMINI_API_KEY</code> or{" "}
                                <code>DEEPGRAM_API_KEY</code> to{" "}
                                <code>.env</code> and restart the panel.
                            </div>
                        )}
                    </>
                )}

                <details style={{ marginTop: "var(--s-4)" }}>
                    <summary
                        className="muted"
                        style={{
                            cursor: "pointer",
                            fontSize: "var(--fs-2)",
                            userSelect: "none",
                        }}
                    >
                        Advanced{perClipStt ? " · per-clip on" : ""}
                    </summary>
                    <div style={{ marginTop: "var(--s-3)" }}>
                        <label
                            style={{
                                display: "flex",
                                gap: "var(--s-2)",
                                alignItems: "center",
                                margin: 0,
                                color: "var(--text-primary)",
                                fontSize: "var(--fs-3)",
                            }}
                        >
                            <input
                                type="checkbox"
                                checked={perClipStt}
                                onChange={(e) => onPerClipSttChange(e.target.checked)}
                                style={{ width: "auto", height: "auto" }}
                            />
                            Per-clip STT — transcribe each timeline item separately
                        </label>
                        <p
                            className="muted"
                            style={{ marginTop: "var(--s-2)", fontSize: "var(--fs-2)" }}
                        >
                            Slower on the first run but richer context: Director sees
                            per-clip metadata, and results cache so re-analyzing a
                            trimmed timeline only re-transcribes the changed takes.
                        </p>
                    </div>
                </details>
            </div>

            {/* Shot-aware editing — power-user toggle, demoted to a
                secondary card so it doesn't compete with the primary path.
                Disables itself with an inline warning when GEMINI_API_KEY
                is missing instead of leaving the user to read prose. */}
            {(() => {
                const geminiConfigured = providers
                    ? providers.find((p) => p.key === "gemini")?.configured ?? false
                    : true;
                const checkboxDisabled = !geminiConfigured;
                return (
                    <div className="card card--secondary">
                        <label
                            style={{
                                display: "flex",
                                gap: "var(--s-2)",
                                alignItems: "center",
                                margin: 0,
                                color: checkboxDisabled
                                    ? "var(--text-tertiary)"
                                    : "var(--text-primary)",
                                fontSize: "var(--fs-3)",
                                cursor: checkboxDisabled ? "not-allowed" : "pointer",
                            }}
                        >
                            <input
                                type="checkbox"
                                checked={sensoryMasterEnabled && !checkboxDisabled}
                                disabled={checkboxDisabled}
                                onChange={(e) =>
                                    onSensoryMasterChange(e.target.checked)
                                }
                                style={{ width: "auto", height: "auto" }}
                            />
                            Shot-aware editing
                            <span
                                className="muted"
                                style={{
                                    fontSize: "var(--fs-2)",
                                    marginLeft: "var(--s-2)",
                                }}
                                title="Samples frames from each timeline item and asks Gemini for shot tags; validates cut boundaries at build time. Cached after first analyze. Fine-tune layers on the Configure screen."
                            >
                                ⓘ Adds 30–60 s on first analyze · cached after
                            </span>
                        </label>
                        {checkboxDisabled && (
                            <div className="inline-warn">
                                Needs <code>GEMINI_API_KEY</code> in{" "}
                                <code>.env</code> to enable shot tagging.
                            </div>
                        )}
                    </div>
                );
            })()}

            <RunsDrawer onReopen={onReopenRun} />

            {err && <div className="error-box">{err}</div>}

            {/* Sticky commit zone — keeps Analyze visible while the user
                scrolls long preset/track lists. ETA is a rough first-run
                estimate so the click feels predictable. */}
            <div className="config-footer">
                <span className="footer-meta">
                    Preset: <code>{preset}</code> &nbsp;·&nbsp; Timeline:{" "}
                    <code>{timelineName}</code>
                </span>
                <span style={{ display: "flex", alignItems: "center" }}>
                    <span className="footer-eta">
                        ≈ {25 + (sensoryMasterEnabled ? 35 : 0) + (perClipStt ? 10 : 0)}
                        s · first run
                    </span>
                    <button
                        disabled={loading || !timelineName.trim()}
                        onClick={submit}
                        data-hotkey="primary"
                    >
                        {loading ? "Starting…" : "Analyze →"}
                    </button>
                </span>
            </div>
        </div>
    );
}
