# `--slides` Mode — Implementation Log (2026-06-04)

Complete record of the `--slides` feature: design → multi-lens review → 3-round plan hardening → implementation → multi-lens verification → review fixes → cross-platform fix.

- **Fork:** `github.com/stepbyjason-lab/claude-watch` (upstream: `devinilabs/claude-watch`)
- **Branch:** `feat/slides-mode`
- **Companion docs:** [`docs/specs/2026-06-04-slides-mode-design.md`](specs/2026-06-04-slides-mode-design.md) (design v2, §8 authoritative) · [`docs/plans/2026-06-04-slides-mode.md`](plans/2026-06-04-slides-mode.md) (11 TDD tasks)

---

## 1. What shipped

A `--slides` mode that captures **every unique slide** of a lecture/seminar video, legibly, without silently dropping distinct slides.

```bash
python3 scripts/watch.py "<url>" --slides
python3 scripts/watch.py "<url>" --slides --cam-corner tl --caption none --hi-res
```

Pipeline (slides mode only; the classic detection pipeline is unchanged — though `watch.py` shared code did gain the UTF-8 stdout reconfigure and the `list(glob)` cache fix below, which also benefit classic mode):

1. **Download 720p** (`--hi-res` → 1080p) — separate yt-dlp format selector.
2. **Crop** out presenter cam + caption band → **scene-detect on the slide region** at low threshold (0.10).
3. **Tight coverage floor** (20s) to catch light→light slide changes.
4. **Extract** every candidate once at native 720p (1 decode + N keyframe seeks).
5. **Perceptual-hash dedup** (zero-dependency ffmpeg 8×8 gray average-hash), **conservative**: drop only near-identical, **flag** (not drop) borderline pairs.
6. **Manifest + stdout** emit `slides_extracted: N` and `review: near-dup …` lines.

### Design principles (spec §8)

| Principle | Decision |
|---|---|
| Completeness over dedup (§8.0) | Over-capture, never silent-miss. Borderline pairs are kept + flagged for human review. Success = "0 slides missed, a few duplicates allowed". |
| Cache identity (§8.1) | Default mode keeps **upstream hash** (`sha1(source\|focus)`) — existing caches preserved. Slides mode folds the **full detection profile** into the slug → any flag change busts the cache. No `slides.json` cache file. |
| Minimal upstream diff (§8.2) | No `kind="slide"` on `Scene`. One `prefilter=""` kwarg on `detect_scenes`. Extraction profile via `extract_frames` args. |
| Pass minimization (§8.3) | v1 = 1 full decode + N seeks (+ N hash spawns, see §5). Single-pass frame-dump deferred. |
| Zero new dependency (§8.4) | Perceptual hash via ffmpeg `scale=8:8,format=gray` → 64 bytes → 64-bit average hash. No Pillow/imagehash. |
| Security (§8.5) | `urlparse`-based scheme allowlist, argparse `choices=` + `ValueError` validation, threshold range check, candidate cap, `-protocol_whitelist file`, enum download format, `--no-playlist`. |
| Native extraction (§8.6) | `extract_frames(native=True)` → no downscale, 1:1 from 720p source. |
| Integration seam (§8.7) | `select_scenes()` strategy seam keeps `main()` linear. SKILL.md slides guidance. |

---

## 2. Process timeline

| Phase | Who | Outcome |
|---|---|---|
| Brainstorming + design | Claude | Design v2; fork created; spec written |
| Pre-implementation multi-lens review | Claude (architect / devils-advocate / security / performance agents) | CRITICAL cache-identity gap found; "0 misses" reframed to high-recall+flag; single-pass + minimal-diff hardening |
| Plan written | Claude (writing-plans) | 11 TDD tasks |
| **Plan review — round 1** | external (eng/design/ceo lenses) | 4 blockers + 3 warnings → all patched |
| **Plan review — round 2** | external | 2 remaining (spec §8.1, `_scheme_ok` hole) → patched |
| **Plan review — round 3** | external | **PASS** |
| Implementation | Codex (against the hardened plan) | Tasks 1–9 committed |
| **Implementation verification — multi-lens** | Claude (security / python / code-compliance / performance agents) | Task 10 incomplete + several fixes |
| Review fixes + finish Task 10/11 | Claude | committed |
| Cross-platform fix (cp949) | Claude | committed |

---

## 3. Commit stack (`feat/slides-mode`)

