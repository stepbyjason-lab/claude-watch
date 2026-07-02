import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import library as lib
from scripts import scenes as scenes_mod
from scripts import watch
from scripts.watch import (
    _emit_probe_frame_lines,
    _emit_slide_review_lines,
    _probe_timestamp,
    _run_probe_frame,
    _scheme_ok,
    _slides_advisories,
    _validate_freeze_args,
    _validate_probe_args,
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


def test_validate_probe_args_rejects_probe_at_without_probe_frame():
    with pytest.raises(SystemExit, match=re.escape("--probe-at requires --probe-frame")):
        _validate_probe_args(probe_frame=False, probe_at="0:03", slides=True, focus=None)


def test_validate_probe_args_rejects_probe_frame_without_slides():
    with pytest.raises(
        SystemExit, match=re.escape("--probe-frame requires --slides")
    ):
        _validate_probe_args(probe_frame=True, probe_at=None, slides=False, focus=None)


def test_validate_probe_args_rejects_probe_frame_with_focus():
    with pytest.raises(
        SystemExit, match=re.escape("--probe-frame cannot be combined with --start/--end")
    ):
        _validate_probe_args(probe_frame=True, probe_at=None, slides=True, focus=(1.0, 2.0))


def test_validate_probe_args_rejects_malformed_probe_at():
    for bad in ("abc", "1:2:3:4"):
        with pytest.raises(SystemExit, match=re.escape(f"--probe-at: bad timestamp {bad!r}")):
            _validate_probe_args(probe_frame=True, probe_at=bad, slides=True, focus=None)


def test_validate_probe_args_rejects_negative_probe_at():
    with pytest.raises(
        SystemExit,
        match=re.escape("--probe-at must be a finite number >= 0; got '-5'"),
    ):
        _validate_probe_args(probe_frame=True, probe_at="-5", slides=True, focus=None)


@pytest.mark.parametrize("bad", ["nan", "inf", "1e400"])
def test_validate_probe_args_rejects_non_finite_probe_at(bad):
    # nan < 0 is False and inf > 0 is True, so a bare `< 0` check lets these
    # slip past validation and crash later in frames.format_filename
    # ("cannot convert float NaN to integer") — must be rejected here instead.
    with pytest.raises(
        SystemExit,
        match=re.escape(f"--probe-at must be a finite number >= 0; got {bad!r}"),
    ):
        _validate_probe_args(probe_frame=True, probe_at=bad, slides=True, focus=None)


def test_validate_probe_args_accepts_valid_combos():
    _validate_probe_args(probe_frame=True, probe_at=None, slides=True, focus=None)
    _validate_probe_args(probe_frame=True, probe_at="0:03", slides=True, focus=None)
    _validate_probe_args(probe_frame=False, probe_at=None, slides=False, focus=None)
    _validate_probe_args(probe_frame=False, probe_at=None, slides=True, focus=None)


def test_probe_timestamp_defaults_to_quarter_duration():
    assert _probe_timestamp(100.0, None) == 25.0


def test_probe_timestamp_uses_override_when_given():
    assert _probe_timestamp(100.0, "10") == 10.0
    assert _probe_timestamp(100.0, "1:30") == 90.0


def test_probe_timestamp_clamps_override_beyond_duration():
    assert _probe_timestamp(100.0, "9999") == 99.5


def test_probe_timestamp_zero_duration_is_zero():
    assert _probe_timestamp(0.0, None) == 0.0
    assert _probe_timestamp(0.0, "5") == 0.0


def test_probe_timestamp_short_duration_stays_in_range():
    t = _probe_timestamp(10.0, None)
    assert 0.0 <= t <= 9.5
    assert t == 2.5


def test_probe_timestamp_exactly_at_hi_clamp_is_not_reduced_further():
    # hi = max(100.0 - 0.5, 0.0) = 99.5; a value exactly at hi must pass through
    # unchanged (not further reduced by the clamp).
    assert _probe_timestamp(100.0, "99.5") == 99.5


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
    {"light_threshold": -1}, {"light_threshold": 256}, {"light_threshold": float("nan")},
    {"merge_gap_s": -1}, {"merge_gap_s": float("nan")}, {"merge_gap_s": float("inf")},
    {"merge_dist": -1},
])
def test_validate_freeze_args_rejects_bad(override):
    with pytest.raises(SystemExit):
        _validate_freeze_args(**{**_FREEZE_OK, **override})


