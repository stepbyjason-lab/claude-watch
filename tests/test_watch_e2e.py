import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.watch import (
    _scheme_ok,
    _validate_freeze_args,
    _validate_slides_args,
    _validate_slides_focus,
    _wipe_frames_dir,
)

ROOT = Path(__file__).parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "sample_10s.mp4"


def test_scheme_ok_allows_http_and_local_rejects_other_schemes():
    assert _scheme_ok("https://youtu.be/x")
    assert _scheme_ok("http://x")
    assert _scheme_ok("/local/path.mp4")
    assert _scheme_ok(r"D:\videos\lecture.mp4")
    assert _scheme_ok("C:/videos/x.mp4")
    assert _scheme_ok("video.mp4")
    assert not _scheme_ok("file:///etc/passwd")
    assert not _scheme_ok("ftp://x/y")
    assert not _scheme_ok("rtmp://x/y")
    assert not _scheme_ok("data:text/plain,x")
    assert not _scheme_ok("x://evil")
    assert not _scheme_ok("z://host/file")


def test_validate_rejects_slides_with_focus():
    with pytest.raises(SystemExit):
        _validate_slides_focus(slides=True, focus=(10.0, 20.0))
    _validate_slides_focus(slides=True, focus=None)
    _validate_slides_focus(slides=False, focus=(1.0, 2.0))


def test_validate_slides_args_threshold_range():
    with pytest.raises(SystemExit):
        _validate_slides_args(scene_threshold=1.5, phash_dist=5)
    with pytest.raises(SystemExit):
        _validate_slides_args(scene_threshold=0.0, phash_dist=5)
    with pytest.raises(SystemExit):
        _validate_slides_args(scene_threshold=0.1, phash_dist=99)
    _validate_slides_args(scene_threshold=0.1, phash_dist=5)


_FREEZE_OK = dict(detect="freeze", crop="100:100:0:0", freeze_noise="-50dB",
                  hold=5.0, candidate_cap=800)


def test_validate_freeze_args_accepts_good():
    _validate_freeze_args(**_FREEZE_OK)
    _validate_freeze_args(**{**_FREEZE_OK, "crop": None})  # crop optional


@pytest.mark.parametrize("override", [
    {"hold": 0}, {"hold": -1}, {"hold": float("nan")}, {"hold": float("inf")},
    {"freeze_noise": "50dB"}, {"freeze_noise": "badval"},
    {"crop": "1:2:3"}, {"crop": "0:100:0:0"},
    {"candidate_cap": 0}, {"candidate_cap": -5},
])
def test_validate_freeze_args_rejects_bad(override):
    with pytest.raises(SystemExit):
        _validate_freeze_args(**{**_FREEZE_OK, **override})


def test_validate_freeze_args_candidate_cap_checked_in_scene_mode():
    # candidate_cap guard must fire regardless of detect mode (not just freeze)
    with pytest.raises(SystemExit):
        _validate_freeze_args(detect="scene", crop=None, freeze_noise="-50dB",
                              hold=5.0, candidate_cap=0)


def test_validate_freeze_args_skips_freeze_knobs_in_scene_mode():
    # bad freeze-only knobs are ignored in scene mode (they don't apply)
    _validate_freeze_args(detect="scene", crop=None, freeze_noise="anything",
                          hold=0, candidate_cap=800)


def test_wipe_frames_dir_refuses_paths_outside_library_root(tmp_path, monkeypatch):
    monkeypatch.setattr("scripts.library.LIBRARY_ROOT", tmp_path / "library")
    outside = tmp_path / "elsewhere" / "frames"
    outside.mkdir(parents=True)
    (outside / "precious.txt").write_text("keep me")
    with pytest.raises(SystemExit):
        _wipe_frames_dir(outside)
    assert (outside / "precious.txt").exists()


def test_wipe_frames_dir_clears_inside_library_root(tmp_path, monkeypatch):
    root = tmp_path / "library"
    monkeypatch.setattr("scripts.library.LIBRARY_ROOT", root)
    frames = root / "slug" / "frames"
    frames.mkdir(parents=True)
    (frames / "0001.jpg").write_text("x")
    _wipe_frames_dir(frames)
    assert list(frames.iterdir()) == []


@pytest.mark.integration
def test_watch_end_to_end_on_local_fixture(tmp_path):
    """`scripts/watch.py <fixture> --no-whisper --out-dir <tmp>` runs the full pipeline."""
    proc = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "watch.py"),
            str(FIXTURE),
            "--no-whisper",
            "--out-dir", str(tmp_path),
        ],
        capture_output=True, text=True, encoding="utf-8", check=False, cwd=str(ROOT),
    )
    assert proc.returncode == 0, f"watch.py failed:\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"

    # Library dir created
    library_dirs = list(tmp_path.glob("*"))
    assert len(library_dirs) == 1, f"expected one library dir, got {library_dirs}"
    lib = library_dirs[0]

    # Manifest exists and has the right shape
    manifest = json.loads((lib / "manifest.json").read_text())
    assert manifest["meta"]["title"]
    assert manifest["transcript_path"]
    # Should have detected at least t=0 anchor + the two cuts at t≈3 and t≈6
    assert len(manifest["frames"]) >= 3

    # All frame files exist
    for frame in manifest["frames"]:
        assert (lib / frame["path"]).exists()

    # Stdout should contain the structured manifest block
    assert "=== claude-watch manifest ===" in proc.stdout
    assert "library_dir:" in proc.stdout


@pytest.mark.integration
def test_watch_slides_end_to_end_on_local_fixture(tmp_path):
    proc = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "watch.py"),
            str(FIXTURE),
            "--no-whisper",
            "--slides",
            "--cam-corner", "none",
            "--caption", "none",
            "--out-dir", str(tmp_path),
        ],
        capture_output=True, text=True, encoding="utf-8", check=False, cwd=str(ROOT),
    )
    assert proc.returncode == 0, f"watch.py failed:\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
    assert "slides_extracted:" in proc.stdout

    library_dirs = list(tmp_path.glob("*"))
    assert len(library_dirs) == 1, f"expected one library dir, got {library_dirs}"
    lib = library_dirs[0]
    manifest = json.loads((lib / "manifest.json").read_text())
    assert manifest["meta"]["mode"] == "slides"
    assert manifest["meta"]["dl_resolution"] == "720p"
    assert manifest["frames"]
    assert all(frame["path"].startswith("frames/") for frame in manifest["frames"])
    for frame in manifest["frames"]:
        assert (lib / frame["path"]).exists()
