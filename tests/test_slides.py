from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import scripts.slides as slides
from scripts.slides import (
    MERGE_MAX_VANISH,
    CandidateCapExceeded,
    build_crop_vf,
    detect_slide_crop,
    detect_slides,
    detect_slides_freeze,
    dhash,
    hamming,
    mean_luma,
    parse_crop,
    phash_dedup,
    probe_dimensions,
    time_aware_merge,
    validate_freeze_noise,
    vanish_ratio,
    _freeze_periods,
    _read_pgm,
    _surviving_audit_lines,
    _trim_high_motion_edges,
    _vanish_from_grays,
)

FIXTURE = Path(__file__).parent / "fixtures" / "sample_10s.mp4"


def test_crop_excludes_top_right_cam_and_bottom_caption():
    vf = build_crop_vf(1920, 1080, cam_corner="tr", caption="bottom")
    assert vf == "crop=1536:918:0:0,"


def test_crop_left_cam_shifts_x_origin():
    vf = build_crop_vf(1000, 1000, cam_corner="bl", caption="none")
    assert vf == "crop=800:1000:200:0,"


def test_crop_none_none_is_full_frame():
    assert build_crop_vf(1280, 720, cam_corner="none", caption="none") == "crop=1280:720:0:0,"


def test_crop_rejects_invalid_enum():
    with pytest.raises(ValueError):
        build_crop_vf(1280, 720, cam_corner="middle", caption="bottom")


def test_crop_falls_back_to_full_frame_when_region_too_small(recwarn):
    vf = build_crop_vf(
        1000,
        1000,
        cam_corner="tr",
        caption="bottom",
        cam_frac=0.7,
        cap_frac=0.7,
    )
    assert vf == "crop=1000:1000:0:0,"
    assert any("slide region" in str(w.message).lower() for w in recwarn.list)


def test_hamming_counts_differing_bits():
    assert hamming(0b1010, 0b1000) == 1
    assert hamming(0, 0xFFFFFFFFFFFFFFFF) == 64
    assert hamming(42, 42) == 0


@pytest.mark.integration
def test_dhash_is_stable_and_returns_64bit(tmp_path):
    import subprocess

    jpg = tmp_path / "f.jpg"
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-ss",
            "1.0",
            "-i",
            str(FIXTURE),
            "-frames:v",
            "1",
            str(jpg),
        ],
        check=True,
    )
    h1 = dhash(jpg)
    h2 = dhash(jpg)
    assert h1 == h2
    assert 0 <= h1 < (1 << 64)


@pytest.mark.integration
def test_dhash_differs_for_different_frames(tmp_path):
    import subprocess

    # dhash keys on EDGES, so it needs frames with content. The shared solid-colour
    # fixture (red/white/blue) has no edges and hashes to ~0 — unrepresentative of
    # real slides, which always carry text/graphics. Use two distinct test patterns.
    a, b = tmp_path / "a.jpg", tmp_path / "b.jpg"
    for src, p in [("testsrc2=size=160x120", a), ("mandelbrot=size=160x120", b)]:
        subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-f",
                "lavfi",
                "-i",
                src,
                "-frames:v",
                "1",
                str(p),
            ],
            check=True,
        )
    # > drop_dist (4): two genuinely distinct slides must stay separable, i.e. the
    # dedup must KEEP them, not just be "non-zero". Distinct patterns clear this easily.
    assert hamming(dhash(a), dhash(b)) > 4


def _records(ts):
    return [{"index": i + 1, "t": float(t), "path": f"/tmp/{i}.jpg"} for i, t in enumerate(ts)]


def test_dedup_collapses_near_identical_consecutive():
    recs = _records([0, 1, 2, 3])
    hashes = {
        "/tmp/0.jpg": 0b0000,
        "/tmp/1.jpg": 0b0001,
        "/tmp/2.jpg": 0b0000,
        "/tmp/3.jpg": 0b1111_1111,
    }
    kept, flagged = phash_dedup(
        recs,
        crop_vf="",
        drop_dist=2,
        flag_dist=4,
        hash_fn=lambda p, c: hashes[p],
    )
    assert [r["t"] for r in kept] == [0.0, 3.0]
    assert flagged == []


def test_dedup_keeps_and_flags_borderline_pair():
    recs = _records([0, 1])
    hashes = {"/tmp/0.jpg": 0b0000, "/tmp/1.jpg": 0b0111}
    kept, flagged = phash_dedup(
        recs,
        crop_vf="",
        drop_dist=2,
        flag_dist=4,
        hash_fn=lambda p, c: hashes[p],
    )
    assert [r["t"] for r in kept] == [0.0, 1.0]
    assert flagged == [(0.0, 1.0, 3)]


def test_dedup_keeps_clearly_distinct_without_flag():
    recs = _records([0, 1])
    hashes = {"/tmp/0.jpg": 0b0000, "/tmp/1.jpg": 0xFFFF}
    kept, flagged = phash_dedup(
        recs,
        crop_vf="",
        drop_dist=2,
        flag_dist=4,
        hash_fn=lambda p, c: hashes[p],
    )
    assert [r["t"] for r in kept] == [0.0, 1.0]
    assert flagged == []


def test_dedup_populates_hash_cache_for_kept_frames_only():
    # FIX 4 (perf): phash_dedup can optionally record the hash of every frame it
    # keeps into a caller-supplied dict, so a later pass (time_aware_merge) reusing
    # the same hash_fn/crop_vf doesn't need to recompute them. Dropped frames are
    # not cached — nothing downstream needs their hash.
    recs = _records([0, 1, 2])
    hashes = {"/tmp/0.jpg": 0b0000, "/tmp/1.jpg": 0b0001, "/tmp/2.jpg": 0xFFFF}
    cache: dict[str, int] = {}
    kept, flagged = phash_dedup(
        recs, crop_vf="", drop_dist=2, flag_dist=4,
        hash_fn=lambda p, c: hashes[p], hash_cache=cache,
    )
    assert [r["t"] for r in kept] == [0.0, 2.0]  # frame 1 dropped (dist 1 <= drop_dist 2)
    assert cache == {"/tmp/0.jpg": 0b0000, "/tmp/2.jpg": 0xFFFF}


def test_dedup_rejects_flag_dist_not_greater_than_drop_dist():
    # flag_dist <= drop_dist would silently drop borderline frames instead of
    # flagging them — the function must refuse this configuration.
    with pytest.raises(ValueError):
        phash_dedup(
            _records([0, 1]),
            crop_vf="",
            drop_dist=5,
            flag_dist=4,
            hash_fn=lambda p, c: 0,
        )


@pytest.mark.integration
def test_probe_dimensions_reads_fixture_wh():
    w, h = probe_dimensions(FIXTURE)
    assert w > 0 and h > 0


@pytest.mark.integration
def test_detect_slides_keeps_multiple_distinct_slides(tmp_path):
    import subprocess

    # The core risk of this dedup is OVER-merging distinct slides. The shared
    # solid-colour fixture has no edges (collapses under an edge hash), so it
    # can't test that. Build a 3-segment content video (distinct patterns with
    # hard cuts) and verify the end-to-end dhash path keeps them apart.
    clip = tmp_path / "deck.mp4"
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-t", "2", "-i", "testsrc2=size=320x240:rate=4",
            "-f", "lavfi", "-t", "2", "-i", "mandelbrot=size=320x240:rate=4",
            "-f", "lavfi", "-t", "2", "-i", "rgbtestsrc=size=320x240:rate=4",
            "-filter_complex",
            "[0:v]format=yuv420p[a];[1:v]format=yuv420p[b];[2:v]format=yuv420p[c];"
            "[a][b][c]concat=n=3:v=1[v]",
            "-map", "[v]", str(clip),
        ],
        check=True,
    )
    out = detect_slides(
        clip,
        out_dir=tmp_path / "frames",
        cam_corner="none",
        caption="none",
        threshold=0.30,
        max_gap=20.0,
        drop_dist=4,
        flag_dist=10,
        width_px=1280,
        candidate_cap=800,
    )
    # three visually distinct segments must survive the dhash dedup
    assert len(out["slides"]) >= 3
    for record in out["slides"]:
        assert (tmp_path / "frames" / record["path"]).exists()
        assert Path(record["path"]).name == record["path"]
    assert "flagged" in out


@pytest.mark.integration
def test_detect_slides_enforces_candidate_cap_after_coverage_floor(tmp_path):
    with pytest.raises(CandidateCapExceeded):
        detect_slides(
            FIXTURE,
            out_dir=tmp_path,
            cam_corner="none",
            caption="none",
            threshold=0.30,
            max_gap=2.0,
            drop_dist=4,
            flag_dist=10,
            width_px=1280,
            candidate_cap=2,
        )
    assert not list(tmp_path.glob("*.jpg"))


# ---- freeze-based slide capture ----

def test_parse_crop_valid():
    assert parse_crop("1840:1180:160:320") == (1840, 1180, 160, 320)
    assert parse_crop("100:100:0:0") == (100, 100, 0, 0)


@pytest.mark.parametrize("bad", ["1840:1180:160", "1840:1180:160:320:0", "a:b:c:d",
                                  "0:100:0:0", "100:0:0:0", "100:100:-1:0", "100:100:0:-5"])
def test_parse_crop_rejects_bad(bad):
    with pytest.raises(ValueError):
        parse_crop(bad)


@pytest.mark.parametrize("ok", ["-50dB", "-60dB", "0", "0.003", "1", "1.0"])
def test_validate_freeze_noise_accepts(ok):
    assert validate_freeze_noise(ok) == ok


@pytest.mark.parametrize("bad", ["-50", "abc", "50db", "2.0", "-1", "0.5x", "; rm -rf",
                                  "12dB", "50dB", "0dB"])  # >=0 dB marks whole video frozen
def test_validate_freeze_noise_rejects(bad):
    with pytest.raises(ValueError):
        validate_freeze_noise(bad)


def _freeze_proc(stderr, rc=0):
    return MagicMock(returncode=rc, stderr=stderr)


def test_freeze_periods_pairs_start_and_duration():
    stderr = (
        "[freezedetect] lavfi.freezedetect.freeze_start: 10.0\n"
        "[freezedetect] lavfi.freezedetect.freeze_duration: 4.0\n"
        "[freezedetect] lavfi.freezedetect.freeze_end: 14.0\n"
        "[freezedetect] lavfi.freezedetect.freeze_start: 30.0\n"
        "[freezedetect] lavfi.freezedetect.freeze_duration: 8.0\n"
        "[freezedetect] lavfi.freezedetect.freeze_end: 38.0\n"
    )
    with patch("scripts.slides.subprocess.run", return_value=_freeze_proc(stderr)):
        out = _freeze_periods(Path("v.mp4"), crop_vf="", hold=3, noise="-50dB")
    assert out == [(10.0, 4.0), (30.0, 8.0)]


def test_freeze_periods_keeps_tail_when_frozen_at_eof():
    stderr = (
        "freeze_start: 10.0\nfreeze_duration: 4.0\nfreeze_end: 14.0\n"
        "freeze_start: 50.0\n"  # no duration/end → still frozen at EOF
    )
    with patch("scripts.slides.subprocess.run", return_value=_freeze_proc(stderr)):
        out = _freeze_periods(Path("v.mp4"), crop_vf="", hold=3, noise="-50dB")
    assert out == [(10.0, 4.0), (50.0, None)]


def test_freeze_periods_zero_events():
    with patch("scripts.slides.subprocess.run", return_value=_freeze_proc("no freezes here")):
        assert _freeze_periods(Path("v.mp4"), crop_vf="", hold=3, noise="-50dB") == []


def test_freeze_periods_raises_on_ffmpeg_failure():
    with patch("scripts.slides.subprocess.run", return_value=_freeze_proc("boom", rc=1)):
        with pytest.raises(RuntimeError, match="freezedetect failed"):
            _freeze_periods(Path("v.mp4"), crop_vf="", hold=3, noise="-50dB")


