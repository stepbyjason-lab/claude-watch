from pathlib import Path

import pytest

from scripts.frames import format_filename, extract_frames
from scripts.scenes import Scene

FIXTURE = Path(__file__).parent / "fixtures" / "sample_10s.mp4"


def test_format_filename_zero_pads_index_and_timestamp():
    assert format_filename(1, 0.0) == "0001_t00-00.jpg"
    assert format_filename(42, 65.0) == "0042_t01-05.jpg"
    assert format_filename(100, 3661.0) == "0100_t61-01.jpg"  # past 1h, MM keeps growing


def test_format_filename_handles_subsecond_t():
    # We round to nearest second for the filename label
    assert format_filename(1, 3.4) == "0001_t00-03.jpg"
    assert format_filename(1, 3.6) == "0001_t00-04.jpg"


@pytest.mark.integration
def test_extract_frames_writes_one_jpeg_per_scene(tmp_path):
    scenes = [
        Scene(t=0.5, score=1.0, kind="detected"),
        Scene(t=4.0, score=0.9, kind="detected"),
        Scene(t=8.0, score=0.9, kind="detected"),
    ]
    frames = extract_frames(FIXTURE, scenes, out_dir=tmp_path, width_px=320)
    assert len(frames) == 3
    for f in frames:
        p = tmp_path / f["path"]
        assert p.exists()
        assert p.stat().st_size > 0
        assert p.suffix == ".jpg"
    # Returned objects carry the scene's timestamp + index
    assert [f["index"] for f in frames] == [1, 2, 3]
    assert [round(f["t"], 1) for f in frames] == [0.5, 4.0, 8.0]


@pytest.mark.integration
def test_extract_frames_resolution_controls_width(tmp_path):
    scenes = [Scene(t=1.0, score=1.0, kind="detected")]
    frames_512 = extract_frames(FIXTURE, scenes, out_dir=tmp_path / "a", width_px=512)
    frames_320 = extract_frames(FIXTURE, scenes, out_dir=tmp_path / "b", width_px=320)
    size_512 = (tmp_path / "a" / frames_512[0]["path"]).stat().st_size
    size_320 = (tmp_path / "b" / frames_320[0]["path"]).stat().st_size
    assert size_512 > size_320, "512px JPEG should be larger than 320px JPEG"
