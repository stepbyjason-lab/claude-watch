"""Slide-deck mode helpers: crop-aware detection and perceptual-hash dedup."""
from __future__ import annotations

import json
import subprocess
import warnings
from collections.abc import Callable
from pathlib import Path

from scripts import frames as frames_mod
from scripts.scenes import apply_coverage_floor, detect_scenes

VALID_CAM = {"tr", "tl", "br", "bl", "none"}
VALID_CAPTION = {"bottom", "top", "none"}


class CandidateCapExceeded(RuntimeError):
    """Raised when slide candidate extraction would exceed the safety cap."""


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
    if cam_corner not in VALID_CAM:
        raise ValueError(f"bad cam_corner: {cam_corner!r}")
    if caption not in VALID_CAPTION:
        raise ValueError(f"bad caption: {caption!r}")

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
    hash_fn: Callable[[Path, str], int] = ahash,
) -> tuple[list[dict], list[tuple[float, float, int]]]:
    """Drop frames near-identical to the last KEPT frame; keep and flag borderline pairs.

    `flag_dist` must be > `drop_dist`. Otherwise borderline frames
    (drop_dist < distance <= flag_dist) would be silently dropped instead of
    flagged, breaking the high-recall guarantee.
    """
    if flag_dist <= drop_dist:
        raise ValueError(
            f"flag_dist ({flag_dist}) must be > drop_dist ({drop_dist}); "
            "otherwise borderline frames are silently dropped instead of flagged"
        )
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


def probe_dimensions(video: Path) -> tuple[int, int]:
    """Read the first video stream dimensions with ffprobe."""
    out = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height",
            "-of",
            "json",
            str(video),
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    data = json.loads(out)
    streams = data.get("streams") or []
    if not streams:
        raise RuntimeError(f"ffprobe found no video stream for {video}")
    stream = streams[0]
    return int(stream["width"]), int(stream["height"])


def _probe_duration(video: Path) -> float:
    """Read media duration in seconds with ffprobe."""
    out = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=nw=1:nk=1",
            str(video),
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    return float(out or 0.0)


def detect_slides(
    video: Path,
    *,
    out_dir: Path,
    cam_corner: str = "tr",
    caption: str = "bottom",
    threshold: float = 0.10,
    max_gap: float = 20.0,
    drop_dist: int = 4,
    flag_dist: int = 10,
    width_px: int = 1280,
    candidate_cap: int = 800,
) -> dict:
    """Detect, extract, and conservatively deduplicate slide candidates."""
    w, h = probe_dimensions(video)
    crop_vf = build_crop_vf(w, h, cam_corner, caption)

    raw = detect_scenes(video, threshold=threshold, prefilter=crop_vf)
    duration_s = _probe_duration(video)
    floored = apply_coverage_floor(raw, duration_s=duration_s, max_gap_s=max_gap)
    if len(floored) > candidate_cap:
        raise CandidateCapExceeded(
            f"{len(floored)} candidate frames exceeds cap {candidate_cap}; "
            "raise --scene-threshold or use --start/--end"
        )

    out_dir.mkdir(parents=True, exist_ok=True)
    records = frames_mod.extract_frames(
        video,
        floored,
        out_dir=out_dir,
        width_px=width_px,
        native=True,
    )
    absolute_records = []
    for rec in records:
        abs_path = (out_dir / rec["path"]).resolve()
        absolute_records.append({**rec, "path": str(abs_path)})

    kept, flagged = phash_dedup(
        absolute_records,
        crop_vf=crop_vf,
        drop_dist=drop_dist,
        flag_dist=flag_dist,
    )
    kept_paths = {rec["path"] for rec in kept}

    slides = []
    for rec, absolute_rec in zip(records, absolute_records):
        abs_path = absolute_rec["path"]
        if abs_path in kept_paths:
            slides.append(
                {
                    "index": len(slides) + 1,
                    "t": rec["t"],
                    "path": rec["path"],
                    "kind": "detected",
                }
            )
        else:
            Path(abs_path).unlink(missing_ok=True)

    return {"slides": slides, "flagged": flagged}
