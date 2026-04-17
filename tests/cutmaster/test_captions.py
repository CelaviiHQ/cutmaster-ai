"""Tests for cutmaster.captions — line chunking + SRT writer."""

from pathlib import Path

from celavii_resolve.cutmaster import captions
from celavii_resolve.cutmaster.captions import CaptionLine


def _w(word: str, start: float, end: float, speaker: str = "S1") -> dict:
    return {"word": word, "start_time": start, "end_time": end, "speaker_id": speaker}


def test_empty_words_returns_empty_list():
    assert captions.build_caption_lines([]) == []


def test_short_sentence_fits_one_line():
    words = [
        _w("Hello", 0.0, 0.4),
        _w("world.", 0.5, 0.9),
    ]
    lines = captions.build_caption_lines(words)
    assert len(lines) == 1
    assert lines[0].text == "Hello world."
    assert lines[0].start_s == 0.0
    assert lines[0].end_s == 0.9


def test_sentence_punctuation_ends_line_even_when_short():
    words = [
        _w("Hi!", 0.0, 0.2),
        _w("Next", 0.3, 0.5),
        _w("sentence.", 0.5, 1.0),
    ]
    lines = captions.build_caption_lines(words)
    assert [line.text for line in lines] == ["Hi!", "Next sentence."]


def test_char_budget_forces_break():
    # Each word ~10 chars, no punctuation; 4 of them exceeds 32 chars.
    words = [
        _w("Lorem", 0.0, 0.4),
        _w("ipsum", 0.5, 0.9),
        _w("dolorem", 1.0, 1.4),
        _w("sitametum", 1.5, 2.0),
        _w("consectetur", 2.1, 2.6),
    ]
    lines = captions.build_caption_lines(words)
    assert len(lines) >= 2
    for line in lines:
        assert len(line.text) <= captions.MAX_CHARS


def test_time_budget_forces_break():
    # Four words spanning 6 seconds with no punctuation — must break by time.
    words = [
        _w("one", 0.0, 1.5),
        _w("two", 1.5, 3.0),
        _w("three", 3.0, 4.5),
        _w("four", 4.5, 6.0),
    ]
    lines = captions.build_caption_lines(words)
    assert len(lines) >= 2
    for line in lines:
        assert (line.end_s - line.start_s) <= captions.MAX_DURATION_S + 0.01


def test_long_gap_between_words_forces_break():
    words = [
        _w("first", 0.0, 0.5),
        _w("part", 0.5, 0.9),
        # 3-second gap — far beyond MAX_GAP_S
        _w("second", 4.0, 4.5),
        _w("part", 4.5, 5.0),
    ]
    lines = captions.build_caption_lines(words)
    assert len(lines) == 2
    assert lines[0].text == "first part"
    assert lines[1].text == "second part"


def test_clause_break_only_past_soft_budget():
    # "Hi, there" is short — should NOT break on the comma.
    short = [_w("Hi,", 0.0, 0.3), _w("there.", 0.4, 0.8)]
    short_lines = captions.build_caption_lines(short)
    assert len(short_lines) == 1
    assert short_lines[0].text == "Hi, there."


def test_srt_timestamp_format():
    assert captions._format_srt_timestamp(0.0) == "00:00:00,000"
    assert captions._format_srt_timestamp(1.234) == "00:00:01,234"
    assert captions._format_srt_timestamp(61.5) == "00:01:01,500"
    assert captions._format_srt_timestamp(3661.0) == "01:01:01,000"


def test_srt_timestamp_clamps_negative():
    assert captions._format_srt_timestamp(-1.0) == "00:00:00,000"


def test_render_srt_produces_valid_structure():
    lines = [
        CaptionLine(start_s=0.0, end_s=1.0, text="Hello world."),
        CaptionLine(start_s=1.5, end_s=3.0, text="Second line."),
    ]
    out = captions.render_srt(lines)
    # SRT indices are 1-based.
    assert "1\n" in out
    assert "2\n" in out
    assert "00:00:00,000 --> 00:00:01,000" in out
    assert "Hello world." in out
    assert "Second line." in out
    # Blank line between entries.
    assert "\n\n" in out


def test_render_empty_yields_empty_string():
    assert captions.render_srt([]) == ""


def test_write_srt_creates_parent_dirs(tmp_path: Path):
    srt = tmp_path / "nested" / "dirs" / "out.srt"
    lines = [CaptionLine(start_s=0.0, end_s=1.0, text="Hi.")]
    captions.write_srt(lines, srt)
    assert srt.exists()
    content = srt.read_text(encoding="utf-8")
    assert "Hi." in content
    assert "00:00:00,000" in content


def test_write_srt_overwrites_existing(tmp_path: Path):
    path = tmp_path / "out.srt"
    path.write_text("stale", encoding="utf-8")
    captions.write_srt(
        [CaptionLine(start_s=0.0, end_s=1.0, text="Fresh.")],
        path,
    )
    assert "Fresh." in path.read_text(encoding="utf-8")
    assert "stale" not in path.read_text(encoding="utf-8")