def test_detect_slides_freeze_extracts_dedups_and_crops(tmp_path):
    periods = [(10.0, 6.0), (30.0, 6.0), (50.0, None)]

    def fake_extract(video, scenes, *, out_dir, width_px, native, crop_vf):
        assert crop_vf == "crop=100:100:0:0,"  # explicit crop forwarded to extraction
        recs = []
        for i, sc in enumerate(scenes, start=1):
            name = f"{i:04d}.jpg"
            (out_dir / name).write_bytes(b"x")
            recs.append({"index": i, "t": sc.t, "path": name, "kind": sc.kind})
        return recs

    # frame 2 is near frame 1 (dist 1 <= drop_dist) → dropped; frame 3 is distinct.
    # Inject hashes via a phash_dedup wrapper, because phash_dedup's default hash_fn is
    # bound to the real dhash at def time (patching slides.dhash wouldn't reach it).
    import scripts.slides as S
    orig_dedup = S.phash_dedup
    hashes = iter([0b0000, 0b0001, 0b1111_1111])

    def fake_dedup(recs, *, crop_vf, drop_dist, flag_dist, hash_fn=None, hash_cache=None):
        return orig_dedup(recs, crop_vf=crop_vf, drop_dist=drop_dist,
                          flag_dist=flag_dist, hash_fn=lambda p, c="": next(hashes),
                          hash_cache=hash_cache)

    with patch("scripts.slides._freeze_periods", return_value=periods), \
         patch("scripts.slides.frames_mod.extract_frames", side_effect=fake_extract), \
         patch("scripts.slides.phash_dedup", side_effect=fake_dedup):
        out = detect_slides_freeze(
            Path("v.mp4"), out_dir=tmp_path, crop="100:100:0:0",
            hold=5.0, drop_dist=4, flag_dist=10, width_px=1280, candidate_cap=800,
            merge_gap_s=0,  # this test targets phash_dedup only, not the R06 merge pass
        )
    assert len(out["slides"]) == 2  # 3 extracted, 1 deduped
    # The *correct* file survives: exactly the kept records' files remain on disk
    # (a count-only check would pass even if a kept frame were wrongly deleted).
    remaining = {p.name for p in tmp_path.glob("*.jpg")}
    kept_names = {Path(s["path"]).name for s in out["slides"]}
    assert remaining == kept_names


def test_detect_slides_freeze_enforces_cap(tmp_path):
    periods = [(float(i), 6.0) for i in range(5)]
    with patch("scripts.slides._freeze_periods", return_value=periods):
        with pytest.raises(CandidateCapExceeded):
            detect_slides_freeze(
                Path("v.mp4"), out_dir=tmp_path, crop="100:100:0:0",
                candidate_cap=3,
            )
    assert not list(tmp_path.glob("*.jpg"))


def test_detect_slides_freeze_uses_camcorner_crop_when_no_explicit_crop(tmp_path):
    with patch("scripts.slides._freeze_periods", return_value=[]), \
         patch("scripts.slides.probe_dimensions", return_value=(1920, 1080)) as pd:
        out = detect_slides_freeze(
            Path("v.mp4"), out_dir=tmp_path, cam_corner="tr", caption="bottom",
        )
    pd.assert_called_once()  # falls back to dimension-based crop
    assert out["slides"] == []


def test_detect_slides_freeze_capture_is_mid_freeze_with_cap(tmp_path):
    # dur known → start+min(dur/2,3); long freeze caps at +3; tail(None) → start+min(hold/2,3)
    periods = [(10.0, 4.0), (20.0, 100.0), (30.0, None)]
    captured = {}

    def fake_extract(video, scenes, *, out_dir, width_px, native, crop_vf):
        captured["t"] = [round(s.t, 2) for s in scenes]
        recs = []
        for i, sc in enumerate(scenes, start=1):
            (out_dir / f"{i:04d}.jpg").write_bytes(b"x")
            recs.append({"index": i, "t": sc.t, "path": f"{i:04d}.jpg", "kind": "detected"})
        return recs

    with patch("scripts.slides._freeze_periods", return_value=periods), \
         patch("scripts.slides.frames_mod.extract_frames", side_effect=fake_extract), \
         patch("scripts.slides.phash_dedup", side_effect=lambda recs, **k: (recs, [])):
        detect_slides_freeze(Path("v.mp4"), out_dir=tmp_path, crop="100:100:0:0", hold=6.0,
                             merge_gap_s=0)  # capture-timing test, not merge behavior
    assert captured["t"] == [12.0, 23.0, 33.0]


def test_detect_slides_freeze_cleans_orphans_but_keeps_pre_existing(tmp_path):
    # A frame from a prior run already on disk (direct caller, no pre-wipe).
    (tmp_path / "0009_t99-99.jpg").write_bytes(b"old")

    def fake_extract(video, scenes, *, out_dir, width_px, native, crop_vf):
        (out_dir / "0001_t00-10.jpg").write_bytes(b"x")
        (out_dir / "0002_t00-20.jpg").write_bytes(b"x")
        raise RuntimeError("ffmpeg boom on frame 3")

    with patch("scripts.slides._freeze_periods",
               return_value=[(10.0, 6.0), (20.0, 6.0), (30.0, 6.0)]), \
         patch("scripts.slides.frames_mod.extract_frames", side_effect=fake_extract):
        with pytest.raises(RuntimeError, match="boom"):
            detect_slides_freeze(Path("v.mp4"), out_dir=tmp_path, crop="100:100:0:0")
    # this run's partial frames removed; the pre-existing frame survives
    assert {p.name for p in tmp_path.glob("*.jpg")} == {"0009_t99-99.jpg"}


def test_freeze_periods_warns_on_orphan_duration(recwarn):
    stderr = "freeze_duration: 4.0\nfreeze_start: 10.0\nfreeze_duration: 5.0\nfreeze_end: 15.0\n"
    with patch("scripts.slides.subprocess.run", return_value=_freeze_proc(stderr)):
        out = _freeze_periods(Path("v.mp4"), crop_vf="", hold=3, noise="-50dB")
    assert out == [(10.0, 5.0)]
    assert any("no preceding freeze_start" in str(w.message) for w in recwarn.list)


def test_freeze_periods_warns_on_double_start(recwarn):
    stderr = "freeze_start: 10.0\nfreeze_start: 20.0\nfreeze_duration: 5.0\nfreeze_end: 25.0\n"
    with patch("scripts.slides.subprocess.run", return_value=_freeze_proc(stderr)):
        out = _freeze_periods(Path("v.mp4"), crop_vf="", hold=3, noise="-50dB")
    assert out == [(20.0, 5.0)]  # later start wins; earlier dropped (with warning)
    assert any("had no" in str(w.message) for w in recwarn.list)


# ---- prefer-light brightness filter (R02) ----

def _freeze_3(tmp_path, luma_by_name):
    """Run detect_slides_freeze with 3 held periods, fake extract + keep-all dedup,
    and mean_luma mapped by filename. Returns the result dict."""
    periods = [(10.0, 6.0), (20.0, 6.0), (30.0, 6.0)]

    def fake_extract(video, scenes, *, out_dir, width_px, native, crop_vf):
        recs = []
        for i, sc in enumerate(scenes, start=1):
            name = f"{i:04d}_t00-{i:02d}.jpg"
            (out_dir / name).write_bytes(b"x")
            recs.append({"index": i, "t": sc.t, "path": name, "kind": "detected"})
        return recs

    return periods, fake_extract


def test_detect_slides_freeze_prefer_light_drops_dark(tmp_path):
    periods, fake_extract = _freeze_3(tmp_path, None)
    luma = {"0001_t00-01.jpg": 200, "0002_t00-02.jpg": 30, "0003_t00-03.jpg": 150}
    with patch("scripts.slides._freeze_periods", return_value=periods), \
         patch("scripts.slides.frames_mod.extract_frames", side_effect=fake_extract), \
         patch("scripts.slides.phash_dedup", side_effect=lambda recs, **k: (recs, [])), \
         patch("scripts.slides.mean_luma", side_effect=lambda p: luma[Path(p).name]):
        out = detect_slides_freeze(Path("v.mp4"), out_dir=tmp_path, crop="100:100:0:0",
                                   prefer_light=True, light_threshold=80.0, merge_gap_s=0)
    names = [Path(s["path"]).name for s in out["slides"]]
    assert names == ["0001_t00-01.jpg", "0003_t00-03.jpg"]  # dark (30) dropped
    assert [s["index"] for s in out["slides"]] == [1, 2]     # reindexed 1-based
    assert {p.name for p in tmp_path.glob("*.jpg")} == set(names)  # dark file unlinked


def test_detect_slides_freeze_prefer_light_off_keeps_all_and_skips_luma(tmp_path):
    periods, fake_extract = _freeze_3(tmp_path, None)
    with patch("scripts.slides._freeze_periods", return_value=periods), \
         patch("scripts.slides.frames_mod.extract_frames", side_effect=fake_extract), \
         patch("scripts.slides.phash_dedup", side_effect=lambda recs, **k: (recs, [])), \
         patch("scripts.slides.mean_luma", side_effect=AssertionError("must not be called")):
        out = detect_slides_freeze(Path("v.mp4"), out_dir=tmp_path, crop="100:100:0:0",
                                   prefer_light=False, merge_gap_s=0)
    assert len(out["slides"]) == 3  # off → no brightness filtering, mean_luma not called


def test_detect_slides_freeze_prefer_light_all_dropped_warns(tmp_path, recwarn):
    periods, fake_extract = _freeze_3(tmp_path, None)
    with patch("scripts.slides._freeze_periods", return_value=periods), \
         patch("scripts.slides.frames_mod.extract_frames", side_effect=fake_extract), \
         patch("scripts.slides.phash_dedup", side_effect=lambda recs, **k: (recs, [])), \
         patch("scripts.slides.mean_luma", side_effect=lambda p: 10):  # all dark
        out = detect_slides_freeze(Path("v.mp4"), out_dir=tmp_path, crop="100:100:0:0",
                                   prefer_light=True, light_threshold=80.0, merge_gap_s=0)
    assert out["slides"] == []
    assert not list(tmp_path.glob("*.jpg"))  # all dropped files unlinked
    assert any("dropped all" in str(w.message) for w in recwarn.list)


def test_detect_slides_freeze_prefer_light_keeps_exactly_at_threshold(tmp_path):
    periods, fake_extract = _freeze_3(tmp_path, None)
    luma = {"0001_t00-01.jpg": 80, "0002_t00-02.jpg": 79, "0003_t00-03.jpg": 81}
    with patch("scripts.slides._freeze_periods", return_value=periods), \
         patch("scripts.slides.frames_mod.extract_frames", side_effect=fake_extract), \
         patch("scripts.slides.phash_dedup", side_effect=lambda recs, **k: (recs, [])), \
         patch("scripts.slides.mean_luma", side_effect=lambda p: luma[Path(p).name]):
        out = detect_slides_freeze(Path("v.mp4"), out_dir=tmp_path, crop="100:100:0:0",
                                   prefer_light=True, light_threshold=80.0, merge_gap_s=0)
    # >= is inclusive: luma 80 == threshold is kept; 79 dropped
    assert [Path(s["path"]).name for s in out["slides"]] == ["0001_t00-01.jpg", "0003_t00-03.jpg"]


def test_detect_slides_freeze_prefer_light_failopen_on_luma_error(tmp_path, recwarn):
    import subprocess as _sp
    periods, fake_extract = _freeze_3(tmp_path, None)

    def luma(p):
        if Path(p).name == "0002_t00-02.jpg":
            raise _sp.CalledProcessError(1, "ffmpeg")  # measurement failure on one frame
        return 200

    with patch("scripts.slides._freeze_periods", return_value=periods), \
         patch("scripts.slides.frames_mod.extract_frames", side_effect=fake_extract), \
         patch("scripts.slides.phash_dedup", side_effect=lambda recs, **k: (recs, [])), \
         patch("scripts.slides.mean_luma", side_effect=luma):
        out = detect_slides_freeze(Path("v.mp4"), out_dir=tmp_path, crop="100:100:0:0",
                                   prefer_light=True, light_threshold=80.0, merge_gap_s=0)
    names = [Path(s["path"]).name for s in out["slides"]]
    assert names == ["0001_t00-01.jpg", "0002_t00-02.jpg", "0003_t00-03.jpg"]  # failed frame kept
    assert [s["index"] for s in out["slides"]] == [1, 2, 3]
    assert any("mean_luma failed" in str(w.message) for w in recwarn.list)


# ---- time-aware merge (R06 --hold recall fix; R08 threshold preserve+flag) ---

