/** Tiny localStorage wrapper for remembering the last run across reloads. */

const KEY_RUN_ID = "celavii.cutmaster.lastRunId";
const KEY_PRESET = "celavii.cutmaster.lastPreset";
const KEY_TIMELINE = "celavii.cutmaster.lastTimeline";

export interface PersistedSession {
    runId: string;
    preset: string;
    timelineName: string;
}

export function saveSession(s: PersistedSession) {
    try {
        localStorage.setItem(KEY_RUN_ID, s.runId);
        localStorage.setItem(KEY_PRESET, s.preset);
        localStorage.setItem(KEY_TIMELINE, s.timelineName);
    } catch { /* private mode / disabled storage */ }
}

export function loadSession(): PersistedSession | null {
    try {
        const runId = localStorage.getItem(KEY_RUN_ID);
        if (!runId) return null;
        return {
            runId,
            preset: localStorage.getItem(KEY_PRESET) || "auto",
            timelineName: localStorage.getItem(KEY_TIMELINE) || "Timeline 1",
        };
    } catch {
        return null;
    }
}

export function clearSession() {
    try {
        localStorage.removeItem(KEY_RUN_ID);
        localStorage.removeItem(KEY_PRESET);
        localStorage.removeItem(KEY_TIMELINE);
    } catch { /* ignore */ }
}
