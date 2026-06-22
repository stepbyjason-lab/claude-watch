from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from scripts.transcribe import (
    parse_vtt,
    dedupe_cues,
    insert_speaker_breaks,
    slice_to_window,
    transcribe_local,
    transcribe_via_whisper,
    transcribe_with_fallback,
    _find_srt,
)
from scripts.whisper import WhisperError

SRT_SAMPLE = """1
00:00:01,000 --> 00:00:03,000
Hello world

2
00:00:03,000 --> 00:00:05,000
second cue
"""


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


def _fake_run_writes_srt(srt_text, srt_name="audio.srt", captured=None):
    def _run(cmd, *args, **kwargs):
        outdir = Path(cmd[cmd.index("--outdir") + 1]) if "--outdir" in cmd \
            else Path(cmd[cmd.index("--output_dir") + 1])
        (outdir / srt_name).write_text(srt_text, encoding="utf-8")
        if captured is not None:
            captured.append(cmd)
        return MagicMock(returncode=0, stdout="", stderr="")
    return _run


CUSTOM_SPEC = {"kind": "custom", "template": "python w.py"}


def test_transcribe_local_parses_srt(tmp_path):
    audio = tmp_path / "audio.m4a"
    audio.write_bytes(b"\x00")
    with patch("scripts.transcribe.subprocess.run",
               side_effect=_fake_run_writes_srt(SRT_SAMPLE)):
        cues = transcribe_local(audio, tmp_path, spec=CUSTOM_SPEC)
    assert cues[0] == {"t_start": 1.0, "t_end": 3.0, "text": "Hello world"}
    assert cues[1]["text"] == "second cue"


def test_transcribe_local_cli_spec_uses_output_dir_flags(tmp_path):
    audio = tmp_path / "audio.m4a"
    audio.write_bytes(b"\x00")
    captured = []
    spec = {"kind": "cli", "bin": "whisper", "model": "small"}
    with patch("scripts.transcribe.subprocess.run",
               side_effect=_fake_run_writes_srt(SRT_SAMPLE, captured=captured)):
        cues = transcribe_local(audio, tmp_path, spec=spec)
    assert cues[0]["text"] == "Hello world"
    cmd = captured[0]
    assert cmd[0] == "whisper"
    assert "--output_dir" in cmd and "--output_format" in cmd and "srt" in cmd
    assert "--model" in cmd and "small" in cmd


def test_transcribe_local_custom_placeholders_substituted(tmp_path):
    audio = tmp_path / "audio.m4a"
    audio.write_bytes(b"\x00")
    captured = []
    spec = {"kind": "custom", "template": "py w.py {audio} --output_dir {outdir}"}
    with patch("scripts.transcribe.subprocess.run",
               side_effect=_fake_run_writes_srt(SRT_SAMPLE, captured=captured)):
        transcribe_local(audio, tmp_path, spec=spec)
    cmd = captured[0]
    assert str(audio) in cmd and str(tmp_path) in cmd
    assert "{audio}" not in " ".join(cmd)


def test_transcribe_local_discovers_srt_by_glob(tmp_path):
    """SRT named differently than the audio stem is still found."""
    audio = tmp_path / "audio.m4a"
    audio.write_bytes(b"\x00")
    with patch("scripts.transcribe.subprocess.run",
               side_effect=_fake_run_writes_srt(SRT_SAMPLE, srt_name="out.srt")):
        cues = transcribe_local(audio, tmp_path, spec=CUSTOM_SPEC)
    assert cues[0]["text"] == "Hello world"


def test_transcribe_local_raises_on_nonzero_exit(tmp_path):
    audio = tmp_path / "audio.m4a"
    audio.write_bytes(b"\x00")
    with patch("scripts.transcribe.subprocess.run",
               return_value=MagicMock(returncode=1, stdout="", stderr="boom")):
        with pytest.raises(WhisperError, match="exited 1"):
            transcribe_local(audio, tmp_path, spec=CUSTOM_SPEC)


def test_transcribe_local_raises_when_no_srt(tmp_path):
    audio = tmp_path / "audio.m4a"
    audio.write_bytes(b"\x00")
    with patch("scripts.transcribe.subprocess.run",
               return_value=MagicMock(returncode=0, stdout="", stderr="")):
        with pytest.raises(WhisperError, match="no SRT"):
            transcribe_local(audio, tmp_path, spec=CUSTOM_SPEC)


def test_transcribe_via_whisper_local_requires_spec(tmp_path):
    audio = tmp_path / "audio.m4a"
    audio.write_bytes(b"\x00")
    with pytest.raises(WhisperError, match="no local Whisper"):
        transcribe_via_whisper(audio, backend="local", groq_key=None,
                               openai_key=None, local_spec=None)


