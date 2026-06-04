from pathlib import Path

import pytest

from scripts.slides import (
    CandidateCapExceeded,
    ahash,
    build_crop_vf,
    detect_slides,
    hamming,
    phash_dedup,
    probe_dimensions,
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
    with pytest.raises(AssertionError):
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
def test_ahash_is_stable_and_returns_64bit(tmp_path):
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
    h1 = ahash(jpg)
    h2 = ahash(jpg)
    assert h1 == h2
    assert 0 <= h1 < (1 << 64)


@pytest.mark.integration
def test_ahash_differs_for_different_frames(tmp_path):
    import subprocess

    a, b = tmp_path / "a.jpg", tmp_path / "b.jpg"
    for t, p in [(1.0, a), (8.0, b)]:
        subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-ss",
                str(t),
                "-i",
                str(FIXTURE),
                "-frames:v",
                "1",
                str(p),
            ],
            check=True,
        )
    assert hamming(ahash(a), ahash(b)) > 0


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


@pytest.mark.integration
def test_probe_dimensions_reads_fixture_wh():
    w, h = probe_dimensions(FIXTURE)
    assert w > 0 and h > 0


@pytest.mark.integration
def test_detect_slides_returns_frame_records_on_fixture(tmp_path):
    out = detect_slides(
        FIXTURE,
        out_dir=tmp_path,
        cam_corner="none",
        caption="none",
        threshold=0.30,
        max_gap=20.0,
        drop_dist=4,
        flag_dist=10,
        width_px=1280,
        candidate_cap=800,
    )
    assert out["slides"]
    assert len(out["slides"]) >= 2
    for record in out["slides"]:
        assert (tmp_path / record["path"]).exists()
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
