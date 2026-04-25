/**
 * Replay axis-resolution fixture inputs through the panel's TS resolver.
 *
 * Reads a JSON array of fixture ``input`` blocks from stdin, runs each
 * through ``resolveCutIntent`` from ``../src/axes.ts``, and writes a JSON
 * array of ``{intent, source}`` results to stdout. Used by
 * ``tests/cutmaster/test_panel_axes_parity.py`` to assert the TS mirror
 * of the Python axis resolver agrees on every fixture.
 *
 * Reason strings are deliberately omitted — they're UX copy and intentionally
 * worded slightly differently between Python and TS. Discrete fields
 * (intent, source) are the contract.
 *
 * Run via: ``node --experimental-strip-types --no-warnings scripts/replay-fixtures.ts``
 * (Node 22.6+ supports native TS stripping.)
 */

import { resolveCutIntent } from "../src/axes.ts";
import type { ContentType, TimelineMode } from "../src/types.ts";

interface FixtureInput {
  content_type: ContentType;
  cut_intent: string | null;
  duration_s: number;
  timeline_mode: TimelineMode;
  num_clips?: number;
  reorder_allowed?: boolean;
  takes_already_scrubbed?: boolean;
}

async function readStdin(): Promise<string> {
  const chunks: Buffer[] = [];
  for await (const chunk of process.stdin) chunks.push(chunk as Buffer);
  return Buffer.concat(chunks).toString("utf8");
}

const inputs: FixtureInput[] = JSON.parse(await readStdin());

const results = inputs.map((inp) => {
  const { intent, source } = resolveCutIntent(
    inp.content_type,
    inp.duration_s,
    inp.num_clips ?? 1,
    inp.timeline_mode,
    inp.takes_already_scrubbed ?? false,
  );
  return { intent, source };
});

process.stdout.write(JSON.stringify(results));