def test_validate_freeze_args_accepts_light_threshold_bounds():
    _validate_freeze_args(**{**_FREEZE_OK, "light_threshold": 0})    # inclusive lower
    _validate_freeze_args(**{**_FREEZE_OK, "light_threshold": 255})  # inclusive upper


def test_validate_freeze_args_accepts_merge_gap_and_dist_zero():
    # 0 disables the merge pass — it must be accepted, not rejected.
    _validate_freeze_args(**{**_FREEZE_OK, "merge_gap_s": 0})
    _validate_freeze_args(**{**_FREEZE_OK, "merge_dist": 0})
    _validate_freeze_args(**{**_FREEZE_OK, "merge_gap_s": 0, "merge_dist": 0})


def _adv_args(**kw):
    base = dict(detect="freeze", crop=None, prefer_light=False, merge_gap=15.0, merge_dist=11)
    base.update(kw)
    return argparse.Namespace(**base)


def test_slides_advisories_freeze_is_silent():
    assert _slides_advisories(_adv_args(detect="freeze", crop="1:1:0:0", prefer_light=True)) == []


def test_slides_advisories_warns_crop_in_scene_mode():
    msgs = _slides_advisories(_adv_args(detect="scene", crop="1:1:0:0"))
    assert any("--crop" in m for m in msgs)


def test_slides_advisories_warns_prefer_light_in_scene_mode():
    msgs = _slides_advisories(_adv_args(detect="scene", prefer_light=True))
    assert any("--prefer-light" in m for m in msgs)


def test_slides_advisories_warns_merge_flags_in_scene_mode():
    msgs = _slides_advisories(_adv_args(detect="scene", merge_gap=5.0))
    assert any("--merge-gap" in m for m in msgs)
    msgs = _slides_advisories(_adv_args(detect="scene", merge_dist=3))
    assert any("--merge-dist" in m for m in msgs)
    # defaults (15.0/11) produce no advisory even in scene mode
    msgs = _slides_advisories(_adv_args(detect="scene"))
    assert not any("--merge-gap" in m or "--merge-dist" in m for m in msgs)


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


def test_emit_probe_frame_lines_prints_full_block(capsys):
    _emit_probe_frame_lines(
        Path("probe/0001_t00-03.jpg"), 2.6, 320, 240, Path("/lib/work"), "https://example.com/x"
    )
    out = capsys.readouterr().out
    assert "=== probe frame ===" in out
    assert "frame: probe" in out and "0001_t00-03.jpg" in out
    # t=2.6 rounds to 3s (round(2.6) == 3), matching frames.format_filename's
    # `round(t)` — NOT int(t), which would truncate to t=00:02 and drift from
    # the actual frame filename above.
    assert "timestamp: t=00:03" in out
    assert "source_resolution: 320x240" in out
    assert "library_dir: " in out and "work" in out
    assert "next:" in out
    assert (
        'python3 watch.py "https://example.com/x" --slides --crop W:H:X:Y [...other flags]'
        in out
    )


def test_emit_probe_frame_lines_mm_can_exceed_59(capsys):
    # t=3725s -> 62:05 (round(3725) == 3725; divmod(3725, 60) == (62, 5)).
    # MM may exceed 59 per house convention (same as frames.format_filename).
    _emit_probe_frame_lines(
        Path("probe/0001_t62-05.jpg"), 3725.0, 1920, 1080, Path("/lib/work"), "video.mp4"
    )
    out = capsys.readouterr().out
    assert "timestamp: t=62:05" in out