def _trecs(ts):
    return [{"index": i + 1, "t": float(t), "path": f"/tmp/m{i}.jpg"} for i, t in enumerate(ts)]


def _permissive_vanish(_a, _b):
    """A fake vanish_fn that always reports 0.0 (full containment) — used by tests
    that exercise the gap/dist band logic and don't want the R10 containment gate to
    interfere (those tests predate R10 and their real vanish_fn would otherwise hit
    the real ffmpeg-backed vanish_ratio on fake /tmp paths)."""
    return 0.0


def test_time_aware_merge_drops_when_gap_and_dist_both_close():
    # gap=10 < 15, dist=3 < 11 -> merge (animation build-step)
    recs = _trecs([0, 10])
    hashes = {"/tmp/m0.jpg": 0b000, "/tmp/m1.jpg": 0b111}
    kept, merged, threshold_flagged = time_aware_merge(
        recs, merge_gap_s=15.0, merge_dist=11, hash_fn=lambda p, c: hashes[p], vanish_fn=_permissive_vanish)
    assert [r["t"] for r in kept] == [0.0]
    assert merged == [(0.0, 10.0, 3, 10.0)]
    assert threshold_flagged == []


def test_time_aware_merge_keeps_when_time_far():
    # gap=20 >= 15 (time far) even though dist=3 < 11 -> keep (genuine re-show/new slide)
    recs = _trecs([0, 20])
    hashes = {"/tmp/m0.jpg": 0b000, "/tmp/m1.jpg": 0b111}
    kept, merged, threshold_flagged = time_aware_merge(
        recs, merge_gap_s=15.0, merge_dist=11, hash_fn=lambda p, c: hashes[p], vanish_fn=_permissive_vanish)
    assert [r["t"] for r in kept] == [0.0, 20.0]
    assert merged == []
    assert threshold_flagged == []


def test_time_aware_merge_keeps_when_dist_far():
    # gap=5 < 15 (time close) but dist=64 (hash far) -> keep, no flag (a different slide)
    recs = _trecs([0, 5])
    hashes = {"/tmp/m0.jpg": 0, "/tmp/m1.jpg": 0xFFFFFFFFFFFFFFFF}
    kept, merged, threshold_flagged = time_aware_merge(
        recs, merge_gap_s=15.0, merge_dist=11, hash_fn=lambda p, c: hashes[p], vanish_fn=_permissive_vanish)
    assert [r["t"] for r in kept] == [0.0, 5.0]
    assert merged == []
    assert threshold_flagged == []


def test_time_aware_merge_boundary_gap_equal_threshold_is_not_merged():
    # gap == merge_gap_s: rule is gap < merge_gap_s (strict), so gap==15 does NOT merge
    # and is NOT flagged, dist notwithstanding.
    recs = _trecs([0, 15])
    hashes = {"/tmp/m0.jpg": 0b000, "/tmp/m1.jpg": 0b111}
    kept, merged, threshold_flagged = time_aware_merge(
        recs, merge_gap_s=15.0, merge_dist=11, hash_fn=lambda p, c: hashes[p], vanish_fn=_permissive_vanish)
    assert [r["t"] for r in kept] == [0.0, 15.0]
    assert merged == []
    assert threshold_flagged == []


def test_time_aware_merge_boundary_dist_equal_threshold_is_flagged_not_merged():
    # R08: dist == merge_dist no longer merges — it's KEPT and recorded in
    # threshold_flagged (the exact-threshold call is the most fragile one; see
    # docstring). Rule is dist < merge_dist (strict) to merge.
    recs = _trecs([0, 10])
    hashes = {"/tmp/m0.jpg": 0, "/tmp/m1.jpg": 0b111_1111_1111}  # 11 bits set -> dist 11
    kept, merged, threshold_flagged = time_aware_merge(
        recs, merge_gap_s=15.0, merge_dist=11, hash_fn=lambda p, c: hashes[p], vanish_fn=_permissive_vanish)
    assert [r["t"] for r in kept] == [0.0, 10.0]
    assert merged == []
    assert threshold_flagged == [(0.0, 10.0, 11, 10.0)]


def test_time_aware_merge_disabled_when_merge_gap_zero():
    recs = _trecs([0, 1, 2])
    hashes = {"/tmp/m0.jpg": 0, "/tmp/m1.jpg": 0, "/tmp/m2.jpg": 0}
    kept, merged, threshold_flagged = time_aware_merge(
        recs, merge_gap_s=0, merge_dist=11, hash_fn=lambda p, c: hashes[p], vanish_fn=_permissive_vanish)
    assert kept == recs
    assert merged == []
    assert threshold_flagged == []


def test_time_aware_merge_at_dist_zero_only_merges_identical_hashes():
    # time_aware_merge itself has no special-case for merge_dist=0 — the disable-on-0
    # gate lives in the caller (detect_slides_freeze). At the function level, dist<0
    # (i.e. dist==0, since hamming distance can't be negative) simply means "only
    # bit-identical frames merge" — verify that raw rule directly. dist==0==merge_dist
    # is itself the exact-threshold case, so m1 is flagged (not merged); m2 (dist 1,
    # compared against the threshold-kept m1) is a clean keep, no flag.
    recs = _trecs([0, 1, 2])
    hashes = {"/tmp/m0.jpg": 0, "/tmp/m1.jpg": 0, "/tmp/m2.jpg": 0b1}
    kept, merged, threshold_flagged = time_aware_merge(
        recs, merge_gap_s=15.0, merge_dist=0, hash_fn=lambda p, c: hashes[p], vanish_fn=_permissive_vanish)
    assert [r["t"] for r in kept] == [0.0, 1.0, 2.0]
    assert merged == []
    assert threshold_flagged == [(0.0, 1.0, 0, 1.0)]


def test_time_aware_merge_empty_input():
    kept, merged, threshold_flagged = time_aware_merge(
        [], merge_gap_s=15.0, merge_dist=11, hash_fn=lambda p, c: 0, vanish_fn=_permissive_vanish)
    assert kept == []
    assert merged == []
    assert threshold_flagged == []


def test_time_aware_merge_single_record():
    recs = _trecs([0])
    kept, merged, threshold_flagged = time_aware_merge(
        recs, merge_gap_s=15.0, merge_dist=11, hash_fn=lambda p, c: 0, vanish_fn=_permissive_vanish)
    assert kept == recs
    assert merged == []
    assert threshold_flagged == []


def test_time_aware_merge_returns_3_tuple_shape():
    # Pin the return arity — R06 shipped a call-site arity swap that unit tests
    # didn't catch (2-tuple -> 2-tuple went unnoticed because both were 2-tuples).
    # R08 changes 2-tuple -> 3-tuple; pin it explicitly so a future signature drift
    # is caught here, not just at the (also-updated) detect_slides_freeze call site.
    result = time_aware_merge(_trecs([0]), merge_gap_s=15.0, merge_dist=11,
                               hash_fn=lambda p, c: 0, vanish_fn=_permissive_vanish)
    assert isinstance(result, tuple)
    assert len(result) == 3
    kept, merged, threshold_flagged = result
    assert isinstance(kept, list)
    assert isinstance(merged, list)
    assert isinstance(threshold_flagged, list)


def test_time_aware_merge_chains_through_multiple_builds():
    # 3-frame animation build (each strictly closer than merge_dist to the previous)
    # all collapse to the first; a 4th frame far in time is kept separately.
    recs = _trecs([0, 5, 9, 100])
    hashes = {
        "/tmp/m0.jpg": 0b000,
        "/tmp/m1.jpg": 0b001,
        "/tmp/m2.jpg": 0b011,
        "/tmp/m3.jpg": 0b011,
    }
    kept, merged, threshold_flagged = time_aware_merge(
        recs, merge_gap_s=15.0, merge_dist=11, hash_fn=lambda p, c: hashes[p], vanish_fn=_permissive_vanish)
    assert [r["t"] for r in kept] == [0.0, 100.0]
    # each merged-in frame is recorded against the last KEPT frame (t=0), not the
    # previous frame in the chain (last-seen) — see test_time_aware_merge_compares_to_last_kept.
    assert merged == [(0.0, 5.0, 1, 5.0), (0.0, 9.0, 2, 9.0)]
    assert threshold_flagged == []


def test_time_aware_merge_compares_to_last_kept_not_last_seen():
    # 3-frame chain: frame1 (dist 1 from frame0) merges into frame0. frame2 must then
    # be compared against frame0 (the last KEPT frame), NOT frame1 (merged/last-seen).
    # hash0=0b0000, hash1=0b0001 (dist 1 from hash0 -> merges, dist < 11).
    # hash2 is chosen so hamming(hash2, hash0) > 11 (far from last-KEPT -> must be kept)
    # but hamming(hash2, hash1) < 11 (close to last-SEEN -> would wrongly merge if the
    # loop tracked last-seen instead of last-kept).
    # hash0 = 0, hash1 = 0b1, hash2 = 0xFFF (12 bits set).
    #   hamming(hash2, hash0) = 12 (> 11 -> keep if compared to last-KEPT, correct)
    #   hamming(hash2, hash1) = 11 (< 12, close to last-SEEN -> would merge if wrongly
    #     anchored there)
    recs = _trecs([0, 10, 19])  # all gaps (10, 9) < 15 so time never gates this test
    hashes = {"/tmp/m0.jpg": 0b0, "/tmp/m1.jpg": 0b1, "/tmp/m2.jpg": 0xFFF}
    kept, merged, threshold_flagged = time_aware_merge(
        recs, merge_gap_s=15.0, merge_dist=11, hash_fn=lambda p, c: hashes[p], vanish_fn=_permissive_vanish)
    assert [r["t"] for r in kept] == [0.0, 19.0]
    assert merged == [(0.0, 10.0, 1, 10.0)]
    assert threshold_flagged == []


def test_time_aware_merge_threshold_keep_promotes_new_anchor():
    # Anchor-promotion proof (R08): A, B, C where B lands EXACTLY at merge_dist from
    # A (threshold-kept, becomes the new anchor), and C is close enough to merge into
    # B but is NOT close enough to have merged into A directly — proving the anchor
    # used for C's comparison is the threshold-kept B, not the original A.
    # hashA = 0, hashB = 0b111_1111_1111 (11 bits set -> dist(B,A) = 11 == merge_dist
    #   -> B is threshold-kept, not merged, and becomes the new anchor).
    # hashC = 0b011_1111_1111 (10 bits set, a strict subset of B's bits):
    #   dist(C, B) = 1  (< 11 -> merges into B, the anchor)
    #   dist(C, A) = 10 (< 11 -> would ALSO have merged into A directly, which doesn't
    #     distinguish the anchor — so this alone isn't proof).
    # To make the anchor unambiguous, use a hashC that is close to B but far from A:
    # hashC = 0b111_1111_1110 (10 bits set, differs from B only in the lowest bit):
    #   dist(C, B) = 1  (< 11 -> merges into B)
    #   dist(C, A) = 10 (< 11 too -- still ambiguous with a pure-subset hash)
    # A hamming distance can't discriminate "close to B, far from A" while B itself
    # is close to A (dist(A,B)=11) without violating the triangle inequality bound
    # dist(C,A) <= dist(C,B) + dist(B,A). Instead, prove anchor promotion via TIME:
    # if C were compared against A, gap(C, tA) would be used; if compared against B
    # (the promoted anchor), gap(C, tB) is used. Choose times so gap(C, tA) >=
    # merge_gap_s (would keep, no merge, if wrongly anchored at A) but
    # gap(C, tB) < merge_gap_s (merges if correctly anchored at the threshold-kept B).
    recs = _trecs([0, 10, 24])  # tA=0, tB=10, tC=24: gap(C,A)=24 (>=15), gap(C,B)=14 (<15)
    hashes = {
        "/tmp/m0.jpg": 0,                     # A
        "/tmp/m1.jpg": 0b111_1111_1111,       # B: dist(B,A)=11 == merge_dist -> threshold-kept
        "/tmp/m2.jpg": 0b111_1111_1110,       # C: dist(C,B)=1 (merges into B if B is anchor)
    }
    kept, merged, threshold_flagged = time_aware_merge(
        recs, merge_gap_s=15.0, merge_dist=11, hash_fn=lambda p, c: hashes[p], vanish_fn=_permissive_vanish)
    # If anchored at A: gap(C,A)=24 >= 15 -> C kept, no merge. That's NOT what we assert —
    # we assert C merges into B, proving the anchor promoted to B (the threshold-kept frame).
    assert [r["t"] for r in kept] == [0.0, 10.0]
    assert threshold_flagged == [(0.0, 10.0, 11, 10.0)]
    assert merged == [(10.0, 24.0, 1, 14.0)]