```
6d382e3  fix(watch): force UTF-8 stdout/stderr so cp949 consoles don't crash
ebc6a1e  feat(slides): finish docs (Task 10/11) + multi-lens review hardening
b2d652c  feat(watch): wire slides mode into watch pipeline            (Task 9)
beced7e  feat(slides): detect slides orchestrator                     (Task 8)
a416d0e  test(watch): decode e2e subprocess output as utf-8
cade634  feat(core): add slides download cache and native frame seams (Task 5-7)
cabb308  feat(slides): add crop hash and conservative dedup helpers   (Task 1-4)
18132d1  docs: harden slides plan/spec v2 (full cache profile, scheme regex, §8.1 sync)
8f7e27a  docs: add --slides mode design spec + implementation plan
```

---

## 4. Plan review findings (pre-implementation) and resolutions

Verified against the live files, then patched in `18132d1`.

### Round 1 — 4 blockers + 3 warnings (all confirmed valid, all fixed)

| ID | Finding | Resolution |
|---|---|---|
| B1 | Cache key folded only `mode`+`resolution`, not the crop/threshold/phash flags (spec §8.1 promised flag-change cache-bust) | `slug_for` slides branch hashes full `slides_profile`; default branch keeps upstream `sha1(source\|focus)` so existing caches aren't invalidated |
| B2 | Slides manifest path would point at `0001…jpg` while files live at `frames/0001…jpg` | `detect_slides` returns basenames; `watch.py` prepends `frames/` for both modes |
| B3 | Task 9 `select_scenes` was `…` + prose — not executable | Wrote both branches as complete copy-pasteable code (classic = upstream verbatim) |
| B4 | Task 8 contained an intentional placeholder line | Removed; duration comes from `_probe_duration` |
| W1 | Spec §8.3 promised single-pass; plan does N seeks | Spec §8.3 reconciled: v1 = 1 decode + N seeks; single-dump deferred |
| W2 | `_scheme_ok` used a narrow prefix blocklist | Replaced with `urlparse`-based allowlist |
| W3 | `--slides` + `--start/--end` behavior undefined | v1 forbids the combination (`_validate_slides_focus`) |

### Round 2 — 2 remaining (both confirmed, both fixed)

| Finding | Resolution |
|---|---|
| Spec §8.1 still described old design (`mode+resolution`, separate `slides.json`) | Spec §8.1 rewritten to match plan (full profile in slug, no `slides.json`, upstream hash preserved for default) |
| `_scheme_ok` `len(scheme)==1` allowed `x://evil`, `z://host/file` | Replaced with drive-path regex `^[A-Za-z]:(?:\\|/(?!/))`; `x://` (a `//` authority) is rejected. Verified 15 cases (`x://evil`, `z://host`, `file://`, `ftp://`, `rtmp://`, `data:`, `smb://`, `javascript:` rejected; http(s) / POSIX / Windows-drive / relative accepted). |

### Round 3 — PASS.

---

## 5. Implementation verification findings (multi-lens, on the code) and resolutions

Four review lenses run on the committed implementation. **No CRITICAL, no exploitable security hole.**

### Fixed in `ebc6a1e`

| Lens | Finding | Resolution |
|---|---|---|
| code-compliance | **Task 10 `SKILL.md` slides guidance not added** (only README done) | Added SKILL.md slides-mode section + flag docs + "one slide = one section / read `review:` lines" guidance |
| python | `phash_dedup` did not guard `flag_dist <= drop_dist` → borderline frames could be silent-dropped (breaks the high-recall promise) | Raises `ValueError` if `flag_dist <= drop_dist`; added test |
| python / security | `build_crop_vf` validated with `assert` (stripped under `python -O`) | Changed to `raise ValueError`; updated tests to `pytest.raises(ValueError)` |
| python | `cached and src_dir.glob("video.*")` — generator is always truthy → `StopIteration` if cache dir is empty (pre-existing upstream) | `cached_videos = list(...); if cached and cached_videos:` |
| performance | `ahash` spawns one ffmpeg per candidate (actual cost = 1 decode + N seeks + **N hash spawns**), not stated in §8.3 | Documented as a known v1 deviation in spec §8.3; batch-hash deferred |
| python | `hash_fn` untyped; docstring said "consecutive" (actually compares to last *kept*) | Added `Callable` type hint; clarified docstring |

### Fixed in `6d382e3` (cross-platform)

| Finding | Resolution |
|---|---|
| `watch.py` prints an en-dash (focus line) and em-dash (final line); on a legacy Windows codepage (**cp949**, Korean) these raise `UnicodeEncodeError` and crash the run *after* frames/transcript are written. Affects **both** classic and slides modes (pre-existing). Surfaced by the e2e tests (only green with `PYTHONUTF8=1`). | `main()` reconfigures `sys.stdout`/`sys.stderr` to UTF-8 (guarded). The e2e suite now passes **without** `PYTHONUTF8`. |

