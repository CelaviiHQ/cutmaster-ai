"""CutMaster AI — pipeline primitives for the A-Roll assistant.

Each module exposes both a plain Python function (callable from the HTTP
backend and the agent pipeline) and a thin ``@mcp.tool`` wrapper so Claude
Code and Claude Desktop can drive the same primitives over MCP stdio.

Three scrubbing axes
--------------------
CutMaster separates "what to remove / keep" into three independent axes.
Keep them independent — v2 product discovery showed that users want to mix
and match.

1. **Word-level** — mechanical removal driven by transcript signals.
   Module: :mod:`scrubber`.
   Examples: fillers ("um", "uh"), dead-air gaps, false-start restarts.
   Controlled by :class:`scrubber.ScrubParams`.

2. **Content-category** — semantic exclusion of whole topic classes.
   Module: :mod:`excludes`.
   Examples (wedding preset): "legal formalities", "MC talking",
   "vendor mentions". Preset-specific category lists live in
   :mod:`presets`; users pick which ones to exclude on the Configure
   screen; the Director prompt consumes the selected keys.

3. **Structural** — editorial segment selection and ordering.
   Module: :mod:`director`.
   The Director agent chooses which contiguous word blocks become the
   cut and in what order (subject to hook rules and pacing).

These axes flow through the pipeline as fields on ``UserSettings`` and
combine multiplicatively — a wedding cut can scrub fillers, exclude
"vendor mentions", and ask the Director to emphasise toasts, all at
once.
"""
