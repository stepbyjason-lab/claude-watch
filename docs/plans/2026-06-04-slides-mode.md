# `--slides` Mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `--slides` mode to claude-watch that captures every unique slide of a lecture video, legibly, without silently dropping distinct slides.

**Architecture:** A separate detection path (`scripts/slides.py`) that (1) downloads 720p, (2) scene-detects on a *cropped* slide region (cam/caption excluded) at a low threshold, (3) adds a tight coverage floor, (4) extracts candidates once at native 720p, (5) deduplicates near-identical frames via a zero-dependency perceptual hash computed on the slide region — conservatively, flagging borderline pairs instead of dropping them. The existing non-slides pipeline is untouched (byte-identical defaults); the only shared-code change is one optional `prefilter=` kwarg on `detect_scenes`.

**Tech Stack:** Python 3.11 stdlib only (no new deps), ffmpeg/ffprobe/yt-dlp shell-outs (list-form subprocess), pytest (`integration` marker for tests that shell to ffmpeg).

**Canonical spec:** `docs/specs/2026-06-04-slides-mode-design.md` — §8 is authoritative.

---

## Design decisions locked from spec §8 (read before starting)

- **Completeness over dedup (§8.0):** over-capture, never silent-miss. `phash_dedup` drops only near-identical frames (`drop_dist`, small); borderline pairs (`drop_dist < d <= flag_dist`) are KEPT and reported.
- **Cache identity (§8.1):** slides runs get a distinct library dir (mode+resolution folded into the slug) and a separate `slides.json` scene cache — never reuse a normal run's 360p `video.*` or whole-frame scenes.
- **Minimal upstream diff (§8.2):** do NOT add `kind="slide"` to `Scene`. Add one `prefilter=""` kwarg to `detect_scenes` (default keeps byte-identical behavior).
- **Single extraction (§8.3):** detect pass writes no frames; extract candidates ONCE at full 720p; phash-dedup on those JPEGs; `unlink()` losers. No re-decode.
- **Zero new dependency (§8.4):** perceptual hash via ffmpeg `scale=8:8,format=gray` → 64 raw bytes → 64-bit average hash.
- **Security (§8.5):** enum `choices=` + assert; reject non-http(s) schemes; `--scene-threshold` ∈ (0,1); candidate hard-cap; `-protocol_whitelist file`; `--out-dir` wipe containment check; download format via enum, never raw `-f` string.

## File structure

| File | Create/Modify | Responsibility |
|---|---|---|
| `scripts/scenes.py` | Modify | Add `prefilter=` kwarg to `detect_scenes` (reuse parser for crop detection) |
| `scripts/slides.py` | **Create** | Slide-mode: `probe_dimensions`, `build_crop_vf`, `ahash`/`hamming`, `phash_dedup`, `detect_slides` orchestrator |
| `scripts/download.py` | Modify | `download_video(..., fmt=...)` enum format selection (default byte-identical) |
| `scripts/library.py` | Modify | `slug_for` folds `mode`+`dl_resolution` into the hash (distinct dirs) |
| `scripts/frames.py` | Modify | `extract_frames(..., native=False)` — native (no-downscale) extraction |
| `scripts/watch.py` | Modify | `--slides/--cam-corner/--caption/--hi-res/--phash-dist` flags, input validation, `select_scenes()` seam, download-format branch, manifest fields, out-dir containment |
| `SKILL.md` | Modify | Slides-mode notes guidance for the agent consumer |
| `tests/test_slides.py` | **Create** | Unit + integration tests for slides.py |
| `tests/test_*` (existing) | Modify | Extend for new kwargs |

---

## Task 1: `detect_scenes` gains a `prefilter` seam (no behavior change)

**Files:**
- Modify: `scripts/scenes.py` (`detect_scenes`, ~line 27-45)
- Test: `tests/test_scenes.py`

- [ ] **Step 1: Write the failing test** (append to `tests/test_scenes.py`)

```python
from scripts.scenes import _build_scene_vf  # new pure helper


def test_build_scene_vf_default_has_no_prefilter():
    assert _build_scene_vf(0.30, "") == "select='gt(scene,0.3)',showinfo,metadata=print"


def test_build_scene_vf_prepends_prefilter():
    vf = _build_scene_vf(0.10, "crop=100:100:0:0,")
    assert vf == "crop=100:100:0:0,select='gt(scene,0.1)',showinfo,metadata=print"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_scenes.py::test_build_scene_vf_default_has_no_prefilter -v`
Expected: FAIL — `ImportError: cannot import name '_build_scene_vf'`

- [ ] **Step 3: Write minimal implementation** — in `scripts/scenes.py`, add the helper above `detect_scenes` and use it; add the kwarg.

```python
def _build_scene_vf(threshold: float, prefilter: str = "") -> str:
    """ffmpeg -vf string for scene detection. `prefilter` (if given) must be a
    complete filter chain ending in a comma, e.g. 'crop=W:H:X:Y,'."""
    return f"{prefilter}select='gt(scene,{threshold})',showinfo,metadata=print"


def detect_scenes(video: Path, threshold: float = 0.30, *, prefilter: str = "") -> list[Scene]:
    # ... docstring unchanged ...
    cmd = [
        "ffmpeg", "-hide_banner", "-nostats",
        "-protocol_whitelist", "file",
        "-i", str(video),
        "-vf", _build_scene_vf(threshold, prefilter),
        "-f", "null", "-",
    ]
    # ... rest of function body unchanged ...
```