def test_time_aware_merge_consults_hash_cache_before_hash_fn():
    # FIX 4 (perf): when hash_cache has an entry for a frame's path, time_aware_merge
    # must use it instead of calling hash_fn — verified here by making hash_fn raise
    # for any path present in the cache.
    recs = _trecs([0, 10])
    cache = {"/tmp/m0.jpg": 0b000, "/tmp/m1.jpg": 0b111}

    def hash_fn_must_not_be_called(p, c):
        raise AssertionError(f"hash_fn should not be called for cached path {p}")

    kept, merged, threshold_flagged = time_aware_merge(
        recs, merge_gap_s=15.0, merge_dist=11, hash_fn=hash_fn_must_not_be_called,
        hash_cache=cache, vanish_fn=_permissive_vanish)
    assert [r["t"] for r in kept] == [0.0]
    assert merged == [(0.0, 10.0, 3, 10.0)]
    assert threshold_flagged == []


def test_time_aware_merge_falls_back_to_hash_fn_when_not_in_cache():
    # A hash_cache that's missing an entry (or None, the default) must still work via
    # hash_fn — the cache is a pure optimization, not a required input.
    recs = _trecs([0, 10])
    hashes = {"/tmp/m0.jpg": 0b000, "/tmp/m1.jpg": 0b111}
    cache: dict[str, int] = {}  # empty — nothing precomputed
    kept, merged, threshold_flagged = time_aware_merge(
        recs, merge_gap_s=15.0, merge_dist=11, hash_fn=lambda p, c: hashes[p], hash_cache=cache, vanish_fn=_permissive_vanish)
    assert [r["t"] for r in kept] == [0.0]


def test_time_aware_merge_large_gap_same_hash_is_reshow_not_merge():
    # Identical hash at a gap >= merge_gap_s must be KEPT — a deliberate re-show of the
    # same slide, not an animation build-step. Proves the gap gate applies even when
    # the hash matches exactly (dist=0, the closest possible hash distance).
    recs = _trecs([0, 20])
    kept, merged, threshold_flagged = time_aware_merge(
        recs, merge_gap_s=15.0, merge_dist=11, hash_fn=lambda p, c: 0, vanish_fn=_permissive_vanish)
    assert [r["t"] for r in kept] == [0.0, 20.0]
    assert merged == []
    assert threshold_flagged == []


# ---- R10 value-mismatch gate: _vanish_from_grays / vanish_ratio / time_aware_merge ----
#
# _vanish_from_grays is a VALUE-based mismatch metric, not mask containment (see its
# docstring for why the earlier mask-containment version was field-rejected: it scored
# a real false-merge pair as low as 0.005 because two distinct slides' titles shared
# the same on-screen position, so their content MASKS overlapped even though the pixel
# VALUES underneath were completely different).

def _flat(value, w, h):
    return [value] * (w * h)


def test_vanish_from_grays_identical_frames_is_zero():
    w, h = 8, 4
    a = _gray_with_content(range(0, 2), w=w, h=h)
    v = _vanish_from_grays(a, a, w, h)
    assert v == 0.0


def test_vanish_from_grays_pure_addition_is_zero():
    # A's content pixels keep their exact values in B; B adds new content elsewhere.
    # Every A-content pixel finds a same-value match in its own B neighborhood ->
    # vanish 0.0, regardless of B's extra additions.
    w, h = 8, 20
    a = _gray_with_content(range(0, 2), w=w, h=h)
    b = _gray_with_content(list(range(0, 2)) + list(range(10, 12)), w=w, h=h)
    v = _vanish_from_grays(a, b, w, h)
    assert v == 0.0


def test_vanish_from_grays_full_replacement_is_one():
    # A's content is entirely gone (background) in B; B has new, non-overlapping
    # content at different rows -> no A-content pixel finds a close value in B.
    w, h = 8, 20
    a = _gray_with_content(range(0, 2), w=w, h=h)
    b = _gray_with_content(range(10, 12), w=w, h=h)
    v = _vanish_from_grays(a, b, w, h)
    assert v == 1.0


def test_vanish_from_grays_same_position_different_content_is_one():
    # THE FIELD BUG THIS REVISION FIXES: A has a content block at rows R with value
    # v1 (a title's glyph brightness); B has a content block at the SAME rows R but a
    # different value v2 (a different title at the same centered position on a
    # uniform-template deck). Both v1 and v2 are far enough from their own frame's
    # background to register as content, but |v1-v2| is also > content_delta, so
    # nothing near A's content position in B has a matching value.
    #
    # Under the OLD mask-containment metric this scored ~0.0: B's mask overlaps A's
    # mask at exactly the same rows, so containment sees "content still there" and
    # calls it a survival -- the false-merge bug. Under the NEW value-mismatch
    # metric this must score ~1.0: no B pixel near A's position has a value close to
    # A's, so every A-content pixel counts as mismatched. This is the regression pin
    # for the whole R10 rewrite -- see the mutation check via subclass override below.
    w, h = 8, 20
    bg, v1, v2 = 40, 220, 120  # |v1-bg|=180>32 content; |v2-bg|=80>32 content; |v1-v2|=100>32 mismatch
    rows = [8, 9, 10, 11]

    def raster_with_value(value):
        buf = []
        for y in range(h):
            buf.extend([value] * w if y in rows else [bg] * w)
        return buf

    a = raster_with_value(v1)
    b = raster_with_value(v2)
    v = _vanish_from_grays(a, b, w, h)
    assert v == pytest.approx(1.0)


def test_vanish_from_grays_partial_vanish_ratio_is_accurate():
    # A has 2 equal-sized, well-separated content rows (0 and 5, far enough apart
    # that the 3x3 neighborhood search around one can't reach the other); B keeps
    # row 0's content at the same value but row 5 has become background -> exactly
    # half of A's content pixels find no close-value match nearby.
    w, h = 8, 20
    a = _gray_with_content([0, 5], w=w, h=h)
    b = _gray_with_content([0], w=w, h=h)
    v = _vanish_from_grays(a, b, w, h)
    assert v == pytest.approx(0.5)


def test_vanish_from_grays_is_asymmetric_one_way_anchor_test():
    # The metric is one-way A -> B (did the ANCHOR's content survive?). On the
    # partial-vanish fixture above: half of A's content is gone in B (0.5), but ALL
    # of B's content still exists in A (0.0). Pins the argument order directly —
    # a swapped call site (vanish_fn(candidate, anchor)) would invert these numbers,
    # silently weakening the gate to "did B's content already exist in A".
    w, h = 8, 20
    a = _gray_with_content([0, 5], w=w, h=h)
    b = _gray_with_content([0], w=w, h=h)
    assert _vanish_from_grays(a, b, w, h) == pytest.approx(0.5)
    assert _vanish_from_grays(b, a, w, h) == pytest.approx(0.0)


def test_merge_max_vanish_stays_below_field_false_merge_floor():
    # Field-measured class separation (docs/dogfood/2026-07-02-merge-defaults-
    # cross-deck.md + R10 tuning): distinct-slide (false-merge) pairs floor at
    # ~0.153, the legitimate forum build-step sits at ~0.058. The constant must
    # stay inside that gap — a drive-by edit to e.g. 0.5 would silently reopen
    # the uniform-template slide-loss bug with every unit test still green.
    assert 0.05 < MERGE_MAX_VANISH < 0.15


def test_vanish_from_grays_1px_shift_tolerated_by_neighborhood_min():
    # A's content occupies column 3 in every row of a content band; B's matching
    # content (same value) is shifted by 1px to column 4. Without neighborhood
    # tolerance this would show as a near-total mismatch; the 3x3 neighborhood-min
    # search around column 3 reaches column 4 in B, finds the same value, and scores
    # a close match -- keeping vanish low despite the shift.
    w, h = 8, 8
    bg, fg = 40, 220

    def col_raster(col):
        buf = []
        for _y in range(h):
            row = [bg] * w
            row[col] = fg
            buf.extend(row)
        return buf

    a = col_raster(3)
    b = col_raster(4)
    v = _vanish_from_grays(a, b, w, h)
    assert v < 0.2  # neighborhood tolerance keeps this low, not ~1.0


def test_vanish_from_grays_empty_anchor_is_zero_no_zero_division():
    # A is pure background (no content pixels at all) -> vanish is trivially 0.0,
    # and must not raise ZeroDivisionError.
    w, h = 8, 8
    a = _flat(40, w, h)
    b = _gray_with_content(range(0, 4), w=w, h=h)
    v = _vanish_from_grays(a, b, w, h)
    assert v == 0.0


def test_vanish_from_grays_rejects_mismatched_pixel_count():
    with pytest.raises(ValueError):
        _vanish_from_grays([0, 1, 2], [0, 1, 2, 3], 2, 2)


def test_vanish_ratio_delegates_to_ffmpeg_and_pure_mask(monkeypatch):
    # vanish_ratio's own contract: extract A and B via ffmpeg (mirroring dhash's
    # subprocess pattern), then delegate to _vanish_from_grays (the value-mismatch
    # metric). Patch subprocess.run to serve deterministic rasters and confirm the
    # returned ratio matches what _vanish_from_grays would compute directly on the
    # same bytes. Uses explicit width/height so this is independent of vanish_ratio's
    # default extraction size.
    w, h = 64, 36
    a_bytes = bytes(_gray_with_content(range(0, 6), w=w, h=h))
    b_bytes = bytes(_gray_with_content(range(20, 26), w=w, h=h))  # full replacement

    def fake_run(cmd, **kwargs):
        image_path = cmd[cmd.index("-i") + 1]
        data = a_bytes if str(image_path) == "a.jpg" else b_bytes
        return MagicMock(returncode=0, stdout=data)

    monkeypatch.setattr(slides.subprocess, "run", fake_run)
    result = vanish_ratio("a.jpg", "b.jpg", width=w, height=h)
    assert result == _vanish_from_grays(a_bytes, b_bytes, w, h)
    assert result == pytest.approx(1.0)


def test_vanish_ratio_default_extraction_size_is_128x72(monkeypatch):
    # R10 revision: default extraction size raised from 64x36 to 128x72 (the
    # value-mismatch metric was field-tuned at 128x72; see MERGE_MAX_VANISH's
    # comment). Pin the default so a future change to it is deliberate, not
    # accidental -- confirm the ffmpeg -vf filter actually requests 128x72 when no
    # explicit width/height is passed.
    seen_cmds = []

    def fake_run(cmd, **kwargs):
        seen_cmds.append(cmd)
        return MagicMock(returncode=0, stdout=bytes(_flat(40, 128, 72)))

    monkeypatch.setattr(slides.subprocess, "run", fake_run)
    vanish_ratio("a.jpg", "b.jpg")
    for cmd in seen_cmds:
        vf = cmd[cmd.index("-vf") + 1]
        assert "scale=128:72" in vf


def test_vanish_ratio_raises_on_short_ffmpeg_output(monkeypatch):
    monkeypatch.setattr(
        slides.subprocess, "run",
        lambda *a, **k: MagicMock(returncode=0, stdout=b"\x00" * 10),
    )
    with pytest.raises(RuntimeError):
        vanish_ratio("a.jpg", "b.jpg", width=64, height=36)


def test_time_aware_merge_band_pass_low_vanish_merges():
    recs = _trecs([0, 10])
    hashes = {"/tmp/m0.jpg": 0b000, "/tmp/m1.jpg": 0b111}
    kept, merged, threshold_flagged = time_aware_merge(
        recs, merge_gap_s=15.0, merge_dist=11, hash_fn=lambda p, c: hashes[p],
        vanish_fn=lambda a, b: 0.0)
    assert [r["t"] for r in kept] == [0.0]
    assert merged == [(0.0, 10.0, 3, 10.0)]
    assert threshold_flagged == []


