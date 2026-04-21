"""v4 Phase 4.5 — sensitive-string sanitizer for vision-agent prose fields.

Every vision prompt carries a GUARDRAILS block instructing the model not
to transcribe on-screen text, identify individuals, or surface private
information. Prompt guardrails aren't bulletproof — this post-hoc
scrubber runs on free-prose fields (``ShotTag.notable``,
``BoundaryVerdict.reason`` / ``suggestion``) and redacts common PII
patterns before the payload hits the cache / logs / Review screen.

Patterns intentionally kept conservative. Over-aggressive matching
hurts useful output ("shift 0.4s earlier" must survive). Under-matching
is OK — prompts already tell the model not to emit these patterns;
this is a second line of defence, not the primary guard.
"""

from __future__ import annotations

import re

_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
# Strict phone: 10-11 digit sequences with optional separators. Tight
# enough to miss timestamps ("1.25s") and segment counts.
_PHONE_RE = re.compile(
    r"""\b
        (?:\+?\d{1,3}[-.\s]?)?     # optional country code
        (?:\(?\d{3}\)?[-.\s]?)     # area code
        \d{3}[-.\s]?\d{4}          # local
        \b""",
    re.VERBOSE,
)
# SSN: NNN-NN-NNNN or NNN NN NNNN.
_SSN_RE = re.compile(r"\b\d{3}[-\s]\d{2}[-\s]\d{4}\b")


def sanitize_prose(value: str | None) -> str | None:
    """Redact common PII patterns from a free-prose string.

    Returns ``None`` unchanged (so optional fields stay optional). Empty
    strings pass through untouched.
    """
    if not value:
        return value
    out = _EMAIL_RE.sub("[redacted-email]", value)
    out = _SSN_RE.sub("[redacted-ssn]", out)
    out = _PHONE_RE.sub("[redacted-phone]", out)
    return out
