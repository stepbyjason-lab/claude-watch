from scripts.transcribe import (
    parse_vtt,
    dedupe_cues,
    insert_speaker_breaks,
    slice_to_window,
)


VTT_SAMPLE = """WEBVTT

00:00:01.000 --> 00:00:03.000
Hello world

00:00:03.000 --> 00:00:05.000
Today we will learn

00:00:05.000 --> 00:00:08.000
about backpropagation
"""


def test_parse_vtt_basic_cues():
    cues = parse_vtt(VTT_SAMPLE)
    assert len(cues) == 3
    assert cues[0] == {"t_start": 1.0, "t_end": 3.0, "text": "Hello world"}
    assert cues[2]["text"] == "about backpropagation"


def test_parse_vtt_handles_hours():
    src = "WEBVTT\n\n01:02:03.000 --> 01:02:04.500\nTime\n"
    cues = parse_vtt(src)
    assert cues[0]["t_start"] == 3723.0
    assert cues[0]["t_end"] == 3724.5


def test_dedupe_cues_drops_repeated_text_when_overlapping():
    cues = [
        {"t_start": 0.0, "t_end": 2.0, "text": "hi"},
        {"t_start": 1.5, "t_end": 3.0, "text": "hi"},  # exact dup, keep extending t_end
        {"t_start": 3.5, "t_end": 4.0, "text": "bye"},
    ]
    out = dedupe_cues(cues)
    assert len(out) == 2
    assert out[0]["t_end"] == 3.0  # extended


def test_dedupe_cues_keeps_distinct_text():
    cues = [
        {"t_start": 0.0, "t_end": 1.0, "text": "a"},
        {"t_start": 0.5, "t_end": 1.5, "text": "b"},
    ]
    out = dedupe_cues(cues)
    assert [c["text"] for c in out] == ["a", "b"]


def test_dedupe_cues_collapses_youtube_rolling_extension():
    """YouTube rolling auto-caption: each cue is the previous + new words."""
    cues = [
        {"t_start": 0.0, "t_end": 1.0, "text": "Hermes might be the most powerful AI"},
        {"t_start": 1.0, "t_end": 2.0, "text": "Hermes might be the most powerful AI agent on the planet"},
        {"t_start": 2.0, "t_end": 3.0, "text": "Hermes might be the most powerful AI agent on the planet right now"},
    ]
    out = dedupe_cues(cues)
    assert len(out) == 1
    assert out[0]["text"] == "Hermes might be the most powerful AI agent on the planet right now"
    assert out[0]["t_start"] == 0.0
    assert out[0]["t_end"] == 3.0


def test_dedupe_cues_drops_tail_repetition():
    """When cue is a tail-only repeat of the prior full cue, drop it and extend t_end."""
    cues = [
        {"t_start": 0.0, "t_end": 4.0, "text": "Hermes might be the most powerful AI agent on the planet"},
        {"t_start": 4.0, "t_end": 5.0, "text": "agent on the planet"},
    ]
    out = dedupe_cues(cues)
    assert len(out) == 1
    assert out[0]["t_end"] == 5.0


def test_dedupe_cues_emits_only_new_tail_on_suffix_prefix_overlap():
    """`prev` ends with the same text `c` starts with — emit only the new tail."""
    cues = [
        {"t_start": 0.0, "t_end": 4.0, "text": "agent on the planet right now. It's an"},
        {"t_start": 4.0, "t_end": 8.0, "text": "agent on the planet right now. It's an AI personal assistant"},
    ]
    out = dedupe_cues(cues)
    # Second case is actually "rolling extension" since c.startswith(prev), so it merges into 1 cue.
    assert len(out) == 1
    assert out[0]["text"] == "agent on the planet right now. It's an AI personal assistant"


def test_dedupe_cues_emits_tail_when_overlap_is_suffix_only():
    """Pure suffix-prefix overlap (not strict startswith) emits the new tail as a fresh cue."""
    cues = [
        {"t_start": 0.0, "t_end": 4.0, "text": "Hermes might be the most powerful AI agent on the planet right now. It's an"},
        {"t_start": 4.0, "t_end": 8.0, "text": "agent on the planet right now. It's an AI personal assistant that runs"},
    ]
    out = dedupe_cues(cues)
    # The full prev does NOT start cue2 (cue2 starts mid-prev), but suffix-prefix overlaps.
    assert len(out) == 2
    assert out[0]["text"].startswith("Hermes might")
    assert out[1]["text"] == "AI personal assistant that runs"


def test_dedupe_cues_keeps_short_coincidental_overlap_separate():
    """Short overlaps (< 10 chars) shouldn't trigger merging — too likely to be coincidence."""
    cues = [
        {"t_start": 0.0, "t_end": 1.0, "text": "I went home"},  # ends with "ome"
        {"t_start": 1.0, "t_end": 2.0, "text": "ome cooking is fun"},  # starts with "ome"
    ]
    out = dedupe_cues(cues)
    assert len(out) == 2  # 3-char overlap is below threshold


def test_insert_speaker_breaks_on_long_pause():
    """Pauses > 2s introduce a 'speaker?' marker."""
    cues = [
        {"t_start": 0.0, "t_end": 1.0, "text": "hello"},
        {"t_start": 5.0, "t_end": 6.0, "text": "what's up"},  # 4s pause → break
        {"t_start": 6.5, "t_end": 7.0, "text": "good"},  # 0.5s pause → no break
    ]
    out = insert_speaker_breaks(cues, threshold_s=2.0)
    assert out[0].get("speaker_break") is False or "speaker_break" not in out[0]
    assert out[1].get("speaker_break") is True
    assert out[2].get("speaker_break") is False or "speaker_break" not in out[2]


def test_slice_to_window_filters_to_range():
    cues = [
        {"t_start": 0.0, "t_end": 1.0, "text": "a"},
        {"t_start": 3.0, "t_end": 5.0, "text": "b"},
        {"t_start": 10.0, "t_end": 12.0, "text": "c"},
    ]
    out = slice_to_window(cues, start_s=2.0, end_s=8.0)
    assert [c["text"] for c in out] == ["b"]


def test_slice_to_window_none_returns_all():
    cues = [{"t_start": 0.0, "t_end": 1.0, "text": "a"}]
    assert slice_to_window(cues, start_s=None, end_s=None) == cues