def test_time_aware_merge_band_pass_high_vanish_rejects_and_becomes_new_anchor():
    # gap+dist band-pass but vanish above MERGE_MAX_VANISH -> reject the merge: the
    # candidate is KEPT (normal keep path), NOT recorded in merged or
    # threshold_flagged, and becomes the new anchor for the next comparison.
    recs = _trecs([0, 10, 11])
    hashes = {"/tmp/m0.jpg": 0b000, "/tmp/m1.jpg": 0b111, "/tmp/m2.jpg": 0b111}
    # frame1 rejects against frame0 (high vanish); frame2 is far in time/dist from
    # frame0 but if frame1 became the anchor, frame2 (gap=1<15, dist=0<11, vanish low)
    # would merge into frame1 -- proving frame1 (not frame0) is the active anchor.
    vanish_calls = []

    def fake_vanish(a, b):
        vanish_calls.append((a, b))
        if b == "/tmp/m1.jpg":
            return 1.0  # reject frame1's merge into frame0
        return 0.0  # frame2 merges into frame1 if frame1 is correctly the anchor

    kept, merged, threshold_flagged = time_aware_merge(
        recs, merge_gap_s=15.0, merge_dist=11, hash_fn=lambda p, c: hashes[p],
        vanish_fn=fake_vanish)
    assert [r["t"] for r in kept] == [0.0, 10.0]
    assert merged == [(10.0, 11.0, 0, 1.0)]
    assert threshold_flagged == []
    # vanish_fn was called for both band-passing pairs, anchored correctly each time.
    assert vanish_calls == [("/tmp/m0.jpg", "/tmp/m1.jpg"), ("/tmp/m1.jpg", "/tmp/m2.jpg")]


@pytest.mark.parametrize("exc", [
    OSError("ffmpeg not found"),
    # vanish_ratio's own short/corrupt-read failure is a RuntimeError — the fail-safe
    # must catch it too, or a single truncated JPEG crashes the whole --slides run
    # instead of the documented warn+reject degradation (R10 iter-1 P1).
    RuntimeError("vanish_ratio expected 9216 gray bytes, got 10"),
])
def test_time_aware_merge_vanish_fn_raising_warns_and_keeps(exc):
    recs = _trecs([0, 10])
    hashes = {"/tmp/m0.jpg": 0b000, "/tmp/m1.jpg": 0b111}

    def boom(a, b):
        raise exc

    with pytest.warns(RuntimeWarning, match="vanish_fn failed"):
        kept, merged, threshold_flagged = time_aware_merge(
            recs, merge_gap_s=15.0, merge_dist=11, hash_fn=lambda p, c: hashes[p],
            vanish_fn=boom)
    assert [r["t"] for r in kept] == [0.0, 10.0]
    assert merged == []
    assert threshold_flagged == []


def test_time_aware_merge_vanish_fn_called_only_for_band_passing_pairs():
    # A pair failing the gap gate and a pair failing the dist gate must NOT trigger
    # vanish_fn at all -- pin the call count/args so vanish_fn's cost tracks only
    # actual merge candidates, not every consecutive pair.
    recs = _trecs([0, 20, 21])  # gap(0,20)=20>=15 (fails gap); gap(20,21)=1<15
    hashes = {
        "/tmp/m0.jpg": 0b000,
        "/tmp/m1.jpg": 0b000,                     # dist(1,0)=0<11 but gap fails -> no vanish call
        "/tmp/m2.jpg": 0xFFFFFFFFFFFFFFFF,        # dist(2,1) huge -> fails dist gate -> no vanish call
    }
    calls = []

    def tracking_vanish(a, b):
        calls.append((a, b))
        return 0.0

    kept, merged, threshold_flagged = time_aware_merge(
        recs, merge_gap_s=15.0, merge_dist=11, hash_fn=lambda p, c: hashes[p],
        vanish_fn=tracking_vanish)
    assert calls == []  # neither pair reached the gap+dist band
    assert [r["t"] for r in kept] == [0.0, 20.0, 21.0]
    assert merged == []


def test_time_aware_merge_dist_equal_threshold_does_not_consult_vanish():
    # dist == merge_dist is the R08 threshold-flag path, unchanged by R10: it must
    # KEEP + flag without ever calling vanish_fn. Pin this with a vanish_fn that
    # raises if called at all.
    recs = _trecs([0, 10])
    hashes = {"/tmp/m0.jpg": 0, "/tmp/m1.jpg": 0b111_1111_1111}  # dist 11 == merge_dist

    def must_not_be_called(a, b):
        raise AssertionError("vanish_fn must not be consulted on the exact-threshold path")

    kept, merged, threshold_flagged = time_aware_merge(
        recs, merge_gap_s=15.0, merge_dist=11, hash_fn=lambda p, c: hashes[p],
        vanish_fn=must_not_be_called)
    assert [r["t"] for r in kept] == [0.0, 10.0]
    assert merged == []
    assert threshold_flagged == [(0.0, 10.0, 11, 10.0)]


def test_merge_max_vanish_boundary_is_inclusive_merge():
    # vanish exactly at MERGE_MAX_VANISH must still merge (<=, not <) — pin the
    # boundary explicitly so a future off-by-one doesn't silently flip it.
    recs = _trecs([0, 10])
    hashes = {"/tmp/m0.jpg": 0b000, "/tmp/m1.jpg": 0b111}
    kept, merged, threshold_flagged = time_aware_merge(
        recs, merge_gap_s=15.0, merge_dist=11, hash_fn=lambda p, c: hashes[p],
        vanish_fn=lambda a, b: MERGE_MAX_VANISH)
    assert merged == [(0.0, 10.0, 3, 10.0)]


def test_merge_max_vanish_just_above_boundary_rejects():
    recs = _trecs([0, 10])
    hashes = {"/tmp/m0.jpg": 0b000, "/tmp/m1.jpg": 0b111}
    kept, merged, threshold_flagged = time_aware_merge(
        recs, merge_gap_s=15.0, merge_dist=11, hash_fn=lambda p, c: hashes[p],
        vanish_fn=lambda a, b: MERGE_MAX_VANISH + 0.001)
    assert merged == []
    assert [r["t"] for r in kept] == [0.0, 10.0]


def test_detect_slides_freeze_merge_gap_zero_skips_merge_pass(tmp_path):
    periods = [(10.0, 6.0), (30.0, 6.0), (50.0, None)]

    def fake_extract(video, scenes, *, out_dir, width_px, native, crop_vf):
        recs = []
        for i, sc in enumerate(scenes, start=1):
            name = f"{i:04d}.jpg"
            (out_dir / name).write_bytes(b"x")
            recs.append({"index": i, "t": sc.t, "path": name, "kind": sc.kind})
        return recs

    import scripts.slides as S
    orig_dedup = S.phash_dedup
    hashes = iter([0b0000, 0b0001, 0b1111_1111])

    def fake_dedup(recs, *, crop_vf, drop_dist, flag_dist, hash_fn=None, hash_cache=None):
        return orig_dedup(recs, crop_vf=crop_vf, drop_dist=drop_dist,
                          flag_dist=flag_dist, hash_fn=lambda p, c="": next(hashes),
                          hash_cache=hash_cache)

    def fail_if_called(*args, **kwargs):
        raise AssertionError("time_aware_merge must not run when merge_gap_s<=0")

    with patch("scripts.slides._freeze_periods", return_value=periods), \
         patch("scripts.slides.frames_mod.extract_frames", side_effect=fake_extract), \
         patch("scripts.slides.phash_dedup", side_effect=fake_dedup), \
         patch("scripts.slides.time_aware_merge", side_effect=fail_if_called):
        out = detect_slides_freeze(
            Path("v.mp4"), out_dir=tmp_path, crop="100:100:0:0",
            hold=5.0, drop_dist=4, flag_dist=10, width_px=1280, candidate_cap=800,
            merge_gap_s=0, merge_dist=11,
        )
    # Same result as the pre-R06 baseline test: 3 extracted, 1 deduped, merge skipped.
    assert len(out["slides"]) == 2
    remaining = {p.name for p in tmp_path.glob("*.jpg")}
    kept_names = {Path(s["path"]).name for s in out["slides"]}
    assert remaining == kept_names
    assert out["merge_flagged"] == []


def test_detect_slides_freeze_merge_dist_zero_also_skips_merge_pass(tmp_path):
    periods = [(10.0, 6.0), (30.0, 6.0), (50.0, None)]

    def fake_extract(video, scenes, *, out_dir, width_px, native, crop_vf):
        recs = []
        for i, sc in enumerate(scenes, start=1):
            name = f"{i:04d}.jpg"
            (out_dir / name).write_bytes(b"x")
            recs.append({"index": i, "t": sc.t, "path": name, "kind": sc.kind})
        return recs

    import scripts.slides as S
    orig_dedup = S.phash_dedup
    hashes = iter([0b0000, 0b0001, 0b1111_1111])

    def fake_dedup(recs, *, crop_vf, drop_dist, flag_dist, hash_fn=None, hash_cache=None):
        return orig_dedup(recs, crop_vf=crop_vf, drop_dist=drop_dist,
                          flag_dist=flag_dist, hash_fn=lambda p, c="": next(hashes),
                          hash_cache=hash_cache)

    def fail_if_called(*args, **kwargs):
        raise AssertionError("time_aware_merge must not run when merge_dist<=0")

    with patch("scripts.slides._freeze_periods", return_value=periods), \
         patch("scripts.slides.frames_mod.extract_frames", side_effect=fake_extract), \
         patch("scripts.slides.phash_dedup", side_effect=fake_dedup), \
         patch("scripts.slides.time_aware_merge", side_effect=fail_if_called):
        out = detect_slides_freeze(
            Path("v.mp4"), out_dir=tmp_path, crop="100:100:0:0",
            hold=5.0, drop_dist=4, flag_dist=10, width_px=1280, candidate_cap=800,
            merge_gap_s=15.0, merge_dist=0,
        )
    assert len(out["slides"]) == 2
    assert out["merge_flagged"] == []


def _dhash_bits_to_gray_bytes(bits64: int) -> bytes:
    """Build a 9x8 grayscale raw buffer (as ffmpeg's dhash filtergraph would emit)
    whose decoded dhash is exactly `bits64` — lets a test fake the ffmpeg subprocess
    call inside `dhash` while still exercising `dhash`'s real bit-decoding logic."""
    flat = bytearray()
    pos = 0
    for _row in range(8):
        vals = [128]
        for _col in range(8):
            bit = (bits64 >> pos) & 1
            vals.append(vals[-1] - 1 if bit else vals[-1] + 1)
            pos += 1
        flat.extend(vals)
    assert len(flat) == 72
    return bytes(flat)


# R10: vanish_ratio's ffmpeg call uses `-vf scale=WxH,format=gray` (default 128x72,
# raised from 64x36 with this revision — the value-mismatch metric was field-tuned at
# 128x72), distinguishable from dhash's `-vf {crop}scale=9:8,format=gray` by the scale
# size — a real-pipeline test's fake_run must dispatch on this to serve the right
# raster to each of dhash's and vanish_ratio's ffmpeg calls.
_VANISH_W, _VANISH_H = 128, 72


def _is_vanish_call(cmd: list[str]) -> bool:
    vf = cmd[cmd.index("-vf") + 1]
    return f"scale={_VANISH_W}:{_VANISH_H}" in vf


def _solid_gray_bytes(value: int, w: int = _VANISH_W, h: int = _VANISH_H) -> bytes:
    """A uniform background-only raster (no content) at the vanish extraction size."""
    return bytes([value]) * (w * h)


def _gray_with_content(
    content_rows: range | list[int], *, bg: int = 40, fg: int = 220,
    w: int = _VANISH_W, h: int = _VANISH_H,
) -> bytes:
    """A raster with a uniform background and a block of bright "content" rows —
    stands in for a title line / bullet block against a dark background, at the
    vanish extraction size (default 128x72)."""
    rows = set(content_rows)
    buf = bytearray()
    for y in range(h):
        row_val = fg if y in rows else bg
        buf.extend([row_val] * w)
    assert len(buf) == w * h
    return bytes(buf)


