"""Caption generation — scrubbed words → caption lines → SRT.

Line-chunking rules (per v2-10.5):
  - Each line carries ``≤ MAX_CHARS`` characters and ``≤ MAX_DURATION_S``
    seconds of speech. Whichever limit hits first ends the line.
  - Preferred break points, in order:
      1. sentence punctuation (``. ? !``)
      2. clause punctuation (``, ; :``)
      3. any inter-word gap longer than ``MAX_GAP_S``
  - Never split a word.

The algorithm is deterministic and pure — given the same words it always
yields the same lines. Integration callers are responsible for:
  - filtering the scrubbed transcript down to words that survived the cut
  - remapping each word's source-timeline timestamp to the NEW-timeline
    time domain (see ``execute._map_marker_to_new_timeline``) before
    calling :func:`build_caption_lines`.

The SRT writer uses UTF-8 BOM-less output with LF line endings — the
format most social platforms expect on upload.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

# Tunables — keep these as module-level constants so tests + configure-screen
# UI can read them without duplicating the numbers.
MAX_CHARS = 32
MAX_DURATION_S = 4.0
MAX_GAP_S = 0.20  # 200 ms

SENTENCE_PUNCT = frozenset(".?!")
CLAUSE_PUNCT = frozenset(",;:")


@dataclass(frozen=True)
class CaptionLine:
    """One rendered caption line in the NEW-timeline time domain."""

    start_s: float
    end_s: float
    text: str


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------


def _word_text(word: dict) -> str:
    return str(word.get("word", "")).strip()


def _ends_with(text: str, punct: frozenset[str]) -> bool:
    if not text:
        return False
    # Handle trailing quote/close-paren: peek past them.
    for ch in reversed(text):
        if ch.isalnum():
            return False
        if ch in punct:
            return True
    return False


def _joined_length(current: str, next_word: str) -> int:
    if not current:
        return len(next_word)
    return len(current) + 1 + len(next_word)  # +1 for the joining space


def _emit(buffer: list[dict]) -> CaptionLine | None:
    """Assemble a CaptionLine from buffered words. None if empty."""
    if not buffer:
        return None
    text = " ".join(_word_text(w) for w in buffer if _word_text(w))
    if not text:
        return None
    return CaptionLine(
        start_s=float(buffer[0]["start_time"]),
        end_s=float(buffer[-1]["end_time"]),
        text=text,
    )


def build_caption_lines(words: list[dict]) -> list[CaptionLine]:
    """Group timestamped words into readable caption lines.

    ``words`` must be a list of dicts shaped like the STT output
    (``{"word": str, "start_time": float, "end_time": float, ...}``).
    Timestamps should already be in the target (new-timeline) domain.
    The function itself does not remap times.
    """
    if not words:
        return []

    lines: list[CaptionLine] = []
    buffer: list[dict] = []
    line_start_s: float | None = None
    prev_end_s: float | None = None
    current_text: str = ""

    def flush() -> None:
        nonlocal buffer, line_start_s, current_text
        line = _emit(buffer)
        if line is not None:
            lines.append(line)
        buffer = []
        line_start_s = None
        current_text = ""

    for w in words:
        word_text = _word_text(w)
        if not word_text:
            continue
        start = float(w["start_time"])
        end = float(w["end_time"])

        # Gap-break: if the incoming word sits past MAX_GAP_S from the
        # buffer's last word, close the current line before appending.
        if prev_end_s is not None and buffer and (start - prev_end_s) > MAX_GAP_S:
            flush()

        candidate_len = _joined_length(current_text, word_text)
        would_overflow_chars = candidate_len > MAX_CHARS
        would_overflow_time = (
            line_start_s is not None and (end - line_start_s) > MAX_DURATION_S
        )

        if buffer and (would_overflow_chars or would_overflow_time):
            flush()

        if not buffer:
            line_start_s = start
            current_text = word_text
        else:
            current_text = f"{current_text} {word_text}"

        buffer.append(w)
        prev_end_s = end

        # Preferred break: sentence punctuation ends the line aggressively.
        if _ends_with(word_text, SENTENCE_PUNCT):
            flush()
            prev_end_s = None
            continue

        # Secondary break: clause punctuation flushes if we're already past
        # the soft char budget. Avoids over-aggressive commas.
        if (
            _ends_with(word_text, CLAUSE_PUNCT)
            and len(current_text) >= int(MAX_CHARS * 0.7)
        ):
            flush()
            prev_end_s = None

    flush()
    return lines


# ---------------------------------------------------------------------------
# SRT emitter
# ---------------------------------------------------------------------------


def _format_srt_timestamp(seconds: float) -> str:
    """``1.234`` → ``00:00:01,234`` (SRT-style, comma decimal)."""
    if seconds < 0:
        seconds = 0.0
    total_ms = int(round(seconds * 1000))
    hours, rem_ms = divmod(total_ms, 3600 * 1000)
    minutes, rem_ms = divmod(rem_ms, 60 * 1000)
    secs, ms = divmod(rem_ms, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"


def render_srt(lines: list[CaptionLine]) -> str:
    """Render caption lines into an SRT-formatted string.

    Output uses LF line endings and no BOM. Empty ``lines`` yields an
    empty string (callers can decide whether to write the file).
    """
    out: list[str] = []
    for i, line in enumerate(lines, start=1):
        out.append(str(i))
        out.append(
            f"{_format_srt_timestamp(line.start_s)} --> "
            f"{_format_srt_timestamp(line.end_s)}"
        )
        out.append(line.text)
        out.append("")
    return "\n".join(out)


def write_srt(lines: list[CaptionLine], path: Path | str) -> Path:
    """Write an SRT file. Returns the resolved path.

    Parent directories are created on demand. File is written UTF-8
    without a BOM. Overwrites existing files.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = render_srt(lines)
    path.write_text(text, encoding="utf-8", newline="\n")
    return path