Note: `{threshold}` formats `0.30` as `0.3` (Python float repr) — the test expects `0.3`. This matches existing runtime behavior (the old hardcoded string also used `{threshold}`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_scenes.py -v`
Expected: PASS (all existing scene tests + 2 new). The integration test `test_detect_scenes_finds_known_cuts_in_fixture` still passes (default `prefilter=""`).

- [ ] **Step 5: Commit**

```bash
git add scripts/scenes.py tests/test_scenes.py
git commit -m "feat(scenes): add prefilter kwarg to detect_scenes (reuse seam for slides)"
```

---

## Task 2: `build_crop_vf` — slide-region crop string (pure)

**Files:**
- Create: `scripts/slides.py`
- Test: `tests/test_slides.py`

- [ ] **Step 1: Write the failing test** (`tests/test_slides.py`)

```python
import pytest
from scripts.slides import build_crop_vf


def test_crop_excludes_top_right_cam_and_bottom_caption():
    # 1920x1080, cam top-right 20% width, caption bottom 15% height
    vf = build_crop_vf(1920, 1080, cam_corner="tr", caption="bottom")
    # width = 1920 - int(1920*0.20) = 1920-384 = 1536 ; x=0
    # height = 1080 - int(1080*0.15) = 1080-162 = 918 ; y=0
    assert vf == "crop=1536:918:0:0,"


def test_crop_left_cam_shifts_x_origin():
    vf = build_crop_vf(1000, 1000, cam_corner="bl", caption="none")
    # left 20% excluded: x0 = 200, width = 800; no caption: full height
    assert vf == "crop=800:1000:200:0,"


def test_crop_none_none_is_full_frame():
    assert build_crop_vf(1280, 720, cam_corner="none", caption="none") == "crop=1280:720:0:0,"


def test_crop_rejects_invalid_enum():
    with pytest.raises(AssertionError):
        build_crop_vf(1280, 720, cam_corner="middle", caption="bottom")


def test_crop_falls_back_to_full_frame_when_region_too_small(recwarn):
    # absurd fractions would leave <50% area → fall back to full frame + warn
    vf = build_crop_vf(1000, 1000, cam_corner="tr", caption="bottom",
                       cam_frac=0.7, cap_frac=0.7)
    assert vf == "crop=1000:1000:0:0,"
    assert any("slide region" in str(w.message).lower() for w in recwarn.list)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_slides.py::test_crop_excludes_top_right_cam_and_bottom_caption -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scripts.slides'`

- [ ] **Step 3: Write minimal implementation** (`scripts/slides.py`)

```python
"""Slide-deck mode: crop-aware detection + zero-dep perceptual-hash dedup."""
from __future__ import annotations

import warnings

VALID_CAM = {"tr", "tl", "br", "bl", "none"}
VALID_CAPTION = {"bottom", "top", "none"}


def build_crop_vf(
    w: int, h: int,
    cam_corner: str = "tr", caption: str = "bottom",
    *, cam_frac: float = 0.20, cap_frac: float = 0.15,
) -> str:
    """ffmpeg crop filter 'crop=CW:CH:CX:CY,' for the slide region = full frame
    minus the presenter-cam strip and the caption band. Always ends with ','.

    cam in a right corner (tr/br) excludes the right `cam_frac` of width; a left
    corner (tl/bl) excludes the left. caption bottom/top excludes that band.
    If the result would be <50% of frame area, falls back to the full frame.
    """
    assert cam_corner in VALID_CAM, f"bad cam_corner: {cam_corner!r}"
    assert caption in VALID_CAPTION, f"bad caption: {caption!r}"
    x0, x1, y0, y1 = 0, w, 0, h
    cw = int(w * cam_frac)
    if cam_corner in ("tr", "br"):
        x1 = w - cw
    elif cam_corner in ("tl", "bl"):
        x0 = cw
    ch = int(h * cap_frac)
    if caption == "bottom":
        y1 = h - ch
    elif caption == "top":
        y0 = ch
    cw_out, ch_out = x1 - x0, y1 - y0
    if cw_out <= 0 or ch_out <= 0 or cw_out * ch_out < 0.5 * w * h:
        warnings.warn(
            "computed slide region <50% of frame; falling back to full frame "
            "(tune --cam-corner/--caption)",
            RuntimeWarning, stacklevel=2,
        )
        return f"crop={w}:{h}:0:0,"
    return f"crop={cw_out}:{ch_out}:{x0}:{y0},"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_slides.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add scripts/slides.py tests/test_slides.py
git commit -m "feat(slides): build_crop_vf slide-region crop builder with validation"
```

---

## Task 3: Zero-dependency perceptual hash (`ahash`, `hamming`)

**Files:**
- Modify: `scripts/slides.py`
- Test: `tests/test_slides.py`

- [ ] **Step 1: Write the failing test** (append to `tests/test_slides.py`)

```python
from pathlib import Path
from scripts.slides import ahash, hamming

FIXTURE = Path(__file__).parent / "fixtures" / "sample_10s.mp4"


def test_hamming_counts_differing_bits():
    assert hamming(0b1010, 0b1000) == 1
    assert hamming(0, 0xFFFFFFFFFFFFFFFF) == 64
    assert hamming(42, 42) == 0


@pytest.mark.integration
def test_ahash_is_stable_and_returns_64bit(tmp_path):
    # extract one frame to a JPEG, hash it twice → identical
    import subprocess
    jpg = tmp_path / "f.jpg"
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
         "-ss", "1.0", "-i", str(FIXTURE), "-frames:v", "1", str(jpg)],
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
    for t, p in [(1.0, a), (8.0, b)]:  # sample_10s has cuts at 3 and 6 → 1s vs 8s differ
        subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
             "-ss", str(t), "-i", str(FIXTURE), "-frames:v", "1", str(p)],
            check=True,
        )
    assert hamming(ahash(a), ahash(b)) > 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_slides.py::test_hamming_counts_differing_bits -v`
Expected: FAIL — `ImportError: cannot import name 'ahash'`

- [ ] **Step 3: Write minimal implementation** (append to `scripts/slides.py`)

```python
import subprocess
from pathlib import Path


def hamming(a: int, b: int) -> int:
    """Number of differing bits between two 64-bit hashes."""
    return bin(a ^ b).count("1")


def ahash(image_path: Path, crop_vf: str = "") -> int:
    """64-bit average-hash of the (optionally cropped) image, computed via ffmpeg
    (no Pillow). Downscales the slide region to 8x8 grayscale and thresholds each
    pixel against the mean. `crop_vf` (if given) must end in ','."""
    vf = f"{crop_vf}scale=8:8,format=gray"
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin",
        "-protocol_whitelist", "file",
        "-i", str(image_path),
        "-vf", vf, "-frames:v", "1", "-f", "rawvideo", "-",
    ]
    raw = subprocess.run(cmd, capture_output=True, check=True).stdout
    if len(raw) != 64:
        raise RuntimeError(f"ahash expected 64 gray bytes, got {len(raw)} for {image_path}")
    avg = sum(raw) / 64.0
    bits = 0
    for i, b in enumerate(raw):
        if b >= avg:
            bits |= (1 << i)
    return bits
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_slides.py -v -m "not integration"` then `python3 -m pytest tests/test_slides.py -v -m integration`
Expected: PASS (hamming unit test always; ahash integration tests pass with ffmpeg present)

- [ ] **Step 5: Commit**

```bash
git add scripts/slides.py tests/test_slides.py
git commit -m "feat(slides): zero-dep 8x8 average perceptual hash + hamming"
```

---

## Task 4: `phash_dedup` — conservative dedup with borderline flagging

**Files:**
- Modify: `scripts/slides.py`
- Test: `tests/test_slides.py`

- [ ] **Step 1: Write the failing test** (append to `tests/test_slides.py`)

`phash_dedup` is made unit-testable by injecting the hash function (`hash_fn`) so no ffmpeg is needed.

```python
from scripts.slides import phash_dedup


def _records(ts):
    return [{"index": i + 1, "t": float(t), "path": f"/tmp/{i}.jpg"} for i, t in enumerate(ts)]


def test_dedup_collapses_near_identical_consecutive():
    recs = _records([0, 1, 2, 3])
    # hashes: slide A held across t0..t2 (tiny noise), then slide B at t3
    hashes = {"/tmp/0.jpg": 0b0000, "/tmp/1.jpg": 0b0001,
              "/tmp/2.jpg": 0b0000, "/tmp/3.jpg": 0b1111_1111}
    kept, flagged = phash_dedup(recs, crop_vf="", drop_dist=2, flag_dist=4,
                                hash_fn=lambda p, c: hashes[p])
    assert [r["t"] for r in kept] == [0.0, 3.0]   # A and B; the two A-noise frames dropped
    assert flagged == []


def test_dedup_keeps_and_flags_borderline_pair():
    recs = _records([0, 1])
    hashes = {"/tmp/0.jpg": 0b0000, "/tmp/1.jpg": 0b0111}  # hamming=3, between drop(2) and flag(4)
    kept, flagged = phash_dedup(recs, crop_vf="", drop_dist=2, flag_dist=4,
                                hash_fn=lambda p, c: hashes[p])
    assert [r["t"] for r in kept] == [0.0, 1.0]     # KEPT (never silent-drop borderline)
    assert flagged == [(0.0, 1.0, 3)]


def test_dedup_keeps_clearly_distinct_without_flag():
    recs = _records([0, 1])
    hashes = {"/tmp/0.jpg": 0b0000, "/tmp/1.jpg": 0xFFFF}  # hamming large
    kept, flagged = phash_dedup(recs, crop_vf="", drop_dist=2, flag_dist=4,
                                hash_fn=lambda p, c: hashes[p])
    assert [r["t"] for r in kept] == [0.0, 1.0]
    assert flagged == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_slides.py::test_dedup_collapses_near_identical_consecutive -v`
Expected: FAIL — `ImportError: cannot import name 'phash_dedup'`

- [ ] **Step 3: Write minimal implementation** (append to `scripts/slides.py`)

```python
def phash_dedup(
    frame_records: list[dict], *, crop_vf: str,
    drop_dist: int = 4, flag_dist: int = 10,
    hash_fn=ahash,
) -> tuple[list[dict], list[tuple[float, float, int]]]:
    """Keep one frame per unique slide, comparing each to the last KEPT frame.

    - hamming <= drop_dist  → near-identical → DROP (true duplicate of held slide)
    - drop_dist < h <= flag_dist → borderline → KEEP both, record (t_prev, t_cur, h)
    - h > flag_dist → clearly distinct → KEEP

    Conservative by design (spec §8.0): never silent-drops a borderline pair.
    `hash_fn(path, crop_vf) -> int` is injectable for testing.
    """
    kept: list[dict] = []
    flagged: list[tuple[float, float, int]] = []
    last_hash = None
    last_rec = None
    for rec in frame_records:
        h = hash_fn(rec["path"], crop_vf)
        if last_hash is None:
            kept.append(rec); last_hash = h; last_rec = rec
            continue
        d = hamming(h, last_hash)
        if d <= drop_dist:
            continue
        if d <= flag_dist:
            flagged.append((last_rec["t"], rec["t"], d))
        kept.append(rec); last_hash = h; last_rec = rec
    return kept, flagged
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_slides.py -v -m "not integration"`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/slides.py tests/test_slides.py
git commit -m "feat(slides): conservative phash dedup with borderline flagging"
```

---

## Task 5: `download.py` enum format selection (720p/1080p/best)

**Files:**
- Modify: `scripts/download.py`
- Test: `tests/test_download.py`

- [ ] **Step 1: Write the failing test** (append to `tests/test_download.py`)

```python
from scripts.download import format_selector


def test_format_selector_known_enums():
    assert format_selector("best") == "best[ext=mp4]/best"
    assert format_selector("720p") == "bv*[height<=720]+ba/b[height<=720]/best"
    assert format_selector("1080p") == "bv*[height<=1080]+ba/b[height<=1080]/best"


def test_format_selector_unknown_falls_back_to_best():
    assert format_selector("4k") == "best[ext=mp4]/best"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_download.py::test_format_selector_known_enums -v`
Expected: FAIL — `ImportError: cannot import name 'format_selector'`

- [ ] **Step 3: Write minimal implementation** — in `scripts/download.py`

```python
_FORMATS = {
    "best": "best[ext=mp4]/best",
    "720p": "bv*[height<=720]+ba/b[height<=720]/best",
    "1080p": "bv*[height<=1080]+ba/b[height<=1080]/best",
}


def format_selector(fmt: str) -> str:
    """Map an internal format enum to a yt-dlp -f selector. Unknown → 'best'.
    Callers pass an enum key (never a raw user string)."""
    return _FORMATS.get(fmt, _FORMATS["best"])


def download_video(url: str, out_dir: Path, *, basename: str = "video", fmt: str = "best") -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    template = str(out_dir / f"{basename}.%(ext)s")
    cmd = [
        "yt-dlp", "--no-playlist",
        "-f", format_selector(fmt),
        "-o", template,
        url,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"yt-dlp failed: {proc.stderr.strip()}")
    matches = sorted(out_dir.glob(f"{basename}.*"))
    if not matches:
        raise RuntimeError(f"yt-dlp returned 0 but no {basename}.* file in {out_dir}")
    return matches[0]
```

Default `fmt="best"` → selector `best[ext=mp4]/best` (byte-identical to old hardcoded). `--no-playlist` preserved.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_download.py -v`
Expected: PASS (existing + new)

- [ ] **Step 5: Commit**

```bash
git add scripts/download.py tests/test_download.py
git commit -m "feat(download): enum format selection (720p/1080p), default unchanged"
```

---

## Task 6: `slug_for` folds mode + resolution into identity (cache isolation)

**Files:**
- Modify: `scripts/library.py` (`slug_for`)
- Test: `tests/test_library.py`

- [ ] **Step 1: Write the failing test** (append to `tests/test_library.py`)

```python
def test_slug_differs_for_slides_mode():
    base = {"title": "L", "source": "https://x", "watched_at": "2026-05-03", "focus_range_str": ""}
    slides = dict(base, mode="slides", dl_resolution="720p")
    assert slug_for(base) != slug_for(slides)


def test_slug_differs_for_resolution():
    a = {"title": "L", "source": "https://x", "watched_at": "2026-05-03",
         "focus_range_str": "", "mode": "slides", "dl_resolution": "720p"}
    b = dict(a, dl_resolution="1080p")
    assert slug_for(a) != slug_for(b)


def test_slug_default_mode_unchanged_when_fields_absent():
    # Back-compat: a meta without mode/dl_resolution must hash the same as one
    # that explicitly sets the defaults.
    bare = {"title": "L", "source": "https://x", "watched_at": "2026-05-03", "focus_range_str": ""}
    explicit = dict(bare, mode="default", dl_resolution="best")
    assert slug_for(bare) == slug_for(explicit)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_library.py::test_slug_differs_for_slides_mode -v`
Expected: FAIL — slugs equal (mode not in hash)

- [ ] **Step 3: Write minimal implementation** — modify `slug_for` in `scripts/library.py`

```python
def slug_for(meta: dict) -> str:
    """`YYYY-MM-DD-<sanitized-title>-<short-hash>` where
    hash = sha1(source + focus + mode + dl_resolution)[:4].
    mode/dl_resolution default to 'default'/'best' so non-slides callers are unchanged."""
    date = meta.get("watched_at") or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    title = sanitize_title(meta.get("title", "untitled"))
    src = meta.get("source", "")
    focus = meta.get("focus_range_str", "")
    mode = meta.get("mode", "default")
    res = meta.get("dl_resolution", "best")
    key = "|".join([src, focus, mode, res])
    h = hashlib.sha1(key.encode("utf-8")).hexdigest()[:4]
    return f"{date}-{title}-{h}"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_library.py -v`
Expected: PASS. NOTE: existing `test_slug_is_stable_for_same_url_and_focus` and `test_slug_differs_for_different_focus_range` still pass (defaults preserved). The old hardcoded hash value tests (if any assert exact hash) — there are none; only stability/difference asserts.

- [ ] **Step 5: Commit**

```bash
git add scripts/library.py tests/test_library.py
git commit -m "feat(library): fold mode+resolution into slug (slides cache isolation)"
```

---

## Task 7: `extract_frames` native (no-downscale) option

**Files:**
- Modify: `scripts/frames.py` (`extract_frames`, ~line 18-48)
- Test: `tests/test_frames.py`

- [ ] **Step 1: Write the failing test** (append to `tests/test_frames.py`)

```python
@pytest.mark.integration
def test_extract_frames_native_keeps_source_width(tmp_path):
    # native=True must not downscale; width must exceed an explicit 64px request
    scenes = [Scene(t=1.0, score=1.0, kind="detected")]
    native = extract_frames(FIXTURE, scenes, out_dir=tmp_path / "n", width_px=64, native=True)
    small = extract_frames(FIXTURE, scenes, out_dir=tmp_path / "s", width_px=64, native=False)
    size_native = (tmp_path / "n" / native[0]["path"]).stat().st_size
    size_small = (tmp_path / "s" / small[0]["path"]).stat().st_size
    assert size_native > size_small, "native must ignore width_px downscale"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_frames.py::test_extract_frames_native_keeps_source_width -v`
Expected: FAIL — `TypeError: extract_frames() got an unexpected keyword argument 'native'`

- [ ] **Step 3: Write minimal implementation** — modify `extract_frames` in `scripts/frames.py`

```python
def extract_frames(
    video: Path,
    scenes: list[Scene],
    *,
    out_dir: Path,
    width_px: int = 512,
    native: bool = False,
) -> list[dict]:
    """... existing docstring ... If `native`, no scale filter is applied (1:1)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    results: list[dict] = []
    for i, scene in enumerate(scenes, start=1):
        name = format_filename(i, scene.t)
        out_path = out_dir / name
        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
            "-protocol_whitelist", "file",
            "-ss", f"{scene.t:.3f}",
            "-i", str(video),
            "-frames:v", "1",
        ]
        if not native:
            cmd += ["-vf", f"scale={width_px}:-2"]
        cmd += ["-q:v", "3", str(out_path)]
        try:
            subprocess.run(cmd, check=True, capture_output=True)
        except subprocess.CalledProcessError as e:
            # ... existing error handling unchanged ...
            stderr_text = (
                e.stderr.decode("utf-8", errors="replace")
                if isinstance(e.stderr, (bytes, bytearray))
                else (e.stderr or "")
            )
            raise RuntimeError(
                f"ffmpeg failed extracting frame {i} at t={scene.t:.3f}s "
                f"(exit {e.returncode}): {stderr_text.strip()}"
            ) from e
        results.append({"index": i, "t": scene.t, "path": name, "kind": scene.kind})
    return results
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_frames.py -v`
Expected: PASS (existing default-path tests + native test)

- [ ] **Step 5: Commit**

```bash
git add scripts/frames.py tests/test_frames.py
git commit -m "feat(frames): native (no-downscale) extraction + protocol whitelist"
```

---

## Task 8: `detect_slides` orchestrator (compose detect → floor → extract → dedup)

**Files:**
- Modify: `scripts/slides.py`
- Test: `tests/test_slides.py`

- [ ] **Step 1: Write the failing test** (append to `tests/test_slides.py`)

`probe_dimensions` is integration (ffprobe). `detect_slides` is integration (full pipeline on the fixture). Candidate cap is unit-tested via the helper.

```python
from scripts.slides import probe_dimensions, detect_slides, CandidateCapExceeded


@pytest.mark.integration
def test_probe_dimensions_reads_fixture_wh():
    w, h = probe_dimensions(FIXTURE)
    assert w > 0 and h > 0


@pytest.mark.integration
def test_detect_slides_returns_frame_records_on_fixture(tmp_path):
    # sample_10s.mp4 has 3 visually distinct segments (cuts at 3,6) → expect >=2 unique
    out = detect_slides(
        FIXTURE, out_dir=tmp_path, cam_corner="none", caption="none",
        threshold=0.30, max_gap=20.0, drop_dist=4, flag_dist=10,
        width_px=1280, candidate_cap=800,
    )
    assert out["slides"]  # list of frame records
    assert len(out["slides"]) >= 2
    for r in out["slides"]:
        assert (tmp_path / r["path"]).exists()
    assert "flagged" in out  # list (possibly empty)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_slides.py::test_probe_dimensions_reads_fixture_wh -v`
Expected: FAIL — `ImportError: cannot import name 'probe_dimensions'`

- [ ] **Step 3: Write minimal implementation** (append to `scripts/slides.py`)

```python
import json
from scripts.scenes import detect_scenes, apply_coverage_floor
from scripts import frames as frames_mod


class CandidateCapExceeded(RuntimeError):
    """Raised when scene-detect yields more candidate frames than the safety cap."""


def probe_dimensions(video: Path) -> tuple[int, int]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x", str(video)],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    w, h = out.split("x")
    return int(w), int(h)


def detect_slides(
    video: Path, *, out_dir: Path,
    cam_corner: str = "tr", caption: str = "bottom",
    threshold: float = 0.10, max_gap: float = 20.0,
    drop_dist: int = 4, flag_dist: int = 10,
    width_px: int = 1280, candidate_cap: int = 800,
) -> dict:
    """Full slides pipeline. Returns {"slides": [frame_records], "flagged": [(t,t,d)]}.

    1. crop-detect on the slide region (one full decode, no frames written)
    2. coverage floor (tight) to catch light->light slides
    3. extract every candidate ONCE at native width (full-frame JPEGs)
    4. phash-dedup on the slide region of those JPEGs; unlink dropped files
    """
    from scripts.scenes import Scene  # local import to avoid cycle at top
    duration = float(probe_dimensions.__doc__ and 0.0)  # placeholder removed below
    w, h = probe_dimensions(video)
    crop_vf = build_crop_vf(w, h, cam_corner, caption)

    # 1. detect on cropped region (reuse parser via prefilter)
    raw = detect_scenes(video, threshold=threshold, prefilter=crop_vf)
    # duration for the floor = last scene t or probe; use ffprobe duration:
    dur = _probe_duration(video)
    floored = apply_coverage_floor(raw, duration_s=dur, max_gap_s=max_gap)

    if len(floored) > candidate_cap:
        raise CandidateCapExceeded(
            f"{len(floored)} candidate frames exceeds cap {candidate_cap}; "
            f"raise --scene-threshold or use --start/--end"
        )

    # 3. extract candidates ONCE at native full-frame
    out_dir.mkdir(parents=True, exist_ok=True)
    records = frames_mod.extract_frames(
        video, floored, out_dir=out_dir, width_px=width_px, native=True
    )
    for r in records:  # make path absolute for hashing/unlink
        r["abspath"] = str(out_dir / r["path"])
        r["path_for_hash"] = r["abspath"]

    # 4. phash dedup on slide region (crop), conservative
    hash_records = [{**r, "path": r["abspath"]} for r in records]
    kept, flagged = phash_dedup(
        hash_records, crop_vf=crop_vf, drop_dist=drop_dist, flag_dist=flag_dist
    )
    kept_paths = {r["path"] for r in kept}
    for r in records:
        if r["abspath"] not in kept_paths:
            Path(r["abspath"]).unlink(missing_ok=True)

    # renumber survivors 1..N, restore relative path
    slides = []
    for i, r in enumerate(records, start=1):
        if r["abspath"] in kept_paths:
            slides.append({"index": len(slides) + 1, "t": r["t"],
                           "path": r["path"], "kind": "detected"})
    return {"slides": slides, "flagged": flagged}


def _probe_duration(video: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(video)],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    return float(out or 0.0)
```

> **Implementer note:** delete the `duration = float(probe_dimensions.__doc__ ...)` placeholder line — it is a marker that must not survive; `dur` is computed by `_probe_duration`. (Kept here only to flag that the orchestrator needs duration; do NOT ship the placeholder.)

> **Known v1 limitation (spec §8.3):** candidate extraction is one `extract_frames` pass (1 decode + N keyframe seeks). The further optimization (single ffmpeg `select`+frame-dump pass) is deferred — N≈20–60 seeks is acceptable.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_slides.py -v -m integration`
Expected: PASS (probe + detect_slides on fixture). Also run `-m "not integration"` to confirm unit tests still green.

- [ ] **Step 5: Commit**

```bash
git add scripts/slides.py tests/test_slides.py
git commit -m "feat(slides): detect_slides orchestrator (detect->floor->extract->dedup)"
```

---

## Task 9: Wire `--slides` into `watch.py` (flags, validation, seam, manifest)

**Files:**
- Modify: `scripts/watch.py`
- Test: `tests/test_watch_e2e.py`

- [ ] **Step 1: Write the failing test** (append to `tests/test_watch_e2e.py`)

```python
import pytest
from scripts.watch import _validate_slides_args, _scheme_ok


def test_scheme_ok_rejects_file_and_ftp():
    assert _scheme_ok("https://youtu.be/x")
    assert _scheme_ok("http://x")
    assert _scheme_ok("/local/path.mp4")          # local path allowed (not a URL scheme)
    assert not _scheme_ok("file:///etc/passwd")
    assert not _scheme_ok("ftp://x/y")


def test_validate_slides_args_threshold_range():
    with pytest.raises(SystemExit):
        _validate_slides_args(scene_threshold=1.5, phash_dist=5)
    with pytest.raises(SystemExit):
        _validate_slides_args(scene_threshold=0.0, phash_dist=5)
    with pytest.raises(SystemExit):
        _validate_slides_args(scene_threshold=0.1, phash_dist=99)
    _validate_slides_args(scene_threshold=0.1, phash_dist=5)  # ok, no raise
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_watch_e2e.py::test_scheme_ok_rejects_file_and_ftp -v`
Expected: FAIL — `ImportError: cannot import name '_scheme_ok'`

- [ ] **Step 3: Write minimal implementation** — in `scripts/watch.py`

Add helpers + argparse flags + the `select_scenes` seam. Add near the top (after imports):

```python
from scripts import slides as slides_mod


def _scheme_ok(source: str) -> bool:
    """Reject non-http(s) URL schemes (file://, ftp://, ...). Local paths (no scheme) ok."""
    lowered = source.lower()
    for bad in ("file:", "ftp:", "data:", "gopher:"):
        if lowered.startswith(bad):
            return False
    return True


def _validate_slides_args(*, scene_threshold: float, phash_dist: int) -> None:
    if not (0.0 < scene_threshold < 1.0):
        sys.exit(f"--scene-threshold must be in (0,1); got {scene_threshold}")
    if not (0 <= phash_dist <= 64):
        sys.exit(f"--phash-dist must be in [0,64]; got {phash_dist}")
```

Add argparse flags in `main()` after the existing ones:

```python
    p.add_argument("--slides", action="store_true",
                   help="slide-deck mode: capture every unique slide (crop-detect + phash dedup)")
    p.add_argument("--cam-corner", choices=["tr", "tl", "br", "bl", "none"], default="tr",
                   help="presenter-cam corner to exclude from slide detection (slides mode)")
    p.add_argument("--caption", choices=["bottom", "top", "none"], default="bottom",
                   help="burned-in caption band to exclude (slides mode)")
    p.add_argument("--hi-res", action="store_true",
                   help="slides mode: download 1080p instead of 720p (tiny-text decks)")
    p.add_argument("--phash-dist", type=int, default=4,
                   help="slides dedup drop distance (<= this = duplicate)")
```

After `args = p.parse_args(argv)` add the guards:

```python
    if not _scheme_ok(args.source):
        sys.exit(f"refusing non-http(s)/local scheme: {args.source}")
    if args.slides:
        _validate_slides_args(scene_threshold=args.scene_threshold, phash_dist=args.phash_dist)
```

Set mode/resolution into meta BEFORE `slug = lib.slug_for(meta)` (around line 68-69):

```python
    meta["watched_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    meta["mode"] = "slides" if args.slides else "default"
    if args.slides:
        meta["dl_resolution"] = "1080p" if args.hi_res else "720p"
    else:
        meta["dl_resolution"] = "best"
    slug = lib.slug_for(meta)
```

In the download stage (line 80-84), branch the format:

```python
        if meta["is_url"]:
            video = download_mod.download_video(
                meta["source"], src_dir, basename="video", fmt=meta["dl_resolution"]
            )
        else:
            video = download_mod.copy_local(Path(meta["source"]), src_dir, basename="video")
```

Replace the Stage-4 scene block (lines 129-160) with a `select_scenes` call. Add this function above `main()`:

```python
def select_scenes(video, meta, args, focus, work):
    """Strategy seam: returns (capped_scenes, flagged_pairs, scene_cache_name).
    Slides mode -> slides.detect_slides; else the classic detect+floor+cap path."""
    if args.slides:
        frames_dir = work / "frames"
        result = slides_mod.detect_slides(
            video, out_dir=frames_dir,
            cam_corner=args.cam_corner, caption=args.caption,
            threshold=args.scene_threshold, max_gap=min(args.max_gap, 20.0),
            drop_dist=args.phash_dist, flag_dist=args.phash_dist + 6,
            width_px=1280, candidate_cap=800,
        )
        # detect_slides already extracted+deduped the frames into frames_dir.
        return result, "slides.json"
    # classic path (unchanged logic, moved verbatim) ...
```

> **Implementer note:** because `detect_slides` both detects AND extracts (single pass, §8.3), the slides branch SKIPS the later `extract_frames` call. Restructure Stage 5 so: if `args.slides`, use `result["slides"]` directly as the frame records (they are already on disk, relative paths) and `result["flagged"]` for the manifest; else run the existing `detect_scenes/floor/cap` + `extract_frames`. Keep the two branches in `select_scenes` / a sibling `extract_for_default()` so `main()` stays linear (architect H4).

Out-dir containment before the frames wipe (Stage 5, line 164-167) — guard it:

```python
    frames_dir = work / "frames"
    frames_dir_r = frames_dir.resolve()
    frames_dir_r.relative_to(lib.LIBRARY_ROOT.resolve())  # raises ValueError if escaping root
    if frames_dir.exists() and not args.slides:  # slides mode manages its own dir
        for f in frames_dir.iterdir():
            f.unlink()
```

Manifest/stdout: add `slides_extracted` and `review` lines when `args.slides`:

```python
    if args.slides:
        print(f"slides_extracted: {len(frame_records)}")
        for (ta, tb, d) in flagged:
            print(f"review: near-dup t={int(ta)//60:02d}:{int(ta)%60:02d} "
                  f"~ t={int(tb)//60:02d}:{int(tb)%60:02d} (dist {d})")
```

The scene cache write (line 156-160) must use `scene_cache_name` (`slides.json` in slides mode) and be gated so slides mode does not collide with `scenes.json`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_watch_e2e.py -v` then full suite `python3 -m pytest -v -m "not integration and not network"`
Expected: PASS (unit-level helpers). The existing e2e integration test still passes (default path untouched).

- [ ] **Step 5: Commit**

```bash
git add scripts/watch.py tests/test_watch_e2e.py
git commit -m "feat(watch): wire --slides mode (flags, validation, select_scenes seam, manifest)"
```

---

## Task 10: SKILL.md slides-mode guidance + README note

**Files:**
- Modify: `SKILL.md`
- Modify: `README.md`

- [ ] **Step 1: Add a slides-mode section to `SKILL.md`** (after the "How to invoke" section)

```markdown
## Slides mode (`--slides`)

For lecture/seminar videos where the speaker presents slides, add `--slides`:

```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/watch.py" "<url>" --slides
```

This captures **every unique slide** (page 1 → last): downloads 720p, detects slide
changes on the slide region (excluding the presenter cam + burned-in caption),
deduplicates near-identical frames, and extracts at native 720p.

- `--cam-corner {tr,tl,br,bl,none}` (default tr) — which corner the presenter cam occupies.
- `--caption {bottom,top,none}` (default bottom) — burned-in caption band to ignore.
- `--hi-res` — download 1080p (only for decks with very small text).
- `--phash-dist N` (default 4) — dedup aggressiveness; lower = keep more near-duplicates.

**Reading the output in slides mode:** treat **one slide = one section** (there is no
floor/detected distinction). The stdout prints `slides_extracted: N` and may print
`review: near-dup t=A ~ t=B` lines — these are borderline pairs the tool kept on purpose;
glance at both frames and drop one if they are truly the same slide. Frames are ordered
by timestamp = deck order.
```

- [ ] **Step 2: Add a fork note to `README.md`** (top, after the title)

```markdown
> **Fork note:** This fork (`stepbyjason-lab/claude-watch`) adds a `--slides` mode for
> capturing every slide of a lecture deck. See `docs/specs/2026-06-04-slides-mode-design.md`
> and `docs/plans/2026-06-04-slides-mode.md`. Upstream: `devinilabs/claude-watch`.
```

- [ ] **Step 3: Commit**

```bash
git add SKILL.md README.md
git commit -m "docs: document --slides mode in SKILL.md + fork note in README"
```

---

## Task 11: Integration regression on a real slide deck (manual / network-gated)

**Files:**
- Test: `tests/test_slides_regression.py` (network + integration marked, skipped by default)

- [ ] **Step 1: Write a network-gated regression test**

```python
import os
import pytest
from pathlib import Path
from scripts.slides import detect_slides

HARNESS_URL = "https://youtu.be/5buNm0pA1mg"  # known ~28 unique slides


@pytest.mark.network
@pytest.mark.integration
@pytest.mark.skipif(not os.environ.get("CW_RUN_NETWORK"), reason="set CW_RUN_NETWORK=1 to run")
def test_harness_deck_recovers_most_slides(tmp_path):
    # NOTE: requires yt-dlp + network; downloads 720p. Manual gate only.
    from scripts.download import download_video
    video = download_video(HARNESS_URL, tmp_path / "src", fmt="720p")
    out = detect_slides(video, out_dir=tmp_path / "frames",
                        cam_corner="tr", caption="bottom",
                        threshold=0.10, max_gap=20.0, drop_dist=4, flag_dist=10,
                        width_px=1280, candidate_cap=800)
    n = len(out["slides"])
    # Ground truth ~28 unique slides; allow a band (over-capture acceptable, misses not).
    assert 24 <= n <= 40, f"expected ~28 slides, got {n}"
```

- [ ] **Step 2: Run manually**

Run: `CW_RUN_NETWORK=1 python3 -m pytest tests/test_slides_regression.py -v -m network`
Expected: PASS with `n` near 28. If `n` is far off, tune the default `cam_frac`/`cap_frac` in `build_crop_vf` and the threshold, then re-run. Record the tuned defaults in the spec §8.9.

- [ ] **Step 3: Commit**

```bash
git add tests/test_slides_regression.py
git commit -m "test(slides): network-gated regression on harness deck (~28 slides)"
```

---

## Self-review checklist (run before execution)

- **Spec coverage:** §8.0 flag-not-drop → Task 4. §8.1 cache identity → Task 6 (+ Task 9 slides.json). §8.2 prefilter/no-kind-slide → Task 1 (+ Tasks 3/8 reuse Scene unchanged). §8.3 single extraction → Task 8. §8.4 zero-dep hash → Task 3. §8.5 security (choices/scheme/threshold/cap/whitelist/containment/enum-format) → Tasks 2,5,7,9. §8.6 native extract → Task 7. §8.7 select_scenes seam + SKILL.md → Tasks 9,10. §8.8 decomposition → Tasks 2-4,8.
- **Open from §8.9** (tune crop defaults, overlap policy, floor∈dedup): crop defaults tuned in Task 11; overlap/<50% handled in Task 2; floor frames flow through `phash_dedup` in Task 8 (floored list is the candidate set).
- **Placeholder:** Task 8 contains ONE intentional placeholder line flagged for deletion (the `probe_dimensions.__doc__` marker) — implementer must remove it; `dur` comes from `_probe_duration`.
- **Type consistency:** frame records carry `index,t,path,kind`; `detect_slides` returns `{"slides":[records],"flagged":[(t,t,d)]}`; `phash_dedup(records, *, crop_vf, drop_dist, flag_dist, hash_fn)`; `ahash(path, crop_vf)`; `build_crop_vf(w,h,cam_corner,caption,*,cam_frac,cap_frac)`; `download_video(...,fmt=)`; `extract_frames(...,native=)` — consistent across tasks.

## Out of scope (do NOT implement — spec §5/§8.10)
auto slide-region detection; OCR page-number verification; single-ffmpeg-pass frame-dump optimization; cross-deck dedup; cam-cropping the saved output.
