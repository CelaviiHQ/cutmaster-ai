/** Poll-based progress feed for the /build-plan call.
 *
 * The analyze SSE channel closes on `done`, so a fresh subscriber after
 * analyze completes wouldn't see the build_director / build_marker /
 * build_frames events the backend now emits. Standing up a separate
 * SSE just for this short-lived (5-30s) phase is heavier than polling
 * /state/{runId} every ~600ms, which already serves the Review screen.
 *
 * Returns a derived Stage[] suitable for piping straight into
 * MascotLoading. Stages render in deterministic order (Director →
 * Marker → Frames) regardless of which event arrived first, with
 * status driven by the latest event seen for each stage.
 */

import { useEffect, useState } from "react";
import { api } from "./api";
import type { PipelineEvent } from "./types";

export interface BuildStage {
    label: string;
    status: "pending" | "started" | "complete" | "failed";
    message?: string;
    /** Wall-clock seconds since the stage started, or total duration if complete. */
    elapsedS?: number;
    /** Director / Marker LLM attempt count (when telemetry is available). */
    attempts?: number;
    /** Residual validation errors after best-effort fallback (Director/Marker only). */
    validationErrors?: number;
}

// Outcome-language labels — what the editor *gets*, not which agent does
// the work. Internal stage keys stay as-is so SSE/poll wiring is untouched.
const ORDERED_STAGES: { key: string; label: string }[] = [
    { key: "build_director", label: "Composing the cut" },
    { key: "build_marker", label: "Picking B-roll moments" },
    { key: "build_frames", label: "Aligning to source timecode" },
];

interface StageData {
    attempts?: number;
    validation_errors?: number;
    [k: string]: unknown;
}

export function useBuildProgress(
    runId: string | null,
    active: boolean,
    intervalMs = 600,
): BuildStage[] {
    const [events, setEvents] = useState<PipelineEvent[]>([]);
    // 1Hz tick so the per-stage elapsed counter advances between polls
    // even when no new events arrive.
    const [now, setNow] = useState(() => Date.now() / 1000);

    useEffect(() => {
        if (!runId || !active) return;
        const tick = window.setInterval(() => setNow(Date.now() / 1000), 1000);
        return () => window.clearInterval(tick);
    }, [runId, active]);

    useEffect(() => {
        if (!runId || !active) {
            setEvents([]);
            return;
        }
        let cancelled = false;
        const poll = async () => {
            try {
                const s = await api.getState(runId);
                if (cancelled) return;
                const buildEvents = (s.events ?? []).filter((e) =>
                    typeof e.stage === "string" && e.stage.startsWith("build_"),
                );
                setEvents(buildEvents);
            } catch {
                // Transient network/404 — next tick will retry.
            }
        };
        poll();
        const id = window.setInterval(poll, intervalMs);
        return () => {
            cancelled = true;
            window.clearInterval(id);
        };
    }, [runId, active, intervalMs]);

    return ORDERED_STAGES.map(({ key, label }) => {
        // Find the latest event for this stage; events are append-only so
        // the last one wins. Track start ts separately so elapsed ticks.
        let tsStart: number | undefined;
        let tsEnd: number | undefined;
        let latest: PipelineEvent | undefined;
        for (const e of events) {
            if (e.stage !== key) continue;
            if (e.status === "started" && tsStart === undefined) tsStart = e.ts;
            if (e.status === "complete" || e.status === "failed") tsEnd = e.ts;
            latest = e;
        }
        if (!latest) {
            return { label, status: "pending" } satisfies BuildStage;
        }
        const status: BuildStage["status"] =
            latest.status === "complete"
                ? "complete"
                : latest.status === "failed"
                    ? "failed"
                    : "started";
        const data = (latest.data ?? {}) as StageData;
        let elapsedS: number | undefined;
        if (tsStart) {
            const end = tsEnd ?? (status === "started" ? now : tsStart);
            elapsedS = Math.max(0, end - tsStart);
        }
        return {
            label,
            status,
            message: latest.message,
            elapsedS,
            attempts: typeof data.attempts === "number" ? data.attempts : undefined,
            validationErrors:
                typeof data.validation_errors === "number"
                    ? data.validation_errors
                    : undefined,
        } satisfies BuildStage;
    });
}