def test_transcribe_via_whisper_routes_to_local(tmp_path):
    audio = tmp_path / "audio.m4a"
    audio.write_bytes(b"\x00")
    with patch("scripts.transcribe.subprocess.run",
               side_effect=_fake_run_writes_srt(SRT_SAMPLE)):
        cues = transcribe_via_whisper(audio, backend="local", groq_key=None,
                                      openai_key=None, local_spec=CUSTOM_SPEC,
                                      work_dir=tmp_path)
    assert cues[0]["text"] == "Hello world"


def test_transcribe_local_raises_on_empty_srt(tmp_path):
    audio = tmp_path / "audio.m4a"
    audio.write_bytes(b"\x00")
    with patch("scripts.transcribe.subprocess.run",
               side_effect=_fake_run_writes_srt("", srt_name="audio.srt")):
        with pytest.raises(WhisperError, match="no cues"):
            transcribe_local(audio, tmp_path, spec=CUSTOM_SPEC)


def test_transcribe_local_wraps_missing_binary(tmp_path):
    audio = tmp_path / "audio.m4a"
    audio.write_bytes(b"\x00")
    with patch("scripts.transcribe.subprocess.run", side_effect=FileNotFoundError("nope")):
        with pytest.raises(WhisperError, match="not runnable"):
            transcribe_local(audio, tmp_path, spec=CUSTOM_SPEC)


def test_transcribe_local_wraps_unreadable_srt(tmp_path):
    """An SRT that can't be read (here: a directory in its place) surfaces as
    WhisperError, not a raw OSError that would bypass the fallback."""
    audio = tmp_path / "audio.m4a"
    audio.write_bytes(b"\x00")
    (tmp_path / "audio.srt").mkdir()  # exists() is True, but read_text → IsADirectoryError
    with patch("scripts.transcribe.subprocess.run",
               return_value=MagicMock(returncode=0, stdout="", stderr="")):
        with pytest.raises(WhisperError, match="could not read SRT"):
            transcribe_local(audio, tmp_path, spec=CUSTOM_SPEC)


def test_transcribe_local_empty_template_message(tmp_path):
    audio = tmp_path / "audio.m4a"
    audio.write_bytes(b"\x00")
    with pytest.raises(WhisperError, match="set but empty"):
        transcribe_local(audio, tmp_path, spec={"kind": "custom", "template": "   "})


def test_transcribe_local_unparseable_template_message(tmp_path):
    audio = tmp_path / "audio.m4a"
    audio.write_bytes(b"\x00")
    with pytest.raises(WhisperError, match="could not be parsed"):
        transcribe_local(audio, tmp_path, spec={"kind": "custom", "template": 'py "unterminated'})


def test_transcribe_local_strips_control_chars_from_error(tmp_path):
    audio = tmp_path / "audio.m4a"
    audio.write_bytes(b"\x00")
    dirty = "bad\x1b[31mred\nnewline\rcarriage"
    with patch("scripts.transcribe.subprocess.run",
               return_value=MagicMock(returncode=2, stdout="", stderr=dirty)):
        with pytest.raises(WhisperError) as exc:
            transcribe_local(audio, tmp_path, spec=CUSTOM_SPEC)
    msg = str(exc.value)
    assert "\x1b" not in msg and "\n" not in msg and "\r" not in msg


def test_build_cmd_no_placeholder_appends_audio_and_outdir(tmp_path):
    audio = tmp_path / "audio.m4a"
    audio.write_bytes(b"\x00")
    captured = []
    with patch("scripts.transcribe.subprocess.run",
               side_effect=_fake_run_writes_srt(SRT_SAMPLE, captured=captured)):
        transcribe_local(audio, tmp_path, spec={"kind": "custom", "template": "python w.py"})
    cmd = captured[0]
    assert cmd[:2] == ["python", "w.py"]
    assert cmd[-3:] == [str(audio), "--outdir", str(tmp_path)]


def test_build_cmd_audio_only_placeholder_still_appends_outdir(tmp_path):
    """Only {audio} given → {outdir} must still be appended (not silently dropped)."""
    audio = tmp_path / "audio.m4a"
    audio.write_bytes(b"\x00")
    captured = []
    spec = {"kind": "custom", "template": "py w.py {audio}"}
    with patch("scripts.transcribe.subprocess.run",
               side_effect=_fake_run_writes_srt(SRT_SAMPLE, captured=captured)):
        transcribe_local(audio, tmp_path, spec=spec)
    cmd = captured[0]
    assert str(audio) in cmd
    assert "--outdir" in cmd and str(tmp_path) in cmd


