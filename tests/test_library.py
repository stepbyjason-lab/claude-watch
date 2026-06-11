import json
import hashlib
from pathlib import Path

from scripts.library import (
    LIBRARY_ROOT,
    default_library_root,
    resolve_library_root,
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


def test_default_library_root_windows_uses_localappdata(tmp_path, monkeypatch):
    monkeypatch.setattr("scripts.library.platform.system", lambda: "Windows")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert default_library_root() == tmp_path / "claude-watch" / "library"


def test_default_library_root_macos_uses_application_support(monkeypatch):
    monkeypatch.setattr("scripts.library.platform.system", lambda: "Darwin")
    expected = Path.home() / "Library" / "Application Support" / "claude-watch" / "library"
    assert default_library_root() == expected


def test_default_library_root_linux_respects_xdg_data_home(tmp_path, monkeypatch):
    monkeypatch.setattr("scripts.library.platform.system", lambda: "Linux")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    assert default_library_root() == tmp_path / "claude-watch" / "library"


def test_default_library_root_linux_falls_back_to_local_share(monkeypatch):
    monkeypatch.setattr("scripts.library.platform.system", lambda: "Linux")
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    expected = Path.home() / ".local" / "share" / "claude-watch" / "library"
    assert default_library_root() == expected


def test_resolve_env_var_wins_over_everything(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("CLAUDE_WATCH_LIBRARY=" + str(tmp_path / "from-file") + "\n")
    monkeypatch.setattr("scripts.library.CONFIG_ENV_PATH", env_file)
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    monkeypatch.setattr("scripts.library.LEGACY_LIBRARY_ROOT", legacy)
    monkeypatch.setenv("CLAUDE_WATCH_LIBRARY", str(tmp_path / "from-env"))
    assert resolve_library_root() == tmp_path / "from-env"


def test_resolve_config_file_wins_over_legacy(tmp_path, monkeypatch):
    monkeypatch.delenv("CLAUDE_WATCH_LIBRARY", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# comment\nGROQ_API_KEY=g\nCLAUDE_WATCH_LIBRARY=\"" + str(tmp_path / "from-file") + "\"\n"
    )
    monkeypatch.setattr("scripts.library.CONFIG_ENV_PATH", env_file)
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    monkeypatch.setattr("scripts.library.LEGACY_LIBRARY_ROOT", legacy)
    assert resolve_library_root() == tmp_path / "from-file"


def test_resolve_legacy_dir_used_when_it_exists(tmp_path, monkeypatch):
    monkeypatch.delenv("CLAUDE_WATCH_LIBRARY", raising=False)
    monkeypatch.setattr("scripts.library.CONFIG_ENV_PATH", tmp_path / "absent.env")
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    monkeypatch.setattr("scripts.library.LEGACY_LIBRARY_ROOT", legacy)
    assert resolve_library_root() == legacy


def test_resolve_falls_back_to_platform_default(tmp_path, monkeypatch):
    monkeypatch.delenv("CLAUDE_WATCH_LIBRARY", raising=False)
    monkeypatch.setattr("scripts.library.CONFIG_ENV_PATH", tmp_path / "absent.env")
    monkeypatch.setattr("scripts.library.LEGACY_LIBRARY_ROOT", tmp_path / "no-legacy")
    assert resolve_library_root() == default_library_root()


def test_resolve_ignores_commented_and_empty_override(tmp_path, monkeypatch):
    monkeypatch.delenv("CLAUDE_WATCH_LIBRARY", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("# CLAUDE_WATCH_LIBRARY=/nope\nCLAUDE_WATCH_LIBRARY=\n")
    monkeypatch.setattr("scripts.library.CONFIG_ENV_PATH", env_file)
    monkeypatch.setattr("scripts.library.LEGACY_LIBRARY_ROOT", tmp_path / "no-legacy")
    assert resolve_library_root() == default_library_root()


def test_resolve_whitespace_only_env_var_is_ignored(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_WATCH_LIBRARY", "   ")
    monkeypatch.setattr("scripts.library.CONFIG_ENV_PATH", tmp_path / "absent.env")
    monkeypatch.setattr("scripts.library.LEGACY_LIBRARY_ROOT", tmp_path / "no-legacy")
    assert resolve_library_root() == default_library_root()


def test_default_library_root_windows_falls_back_without_localappdata(monkeypatch):
    monkeypatch.setattr("scripts.library.platform.system", lambda: "Windows")
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    expected = Path.home() / "AppData" / "Local" / "claude-watch" / "library"
    assert default_library_root() == expected


def test_resolve_strips_inline_comment_in_env_file(tmp_path, monkeypatch):
    monkeypatch.delenv("CLAUDE_WATCH_LIBRARY", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "CLAUDE_WATCH_LIBRARY=" + str(tmp_path / "real") + "  # set by installer\n"
    )
    monkeypatch.setattr("scripts.library.CONFIG_ENV_PATH", env_file)
    monkeypatch.setattr("scripts.library.LEGACY_LIBRARY_ROOT", tmp_path / "no-legacy")
    assert resolve_library_root() == tmp_path / "real"


def test_resolve_survives_non_utf8_env_file(tmp_path, monkeypatch):
    monkeypatch.delenv("CLAUDE_WATCH_LIBRARY", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_bytes(b"CLAUDE_WATCH_LIBRARY=\xff\xfe broken \x80\n")
    monkeypatch.setattr("scripts.library.CONFIG_ENV_PATH", env_file)
    monkeypatch.setattr("scripts.library.LEGACY_LIBRARY_ROOT", tmp_path / "no-legacy")
    assert resolve_library_root() == default_library_root()


def test_resolve_last_definition_wins_in_env_file(tmp_path, monkeypatch):
    monkeypatch.delenv("CLAUDE_WATCH_LIBRARY", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "CLAUDE_WATCH_LIBRARY=" + str(tmp_path / "old") + "\n"
        "CLAUDE_WATCH_LIBRARY=" + str(tmp_path / "new") + "\n"
    )
    monkeypatch.setattr("scripts.library.CONFIG_ENV_PATH", env_file)
    monkeypatch.setattr("scripts.library.LEGACY_LIBRARY_ROOT", tmp_path / "no-legacy")
    assert resolve_library_root() == tmp_path / "new"


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
