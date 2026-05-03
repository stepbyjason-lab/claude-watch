from pathlib import Path
from unittest.mock import patch, MagicMock

from scripts.download import download_video, copy_local


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
