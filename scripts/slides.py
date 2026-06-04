"""Slide-deck mode helpers: crop-aware detection and perceptual-hash dedup."""
from __future__ import annotations

import subprocess
import warnings
from pathlib import Path

VALID_CAM = {"tr", "tl", "br", "bl", "none"}
VALID_CAPTION = {"bottom", "top", "none"}


def build_crop_vf(
    w: int,
    h: int,
    cam_corner: str = "tr",
    caption: str = "bottom",
    *,
    cam_frac: float = 0.20,
    cap_frac: float = 0.15,
) -> str:
    """Return an ffmpeg crop filter for the slide region, ending with a comma."""
    assert cam_corner in VALID_CAM, f"bad cam_corner: {cam_corner!r}"
    assert caption in VALID_CAPTION, f"bad caption: {caption!r}"

    x0, x1, y0, y1 = 0, w, 0, h

    cam_w = int(w * cam_frac)
    if cam_corner in ("tr", "br"):
        x1 = w - cam_w
    elif cam_corner in ("tl", "bl"):
        x0 = cam_w

    cap_h = int(h * cap_frac)
    if caption == "bottom":
        y1 = h - cap_h
    elif caption == "top":
        y0 = cap_h

    crop_w, crop_h = x1 - x0, y1 - y0
    if crop_w <= 0 or crop_h <= 0 or crop_w * crop_h < 0.5 * w * h:
        warnings.warn(
            "computed slide region <50% of frame; falling back to full frame "
            "(tune --cam-corner/--caption)",
            RuntimeWarning,
            stacklevel=2,
        )
        return f"crop={w}:{h}:0:0,"
    return f"crop={crop_w}:{crop_h}:{x0}:{y0},"


def hamming(a: int, b: int) -> int:
    """Number of differing bits between two hashes."""
    return (a ^ b).bit_count()


def ahash(image_path: Path, crop_vf: str = "") -> int:
    """Compute a zero-dependency 64-bit average hash through ffmpeg."""
    vf = f"{crop_vf}scale=8:8,format=gray"
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-protocol_whitelist",
        "file",
        "-i",
        str(image_path),
        "-vf",
        vf,
        "-frames:v",
        "1",
        "-f",
        "rawvideo",
        "-",
    ]
    raw = subprocess.run(cmd, capture_output=True, check=True).stdout
    if len(raw) != 64:
        raise RuntimeError(f"ahash expected 64 gray bytes, got {len(raw)} for {image_path}")

    avg = sum(raw) / 64.0
    bits = 0
    for i, value in enumerate(raw):
        if value >= avg:
            bits |= 1 << i
    return bits


def phash_dedup(
    frame_records: list[dict],
    *,
    crop_vf: str,
    drop_dist: int = 4,
    flag_dist: int = 10,
    hash_fn=ahash,
) -> tuple[list[dict], list[tuple[float, float, int]]]:
    """Drop only near-identical consecutive frames; keep and flag borderline pairs."""
    kept: list[dict] = []
    flagged: list[tuple[float, float, int]] = []
    last_hash: int | None = None
    last_rec: dict | None = None

    for rec in frame_records:
        current_hash = hash_fn(rec["path"], crop_vf)
        if last_hash is None:
            kept.append(rec)
            last_hash = current_hash
            last_rec = rec
            continue

        distance = hamming(current_hash, last_hash)
        if distance <= drop_dist:
            continue
        if distance <= flag_dist:
            flagged.append((last_rec["t"], rec["t"], distance))
        kept.append(rec)
        last_hash = current_hash
        last_rec = rec

    return kept, flagged
