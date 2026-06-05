# Dogfood: dHash white-deck recovery — end-to-end validation

**Date:** 2026-06-05
**Branch:** `feat/slides-mode`
**What this closes:** the last open item from the slides-mode hardening — confirming the
`aHash → dHash` change actually recovers the white-text-deck failure on a real video, not
just in unit tests.

## Why this run existed

`--slides` originally hashed candidate frames with an **8×8 average hash (aHash)**. On a
monochrome, white-background, black-text deck the per-cell averages are nearly identical
across genuinely different slides, so the dedup collapsed the deck:

- **aHash @ phash-dist 4 → 6 slides** kept (catastrophic)
- **aHash @ phash-dist 1 → 15 slides** kept (still merging ~half the distinct slides)

The fix swaps in a **9×8 difference hash (dHash)**, which keys on horizontal edges (text
strokes) instead of regional brightness. A hash-discrimination experiment on the 28
ground-truth harness frames had shown aHash-8 wrongly merges 18/27 adjacent distinct slides
vs dHash-8's 1/27. This run validates that on the live 38-minute video.

## Runs

Both ran zero-dependency (ffmpeg + Python stdlib), 720p, `--no-whisper`.

### Harness (white deck — the failure case)

```
python scripts/watch.py "https://youtu.be/5buNm0pA1mg" \
  --slides --cam-corner tr --caption bottom --no-whisper
```

| Metric | Old aHash | **New dHash** | v1 ground truth |
|---|---|---|---|
| Slides kept | 6 (dist 4) / 15 (dist 1) | **25** | 28 (incl. 2 manual sub-slide variants → 26 distinct) |
| Silent drops | many distinct slides merged away | **0** | — |
| Flagged for review (kept) | — | 3 (dist 9–10) | — |

`scenes_detected: 25 → slides_extracted: 25` — dHash dropped **nothing**; it only flagged 3
borderline pairs for human review (t=21:14~23:14 d10, 23:14~23:54 d9, 26:06~27:06 d10), all
kept. The 6/15 collapse is fully resolved.

**Coverage vs the 26 distinct ground-truth slides** (spot-checked by timestamp, ~40s
tolerance): ~21–22 transition points matched directly; dHash also separated a few points the
manual pass had merged (01:00 & 02:00, 11:54). **Genuine misses** — slide changes the scene
detector never registered (gradual / low-contrast transitions): ~03:00–03:45, ~15:44, ~30:44,
final 37:58. These are exactly the gaps the **Slide Coverage Ledger** records honestly; this
is the designed "high-recall, not exhaustive" behavior, not a silent loss.

### 은코치 (color deck — regression check)

```
python scripts/watch.py "https://youtu.be/kG-bqmrZY7E" \
  --slides --cam-corner br --caption none --no-whisper
```

| Metric | Old aHash | **New dHash** |
|---|---|---|
| Slides kept | 28 | **30** |
| Flagged for review (kept) | — | 3 (dist 5–9) |

