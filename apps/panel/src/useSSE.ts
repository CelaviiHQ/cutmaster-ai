/** Hook: subscribe to /cutmaster/events/{runId} SSE stream. */

import { useEffect, useRef, useState } from "react";
import type { PipelineEvent } from "./types";

type Terminal = "done" | "error" | null;

export function useSSE(runId: string | null) {
  const [events, setEvents] = useState<PipelineEvent[]>([]);
  const [terminal, setTerminal] = useState<Terminal>(null);
  const sourceRef = useRef<EventSource | null>(null);

  useEffect(() => {
    if (!runId) {
      setEvents([]);
      setTerminal(null);
      return;
    }

    const es = new EventSource(`/cutmaster/events/${runId}`);
    sourceRef.current = es;

    // Uniform handler — every SSE event name also arrives on the generic
    // onmessage handler for servers that don't send `event:` lines. Our
    // sse-starlette emits them, so listen per-stage-name below too.
    const record = (raw: MessageEvent) => {
      try {
        const payload = JSON.parse(raw.data) as PipelineEvent;
        setEvents((prev) => [...prev, payload]);
        if (payload.stage === "done" || payload.stage === "error") {
          setTerminal(payload.stage);
          es.close();
        }
      } catch {
        // ignore parse errors
      }
    };

    for (const name of [
      "vfr_check",
      "audio_extract",
      "stt",
      "speakers",
      "scrub",
      "shot_tag",
      "audio_cues",
      "done",
      "error",
      "keepalive",
      "message",
    ]) {
      es.addEventListener(name, record as EventListener);
    }

    es.onerror = () => {
      // sse-starlette closes on terminal events; onerror fires naturally then.
      if (terminal === null) {
        // Only surface as a real error if we never saw a terminal event.
      }
    };

    return () => {
      es.close();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId]);

  return { events, terminal };
}