def test_run_probe_frame_writes_meta_and_probe_frame_no_manifest(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("scripts.library.LIBRARY_ROOT", tmp_path)

    def fake_extract_frames(video, scenes, *, out_dir, native=False, **kwargs):
        assert native is True
        assert len(scenes) == 1
        assert scenes[0].t == 3.0
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "0001_t00-03.jpg").write_bytes(b"fake-jpg")
        return [{"index": 1, "t": 3.0, "path": "0001_t00-03.jpg", "kind": "probe"}]

    monkeypatch.setattr("scripts.watch.frames_mod.extract_frames", fake_extract_frames)
    monkeypatch.setattr("scripts.watch.slides_mod.probe_dimensions", lambda v: (320, 240))

    work = tmp_path / "slug"
    work.mkdir(parents=True)
    video = tmp_path / "video.mp4"
    video.write_bytes(b"fake-video")

    meta = {
        "source": "https://example.com/x",
        "duration_s": 10.0,
        "title": "Test video",
        "is_url": True,
        "source_hash": "abc123",
    }
    args = argparse.Namespace(probe_at="3")

    rc = _run_probe_frame(video, meta, work, args)

    assert rc == 0

    meta_path = work / "meta.json"
    assert meta_path.exists()
    assert json.loads(meta_path.read_text()) == meta

    assert not (work / "manifest.json").exists()

    probe_dir = work / "probe"
    probe_files = list(probe_dir.iterdir())
    assert len(probe_files) == 1
    assert probe_files[0].name == "0001_t00-03.jpg"

    captured = capsys.readouterr()
    out = captured.out
    assert "=== probe frame ===" in out
    assert "timestamp: t=00:03" in out
    assert "source_resolution: 320x240" in out
    assert "library_dir:" in out
    assert "next:" in out
    # probe_at=3 on a 10s video is NOT clamped — the stderr clamp advisory must
    # stay silent. Pins the guard's false branch: a mutant that prints the
    # advisory unconditionally (dropping the clamp-detection check) fails here.
    assert "note:" not in captured.err


def test_run_probe_frame_routes_through_probe_timestamp_clamp(tmp_path, monkeypatch, capsys):
    # Proves _run_probe_frame calls _probe_timestamp (which clamps into
    # [0, duration - 0.5]) rather than a bare _parse_ts. A mutant that swapped
    # in _parse_ts would extract at t=9999 and fail the scenes[0].t assertion
    # below, instead of the clamped t=9.5.
    monkeypatch.setattr("scripts.library.LIBRARY_ROOT", tmp_path)

    def fake_extract_frames(video, scenes, *, out_dir, native=False, **kwargs):
        assert native is True
        assert len(scenes) == 1
        assert scenes[0].t == 9.5
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "0001_t00-10.jpg").write_bytes(b"fake-jpg")
        return [{"index": 1, "t": 9.5, "path": "0001_t00-10.jpg", "kind": "probe"}]

    monkeypatch.setattr("scripts.watch.frames_mod.extract_frames", fake_extract_frames)
    monkeypatch.setattr("scripts.watch.slides_mod.probe_dimensions", lambda v: (320, 240))

    work = tmp_path / "slug"
    work.mkdir(parents=True)
    video = tmp_path / "video.mp4"
    video.write_bytes(b"fake-video")

    meta = {
        "source": "https://example.com/x",
        "duration_s": 10.0,
        "title": "Test video",
        "is_url": True,
        "source_hash": "abc123",
    }
    args = argparse.Namespace(probe_at="9999")

    rc = _run_probe_frame(video, meta, work, args)

    assert rc == 0
    err = capsys.readouterr().err
    assert "note: --probe-at 9999 clamped to t=9.5s (duration 10.0s)" in err


