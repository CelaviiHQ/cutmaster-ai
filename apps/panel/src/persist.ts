/** localStorage-backed "session pointer".
 *
 * We only persist the run_id + a savedAt timestamp. Everything else
 * (preset, timeline_name, user_settings, review_state) lives on the
 * server under /cutmaster/state/{run_id} and is hydrated on mount.
 * That keeps the browser's view consistent with the server's view.
 */

const KEY_RUN_ID = "cutmaster.lastRunId";
const KEY_SAVED_AT = "cutmaster.savedAt";

export function saveRunPointer(runId: string) {
    try {
        localStorage.setItem(KEY_RUN_ID, runId);
        localStorage.setItem(KEY_SAVED_AT, String(Date.now()));
    } catch {
        /* private mode / disabled storage */
    }
}

export function loadRunId(): string | null {
    try {
        return localStorage.getItem(KEY_RUN_ID);
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

/** Bump the savedAt timestamp without changing run_id — used as the
 *  auto-save signal from settings/build/SSE-complete triggers. */
export function touchSavedAt() {
    try {
        localStorage.setItem(KEY_SAVED_AT, String(Date.now()));
    } catch {
        /* ignore */
    }
}

export function clearSession() {
    try {
        localStorage.removeItem(KEY_RUN_ID);
        localStorage.removeItem(KEY_SAVED_AT);
    } catch {
        /* ignore */
    }
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