Color decks were already well-separated under aHash; dHash gives **+2** (slightly higher
recall) with 3 borderline flags — **no over-capture balloon** (didn't jump to 50+). The
conservative dedup (drop near-identical, flag borderline, never silently drop) holds on both
deck types.

## Verdict

- ✅ **White-deck failure recovered:** 6/15 → **25** (of 26 distinct ground-truth slides).
- ✅ **No regression on color decks:** 28 → 30, no balloon.
- ✅ **No silent drops on either run** — only borderline pairs flagged, all kept.
- ⚠️ **Not exhaustive:** ~4–5 white-deck transitions with no scene-cut were not captured;
  these surface in the Slide Coverage Ledger as time-gaps for the reader. Consistent with the
  "high-recall, not exhaustive" contract — claims in docs/SKILL stay accurate.

The `aHash → dHash` change is now validated end-to-end on real footage. No further code change
required for this item.

## Follow-up: closing the residual missed-slide gap

The first pass reported "~4–5 white-deck transitions not captured." We probed each of the 6
ground-truth timestamps the default run missed by measuring the dHash distance of a frame at
that timestamp to the nearest *kept* frame (`D:\Work\_cw-validation\probe_missed.py`):

| missed GT t | dHash dist to nearest kept | reality |
|---|---|---|
| 03:00 | **0** (= kept 04:00) | same slide — already captured |
| 03:45 | **0** (= kept 04:00) | same slide |
| 18:44 | **1** (= kept 17:54) | build/sub-state, correctly merged |
| 30:44 | **1** (= kept 29:26) | build/sub-state, correctly merged |
| 15:44 | **5** (= kept 14:34) | lightly distinct |
| 37:58 | **21** | genuinely distinct — a real miss |

So **4 of 6 were never real misses** (dist 0–1 = the same slide we already had). Only two are
real content gaps, and — critically — **neither is recoverable by `--phash-dist`**: a sweep at
the default threshold gave dist4 = 25, dist2 = 26 (recovers an over-merged slide at 22:57,
1 flag, no balloon), **dist1 = 37** (over-capture, +12 flags) **yet still missed both**. They
are missing at the *candidate* stage, not the dedup stage.

**Two levers, by root cause:**

1. **Mid-video lightly-changed slide (15:44)** → lower `--scene-threshold`. Re-run at
   `--scene-threshold 0.15 --phash-dist 2` → **31 slides, 4 flags**, recovers the 15:44 region;
   the edge-hash dedup keeps the extra candidates from ballooning. (Recommended deck-specific
   setting; **not** promoted to the global default.)
2. **Final slide (37:58)** → *not* a threshold problem (still missed at 0.15, last frame 36:47).
   It is a structural **end-of-video coverage gap**: `apply_coverage_floor` (`scripts/scenes.py`)
   steps floors by `max_gap` from the last scene with `while t < duration`, leaving the final
   ≤`max_gap` (20s) tail uncovered. A slide in that tail is never a candidate under any tuning.

**Fix (slides-only opt-in tail anchor).** `apply_coverage_floor(..., include_tail_anchor=True)`
guarantees one floor at `duration − 0.5s` (an extractable point, not EOF), skipped if an existing
boundary is already within `tail_eps`. Slides mode opts in; **classic mode keeps the upstream
default (`False`)**, so classic notes are byte-identical. Covered by 5 unit tests in
`tests/test_scenes.py` (tail covered when opted in; skipped near an existing end scene/floor;
default-False leaves the short tail uncovered).

**Verified.** Default-flag harness re-run after the fix: **25 → 26 slides**, new final frame
`0117_t38-01.jpg` at **t=38:01** (the previously-missed 37:58 slide, dist 21 from 37:06 = kept),
**same 3 review flags, zero added noise** — the anchor added exactly one frame: the real final
slide. The "even the last slide" gap is now closed.

**Hardening (multi-lens review follow-up).** A four-lens review (code / Python / test / silent-failure)
surfaced edge cases now guarded: an explicit `anchor_t > 0` check (never emit a negative seek on a
video shorter than `tail_eps` or with an unprobed duration), a `warnings.warn` in `_probe_duration`
when ffprobe reports no duration (so the anchor degrading to a no-op is *visible*, not silent), and
four added unit tests pinning the byte-identical classic-mode default, the strict `>` guard boundary,
the no-negative-timestamp invariant, and the empty-input contract. **Known limitation (accepted):** the
anchor seeks to `duration − 0.5s`; on a pathological encoding whose container duration overruns the last
decodable frame, ffmpeg could emit a near-EOF black frame (exit 0). The perceptual-hash dedup is a partial
backstop and the real-footage run produced a valid frame, so this is documented rather than guarded with a
luma check (which would risk false-positives on legitimately dark slides). Candidate follow-up if it ever
surfaces in the field.

## Artifacts (local, not committed)

- `D:\Work\_cw-validation\harness\…\frames\` — 25 extracted white-deck slides (54 MB incl. 720p source)
- `D:\Work\_cw-validation\eunco\…\frames\` — 30 extracted color-deck slides (42 MB incl. source)
- v1 ground truth: `D:\SecondBrain\_assets\harness-engineering-2026-06-04\` (28 frames)
