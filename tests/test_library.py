import json
import hashlib
from pathlib import Path

from scripts.library import (
    LIBRARY_ROOT,
    slug_for,
    cache_lookup,
    write_manifest,
    sanitize_title,
)


def test_sanitize_title_lowercases_and_dashes():
    assert sanitize_title("Lecture 3 — Backpropagation!") == "lecture-3-backpropagation"


def test_sanitize_title_collapses_runs_of_dashes():
    assert sanitize_title("a__b   c") == "a-b-c"


def test_slug_is_stable_for_same_url_and_focus():
    meta = {
        "title": "Lecture 3",
        "source": "https://youtu.be/abc",
        "watched_at": "2026-05-03",
        "focus_range_str": "",
    }
    assert slug_for(meta) == slug_for(meta)


def test_slug_differs_for_different_focus_range():
    base = {
        "title": "Lecture 3",
        "source": "https://youtu.be/abc",
        "watched_at": "2026-05-03",
        "focus_range_str": "",
    }
    other = dict(base, focus_range_str="5:00-8:00")
    assert slug_for(base) != slug_for(other)


def test_slug_differs_for_slides_mode():
    base = {
        "title": "L",
        "source": "https://x",
        "watched_at": "2026-05-03",
        "focus_range_str": "",
    }
    slides = dict(base, mode="slides", dl_resolution="720p")
    assert slug_for(base) != slug_for(slides)


def test_slug_differs_for_resolution():
    a = {
        "title": "L",
        "source": "https://x",
        "watched_at": "2026-05-03",
        "focus_range_str": "",
        "mode": "slides",
        "dl_resolution": "720p",
    }
    b = dict(a, dl_resolution="1080p")
    assert slug_for(a) != slug_for(b)


def test_slug_default_mode_unchanged_when_fields_absent():
    bare = {
        "title": "L",
        "source": "https://x",
        "watched_at": "2026-05-03",
        "focus_range_str": "",
    }
    explicit = dict(bare, mode="default", dl_resolution="best", slides_profile="")
    assert slug_for(bare) == slug_for(explicit)


def test_slug_default_mode_matches_upstream_hash():
    meta = {
        "title": "L",
        "source": "https://x",
        "watched_at": "2026-05-03",
        "focus_range_str": "",
    }
    expected = hashlib.sha1(("https://x" + "|" + "").encode("utf-8")).hexdigest()[:4]
    assert slug_for(meta).endswith("-" + expected)


def test_slug_busts_on_any_slides_flag_change():
    a = {
        "title": "L",
        "source": "https://x",
        "watched_at": "2026-05-03",
        "focus_range_str": "",
        "mode": "slides",
        "dl_resolution": "720p",
        "slides_profile": "tr|bottom|0.1|20|4|10",
    }
    for changed in (
        "tl|bottom|0.1|20|4|10",
        "tr|top|0.1|20|4|10",
        "tr|bottom|0.2|20|4|10",
        "tr|bottom|0.1|20|6|12",
    ):
        assert slug_for(a) != slug_for(dict(a, slides_profile=changed))


def test_slug_format_is_date_title_hash():
    meta = {
        "title": "Hello World",
        "source": "https://x",
        "watched_at": "2026-05-03",
        "focus_range_str": "",
    }
    s = slug_for(meta)
    parts = s.split("-")
    # 2026 / 05 / 03 / hello / world / <hash>
    assert s.startswith("2026-05-03-")
    assert len(parts[-1]) == 4  # 4-char short hash


def test_cache_lookup_returns_none_when_missing(tmp_path, monkeypatch):
    monkeypatch.setattr("scripts.library.LIBRARY_ROOT", tmp_path)
    assert cache_lookup("nonexistent-slug", "deadbeef") is None


def test_cache_lookup_hits_when_meta_hash_matches(tmp_path, monkeypatch):
    monkeypatch.setattr("scripts.library.LIBRARY_ROOT", tmp_path)
    slug = "2026-05-03-x-1234"
    d = tmp_path / slug
    d.mkdir()
    (d / "meta.json").write_text(json.dumps({"source_hash": "abc123"}))
    assert cache_lookup(slug, "abc123") == d


def test_cache_lookup_misses_when_meta_hash_differs(tmp_path, monkeypatch):
    monkeypatch.setattr("scripts.library.LIBRARY_ROOT", tmp_path)
    slug = "2026-05-03-x-1234"
    d = tmp_path / slug
    d.mkdir()
    (d / "meta.json").write_text(json.dumps({"source_hash": "old"}))
    assert cache_lookup(slug, "new") is None


def test_write_manifest_emits_expected_shape(tmp_path):
    out = tmp_path / "manifest.json"
    write_manifest(
        path=out,
        meta={"title": "x", "duration_s": 10, "source": "u", "watched_at": "2026-05-03"},
        scenes=[{"t": 0.0, "score": 1.0, "kind": "detected"}],
        frames=[{"index": 1, "t": 0.0, "path": "frames/0001_t00-00.jpg"}],
        transcript_path="transcript.json",
        focus_range=None,
    )
    data = json.loads(out.read_text())
    assert data["meta"]["title"] == "x"
    assert data["scenes"][0]["kind"] == "detected"
    assert data["frames"][0]["path"] == "frames/0001_t00-00.jpg"
    assert data["transcript_path"] == "transcript.json"
    assert data["focus_range"] is None


def test_write_manifest_serializes_focus_range(tmp_path):
    out = tmp_path / "manifest.json"
    write_manifest(
        path=out,
        meta={"title": "x", "duration_s": 10, "source": "u", "watched_at": "2026-05-03"},
        scenes=[],
        frames=[],
        transcript_path="transcript.json",
        focus_range=(120.5, 480.0),
    )
    data = json.loads(out.read_text())
    assert data["focus_range"] == {"start_s": 120.5, "end_s": 480.0}


def test_write_manifest_creates_parent_dir(tmp_path):
    out = tmp_path / "new_slug_dir" / "manifest.json"
    write_manifest(
        path=out,
        meta={},
        scenes=[],
        frames=[],
        transcript_path="transcript.json",
        focus_range=None,
    )
    assert out.exists()
