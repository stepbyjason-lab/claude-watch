"""Slide-deck mode helpers: crop-aware detection and perceptual-hash dedup."""
from __future__ import annotations

import json
import re
import subprocess
import tempfile
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
    hash_cache: dict[str, int] | None = None,
) -> tuple[list[dict], list[tuple[float, float, int]]]:
    """Drop frames near-identical to the last KEPT frame; keep and flag borderline pairs.

    `flag_dist` must be > `drop_dist`. Otherwise borderline frames
    (drop_dist < distance <= flag_dist) would be silently dropped instead of
    flagged, breaking the high-recall guarantee.

    If `hash_cache` is given, every hash computed here for a frame that survives
    dedup (i.e. every "last kept" hash) is stored into it as `{path: hash}`. A
    caller that runs a second hash-based pass (e.g. `time_aware_merge`) over the
    same kept records with the same `hash_fn`/`crop_vf` can pass this cache in to
    skip recomputing hashes that are already known.
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
            if hash_cache is not None:
                hash_cache[rec["path"]] = current_hash
            continue

        distance = hamming(current_hash, last_hash)
        if distance <= drop_dist:
            continue
        if distance <= flag_dist:
            flagged.append((last_rec["t"], rec["t"], distance))
        kept.append(rec)
        last_hash = current_hash
        last_rec = rec
        if hash_cache is not None:
            hash_cache[rec["path"]] = current_hash

    return kept, flagged


def time_aware_merge(
    records: list[dict],
    *,
    crop_vf: str = "",
    merge_gap_s: float = 15.0,
    merge_dist: int = 11,
    hash_fn: Callable[[str | Path, str], int] = dhash,
    hash_cache: dict[str, int] | None = None,
) -> tuple[list[dict], list[tuple[float, float, int, float]]]:
    """Freeze-only post-pass. Drop a frame into the previous KEPT frame iff it is BOTH
    close in time (gap < merge_gap_s) AND close in hash (dist <= merge_dist) — an
    animation/scroll build-step of the same screen, not a genuine re-show or new slide.
    Compares only to the last KEPT frame (same structure as phash_dedup) — a merged
    (dropped) frame never becomes the comparison anchor for the next frame, so a chain
    of small build-steps all collapse toward the first frame of the chain instead of
    drifting away one small step at a time.

    Returns `(kept, merged)`. `merged` records what got folded away, as
    `(prev_kept_t, dropped_t, dist, gap)` tuples, so a caller can surface it to the
    user the same way `phash_dedup` surfaces its `flagged` borderline pairs — this
    pass is otherwise a silent drop, and its default `merge_dist` (11) is looser than
    `phash_dedup`'s `flag_dist` (10), so a genuinely distinct slide can be folded away
    with no visible trace unless the caller prints `merged`.

    `hash_cache` (optional) is a `{path: hash}` map of already-computed hashes (e.g.
    from `phash_dedup`, which uses the same `hash_fn`/`crop_vf` at the freeze call
    site) — consulted before calling `hash_fn`, to avoid re-hashing every kept frame
    a second time. Not supplying it (the default) preserves the original behavior of
    always computing via `hash_fn`, so unit tests injecting a fake `hash_fn` still work.
    """
    kept: list[dict] = []
    merged: list[tuple[float, float, int, float]] = []
    last_hash: int | None = None
    last_t: float | None = None

    def _hash(rec: dict) -> int:
        path = rec["path"]
        if hash_cache is not None and path in hash_cache:
            return hash_cache[path]
        return hash_fn(path, crop_vf)

    for rec in records:
        if last_hash is None:
            kept.append(rec)
            last_hash = _hash(rec)
            last_t = rec["t"]
            continue

        gap = rec["t"] - last_t
        current_hash = _hash(rec)
        distance = hamming(current_hash, last_hash)
        if gap < merge_gap_s and distance <= merge_dist:
            merged.append((last_t, rec["t"], distance, gap))
            continue

        kept.append(rec)
        last_hash = current_hash
        last_t = rec["t"]

    return kept, merged


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


def _read_pgm(path: Path) -> tuple[int, int, bytes]:
    """Parse a binary PGM (P5) as ffmpeg writes it: ``P5\\nW H\\nMAX\\n<raw bytes>``.

    ffmpeg emits no comments, so a minimal whitespace-tokenized header parse suffices.
    Raises ValueError on a malformed or short-read file.
    """
    raw = path.read_bytes()
    if raw[:2] != b"P5":
        raise ValueError(f"not a binary PGM (P5): {path}")
    idx = 2
    header: list[int] = []  # width, height, maxval
    while len(header) < 3:
        while idx < len(raw) and raw[idx] in b" \t\n\r":
            idx += 1
        start = idx
        while idx < len(raw) and raw[idx] not in b" \t\n\r":
            idx += 1
        if start == idx:
            raise ValueError(f"truncated PGM header: {path}")
        header.append(int(raw[start:idx]))
    idx += 1  # exactly one whitespace byte separates the header from the raster
    # (ffmpeg always emits a single \n here; the raster's first byte may itself be a
    # whitespace *value*, so this must stay a fixed +1, not a whitespace-skip loop).
    w, h, _maxval = header
    if not (1 <= w <= 8192 and 1 <= h <= 8192):  # bound a crafted header before slicing
        raise ValueError(f"PGM dimensions out of bounds ({w}x{h}): {path}")
    pixels = raw[idx : idx + w * h]
    if len(pixels) != w * h:
        raise ValueError(f"PGM pixel short read ({len(pixels)} != {w * h}): {path}")
    return w, h, pixels


def _trim_high_motion_edges(
    motion: list[int], pw: int, ph: int, *, hot_quantile: float = 0.85, band: float = 0.12
) -> tuple[int, int, int, int] | None:
    """Trim persistently high-motion edge bands; return (x0, x1, y0, y1) or None.

    `motion` is a per-pixel summed abs-diff map (row-major, length pw*ph). Pixels above
    the `hot_quantile` percentile are "hot" (cam / chat / toolbar motion); rows and
    columns whose hot-pixel ratio exceeds `band` are peeled inward from each edge. The
    remaining centre rectangle is the static slide region. When motion is sparse (a cam
    smaller than the quantile tail) the percentile is 0, so any motion at all counts as
    hot. A fully static capture (no motion) yields the full-frame box; the caller treats
    that as "no crop benefit". Returns None only if the region collapses.
    """
    n = pw * ph
    if n == 0:
        return None
    ordered = sorted(motion)
    hot_cut = ordered[min(int(n * hot_quantile), n - 1)]
    if hot_cut > 0:
        # `>=` so the quantile-pivot value itself (the cam's quietest pixels) is hot.
        hot = [1 if m >= hot_cut else 0 for m in motion]
    else:
        # Sparse cam (smaller than the tail) or fully static: the percentile is 0, so
        # key on any real motion. A fully static map -> no hot pixels -> full-frame box,
        # which the caller rejects as "no crop benefit".
        hot = [1 if m > 0 else 0 for m in motion]
    row_ratio = [sum(hot[y * pw : y * pw + pw]) / pw for y in range(ph)]
    col_ratio = [sum(hot[y * pw + x] for y in range(ph)) / ph for x in range(pw)]

    y0 = 0
    while y0 < ph and row_ratio[y0] > band:
        y0 += 1
    y1 = ph
    while y1 > y0 and row_ratio[y1 - 1] > band:
        y1 -= 1
    x0 = 0
    while x0 < pw and col_ratio[x0] > band:
        x0 += 1
    x1 = pw
    while x1 > x0 and col_ratio[x1 - 1] > band:
        x1 -= 1
    if x1 - x0 <= 0 or y1 - y0 <= 0:
        return None
    return x0, x1, y0, y1


def detect_slide_crop(
    video: Path, vw: int, vh: int, *, samples: int = 24, probe_w: int = 320
) -> str | None:
    """Auto-detect the static slide region as a ``"W:H:X:Y"`` crop string, or None.

    Heuristic (zero-dependency: ffmpeg + stdlib). Sample `samples` grayscale frames,
    build a per-pixel temporal motion map (summed frame-to-frame abs-diff), then trim
    edge bands that are persistently high-motion -- a presenter cam, side chat, or a
    toolbar. The remaining centre rectangle is the slide region, scaled back to source
    resolution and even-aligned for a yuv420 crop.

    Returns None (the caller falls back to --cam-corner/--caption) when detection is
    unreliable: a missing duration, fewer than 3 sampled frames, any ffmpeg/PGM failure,
    a fully static capture, or a trimmed region whose area is under 40% or over 92% of the
    frame (negligible crop benefit -- e.g. a uniform-motion demo where edge-trim does
    almost nothing). A genuine failure (ffmpeg error, PGM parse) is warned before the
    None so the fallback isn't silent. Best-effort by design -- explicit --crop stays the
    precise option.
    """
    try:
        duration = _probe_duration(video)
    except (RuntimeError, subprocess.CalledProcessError, OSError, ValueError) as exc:
        warnings.warn(
            f"--crop auto: could not probe duration ({exc}); skipping auto-detect",
            RuntimeWarning,
            stacklevel=2,
        )
        return None
    if duration <= 0 or samples < 3 or vw <= 0 or vh <= 0:
        return None
    interval = max(duration / samples, 0.5)

    with tempfile.TemporaryDirectory(prefix="cw-autocrop-") as td:
        pattern = str(Path(td) / "f%04d.pgm")
        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin",
            "-protocol_whitelist", "file",
            "-i", str(video),
            "-vf", f"fps=1/{interval:.4f},scale={probe_w}:-2,format=gray",
            "-frames:v", str(samples),
            "-y", pattern,
        ]
        proc = subprocess.run(cmd, capture_output=True)
        if proc.returncode != 0:
            warnings.warn(
                "--crop auto: ffmpeg frame-sampling failed (exit "
                f"{proc.returncode}): "
                f"{(proc.stderr or b'').decode(errors='replace').strip()[-300:]}",
                RuntimeWarning,
                stacklevel=2,
            )
            return None
        pgm_paths = sorted(Path(td).glob("f*.pgm"))
        if len(pgm_paths) < 3:
            return None
        try:
            frames = [_read_pgm(p) for p in pgm_paths]
        except (ValueError, OSError) as exc:
            warnings.warn(
                f"--crop auto: PGM parse failed ({exc}); skipping auto-detect",
                RuntimeWarning,
                stacklevel=2,
            )
            return None

    pw, ph, _ = frames[0]
    if pw < 8 or ph < 8 or any(f[0] != pw or f[1] != ph for f in frames):
        return None

    n = pw * ph
    motion = [0] * n
    for i in range(1, len(frames)):
        a, b = frames[i - 1][2], frames[i][2]
        motion = [m + abs(bj - aj) for m, aj, bj in zip(motion, a, b)]

    box = _trim_high_motion_edges(motion, pw, ph)
    if box is None:
        return None
    x0, x1, y0, y1 = box
    cw, ch = x1 - x0, y1 - y0
    area = cw * ch
    # Reject a region that is too small (over-trimmed) or barely trimmed at all (>92%
    # area = uniform-motion noise that peeled a sliver -> a plausible-but-useless crop).
    if area < 0.40 * n or area > 0.92 * n:
        return None

    sx, sy = vw / pw, vh / ph
    X = int(round(x0 * sx)) & ~1
    Y = int(round(y0 * sy)) & ~1
    W = int(round(cw * sx)) & ~1
    H = int(round(ch * sy)) & ~1
    W = min(W, (vw - X) & ~1)
    H = min(H, (vh - Y) & ~1)
    if W <= 0 or H <= 0:
        return None
    return f"{W}:{H}:{X}:{Y}"


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
    merge_gap_s: float = 15.0,
    merge_dist: int = 11,
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

    After dedup, `time_aware_merge` collapses animation/scroll build-steps that are
    both close in time (< `merge_gap_s`) and close in hash (<= `merge_dist`) into the
    preceding kept frame. Pass `merge_gap_s <= 0` or `merge_dist <= 0` to disable this
    pass entirely and restore byte-identical pre-merge (R05) behavior.

    The merged pairs are returned as `result["merged"]` (mirroring `result["flagged"]`
    from `phash_dedup`) so a caller can surface exactly what got folded away — this
    pass is otherwise a silent drop, and its default `merge_dist` (11) is looser than
    `flag_dist` (10), so a genuinely distinct slide shown within `merge_gap_s` could
    vanish with no visible trace if the caller doesn't print `merged`.
    """
    if crop == "auto":
        vw, vh = probe_dimensions(video)
        spec = detect_slide_crop(video, vw, vh)
        if spec:
            w, h, x, y = parse_crop(spec)
            crop_vf = f"crop={w}:{h}:{x}:{y},"
        else:
            warnings.warn(
                "--crop auto: slide-region detection unreliable; falling back to "
                "--cam-corner/--caption (use explicit --crop W:H:X:Y for precision)",
                RuntimeWarning,
                stacklevel=2,
            )
            crop_vf = build_crop_vf(vw, vh, cam_corner, caption)
    elif crop:
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
        # hash_cache collects the per-kept-frame dhash phash_dedup already computed,
        # so the merge pass below can reuse it instead of re-hashing the same JPEGs
        # (both passes use the same hash_fn=dhash / crop_vf="" at this call site).
        hash_cache: dict[str, int] = {}
        kept, flagged = phash_dedup(
            absolute_records, crop_vf="", drop_dist=drop_dist, flag_dist=flag_dist,
            hash_cache=hash_cache,
        )
        # merge_gap_s/merge_dist <= 0 disables the pass entirely, restoring R05
        # byte-identical behavior.
        merged: list[tuple[float, float, int, float]] = []
        if merge_gap_s > 0 and merge_dist > 0:
            kept, merged = time_aware_merge(
                kept, crop_vf="", merge_gap_s=merge_gap_s, merge_dist=merge_dist,
                hash_cache=hash_cache,
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

    return {"slides": slides, "flagged": flagged, "merged": merged}