def test_build_cmd_path_with_spaces_stays_one_token(tmp_path):
    """A path with spaces must not re-split into extra argv tokens (injection guard)."""
    spaced = tmp_path / "my audio file.m4a"
    spaced.write_bytes(b"\x00")
    captured = []
    spec = {"kind": "custom", "template": "py w.py {audio} --outdir {outdir}"}
    with patch("scripts.transcribe.subprocess.run",
               side_effect=_fake_run_writes_srt(SRT_SAMPLE, srt_name="my audio file.srt",
                                                captured=captured)):
        transcribe_local(spaced, tmp_path, spec=spec)
    cmd = captured[0]
    assert str(spaced) in cmd  # the full spaced path is exactly one token


def test_find_srt_prefers_stem_match_over_glob(tmp_path):
    (tmp_path / "audio.srt").write_text(SRT_SAMPLE, encoding="utf-8")
    (tmp_path / "other.srt").write_text("garbage", encoding="utf-8")
    assert _find_srt(tmp_path, "audio").name == "audio.srt"


def _fake_backend(results):
    """results: dict backend -> WhisperError instance OR cue list."""
    calls = []

    def _via(audio, *, backend, groq_key, openai_key, local_spec=None, work_dir=None):
        calls.append(backend)
        r = results[backend]
        if isinstance(r, Exception):
            raise r
        return r
    return _via, calls


def test_fallback_local_fail_uses_cloud(tmp_path):
    audio = tmp_path / "audio.m4a"
    audio.write_bytes(b"\x00")
    via, calls = _fake_backend({"local": WhisperError("boom"),
                                "groq": [{"t_start": 0.0, "t_end": 1.0, "text": "hi"}]})
    with patch("scripts.transcribe.transcribe_via_whisper", side_effect=via):
        out = transcribe_with_fallback(audio, backend="local", groq_key="g", openai_key=None,
                                       forced=None, local_spec=CUSTOM_SPEC, work_dir=tmp_path)
    assert out[0]["text"] == "hi"
    assert calls == ["local", "groq"]


def test_fallback_forced_local_does_not_fall_back(tmp_path):
    audio = tmp_path / "audio.m4a"
    audio.write_bytes(b"\x00")
    via, calls = _fake_backend({"local": WhisperError("boom"),
                                "groq": [{"t_start": 0.0, "t_end": 1.0, "text": "hi"}]})
    with patch("scripts.transcribe.transcribe_via_whisper", side_effect=via):
        out = transcribe_with_fallback(audio, backend="local", groq_key="g", openai_key=None,
                                       forced="local", local_spec=CUSTOM_SPEC, work_dir=tmp_path)
    assert out == []
    assert calls == ["local"]  # cloud was never attempted


def test_fallback_both_fail_returns_empty(tmp_path):
    audio = tmp_path / "audio.m4a"
    audio.write_bytes(b"\x00")
    via, calls = _fake_backend({"local": WhisperError("boom"), "groq": WhisperError("nope")})
    msgs = []
    with patch("scripts.transcribe.transcribe_via_whisper", side_effect=via):
        out = transcribe_with_fallback(audio, backend="local", groq_key="g", openai_key=None,
                                       forced=None, local_spec=CUSTOM_SPEC, work_dir=tmp_path,
                                       on_message=msgs.append)
    assert out == []
    assert calls == ["local", "groq"]
    assert any("fallback failed" in m for m in msgs)


def test_fallback_cloud_initial_failure_no_retry(tmp_path):
    """An auto-picked cloud backend that fails returns [] with no fallback retry,
    but the failure is reported via on_message."""
    audio = tmp_path / "audio.m4a"
    audio.write_bytes(b"\x00")
    via, calls = _fake_backend({"groq": WhisperError("rate limited")})
    msgs = []
    with patch("scripts.transcribe.transcribe_via_whisper", side_effect=via):
        out = transcribe_with_fallback(audio, backend="groq", groq_key="g", openai_key="o",
                                       forced=None, local_spec=None, work_dir=tmp_path,
                                       on_message=msgs.append)
    assert out == []
    assert calls == ["groq"]  # no second cloud attempt
    assert any("Whisper failed (groq)" in m for m in msgs)


def test_fallback_local_success_no_cloud(tmp_path):
    audio = tmp_path / "audio.m4a"
    audio.write_bytes(b"\x00")
    via, calls = _fake_backend({"local": [{"t_start": 0.0, "t_end": 1.0, "text": "ok"}]})
    with patch("scripts.transcribe.transcribe_via_whisper", side_effect=via):
        out = transcribe_with_fallback(audio, backend="local", groq_key="g", openai_key=None,
                                       forced=None, local_spec=CUSTOM_SPEC, work_dir=tmp_path)
    assert out[0]["text"] == "ok"
    assert calls == ["local"]
