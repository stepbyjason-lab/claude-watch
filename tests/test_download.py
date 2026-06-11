from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from scripts.download import download_video, copy_local, format_selector


def test_format_selector_known_enums():
    assert format_selector("best") == "best[ext=mp4]/best"
    assert format_selector("720p") == "bv*[height<=720]+ba/b[height<=720]/best"
    assert format_selector("1080p") == "bv*[height<=1080]+ba/b[height<=1080]/best"


def test_format_selector_unknown_falls_back_to_best():
    assert format_selector("4k") == "best[ext=mp4]/best"


@patch("scripts.download.subprocess.run")
def test_download_video_invokes_yt_dlp_with_target_path(mock_run, tmp_path):
    mock_run.return_value = MagicMock(returncode=0)
    out_dir = tmp_path / "src"
    out_dir.mkdir()
    expected = out_dir / "video.mp4"
    expected.write_bytes(b"\x00")  # simulate yt-dlp wrote the file
    result = download_video("https://youtu.be/x", out_dir, basename="video")
    assert result == expected
    cmd = mock_run.call_args[0][0]
    assert cmd[0] == "yt-dlp"
    assert cmd[cmd.index("-f") + 1] == "best[ext=mp4]/best"
    assert "-o" in cmd
    out_template_idx = cmd.index("-o") + 1
    assert "video.%(ext)s" in cmd[out_template_idx]


def test_copy_local_symlinks_into_library(tmp_path):
    src = tmp_path / "in.mp4"
    src.write_bytes(b"data")
    dst_dir = tmp_path / "src"
    dst_dir.mkdir()
    result = copy_local(src, dst_dir, basename="video")
    assert result.is_symlink() or result.is_file()
    assert result.exists()
    assert result.read_bytes() == b"data"


def test_copy_local_rejects_directory_source(tmp_path):
    src_dir = tmp_path / "not-a-file"
    src_dir.mkdir()
    dst_dir = tmp_path / "src"
    dst_dir.mkdir()
    with pytest.raises(RuntimeError, match="not a regular file"):
        copy_local(src_dir, dst_dir, basename="video")


def test_copy_local_rejects_non_video_extension(tmp_path):
    src = tmp_path / "secrets.txt"
    src.write_text("not a video")
    dst_dir = tmp_path / "src"
    dst_dir.mkdir()
    with pytest.raises(RuntimeError, match="does not look like a video"):
        copy_local(src, dst_dir, basename="video")
    assert not (dst_dir / "video.txt").exists()
