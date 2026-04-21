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
                    {presets.map((p) => (
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

            {/* Speakers on camera — regular card. A semantic decision about
                the content (who's on screen), not a transcription tuning knob.
                Stays visible so users don't miss it. */}
            <div className="card">
                <h2>Speakers on camera</h2>
                <p className="muted">
                    How many people speak in the shoot? Helps the Director /
                    Marker agents reason about roles and stops Gemini from
                    inventing phantom speakers on solo content.
                </p>
                {/* v3-1.6 — Unsure (ghost) first, then numeric range 1…5+ */}
                <div
                    className="row"
                    style={{ gap: "var(--s-2)", flexWrap: "wrap" }}
                >
                    <button
                        className={expectedSpeakers == null ? "secondary" : "btn-ghost"}
                        onClick={() => onExpectedSpeakersChange(null)}
                        style={
                            expectedSpeakers == null
                                ? {
                                      borderColor: "var(--accent-blue)",
                                      color: "var(--accent-blue)",
                                  }
                                : undefined
                        }
                    >
                        Unsure
                    </button>
                    {[1, 2, 3, 4].map((n) => (
                        <button
                            key={n}
                            className={expectedSpeakers === n ? "" : "secondary"}
                            onClick={() => onExpectedSpeakersChange(n)}
                            style={{ minWidth: 56 }}
                        >
                            {n === 1 ? "1 (solo)" : `${n}`}
                        </button>
                    ))}
                    <button
                        className={
                            expectedSpeakers != null && expectedSpeakers > 4
                                ? ""
                                : "secondary"
                        }
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

            {/* v3-1.5 (revised) — Transcription details: STT provider + per-clip
                mode only. Speakers graduated to its own regular card above. */}
            <details className="card card--advanced">
                <summary>
                    <span>
                        Transcription details
                        {sttConfigured && (
                            <>
                                {" "}
                                <span className="muted" style={{ fontSize: "var(--fs-2)" }}>
                                    · {effectiveProvider}
                                </span>
                            </>
                        )}
                        {perClipStt && (
                            <>
                                {" "}
                                <span className="muted" style={{ fontSize: "var(--fs-2)" }}>
                                    · per-clip
                                </span>
                            </>
                        )}
                    </span>
                </summary>
                <div className="card-body">
                    {providers && providers.length > 0 && (
                        <div style={{ marginBottom: "var(--s-4)" }}>
                            <label>Transcription service</label>
                            <p className="muted" style={{ marginBottom: "var(--s-3)" }}>
                                Gemini is free with a key and fine for ≤ 8 min audio.
                                Deepgram Nova-3 handles long-form (no word-level cap)
                                and bundles diarization — recommended for interviews.
                            </p>
                            <div
                                className="row"
                                style={{
                                    gap: "var(--s-2)",
                                    flexWrap: "wrap",
                                    marginTop: 0,
                                }}
                            >
                                {providers.map((p) => {
                                    const selected = effectiveProvider === p.key;
                                    const disabled = !p.configured;
                                    return (
                                        <button
                                            key={p.key}
                                            className={selected ? "" : "secondary"}
                                            disabled={disabled}
                                            onClick={() => onSttProviderChange(p.key)}
                                            title={
                                                disabled
                                                    ? `${p.key.toUpperCase()}_API_KEY not set in .env`
                                                    : p.label
                                            }
                                        >
                                            {p.label}
                                            {disabled && " · key missing"}
                                        </button>
                                    );
                                })}
                            </div>
                            {!sttConfigured && (
                                <p
                                    className="muted"
                                    style={{
                                        marginTop: "var(--s-3)",
                                        color: "var(--err)",
                                        fontSize: "var(--fs-2)",
                                    }}
                                >
                                    No STT key configured. Add either{" "}
                                    <code>GEMINI_API_KEY</code> or{" "}
                                    <code>DEEPGRAM_API_KEY</code> to your{" "}
                                    <code>.env</code> and restart the panel.
                                </p>
                            )}
                        </div>
                    )}

                    <div>
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
                </div>
            </details>

            {/* v4 Phase 4.4 — Shot-aware editing pre-analyze toggle.
                Fine-grained per-layer overrides live on the Configure
                screen; this is just the master switch so new runs can
                opt in before analyze kicks off. */}
            <details className="card card--advanced">
                <summary>
                    <span>
                        Shot-aware editing
                        {sensoryMasterEnabled && (
                            <>
                                {" "}
                                <span
                                    className="muted"
                                    style={{ fontSize: "var(--fs-2)" }}
                                >
                                    · on
                                </span>
                            </>
                        )}
                    </span>
                </summary>
                <div className="card-body">
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
                            checked={sensoryMasterEnabled}
                            onChange={(e) =>
                                onSensoryMasterChange(e.target.checked)
                            }
                            style={{ width: "auto", height: "auto" }}
                        />
                        Enable shot tagging + boundary validation
                    </label>
                    <p
                        className="muted"
                        style={{ marginTop: "var(--s-2)", fontSize: "var(--fs-2)" }}
                    >
                        Samples frames from each timeline item and asks Gemini
                        for shot tags; validates cut boundaries at build time.
                        Adds 30–60 s on first analyze; cached after. Needs{" "}
                        <code>GEMINI_API_KEY</code>. Fine-tune individual
                        layers on the Configure screen.
                    </p>
                </div>
            </details>

            <RunsDrawer onReopen={onReopenRun} />

            {err && <div className="error-box">{err}</div>}

            <div className="row between">
                <span className="muted">
                    Preset: <code>{preset}</code> &nbsp;·&nbsp; Timeline:{" "}
                    <code>{timelineName}</code>
                </span>
                <button
                    disabled={loading || !timelineName.trim()}
                    onClick={submit}
                    data-hotkey="primary"
                >
                    {loading ? "Starting…" : "Analyze →"}
                </button>
            </div>
        </div>
    );
}