def test_detect_slides_freeze_real_merge_end_to_end(tmp_path):
    # Unlike the mocked-time_aware_merge tests above, this exercises the REAL
    # phash_dedup + REAL time_aware_merge running back to back (patching only the
    # ffmpeg subprocess call inside `dhash`, not `dhash` itself — `phash_dedup`'s
    # default hash_fn is bound to the real `dhash` at def time, so patching the
    # module-level name wouldn't be seen by it). 3 held periods: frame1/frame2 are
    # distinct enough to both survive phash_dedup (dist 12 > flag_dist 10), but
    # frame2/frame3 are close enough in both time and hash to merge (gap=1<15,
    # dist=10<11).
    periods = [(1.0, 4.0), (10.0, 4.0), (11.0, 4.0)]

    def fake_extract(video, scenes, *, out_dir, width_px, native, crop_vf):
        recs = []
        for i, sc in enumerate(scenes, start=1):
            name = f"{i:04d}.jpg"
            (out_dir / name).write_bytes(b"x")
            recs.append({"index": i, "t": sc.t, "path": name, "kind": sc.kind})
        return recs

    hashes_by_name = {"0001.jpg": 0b0000, "0002.jpg": 0xFFF, "0003.jpg": 0b0011}
    # vanish rasters: only frame2/frame3 (the merge-band pair) need a deliberate
    # additive relationship — frame2's content (rows 0-5) stays present in frame3,
    # which also adds a second content block (rows 10-13) -> low vanish -> merge
    # proceeds. Content rows stay a clear minority of the 72-row raster so the
    # per-frame median background level doesn't flip to the content value.
    # frame1's raster is irrelevant to any vanish call (it's never a merge anchor).
    vanish_grays = {
        "0002.jpg": _gray_with_content(range(0, 6)),
        "0003.jpg": _gray_with_content(list(range(0, 6)) + list(range(10, 14))),
    }

    def fake_run(cmd, **kwargs):
        image_path = cmd[cmd.index("-i") + 1]
        name = Path(image_path).name
        if _is_vanish_call(cmd):
            return MagicMock(returncode=0, stdout=vanish_grays.get(name, _solid_gray_bytes(40)))
        bits = hashes_by_name[name]
        return MagicMock(returncode=0, stdout=_dhash_bits_to_gray_bytes(bits))

    with patch("scripts.slides._freeze_periods", return_value=periods), \
         patch("scripts.slides.frames_mod.extract_frames", side_effect=fake_extract), \
         patch("scripts.slides.subprocess.run", side_effect=fake_run):
        out = detect_slides_freeze(
            Path("v.mp4"), out_dir=tmp_path, crop="100:100:0:0",
            hold=4.0, drop_dist=4, flag_dist=10, width_px=1280, candidate_cap=800,
            merge_gap_s=15.0, merge_dist=11,
        )
    # scene t = period start + min(dur/2, 3.0): starts 1/10/11 (dur 4) -> t 3/12/13.
    # frame1 (t=3) and frame2 (t=12) both survive phash_dedup (dist 12 > flag_dist 10);
    # time_aware_merge then folds frame3 (t=13) into frame2 (gap=1<15, dist=10<11,
    # vanish~0 -- frame2's content survives additively in frame3).
    assert [s["t"] for s in out["slides"]] == [3.0, 12.0]
    assert [s["index"] for s in out["slides"]] == [1, 2]  # reindexed 1..N
    remaining = {p.name for p in tmp_path.glob("*.jpg")}
    kept_names = {Path(s["path"]).name for s in out["slides"]}
    assert remaining == kept_names  # the merged-out frame's file was unlinked
    assert out["merged"] == [(12.0, 13.0, 10, 1.0)]
    assert out["merge_flagged"] == []


def test_detect_slides_freeze_survivors_filter_drops_flagged_via_real_pipeline(tmp_path):
    # R08 field bug, reproduced end-to-end through REAL phash_dedup + REAL
    # time_aware_merge (not pre-fabricated tuples like the mocked tests above, and
    # not the real_merge_end_to_end test above either — that one's first pair has
    # dist 12 > flag_dist 10, so it never reaches phash_dedup's flag branch). Here
    # the SAME pair of frames is both:
    #   (a) flagged as a near-dup by real phash_dedup (drop_dist < dist <= flag_dist)
    #   (b) folded away by real time_aware_merge (dist < merge_dist AND gap < merge_gap_s)
    # so the (t=3.0, t=12.0, dist) line phash_dedup emits now references a frame
    # (t=12.0) that no longer exists once time_aware_merge runs. Without R09's
    # `_surviving_audit_lines` call, out["flagged"] would regress to
    # [(3.0, 12.0, 7)] — a stale audit line pointing at a deleted frame.
    periods = [(1.0, 4.0), (10.0, 4.0)]

    def fake_extract(video, scenes, *, out_dir, width_px, native, crop_vf):
        recs = []
        for i, sc in enumerate(scenes, start=1):
            name = f"{i:04d}.jpg"
            (out_dir / name).write_bytes(b"x")
            recs.append({"index": i, "t": sc.t, "path": name, "kind": sc.kind})
        return recs

    # dist(0b0000000, 0b1111111) = popcount(0b1111111) = 7 (verified against the
    # real dhash bit-decoding via _dhash_bits_to_gray_bytes, not assumed). With
    # drop_dist=4, flag_dist=10, merge_dist=11: 4 < 7 <= 10 -> real phash_dedup
    # FLAGS the pair (keeps both frames). Then dist 7 < merge_dist 11.
    hashes_by_name = {"0001.jpg": 0b0000000, "0002.jpg": 0b1111111}
    # vanish raster: frame1's content (rows 0-5) stays present (additively) in
    # frame2 (rows 0-5 plus a new block 10-13) -> low vanish -> the merge this test
    # is pinning still fires. Content rows stay a minority of the 72-row raster.
    vanish_grays = {
        "0001.jpg": _gray_with_content(range(0, 6)),
        "0002.jpg": _gray_with_content(list(range(0, 6)) + list(range(10, 14))),
    }

    def fake_run(cmd, **kwargs):
        image_path = cmd[cmd.index("-i") + 1]
        name = Path(image_path).name
        if _is_vanish_call(cmd):
            return MagicMock(returncode=0, stdout=vanish_grays.get(name, _solid_gray_bytes(40)))
        bits = hashes_by_name[name]
        return MagicMock(returncode=0, stdout=_dhash_bits_to_gray_bytes(bits))

    with patch("scripts.slides._freeze_periods", return_value=periods), \
         patch("scripts.slides.frames_mod.extract_frames", side_effect=fake_extract), \
         patch("scripts.slides.subprocess.run", side_effect=fake_run):
        out = detect_slides_freeze(
            Path("v.mp4"), out_dir=tmp_path, crop="100:100:0:0",
            hold=4.0, drop_dist=4, flag_dist=10, width_px=1280, candidate_cap=800,
            merge_gap_s=15.0, merge_dist=11,
        )
    # scene t = period start + min(dur/2, 3.0): starts 1/10 (dur 4) -> t 3.0/12.0.
    # gap = 12.0 - 3.0 = 9.0 (< merge_gap_s 15) and dist 7 < merge_dist 11, vanish~0
    # (additive raster) -> time_aware_merge folds frame2 (t=12.0) into frame1 (t=3.0).
    assert [s["t"] for s in out["slides"]] == [3.0]
    assert out["merged"] == [(3.0, 12.0, 7, 9.0)]
    # The key R09 assertion: real phash_dedup produced flagged=[(3.0, 12.0, 7)],
    # but real time_aware_merge then unlinked the t=12.0 frame, so the survivors
    # filter must suppress the now-stale flagged line.
    assert out["flagged"] == []
    assert out["merge_flagged"] == []
    remaining = {p.name for p in tmp_path.glob("*.jpg")}
    kept_names = {Path(s["path"]).name for s in out["slides"]}
    assert remaining == kept_names  # frame2's file was unlinked by the merge fold


def test_detect_slides_freeze_applies_merge_and_unlinks_reindexes(tmp_path):
    # 3 kept-by-dedup frames at t=1,10,11 (all distinct enough to survive phash_dedup);
    # time_aware_merge then merges the t=10/t=11 pair (gap=1<15, dist close) into t=1's
    # successor boundary — i.e. only t=1 and t=10 survive, t=11 is unlinked + reindexed.
    periods = [(1.0, 4.0), (10.0, 4.0), (11.0, 4.0)]

    def fake_extract(video, scenes, *, out_dir, width_px, native, crop_vf):
        recs = []
        for i, sc in enumerate(scenes, start=1):
            name = f"{i:04d}.jpg"
            (out_dir / name).write_bytes(b"x")
            recs.append({"index": i, "t": sc.t, "path": name, "kind": sc.kind})
        return recs

    # phash_dedup keeps all 3 (all far apart); time_aware_merge then merges the last
    # pair (close in both time and hash). Real scene t = start + min(dur/2, 3.0), so
    # periods (1.0,10.0,11.0) with dur=4.0 yield t=(3.0,12.0,13.0) — the anchor here
    # (12.0) must match the 2nd record's real t for the R09 survivors filter to keep
    # this merged line (it drops lines whose anchor isn't in the final slide set).
    fake_merged = [(12.0, 13.0, 3, 1.0)]
    with patch("scripts.slides._freeze_periods", return_value=periods), \
         patch("scripts.slides.frames_mod.extract_frames", side_effect=fake_extract), \
         patch("scripts.slides.phash_dedup", side_effect=lambda recs, **k: (recs, [])), \
         patch("scripts.slides.time_aware_merge",
               side_effect=lambda recs, **k: (recs[:2], fake_merged, [])):
        out = detect_slides_freeze(
            Path("v.mp4"), out_dir=tmp_path, crop="100:100:0:0",
            hold=4.0, drop_dist=4, flag_dist=10, width_px=1280, candidate_cap=800,
            merge_gap_s=15.0, merge_dist=11,
        )
    assert len(out["slides"]) == 2
    assert [s["index"] for s in out["slides"]] == [1, 2]  # reindexed
    assert out["merge_flagged"] == []
    remaining = {p.name for p in tmp_path.glob("*.jpg")}
    kept_names = {Path(s["path"]).name for s in out["slides"]}
    assert remaining == kept_names  # the merged-out frame's file was unlinked
    assert out["merged"] == fake_merged  # merged pairs surfaced on the result dict


def test_detect_slides_freeze_surfaces_merge_flagged_on_result_dict(tmp_path):
    # R08 wiring: threshold_flagged pairs returned by time_aware_merge must reach
    # detect_slides_freeze's result dict as result["merge_flagged"] — mirrors the
    # existing "merged pairs surfaced on the result dict" proof above for the new
    # third element of the tuple, so a dict-key typo or dropped element is caught
    # here rather than only at the (separately tested) watch.py wiring layer.
    periods = [(1.0, 4.0), (10.0, 4.0)]

    def fake_extract(video, scenes, *, out_dir, width_px, native, crop_vf):
        recs = []
        for i, sc in enumerate(scenes, start=1):
            name = f"{i:04d}.jpg"
            (out_dir / name).write_bytes(b"x")
            recs.append({"index": i, "t": sc.t, "path": name, "kind": sc.kind})
        return recs

    # Real scene t for periods (1.0,10.0) with dur=4.0 is (3.0,12.0) — both records
    # here survive (time_aware_merge fake keeps `recs` unchanged), so a merge_flagged
    # line naming those two real t values survives the R09 survivors filter (both
    # sides present).
    fake_flagged = [(3.0, 12.0, 11, 9.0)]
    with patch("scripts.slides._freeze_periods", return_value=periods), \
         patch("scripts.slides.frames_mod.extract_frames", side_effect=fake_extract), \
         patch("scripts.slides.phash_dedup", side_effect=lambda recs, **k: (recs, [])), \
         patch("scripts.slides.time_aware_merge",
               side_effect=lambda recs, **k: (recs, [], fake_flagged)):
        out = detect_slides_freeze(
            Path("v.mp4"), out_dir=tmp_path, crop="100:100:0:0",
            hold=4.0, drop_dist=4, flag_dist=10, width_px=1280, candidate_cap=800,
            merge_gap_s=15.0, merge_dist=11,
        )
    assert out["merge_flagged"] == fake_flagged
    assert out["merged"] == []
    assert len(out["slides"]) == 2  # threshold-flagged frames are KEPT, not dropped


