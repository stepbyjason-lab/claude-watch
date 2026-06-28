from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import scripts.slides as slides
from scripts.slides import (
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
    validate_freeze_noise,
    _freeze_periods,
    _read_pgm,
    _trim_high_motion_edges,
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

    def fake_dedup(recs, *, crop_vf, drop_dist, flag_dist, hash_fn=None):
        return orig_dedup(recs, crop_vf=crop_vf, drop_dist=drop_dist,
                          flag_dist=flag_dist, hash_fn=lambda p, c="": next(hashes))

    with patch("scripts.slides._freeze_periods", return_value=periods), \
         patch("scripts.slides.frames_mod.extract_frames", side_effect=fake_extract), \
         patch("scripts.slides.phash_dedup", side_effect=fake_dedup):
        out = detect_slides_freeze(
            Path("v.mp4"), out_dir=tmp_path, crop="100:100:0:0",
            hold=5.0, drop_dist=4, flag_dist=10, width_px=1280, candidate_cap=800,
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
        detect_slides_freeze(Path("v.mp4"), out_dir=tmp_path, crop="100:100:0:0", hold=6.0)
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
                                   prefer_light=True, light_threshold=80.0)
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
                                   prefer_light=False)
    assert len(out["slides"]) == 3  # off → no brightness filtering, mean_luma not called


def test_detect_slides_freeze_prefer_light_all_dropped_warns(tmp_path, recwarn):
    periods, fake_extract = _freeze_3(tmp_path, None)
    with patch("scripts.slides._freeze_periods", return_value=periods), \
         patch("scripts.slides.frames_mod.extract_frames", side_effect=fake_extract), \
         patch("scripts.slides.phash_dedup", side_effect=lambda recs, **k: (recs, [])), \
         patch("scripts.slides.mean_luma", side_effect=lambda p: 10):  # all dark
        out = detect_slides_freeze(Path("v.mp4"), out_dir=tmp_path, crop="100:100:0:0",
                                   prefer_light=True, light_threshold=80.0)
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
                                   prefer_light=True, light_threshold=80.0)
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
                                   prefer_light=True, light_threshold=80.0)
    names = [Path(s["path"]).name for s in out["slides"]]
    assert names == ["0001_t00-01.jpg", "0002_t00-02.jpg", "0003_t00-03.jpg"]  # failed frame kept
    assert [s["index"] for s in out["slides"]] == [1, 2, 3]
    assert any("mean_luma failed" in str(w.message) for w in recwarn.list)


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
        out = detect_slides_freeze(Path("v.mp4"), out_dir=tmp_path, crop="auto")
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
        out = detect_slides_freeze(Path("v.mp4"), out_dir=tmp_path, crop="auto")
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
