"""Slide-deck mode helpers: crop-aware detection and perceptual-hash dedup."""
from __future__ import annotations

import json
import re
import subprocess
import warnings
from collections.abc import Callable
from pathlib import Path

from scripts import frames as frames_mod
from scripts.scenes import Scene, apply_coverage_floor, detect_scenes

VALID_CAM = {"tr", "tl", "br", "bl", "none"}
VALID_CAPTION = {"bottom", "top", "none"}

# freeze-detect knob validation: an ffmpeg freezedetect noise value is either a
# decibel form (e.g. "-50dB") or a 0..1 ratio (e.g. "0.003"). Anything else must be
# rejected before it reaches the filtergraph string (injection guard).
_FREEZE_NOISE_RX = re.compile(r"^-?\d+(?:\.\d+)?dB$|^(?:0(?:\.\d+)?|1(?:\.0+)?)$")
_FREEZE_EVENT_RX = re.compile(r"freeze_(start|duration|end):\s*([0-9.]+)")


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


def dhash(image_path: str | Path, crop_vf: str = "") -> int:
    """Compute a zero-dependency 64-bit difference (gradient) hash through ffmpeg.

    A difference hash compares each pixel to its RIGHT neighbour, so it keys on
    edges / text layout rather than overall brightness. This is dramatically better
    than an average hash at telling apart **monochrome text slides** (white decks):
    such slides have near-identical average grayness but different text, so an
    average hash collapses them (over-merge) while dhash keeps them distinct.
    Measured on a 28-slide white-text deck, switching avg->diff hash cut wrongly
    merged adjacent slides from 18/27 to 1/27 at the same 64-bit / drop_dist=4.

    Downscales the (optionally cropped) region to 9x8 grayscale and emits one bit
    per `pixel > right-neighbour` comparison -> 8x8 = 64 bits.
    """
    vf = f"{crop_vf}scale=9:8,format=gray"
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
    if len(raw) != 72:
        raise RuntimeError(f"dhash expected 72 gray bytes (9x8), got {len(raw)} for {image_path}")

    bits = 0
    pos = 0
    for row in range(8):
        base = row * 9
        for col in range(8):
            if raw[base + col] > raw[base + col + 1]:
                bits |= 1 << pos
            pos += 1
    return bits