@pytest.mark.integration
def test_mean_luma_white_vs_black(tmp_path):
    import subprocess
    white, black = tmp_path / "w.jpg", tmp_path / "b.jpg"
    for color, p in [("white", white), ("black", black)]:
        subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                        "-i", f"color={color}:size=32x32", "-frames:v", "1", str(p)], check=True)
    assert mean_luma(white) >= 240
    assert mean_luma(black) <= 16


# ---- auto-crop (--crop auto) -------------------------------------------------

def _make_pgm(w: int, h: int, pixels: list[int]) -> bytes:
    assert len(pixels) == w * h
    return f"P5\n{w} {h}\n255\n".encode() + bytes(pixels)


def test_read_pgm_parses_valid(tmp_path):
    p = tmp_path / "f.pgm"
    p.write_bytes(_make_pgm(3, 2, [0, 1, 2, 3, 4, 5]))
    w, h, px = _read_pgm(p)
    assert (w, h) == (3, 2)
    assert list(px) == [0, 1, 2, 3, 4, 5]


def test_read_pgm_rejects_non_p5(tmp_path):
    p = tmp_path / "f.pgm"
    p.write_bytes(b"P6\n3 2\n255\n" + bytes(6))
    with pytest.raises(ValueError):
        _read_pgm(p)


def test_read_pgm_rejects_truncated_header(tmp_path):
    p = tmp_path / "f.pgm"
    p.write_bytes(b"P5\n3 2\n")  # missing maxval token and raster
    with pytest.raises(ValueError):
        _read_pgm(p)


def test_read_pgm_rejects_short_pixels(tmp_path):
    p = tmp_path / "f.pgm"
    p.write_bytes(_make_pgm(3, 2, [0] * 6)[:-2])  # drop 2 raster bytes
    with pytest.raises(ValueError):
        _read_pgm(p)


def test_trim_removes_high_motion_corner():
    # 10x10 motion map: a 3x3 hot block top-right, rest quiet -> corner trimmed off.
    pw = ph = 10
    motion = [0] * (pw * ph)
    for y in range(3):
        for x in range(7, 10):
            motion[y * pw + x] = 1000
    box = _trim_high_motion_edges(motion, pw, ph)
    assert box == (0, 7, 3, 10)  # right cols and top rows peeled


def test_trim_fully_static_returns_full_frame():
    # No motion anywhere -> nothing to trim -> the full-frame box (caller rejects it).
    assert _trim_high_motion_edges([0] * 64, 8, 8) == (0, 8, 0, 8)


def test_detect_slide_crop_none_on_probe_failure(monkeypatch, recwarn):
    def boom(_v):
        raise RuntimeError("no duration")

    monkeypatch.setattr(slides, "_probe_duration", boom)
    assert detect_slide_crop(Path("x.mp4"), 1920, 1080) is None
    assert any("could not probe duration" in str(w.message) for w in recwarn.list)


def test_detect_slide_crop_none_on_too_few_samples(monkeypatch):
    monkeypatch.setattr(slides, "_probe_duration", lambda _v: 60.0)
    assert detect_slide_crop(Path("x.mp4"), 1920, 1080, samples=2) is None


def test_detect_slide_crop_none_on_bad_dims(monkeypatch):
    monkeypatch.setattr(slides, "_probe_duration", lambda _v: 60.0)
    assert detect_slide_crop(Path("x.mp4"), 0, 1080) is None


def test_detect_slide_crop_none_on_ffmpeg_failure(monkeypatch, tmp_path, recwarn):
    monkeypatch.setattr(slides, "_probe_duration", lambda _v: 60.0)
    monkeypatch.setattr(
        slides.subprocess, "run",
        lambda *a, **k: MagicMock(returncode=1, stderr=b"ffmpeg: boom"),
    )
    assert detect_slide_crop(tmp_path / "x.mp4", 1920, 1080) is None
    assert any("ffmpeg frame-sampling failed" in str(w.message) for w in recwarn.list)


def test_detect_slide_crop_warns_on_pgm_parse_failure(monkeypatch, recwarn):
    monkeypatch.setattr(slides, "_probe_duration", lambda _v: 24.0)

    def fake_run(cmd, **kwargs):
        pattern = cmd[-1]
        for i in range(3):
            Path(pattern % (i + 1)).write_bytes(b"P6 not-a-pgm")  # non-P5 -> ValueError
        return MagicMock(returncode=0)

    monkeypatch.setattr(slides.subprocess, "run", fake_run)
    assert detect_slide_crop(Path("x.mp4"), 1920, 1080, samples=3) is None
    assert any("PGM parse failed" in str(w.message) for w in recwarn.list)


def test_detect_slide_crop_excludes_moving_corner(monkeypatch):
    # ffmpeg is faked to emit PGMs: a mid-gray frame with a flickering top-right
    # corner (cam). The detector must trim that corner from the returned crop.
    monkeypatch.setattr(slides, "_probe_duration", lambda _v: 24.0)
    pw, ph = 40, 24

    def fake_run(cmd, **kwargs):
        pattern = cmd[-1]  # ".../f%04d.pgm"
        for i in range(6):
            px = [128] * (pw * ph)
            val = 0 if i % 2 == 0 else 255  # corner flickers each frame
            for y in range(6):
                for x in range(pw - 10, pw):
                    px[y * pw + x] = val
            Path(pattern % (i + 1)).write_bytes(_make_pgm(pw, ph, px))
        return MagicMock(returncode=0)

    monkeypatch.setattr(slides.subprocess, "run", fake_run)
    spec = detect_slide_crop(Path("x.mp4"), 1920, 1080, samples=6, probe_w=pw)
    assert spec is not None
    w, h, x, y = parse_crop(spec)
    assert w < 1920  # right cam columns trimmed
    assert y > 0     # top cam rows trimmed
    assert x == 0    # static left edge kept


def test_detect_slide_crop_none_when_whole_frame_static(monkeypatch):
    # A uniform deck with no moving chrome -> full-frame box -> no crop benefit -> None.
    monkeypatch.setattr(slides, "_probe_duration", lambda _v: 24.0)
    pw, ph = 40, 24

    def fake_run(cmd, **kwargs):
        pattern = cmd[-1]
        for i in range(6):
            # tiny global flicker only (no localized chrome) -> trims to nothing
            px = [128 + (i % 2)] * (pw * ph)
            Path(pattern % (i + 1)).write_bytes(_make_pgm(pw, ph, px))
        return MagicMock(returncode=0)

    monkeypatch.setattr(slides.subprocess, "run", fake_run)
    assert detect_slide_crop(Path("x.mp4"), 1920, 1080, samples=6, probe_w=pw) is None


def test_detect_slides_freeze_auto_crop_falls_back_on_none(tmp_path, recwarn):
    # crop="auto" with detection returning None must warn and fall back to cam/caption.
    periods = [(0.0, 5.0)]

    def fake_extract(video, scenes, **kwargs):
        return [{"path": "0001_t00-02.jpg", "t": 2.0, "index": 1, "kind": "detected"}]

    with patch("scripts.slides.detect_slide_crop", return_value=None), \
         patch("scripts.slides.probe_dimensions", return_value=(1920, 1080)), \
         patch("scripts.slides._freeze_periods", return_value=periods), \
         patch("scripts.slides.frames_mod.extract_frames", side_effect=fake_extract), \
         patch("scripts.slides.phash_dedup", side_effect=lambda recs, **k: (recs, [])):
        out = detect_slides_freeze(Path("v.mp4"), out_dir=tmp_path, crop="auto", merge_gap_s=0)
    assert out["slides"]
    assert any("falling back" in str(w.message) for w in recwarn.list)


def test_detect_slides_freeze_auto_crop_uses_detected_spec(tmp_path):
    # crop="auto" SUCCESS path: a detected spec must reach freeze + extract as crop_vf.
    captured = {}

    def fake_periods(video, *, crop_vf, hold, noise):
        captured["freeze_crop_vf"] = crop_vf
        return [(0.0, 5.0)]

    def fake_extract(video, scenes, **kwargs):
        captured["extract_crop_vf"] = kwargs.get("crop_vf")
        return [{"path": "0001_t00-02.jpg", "t": 2.0, "index": 1, "kind": "detected"}]

    with patch("scripts.slides.detect_slide_crop", return_value="1600:900:160:90"), \
         patch("scripts.slides.probe_dimensions", return_value=(1920, 1080)), \
         patch("scripts.slides._freeze_periods", side_effect=fake_periods), \
         patch("scripts.slides.frames_mod.extract_frames", side_effect=fake_extract), \
         patch("scripts.slides.phash_dedup", side_effect=lambda recs, **k: (recs, [])):
        out = detect_slides_freeze(Path("v.mp4"), out_dir=tmp_path, crop="auto", merge_gap_s=0)
    assert captured["freeze_crop_vf"] == "crop=1600:900:160:90,"
    assert captured["extract_crop_vf"] == "crop=1600:900:160:90,"
    assert out["slides"]


def test_read_pgm_tolerates_multispace_header(tmp_path):
    p = tmp_path / "f.pgm"
    p.write_bytes(b"P5\n  4   3  \n255\n" + bytes(range(12)))
    w, h, px = _read_pgm(p)
    assert (w, h) == (4, 3)
    assert px[0] == 0  # first raster byte intact (separator is a fixed +1, not a skip)


def test_read_pgm_rejects_oversized_dims(tmp_path):
    p = tmp_path / "f.pgm"
    p.write_bytes(b"P5\n100000 100000\n255\n")  # crafted header -> bounded before slice
    with pytest.raises(ValueError):
        _read_pgm(p)


def test_trim_removes_bottom_left_cam():
    # cam on the opposite corner from the top-right test -> exercises the other two loops.
    pw = ph = 10
    motion = [0] * (pw * ph)
    for y in range(7, 10):       # bottom rows
        for x in range(0, 3):    # left cols
            motion[y * pw + x] = 1000
    assert _trim_high_motion_edges(motion, pw, ph) == (3, 10, 0, 7)


def test_trim_collapses_to_none_when_all_edges_hot():
    # uniform high motion -> every row/col peeled -> region collapses -> None.
    pw = ph = 6
    assert _trim_high_motion_edges([1000] * (pw * ph), pw, ph) is None


def test_detect_slide_crop_none_on_zero_duration(monkeypatch):
    monkeypatch.setattr(slides, "_probe_duration", lambda _v: 0.0)
    assert detect_slide_crop(Path("x.mp4"), 1920, 1080) is None


def test_detect_slide_crop_none_on_mismatched_pgm_dims(monkeypatch):
    monkeypatch.setattr(slides, "_probe_duration", lambda _v: 24.0)

    def fake_run(cmd, **kwargs):
        pattern = cmd[-1]
        for i, (w, h) in enumerate([(40, 24), (40, 22), (40, 24)], start=1):
            Path(pattern % i).write_bytes(_make_pgm(w, h, [128] * (w * h)))
        return MagicMock(returncode=0)

    monkeypatch.setattr(slides.subprocess, "run", fake_run)
    assert detect_slide_crop(Path("x.mp4"), 1920, 1080, samples=3, probe_w=40) is None


def test_detect_slide_crop_even_aligned_and_bounded(monkeypatch):
    # SUCCESS path with odd source dims -> assert even-alignment + in-bounds + both axes trimmed.
    monkeypatch.setattr(slides, "_probe_duration", lambda _v: 24.0)
    pw, ph = 41, 23

    def fake_run(cmd, **kwargs):
        pattern = cmd[-1]
        for i in range(6):
            px = [128] * (pw * ph)
            val = 0 if i % 2 == 0 else 255
            for y in range(6):
                for x in range(pw - 10, pw):
                    px[y * pw + x] = val
            Path(pattern % (i + 1)).write_bytes(_make_pgm(pw, ph, px))
        return MagicMock(returncode=0)

    monkeypatch.setattr(slides.subprocess, "run", fake_run)
    spec = detect_slide_crop(Path("x.mp4"), 1920, 1081, samples=6, probe_w=pw)
    assert spec is not None
    w, h, x, y = parse_crop(spec)
    assert w % 2 == 0 and h % 2 == 0 and x % 2 == 0 and y % 2 == 0  # yuv420 even-aligned
    assert x + w <= 1920 and y + h <= 1081                          # clamped in-bounds
    assert w < 1920 and h < 1081                                    # top-right cam trimmed on both axes