def test_run_probe_frame_wipes_stale_frames_before_extracting(tmp_path, monkeypatch):
    # Mutation this kills: deleting the _wipe_frames_dir(probe_dir) call in
    # _run_probe_frame, which would leave a stale probe frame from a prior run
    # (e.g. a different --probe-at) alongside the fresh one.
    monkeypatch.setattr("scripts.library.LIBRARY_ROOT", tmp_path)

    def fake_extract_frames(video, scenes, *, out_dir, native=False, **kwargs):
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "0001_t00-03.jpg").write_bytes(b"fake-jpg")
        return [{"index": 1, "t": 3.0, "path": "0001_t00-03.jpg", "kind": "probe"}]

    monkeypatch.setattr("scripts.watch.frames_mod.extract_frames", fake_extract_frames)
    monkeypatch.setattr("scripts.watch.slides_mod.probe_dimensions", lambda v: (320, 240))

    work = tmp_path / "slug"
    probe_dir = work / "probe"
    probe_dir.mkdir(parents=True)
    stale = probe_dir / "stale_0001_t00-99.jpg"
    stale.write_bytes(b"stale-jpg")

    video = tmp_path / "video.mp4"
    video.write_bytes(b"fake-video")

    meta = {
        "source": "https://example.com/x",
        "duration_s": 10.0,
        "title": "Test video",
        "is_url": True,
        "source_hash": "abc123",
    }
    args = argparse.Namespace(probe_at="3")

    rc = _run_probe_frame(video, meta, work, args)

    assert rc == 0
    remaining = list(probe_dir.iterdir())
    assert not stale.exists()
    assert [f.name for f in remaining] == ["0001_t00-03.jpg"]


def _poison(msg):
    def _raise(*args, **kwargs):
        raise AssertionError(msg)
    return _raise


def test_main_probe_frame_dispatch_never_reaches_detect_or_transcribe(
    tmp_path, monkeypatch, capsys
):
    # Deterministic (no subprocess/network) proof that `main()` short-circuits
    # to `_run_probe_frame` and returns before Stage 3/4/5. Mutation this
    # kills: removing the `if args.probe_frame: return _run_probe_frame(...)`
    # branch — a fall-through would hit one of the poisoned stubs below and
    # raise AssertionError instead of returning 0.
    monkeypatch.setattr(lib, "LIBRARY_ROOT", tmp_path)

    fake_source = tmp_path / "source.mp4"
    fake_source.write_bytes(b"fake-video")

    fake_meta = {
        "title": "Test video",
        "duration_s": 10.0,
        "source": str(fake_source),
        "is_url": False,
        "source_hash": "deadbeef",
        "focus_range_str": "",
    }
    monkeypatch.setattr(
        watch.resolve_mod, "resolve_source", lambda *a, **kw: dict(fake_meta)
    )

    def fake_copy_local(src, out_dir, *, basename="video"):
        out_dir.mkdir(parents=True, exist_ok=True)
        dest = out_dir / f"{basename}.mp4"
        dest.write_bytes(b"fake-video")
        return dest

    monkeypatch.setattr(watch.download_mod, "copy_local", fake_copy_local)

    def fake_extract_frames(video, scenes, *, out_dir, native=False, **kwargs):
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "0001_t00-03.jpg").write_bytes(b"fake-jpg")
        return [{"index": 1, "t": 3.0, "path": "0001_t00-03.jpg", "kind": "probe"}]

    monkeypatch.setattr(watch.frames_mod, "extract_frames", fake_extract_frames)
    monkeypatch.setattr(watch.slides_mod, "probe_dimensions", lambda v: (320, 240))

    # Poisoned: the probe path must never reach transcription or detection.
    monkeypatch.setattr(
        watch.transcribe_mod, "fetch_native_captions",
        _poison("probe path must not transcribe"),
    )
    monkeypatch.setattr(
        watch.transcribe_mod, "extract_audio_for_whisper",
        _poison("probe path must not transcribe"),
    )
    monkeypatch.setattr(
        watch.scenes_mod, "detect_scenes", _poison("probe path must not detect")
    )
    monkeypatch.setattr(
        watch.slides_mod, "detect_slides_freeze", _poison("probe path must not detect")
    )
    monkeypatch.setattr(
        watch.slides_mod, "detect_slides", _poison("probe path must not detect")
    )

    rc = watch.main([
        str(fake_source), "--slides", "--probe-frame", "--probe-at", "3",
        "--no-whisper", "--out-dir", str(tmp_path),
    ])

    assert rc == 0
    out = capsys.readouterr().out
    assert "=== probe frame ===" in out
    assert "source_resolution: 320x240" in out


