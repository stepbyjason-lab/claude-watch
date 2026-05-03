import io
import json
from unittest.mock import patch, MagicMock

import pytest

from scripts.whisper import (
    pick_backend,
    transcribe_groq,
    transcribe_openai,
    WhisperError,
)


def test_pick_backend_prefers_groq_when_both_keys_set():
    assert pick_backend(groq_key="g", openai_key="o", forced=None) == "groq"


def test_pick_backend_falls_back_to_openai():
    assert pick_backend(groq_key=None, openai_key="o", forced=None) == "openai"


def test_pick_backend_returns_none_when_no_keys():
    assert pick_backend(groq_key=None, openai_key=None, forced=None) is None


def test_pick_backend_honors_forced_backend():
    assert pick_backend(groq_key="g", openai_key="o", forced="openai") == "openai"


def test_pick_backend_forced_without_key_returns_none():
    assert pick_backend(groq_key=None, openai_key="o", forced="groq") is None


@patch("scripts.whisper.urlopen")
def test_transcribe_groq_posts_to_correct_endpoint_with_api_key(mock_urlopen, tmp_path):
    audio = tmp_path / "a.m4a"
    audio.write_bytes(b"\x00\x00\x00\x00")
    resp = MagicMock()
    resp.read.return_value = json.dumps({
        "segments": [{"start": 0.0, "end": 1.0, "text": "hello"}]
    }).encode()
    resp.__enter__.return_value = resp
    mock_urlopen.return_value = resp
    out = transcribe_groq(audio, api_key="testkey")
    assert out == [{"t_start": 0.0, "t_end": 1.0, "text": "hello"}]
    req = mock_urlopen.call_args[0][0]
    assert "api.groq.com" in req.full_url
    # Use the documented Request API; urllib stores headers with title-case keys.
    assert req.get_header("Authorization") == "Bearer testkey"


@patch("scripts.whisper.urlopen")
def test_transcribe_openai_posts_to_correct_endpoint(mock_urlopen, tmp_path):
    audio = tmp_path / "a.m4a"
    audio.write_bytes(b"\x00")
    resp = MagicMock()
    resp.read.return_value = json.dumps({
        "segments": [{"start": 1.0, "end": 2.0, "text": "world"}]
    }).encode()
    resp.__enter__.return_value = resp
    mock_urlopen.return_value = resp
    out = transcribe_openai(audio, api_key="k")
    assert out == [{"t_start": 1.0, "t_end": 2.0, "text": "world"}]
    req = mock_urlopen.call_args[0][0]
    assert "api.openai.com" in req.full_url


@patch("scripts.whisper.urlopen", side_effect=Exception("boom"))
def test_transcribe_groq_wraps_errors_in_whisper_error(mock_urlopen, tmp_path):
    audio = tmp_path / "a.m4a"
    audio.write_bytes(b"\x00")
    with pytest.raises(WhisperError):
        transcribe_groq(audio, api_key="k")