### Post-review polish (this commit)

| Finding | Resolution |
|---|---|
| `select_scenes()` had a dead containment check: `frames_dir` is structurally `LIBRARY_ROOT/slug/frames`, and the `relative_to()` result was discarded. | Removed the no-op line. |
| `args.phash_dist + 6` was duplicated in the detector call and the cache-profile slug. | Centralized it through `SLIDES_FLAG_DIST_OFFSET` + `_slides_flag_dist()` to prevent hash-vs-behavior drift. |
| Both branches duplicated the `frames/` manifest-path prefix transform. | Centralized it in `_prefix_frame_paths()`. |
| `hash_fn` accepted `Path` in the type hint, while `detect_slides()` passes absolute paths as strings. | Updated `ahash` and `hash_fn` typing to `str \| Path`. |

### Acknowledged, not fixed (single-user CLI / out of scope / deferred)

- **Single-ffmpeg-pass frame-dump** (0 seeks) — deferred (spec §8.3 ⏸).
- **`ahash` average-hash collides solid-black vs solid-white** — known naive-aHash limitation; irrelevant for real slides.
- **Two `ffprobe` spawns** (`probe_dimensions` + `_probe_duration`) could be one — micro-opt.
- **SSRF via yt-dlp HTTP redirects** to RFC1918/loopback — within the single-user threat model; only matters if ever wrapped server-side.

---

## 6. Test status

- **87 tests pass** (verified after post-review polish) with `pytest -q -m "not network"`, in a default (cp949) shell, **without** `PYTHONUTF8`.
- Network regression (`tests/test_slides_regression.py`, harness deck `5buNm0pA1mg`, expect ~24–40 unique slides) is **gated** behind `@pytest.mark.network` + `CW_RUN_NETWORK=1` so routine runs aren't broken.
- New/updated tests: crop builder + `ValueError`, hamming, dedup (+ `flag_dist<=drop_dist` guard), format selector, slug (default=upstream hash / slides=full profile / per-flag bust), native extract, scheme allowlist (incl. `x://` rejection), focus×slides conflict, `detect_slides` integration + candidate-cap.

> Note: run pytest from the canonical-case path `D:\Code\_tools\claude-watch` (capital `C`). Running from lowercase `d:\code\…` makes one `test_resolve` assertion fail on a path-casing mismatch (Windows path-casing artifact, not a bug).

---

## 7. File-by-file change summary

| File | Change |
|---|---|
| `scripts/scenes.py` | `detect_scenes(prefilter="")` kwarg + `_build_scene_vf` helper; `-protocol_whitelist file` |
| `scripts/slides.py` | **new** — `build_crop_vf`, `ahash`/`hamming`, `phash_dedup`, `probe_dimensions`, `_probe_duration`, `detect_slides`, `CandidateCapExceeded`; `hash_fn` accepts `str \| Path` |
| `scripts/download.py` | `format_selector` enum (720p/1080p/best, default byte-identical); `download_video(fmt=)`; `--no-playlist` preserved |
| `scripts/library.py` | `slug_for` — default=upstream hash, slides=full profile |
| `scripts/frames.py` | `extract_frames(native=)`; `-protocol_whitelist file` |
| `scripts/watch.py` | `_scheme_ok` (drive-path regex), `_validate_slides_args`, `_validate_slides_focus`, `_wipe_frames_dir`, `_slides_flag_dist`, `_prefix_frame_paths`, `select_scenes` seam, `--slides/--cam-corner/--caption/--hi-res/--phash-dist` flags, `meta['slides_profile']`, download fmt branch, manifest `slides_extracted` + `review:` lines, UTF-8 stdout reconfigure |
| `SKILL.md` | Slides-mode section + flag docs + output-reading guidance |
| `README.md` | Fork note + `--slides` usage example |
| `tests/test_*.py` | Unit + integration coverage for all of the above |
| `tests/test_slides_regression.py` | **new** — network-gated harness regression |
| `docs/specs/…`, `docs/plans/…` | Design v2 + hardened plan |

---

## 8. Status & next steps

**Feature complete.** 11 tasks closed, multi-lens reviewed (plan + implementation), cross-platform verified. Branch `feat/slides-mode` is local — **not pushed**.

Open options:
1. Push to fork origin (`git push -u origin feat/slides-mode`).
2. Open a PR to upstream `devinilabs/claude-watch`.
3. Address the deferred items (single-pass dump, batch-hash, containment guard cleanup) in a follow-up.

MemKraft entity: `claude-watch-slides-fork`.
