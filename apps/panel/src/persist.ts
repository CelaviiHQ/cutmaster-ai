/** Tiny localStorage wrapper for remembering the last run across reloads. */

const KEY_RUN_ID = "celavii.cutmaster.lastRunId";
const KEY_PRESET = "celavii.cutmaster.lastPreset";
const KEY_TIMELINE = "celavii.cutmaster.lastTimeline";
const KEY_SAVED_AT = "celavii.cutmaster.savedAt";

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
        localStorage.setItem(KEY_SAVED_AT, String(Date.now()));
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

export function loadSavedAt(): number | null {
    try {
        const raw = localStorage.getItem(KEY_SAVED_AT);
        return raw ? Number(raw) : null;
    } catch {
        return null;
    }
}

export function clearSession() {
    try {
        localStorage.removeItem(KEY_RUN_ID);
        localStorage.removeItem(KEY_PRESET);
        localStorage.removeItem(KEY_TIMELINE);
        localStorage.removeItem(KEY_SAVED_AT);
    } catch { /* ignore */ }
}

export function formatRelativeTime(ts: number, now: number = Date.now()): string {
    const diff = Math.max(0, now - ts);
    const sec = Math.floor(diff / 1000);
    if (sec < 10) return "just now";
    if (sec < 60) return `${sec}s ago`;
    const min = Math.floor(sec / 60);
    if (min < 60) return `${min}m ago`;
    const hr = Math.floor(min / 60);
    if (hr < 24) return `${hr}h ago`;
    const day = Math.floor(hr / 24);
    return `${day}d ago`;
}