def test_main_emits_slide_review_lines_with_correct_call_site_arg_order(
    tmp_path, monkeypatch, capsys
):
    # R08 iter-1 P1: main()'s call `_emit_slide_review_lines(flagged, merged,
    # merge_flagged)` at the Stage 7 print block must pass those three lists in
    # that exact order. flagged/merged/merge_flagged carry DISTINGUISHABLE dist
    # values (8 / 9 / 11) so that swapping the last two positional args at the
    # call site prints "merged:" with dist 11 (should be 9) and "review:
    # merge-threshold" with dist 9 (should be 11) — a mismatch this test catches.
    monkeypatch.setattr(lib, "LIBRARY_ROOT", tmp_path)

    fake_source = tmp_path / "source.mp4"
    fake_source.write_bytes(b"fake-video")

    fake_meta = {
        "title": "Test video",
        "duration_s": 10.0,
        "source": str(fake_source),
        "is_url": False,
        "source_hash": "deadbeef",
        "focus_range_str": "",
    }
    monkeypatch.setattr(
        watch.resolve_mod, "resolve_source", lambda *a, **kw: dict(fake_meta)
    )

    def fake_copy_local(src, out_dir, *, basename="video"):
        out_dir.mkdir(parents=True, exist_ok=True)
        dest = out_dir / f"{basename}.mp4"
        dest.write_bytes(b"fake-video")
        return dest

    monkeypatch.setattr(watch.download_mod, "copy_local", fake_copy_local)

    def fake_detect_slides_freeze(video, *, out_dir, **kwargs):
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "0001_t00-03.jpg").write_bytes(b"fake-jpg")
        return {
            "slides": [{"index": 1, "t": 3.0, "path": "0001_t00-03.jpg", "kind": "detected"}],
            "flagged": [(10.0, 20.0, 8)],
            "merged": [(100.0, 105.0, 9, 5.0)],
            "merge_flagged": [(200.0, 205.0, 11, 5.0)],
        }

    monkeypatch.setattr(watch.slides_mod, "detect_slides_freeze", fake_detect_slides_freeze)

    rc = watch.main([
        str(fake_source), "--slides", "--no-whisper", "--out-dir", str(tmp_path),
    ])

    assert rc == 0
    out = capsys.readouterr().out
    assert "review: near-dup t=00:10 ~ t=00:20 (dist 8)" in out
    assert "merged: t=01:40 ~ t=01:45 (dist 9, gap 5.0s)" in out
    assert "review: merge-threshold t=03:20 ~ t=03:25 (dist 11, gap 5.0s)" in out


def test_select_scenes_classic_mode_returns_five_tuple_with_empty_slide_lists(
    tmp_path, monkeypatch
):
    # R08 iter-1 P1: classic (non-slides) mode's `select_scenes` return statement
    # `return frame_records, scenes, [], [], []` must stay a 5-tuple with the
    # slides-only positions (flagged, merged, merge_flagged) all empty. A mutant
    # that drops the trailing `[]` (making it a 4-tuple) fails the unpack below.
    monkeypatch.setattr(lib, "LIBRARY_ROOT", tmp_path)

    fake_scene = scenes_mod.Scene(t=0.0, score=1.0, kind="detected")
    monkeypatch.setattr(watch.scenes_mod, "detect_scenes", lambda video, **kw: [fake_scene])

    def fake_extract_frames(video, scenes, *, out_dir, width_px=512, **kwargs):
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "0001_t00-00.jpg").write_bytes(b"fake-jpg")
        return [{"index": 1, "t": 0.0, "path": "0001_t00-00.jpg", "kind": "detected"}]

    monkeypatch.setattr(watch.frames_mod, "extract_frames", fake_extract_frames)

    args = argparse.Namespace(
        slides=False,
        scene_threshold=0.30,
        max_gap=45.0,
        max_frames=80,
        resolution=512,
    )
    meta = {"duration_s": 10.0}
    work = tmp_path / "slug"
    work.mkdir(parents=True, exist_ok=True)

    result = watch.select_scenes(Path("fake.mp4"), meta, args, None, work, cached=False)

    assert len(result) == 5
    frame_records, scenes, flagged, merged, merge_flagged = result
    assert flagged == []
    assert merged == []
    assert merge_flagged == []
    assert [fr["path"] for fr in frame_records] == ["frames/0001_t00-00.jpg"]
    assert [s["kind"] for s in scenes] == ["detected"]


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


