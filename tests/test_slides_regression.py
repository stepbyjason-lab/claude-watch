import os
from pathlib import Path

import pytest

from scripts.download import download_video
from scripts.slides import detect_slides


HARNESS_URL = "https://youtu.be/5buNm0pA1mg"


@pytest.mark.network
@pytest.mark.integration
@pytest.mark.skipif(not os.environ.get("CW_RUN_NETWORK"), reason="set CW_RUN_NETWORK=1 to run")
def test_harness_deck_recovers_most_slides(tmp_path: Path):
    video = download_video(HARNESS_URL, tmp_path / "src", fmt="720p")
    out = detect_slides(
        video,
        out_dir=tmp_path / "frames",
        cam_corner="tr",
        caption="bottom",
        threshold=0.10,
        max_gap=20.0,
        drop_dist=4,
        flag_dist=10,
        width_px=1280,
        candidate_cap=800,
    )
    n = len(out["slides"])
    assert 24 <= n <= 40, f"expected ~28 slides, got {n}"