def test_detect_slide_crop_none_when_overtrimmed(monkeypatch):
    # thick moving border on all edges -> static centre <40% area -> None (over-trim guard).
    monkeypatch.setattr(slides, "_probe_duration", lambda _v: 24.0)
    pw, ph = 40, 24

    def fake_run(cmd, **kwargs):
        pattern = cmd[-1]
        for i in range(6):
            px = [128] * (pw * ph)
            val = 0 if i % 2 == 0 else 255
            for y in range(ph):
                for x in range(pw):
                    if x < 14 or x >= pw - 14 or y < 9 or y >= ph - 9:
                        px[y * pw + x] = val
            Path(pattern % (i + 1)).write_bytes(_make_pgm(pw, ph, px))
        return MagicMock(returncode=0)

    monkeypatch.setattr(slides.subprocess, "run", fake_run)
    assert detect_slide_crop(Path("x.mp4"), 1920, 1080, samples=6, probe_w=pw) is None


# ---- R09: audit lines filtered to surviving frames only --------------------

def test_surviving_audit_lines_flagged_suppressed_when_one_side_missing():
    flagged = [(1.0, 2.0, 5)]
    kept, merged, merge_flagged = _surviving_audit_lines({1.0}, flagged, [], [])
    assert kept == []
    assert merged == []
    assert merge_flagged == []


def test_surviving_audit_lines_flagged_kept_when_both_survive():
    flagged = [(1.0, 2.0, 5)]
    kept, merged, merge_flagged = _surviving_audit_lines({1.0, 2.0}, flagged, [], [])
    assert kept == [(1.0, 2.0, 5)]


def test_surviving_audit_lines_merge_flagged_suppressed_when_one_side_missing():
    merge_flagged = [(1.0, 2.0, 11, 9.0)]
    _, _, kept_mf = _surviving_audit_lines({1.0}, [], [], merge_flagged)
    assert kept_mf == []


def test_surviving_audit_lines_merge_flagged_kept_when_both_survive():
    merge_flagged = [(1.0, 2.0, 11, 9.0)]
    _, _, kept_mf = _surviving_audit_lines({1.0, 2.0}, [], [], merge_flagged)
    assert kept_mf == [(1.0, 2.0, 11, 9.0)]


def test_surviving_audit_lines_merged_kept_when_anchor_survives_dropped_absent():
    # merged's dropped_t is unlinked BY DESIGN — it must be ABSENT from surviving_ts
    # and the line must still survive on the anchor alone. This pins the correct
    # "anchor-only" rule and kills a mutant that filters merged on both timestamps
    # (which would wrongly delete every merged line, since dropped_t is never present).
    merged = [(1.0, 2.0, 3, 1.0)]
    _, kept_merged, _ = _surviving_audit_lines({1.0}, [], merged, [])
    assert kept_merged == [(1.0, 2.0, 3, 1.0)]


def test_surviving_audit_lines_merged_suppressed_when_anchor_missing():
    merged = [(1.0, 2.0, 3, 1.0)]
    _, kept_merged, _ = _surviving_audit_lines(set(), [], merged, [])
    assert kept_merged == []


def test_surviving_audit_lines_empty_inputs_yield_empty_outputs():
    assert _surviving_audit_lines(set(), [], [], []) == ([], [], [])


def test_surviving_audit_lines_mixed_survivors_and_casualties():
    surviving = {1.0, 2.0, 5.0}
    flagged = [(1.0, 2.0, 5), (2.0, 3.0, 6)]  # 2nd pair: 3.0 missing -> suppressed
    merged = [(2.0, 3.5, 2, 1.5), (9.0, 9.5, 2, 0.5)]  # 2nd anchor 9.0 missing -> suppressed
    merge_flagged = [(1.0, 5.0, 11, 4.0), (5.0, 6.0, 11, 1.0)]  # 2nd: 6.0 missing
    kept_f, kept_m, kept_mf = _surviving_audit_lines(surviving, flagged, merged, merge_flagged)
    assert kept_f == [(1.0, 2.0, 5)]
    assert kept_m == [(2.0, 3.5, 2, 1.5)]
    assert kept_mf == [(1.0, 5.0, 11, 4.0)]


def test_detect_slides_freeze_survivors_filter_drops_stale_flagged_after_merge(tmp_path):
    # Deterministic end-to-end pin: phash_dedup flags a near-dup pair, then
    # time_aware_merge folds the later member away (merging it into the first
    # record). The near-dup line referencing the merged-away frame must be
    # suppressed from result["flagged"] because one of its two frames no longer
    # survives, even though the flagged pair itself was never re-evaluated by the
    # merge pass. Real scene t = start + min(dur/2, 3.0); periods (1.0,10.0,20.0)
    # with dur=4.0 -> t=(3.0,12.0,22.0).
    periods = [(1.0, 4.0), (10.0, 4.0), (20.0, 4.0)]

    def fake_extract(video, scenes, *, out_dir, width_px, native, crop_vf):
        recs = []
        for i, sc in enumerate(scenes, start=1):
            name = f"{i:04d}.jpg"
            (out_dir / name).write_bytes(b"x")
            recs.append({"index": i, "t": sc.t, "path": name, "kind": sc.kind})
        return recs

    fake_flagged = [(12.0, 22.0, 8)]  # near-dup pair kept by dedup
    fake_merged = [(3.0, 22.0, 3, 19.0)]  # merge later folds t=22 into t=3

    with patch("scripts.slides._freeze_periods", return_value=periods), \
         patch("scripts.slides.frames_mod.extract_frames", side_effect=fake_extract), \
         patch("scripts.slides.phash_dedup",
               side_effect=lambda recs, **k: (recs, fake_flagged)), \
         patch("scripts.slides.time_aware_merge",
               side_effect=lambda recs, **k: ([recs[0], recs[1]], fake_merged, [])):
        out = detect_slides_freeze(
            Path("v.mp4"), out_dir=tmp_path, crop="100:100:0:0",
            hold=4.0, drop_dist=4, flag_dist=10, width_px=1280, candidate_cap=800,
            merge_gap_s=15.0, merge_dist=11,
        )
    # t=22 was merged away; the stale near-dup line referencing it must be gone.
    assert out["flagged"] == []
    assert out["merged"] == fake_merged  # anchor t=3 survives -> drop notice kept
    assert [s["t"] for s in out["slides"]] == [3.0, 12.0]


def test_detect_slides_freeze_survivors_filter_prefer_light_drops_referenced_frame(tmp_path):
    # A near-dup/merge-threshold/merged line can reference a frame that survives
    # dedup+merge but is then dropped by --prefer-light for being too dark. All
    # lines referencing that frame must be suppressed. Real scene t = start +
    # min(dur/2, 3.0); periods (1.0,10.0,20.0) with dur=4.0 -> t=(3.0,12.0,22.0).
    periods = [(1.0, 4.0), (10.0, 4.0), (20.0, 4.0)]

    def fake_extract(video, scenes, *, out_dir, width_px, native, crop_vf):
        recs = []
        for i, sc in enumerate(scenes, start=1):
            name = f"{i:04d}_t{int(sc.t):02d}.jpg"
            (out_dir / name).write_bytes(b"x")
            recs.append({"index": i, "t": sc.t, "path": name, "kind": sc.kind})
        return recs

    fake_flagged = [(12.0, 22.0, 8)]
    fake_merge_flagged = [(3.0, 12.0, 11, 9.0)]
    luma = {"0001_t03.jpg": 200, "0002_t12.jpg": 200, "0003_t22.jpg": 10}  # t=22 is dark

    with patch("scripts.slides._freeze_periods", return_value=periods), \
         patch("scripts.slides.frames_mod.extract_frames", side_effect=fake_extract), \
         patch("scripts.slides.phash_dedup",
               side_effect=lambda recs, **k: (recs, fake_flagged)), \
         patch("scripts.slides.time_aware_merge",
               side_effect=lambda recs, **k: (recs, [], fake_merge_flagged)), \
         patch("scripts.slides.mean_luma", side_effect=lambda p: luma[Path(p).name]):
        out = detect_slides_freeze(
            Path("v.mp4"), out_dir=tmp_path, crop="100:100:0:0",
            hold=4.0, drop_dist=4, flag_dist=10, width_px=1280, candidate_cap=800,
            merge_gap_s=15.0, merge_dist=11,
            prefer_light=True, light_threshold=80.0,
        )
    assert [s["t"] for s in out["slides"]] == [3.0, 12.0]  # t=22 dropped (dark)
    assert out["flagged"] == []  # near-dup line referenced dropped t=22 -> suppressed
    assert out["merge_flagged"] == fake_merge_flagged  # both t=3,t=12 survive -> kept


def test_detect_slides_freeze_survivors_filter_noop_when_merge_and_prefer_light_off(tmp_path):
    # merge disabled (merge_gap_s=0) + prefer-light off: no frame is ever unlinked
    # after phash_dedup, so the filter must be a pure no-op (byte-identical to R05).
    # Real scene t = start + min(dur/2, 3.0) (or hold/2 for the open-ended tail
    # period): periods (10.0,30.0,50.0/None) with dur=6.0, hold=5.0 -> t=(13.0,33.0,52.5).
    periods = [(10.0, 6.0), (30.0, 6.0), (50.0, None)]

    def fake_extract(video, scenes, *, out_dir, width_px, native, crop_vf):
        recs = []
        for i, sc in enumerate(scenes, start=1):
            name = f"{i:04d}.jpg"
            (out_dir / name).write_bytes(b"x")
            recs.append({"index": i, "t": sc.t, "path": name, "kind": sc.kind})
        return recs

    fake_flagged = [(13.0, 33.0, 9)]  # both frames survive (merge is off, no drops)

    with patch("scripts.slides._freeze_periods", return_value=periods), \
         patch("scripts.slides.frames_mod.extract_frames", side_effect=fake_extract), \
         patch("scripts.slides.phash_dedup",
               side_effect=lambda recs, **k: (recs, fake_flagged)):
        out = detect_slides_freeze(
            Path("v.mp4"), out_dir=tmp_path, crop="100:100:0:0",
            hold=5.0, drop_dist=4, flag_dist=10, width_px=1280, candidate_cap=800,
            merge_gap_s=0,  # disabled
            prefer_light=False,
        )
    assert out["flagged"] == fake_flagged  # unchanged — no-op path
    assert out["merged"] == []
    assert out["merge_flagged"] == []


def test_detect_slides_freeze_survivors_filter_all_survive_passthrough(tmp_path):
    # When every referenced frame survives merge + prefer-light, all three lists
    # pass through unchanged. Real scene t = start + min(dur/2, 3.0); periods
    # (1.0,10.0,20.0) with dur=4.0 -> t=(3.0,12.0,22.0).
    periods = [(1.0, 4.0), (10.0, 4.0), (20.0, 4.0)]

    def fake_extract(video, scenes, *, out_dir, width_px, native, crop_vf):
        recs = []
        for i, sc in enumerate(scenes, start=1):
            name = f"{i:04d}.jpg"
            (out_dir / name).write_bytes(b"x")
            recs.append({"index": i, "t": sc.t, "path": name, "kind": sc.kind})
        return recs

    fake_flagged = [(3.0, 12.0, 8)]
    fake_merge_flagged = [(12.0, 22.0, 11, 10.0)]

    with patch("scripts.slides._freeze_periods", return_value=periods), \
         patch("scripts.slides.frames_mod.extract_frames", side_effect=fake_extract), \
         patch("scripts.slides.phash_dedup",
               side_effect=lambda recs, **k: (recs, fake_flagged)), \
         patch("scripts.slides.time_aware_merge",
               side_effect=lambda recs, **k: (recs, [], fake_merge_flagged)):
        out = detect_slides_freeze(
            Path("v.mp4"), out_dir=tmp_path, crop="100:100:0:0",
            hold=4.0, drop_dist=4, flag_dist=10, width_px=1280, candidate_cap=800,
            merge_gap_s=15.0, merge_dist=11,
        )
    assert [s["t"] for s in out["slides"]] == [3.0, 12.0, 22.0]  # nothing dropped
    assert out["flagged"] == fake_flagged
    assert out["merged"] == []
    assert out["merge_flagged"] == fake_merge_flagged