@pytest.mark.integration
def test_watch_probe_frame_end_to_end_on_local_fixture(tmp_path):
    proc = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "watch.py"),
            str(FIXTURE),
            "--slides",
            "--probe-frame",
            "--probe-at", "3",
            "--no-whisper",
            "--cam-corner", "none",
            "--caption", "none",
            "--out-dir", str(tmp_path),
        ],
        capture_output=True, text=True, encoding="utf-8", check=False, cwd=str(ROOT),
    )
    assert proc.returncode == 0, f"watch.py failed:\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
    assert "=== probe frame ===" in proc.stdout
    assert "source_resolution:" in proc.stdout

    library_dirs = list(tmp_path.glob("*"))
    assert len(library_dirs) == 1, f"expected one library dir, got {library_dirs}"
    lib = library_dirs[0]

    probe_dir = lib / "probe"
    probe_files = list(probe_dir.glob("*"))
    assert len(probe_files) == 1, f"expected exactly one probe frame, got {probe_files}"

    frames_dir = lib / "frames"
    assert not frames_dir.exists() or not any(frames_dir.iterdir())

    assert not (lib / "manifest.json").exists()
    assert (lib / "meta.json").exists()


def test_emit_slide_review_lines_prints_review_and_merged(capsys):
    _emit_slide_review_lines(
        flagged=[(63.0, 91.0, 8)],
        merged=[(720.0, 733.0, 10, 1.0)],
        merge_flagged=[],
    )
    out = capsys.readouterr().out
    assert "review: near-dup t=01:03 ~ t=01:31 (dist 8)" in out
    assert "merged: t=12:00 ~ t=12:13 (dist 10, gap 1.0s)" in out


def test_emit_slide_review_lines_merged_only(capsys):
    # A merge with no borderline-flag must STILL be reported — this is the R06
    # transparency guarantee. Catches a regression that drops the merged loop or
    # swaps flagged/merged.
    _emit_slide_review_lines(flagged=[], merged=[(300.0, 305.0, 11, 5.0)], merge_flagged=[])
    assert capsys.readouterr().out == "merged: t=05:00 ~ t=05:05 (dist 11, gap 5.0s)\n"


def test_emit_slide_review_lines_empty_is_silent(capsys):
    _emit_slide_review_lines(flagged=[], merged=[], merge_flagged=[])
    assert capsys.readouterr().out == ""


def test_emit_slide_review_lines_merge_threshold_exact_format(capsys):
    # R08: pin the exact `review: merge-threshold` line format — dist is printed
    # bare (it's definitionally == merge_dist), gap keeps the same "%.1f" style as
    # the `merged:` line.
    _emit_slide_review_lines(
        flagged=[], merged=[], merge_flagged=[(300.0, 305.1, 11, 5.1)],
    )
    assert (
        capsys.readouterr().out
        == "review: merge-threshold t=05:00 ~ t=05:05 (dist 11, gap 5.1s)\n"
    )


def test_emit_slide_review_lines_all_three_kinds_order(capsys):
    # Output order: near-dup lines first, then merge-threshold lines, then merged:
    # lines — a fixed reading order regardless of which lists are non-empty.
    _emit_slide_review_lines(
        flagged=[(63.0, 91.0, 8)],
        merged=[(720.0, 733.0, 10, 1.0)],
        merge_flagged=[(300.0, 305.0, 11, 5.0)],
    )
    out = capsys.readouterr().out
    near_dup_idx = out.index("review: near-dup")
    merge_threshold_idx = out.index("review: merge-threshold")
    merged_idx = out.index("merged:")
    assert near_dup_idx < merge_threshold_idx < merged_idx