def phash_dedup(
    frame_records: list[dict],
    *,
    crop_vf: str,
    drop_dist: int = 4,
    flag_dist: int = 10,
    hash_fn: Callable[[str | Path, str], int] = dhash,
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
    if not out or out == "N/A":
        # No duration tag (some transport streams / partial files). Don't silently
        # fall back to 0.0 — that turns the tail anchor into a no-op and the final
        # slide is lost again with no signal. Warn so the cause is visible.
        warnings.warn(
            f"ffprobe returned no duration for {video}; the slides tail anchor is "
            "skipped, so a slide shown only in the final seconds may be missed.",
            RuntimeWarning,
            stacklevel=2,
        )
        return 0.0
    return float(out)


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
    # Slides mode opts into the end-of-video tail anchor so a slide shown only in the
    # final seconds (below the scene-detect threshold, past the last floor step) is
    # still extracted. Classic mode never passes this, preserving upstream behavior.
    floored = apply_coverage_floor(
        raw, duration_s=duration_s, max_gap_s=max_gap, include_tail_anchor=True
    )
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


def parse_crop(spec: str) -> tuple[int, int, int, int]:
    """Parse an explicit slide-region crop "W:H:X:Y" into validated ints.

    Rejects non-integer / negative / zero-size specs so the value is safe to format
    into an ffmpeg crop filter (no filtergraph injection from user input).
    """
    parts = spec.split(":")
    if len(parts) != 4:
        raise ValueError(f"--crop must be W:H:X:Y (4 ints), got {spec!r}")
    try:
        w, h, x, y = (int(p) for p in parts)
    except ValueError:
        raise ValueError(f"--crop fields must be integers, got {spec!r}") from None
    if w <= 0 or h <= 0 or x < 0 or y < 0:
        raise ValueError(f"--crop needs w>0,h>0,x>=0,y>=0, got {spec!r}")
    return w, h, x, y


def validate_freeze_noise(noise: str) -> str:
    """Return `noise` if it is a valid freezedetect noise value, else raise.

    A freeze *threshold* of >= 0 dB means "any difference counts as frozen", which
    marks the whole video as one freeze — a silent mis-result. So dB values must be
    strictly negative (e.g. -50dB); the 0..1 ratio form is also accepted.
    """
    if not _FREEZE_NOISE_RX.match(noise):
        raise ValueError(
            f"--freeze-noise must be a negative dB value (e.g. -50dB) or 0..1 ratio, got {noise!r}"
        )
    if noise.endswith("dB") and float(noise[:-2]) >= 0:
        raise ValueError(
            f"--freeze-noise dB must be negative (e.g. -50dB); {noise!r} would mark "
            "the entire video as frozen"
        )
    return noise


def _freeze_periods(
    video: Path, *, crop_vf: str, hold: float, noise: str
) -> list[tuple[float, float | None]]:
    """Run ffmpeg freezedetect over the (optionally cropped) video.

    Returns [(start_s, duration_s_or_None), ...] for each held period >= `hold`s.
    A trailing period still frozen at EOF has duration None (no freeze_end emitted).
    """
    validate_freeze_noise(noise)
    vf = f"{crop_vf}freezedetect=n={noise}:d={hold}"
    cmd = [
        "ffmpeg", "-hide_banner", "-nostdin",
        "-protocol_whitelist", "file",
        "-i", str(video),
        "-vf", vf,
        "-map", "0:v:0",
        "-f", "null", "-",
    ]
    proc = subprocess.run(cmd, capture_output=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        detail = (proc.stderr or "").strip()[-500:]
        raise RuntimeError(f"freezedetect failed (exit {proc.returncode}): {detail}")

    periods: list[tuple[float, float | None]] = []
    pending: float | None = None
    for kind, val in _FREEZE_EVENT_RX.findall(proc.stderr or ""):
        v = float(val)
        if kind == "start":
            if pending is not None:
                # Real ffmpeg always pairs start->duration; a second start without an
                # intervening duration means malformed output — warn rather than
                # silently drop the earlier period.
                warnings.warn(
                    f"freezedetect: freeze_start at {v}s while {pending}s had no "
                    "duration; the earlier period is dropped",
                    RuntimeWarning, stacklevel=2,
                )
            pending = v
        elif kind == "duration":
            if pending is None:
                warnings.warn(
                    f"freezedetect: freeze_duration {v}s with no preceding "
                    "freeze_start; ignored",
                    RuntimeWarning, stacklevel=2,
                )
                continue
            periods.append((pending, v))
            pending = None
        # freeze_end carries no extra info (duration already paired); ignore.
    if pending is not None:  # still frozen at EOF — keep the tail slide
        periods.append((pending, None))
    return periods


def mean_luma(image_path: str | Path) -> int:
    """Mean grayscale (0-255) of an image via ffmpeg.

    Used to tell light-background prepared slides from dark IDE/terminal demo
    screens. Operates on the file as-is — freeze output is already cropped to the
    slide region, so no crop is applied here. Internal helper: callers pass a path
    they control (here always under out_dir); it is not a path-validation boundary.
    """
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin",
        "-protocol_whitelist", "file",
        "-i", str(image_path),
        "-vf", "format=gray,scale=1:1",
        "-frames:v", "1", "-f", "rawvideo", "-",
    ]
    raw = subprocess.run(cmd, capture_output=True, check=True).stdout
    if len(raw) != 1:
        raise RuntimeError(f"mean_luma expected 1 gray byte, got {len(raw)} for {image_path}")
    return raw[0]


def detect_slides_freeze(
    video: Path,
    *,
    out_dir: Path,
    cam_corner: str = "tr",
    caption: str = "bottom",
    crop: str | None = None,
    hold: float = 5.0,
    freeze_noise: str = "-50dB",
    drop_dist: int = 4,
    flag_dist: int = 10,
    width_px: int = 1280,
    candidate_cap: int = 800,
    prefer_light: bool = False,
    light_threshold: float = 80.0,
) -> dict:
    """Capture one frame per *held* (frozen) screen region, then dedup.

    Unlike scene+floor (one candidate per cut + every max_gap seconds), this keys on
    *stability*: a prepared slide is shown static for >= `hold`s, while a live demo
    (scrolling code/browser) never settles -- so demo scroll-noise is skipped and the
    candidate count tracks held screens, not video length. The output frames are
    cropped (token savings); dedup therefore runs on the already-cropped frame.

    With `prefer_light`, frames whose mean brightness is below `light_threshold`
    (0-255) are dropped after dedup — a cheap heuristic that removes dark IDE/terminal
    demo screens. Assumes light-background slides; leave off for dark-themed decks.
    """
    if crop:
        w, h, x, y = parse_crop(crop)
        crop_vf = f"crop={w}:{h}:{x}:{y},"
    else:
        vw, vh = probe_dimensions(video)
        crop_vf = build_crop_vf(vw, vh, cam_corner, caption)

    periods = _freeze_periods(video, crop_vf=crop_vf, hold=hold, noise=freeze_noise)
    if len(periods) > candidate_cap:
        raise CandidateCapExceeded(
            f"{len(periods)} freeze periods exceeds cap {candidate_cap}; "
            "raise --hold, tighten --crop, or use --start/--end"
        )

    out_dir.mkdir(parents=True, exist_ok=True)
    # Capture mid-freeze (safely inside the held period) so transitions aren't grabbed.
    scenes = [
        Scene(t=start + min((dur if dur is not None else hold) / 2, 3.0),
              score=1.0, kind="detected")
        for start, dur in periods
    ]
    frame_glob = "[0-9][0-9][0-9][0-9]_t*.jpg"  # frames.format_filename output
    pre_existing = set(out_dir.glob(frame_glob))
    try:
        records = frames_mod.extract_frames(
            video, scenes, out_dir=out_dir, width_px=width_px, native=True, crop_vf=crop_vf
        )
        absolute_records = [
            {**rec, "path": str((out_dir / rec["path"]).resolve())} for rec in records
        ]
        # Frames are already cropped on disk, so dedup hashes them as-is (crop_vf="").
        kept, flagged = phash_dedup(
            absolute_records, crop_vf="", drop_dist=drop_dist, flag_dist=flag_dist
        )
    except Exception:
        # A mid-run ffmpeg/dhash failure leaves partial JPEGs. Remove only the frames
        # THIS run created (set-diff vs the pre-run snapshot) so a direct library caller
        # that didn't pre-wipe doesn't lose a prior run's frames.
        for leftover in set(out_dir.glob(frame_glob)) - pre_existing:
            leftover.unlink(missing_ok=True)
        raise
    kept_paths = {rec["path"] for rec in kept}

    slides = []
    for rec, absolute_rec in zip(records, absolute_records):
        if absolute_rec["path"] in kept_paths:
            slides.append(
                {"index": len(slides) + 1, "t": rec["t"], "path": rec["path"], "kind": "detected"}
            )
        else:
            Path(absolute_rec["path"]).unlink(missing_ok=True)

    if prefer_light:
        bright = []
        for s in slides:
            # s["path"] is relative; rebuild the abs path the same way the entries
            # above were created (out_dir is unchanged within this call).
            abs_path = (out_dir / s["path"]).resolve()
            try:
                luma = mean_luma(abs_path)
            except (subprocess.CalledProcessError, RuntimeError, OSError) as exc:
                # Can't measure brightness → KEEP the frame (fail-open for an opt-in
                # filter), don't crash mid-loop leaving frames half-deleted.
                warnings.warn(
                    f"mean_luma failed for {abs_path} ({exc}); keeping frame",
                    RuntimeWarning, stacklevel=2,
                )
                bright.append({**s, "index": len(bright) + 1})
                continue
            if luma >= light_threshold:
                bright.append({**s, "index": len(bright) + 1})
            else:
                abs_path.unlink(missing_ok=True)
        if slides and not bright:
            warnings.warn(
                f"--prefer-light dropped all {len(slides)} slides at threshold "
                f"{light_threshold}; lower --light-threshold (dark-themed deck?)",
                RuntimeWarning, stacklevel=2,
            )
        slides = bright

    return {"slides": slides, "flagged": flagged}