def test_emit_slide_review_lines_merge_flagged_only(capsys):
    # A threshold-preserved pair with no near-dup/merged pairs must STILL be
    # reported — same R06/R08 transparency guarantee as merged-only: the caller
    # can't infer a preserved pair from the manifest alone.
    _emit_slide_review_lines(flagged=[], merged=[], merge_flagged=[(100.0, 105.0, 11, 5.0)])
    assert (
        capsys.readouterr().out
        == "review: merge-threshold t=01:40 ~ t=01:45 (dist 11, gap 5.0s)\n"
    )


def test_select_scenes_wires_merge_flagged_from_detect_result(tmp_path, monkeypatch):
    # R08 wiring proof (R07 lesson: unverified wiring is a P1). detect_slides_freeze
    # is monkeypatched to return a result dict carrying merge_flagged pairs;
    # select_scenes must pass that list through unchanged as its 5th return element,
    # not drop it or swap it with merged.
    monkeypatch.setattr(lib, "LIBRARY_ROOT", tmp_path)

    fake_merge_flagged = [(100.0, 105.0, 11, 5.0)]
    fake_record = {"index": 1, "t": 100.0, "path": "0001.jpg", "kind": "freeze"}

    def fake_detect_slides_freeze(video, *, out_dir, **kwargs):
        return {
            "slides": [fake_record],
            "flagged": [],
            "merged": [],
            "merge_flagged": fake_merge_flagged,
        }

    monkeypatch.setattr(watch.slides_mod, "detect_slides_freeze", fake_detect_slides_freeze)

    args = argparse.Namespace(
        slides=True,
        detect="freeze",
        cam_corner=None,
        caption=None,
        crop=None,
        hold=3.0,
        freeze_noise="-60dB",
        phash_dist=4,
        candidate_cap=800,
        prefer_light=False,
        light_threshold=200,
        merge_gap=15.0,
        merge_dist=11,
    )

    work = tmp_path / "slug"
    work.mkdir(parents=True, exist_ok=True)

    frame_records, scenes, flagged, merged, merge_flagged = watch.select_scenes(
        Path("fake.mp4"), {}, args, None, work, cached=False,
    )

    assert merge_flagged == fake_merge_flagged
    assert merged == []
    assert flagged == []
    assert [fr["path"] for fr in frame_records] == ["frames/0001.jpg"]


def test_select_scenes_scene_mode_defaults_merge_lists_to_empty(tmp_path, monkeypatch):
    # Scene-mode detect_slides legitimately returns a dict WITHOUT "merged"/
    # "merge_flagged" keys — select_scenes must degrade both to [] via .get()
    # defaults. Exercising the .get() default path pins the key spelling: a
    # typo'd key name (e.g. result.get("merged_flagged", [])) would pass every
    # freeze-mode test (those dicts always carry the key) but fail here.
    monkeypatch.setattr(lib, "LIBRARY_ROOT", tmp_path)

    fake_record = {"index": 1, "t": 100.0, "path": "0001.jpg", "kind": "detected"}

    def fake_detect_slides(video, *, out_dir, **kwargs):
        return {"slides": [fake_record], "flagged": []}

    monkeypatch.setattr(watch.slides_mod, "detect_slides", fake_detect_slides)

    args = argparse.Namespace(
        slides=True,
        detect="scene",
        cam_corner=None,
        caption=None,
        scene_threshold=0.30,
        max_gap=45.0,
        phash_dist=4,
        candidate_cap=800,
    )

    work = tmp_path / "slug"
    work.mkdir(parents=True, exist_ok=True)

    result = watch.select_scenes(Path("fake.mp4"), {}, args, None, work, cached=False)

    assert len(result) == 5
    frame_records, scenes, flagged, merged, merge_flagged = result
    assert merged == []
    assert merge_flagged == []
    assert [fr["path"] for fr in frame_records] == ["frames/0001.jpg"]
