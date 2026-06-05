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

## Artifacts (local, not committed)

- `D:\Work\_cw-validation\harness\…\frames\` — 25 extracted white-deck slides (54 MB incl. 720p source)
- `D:\Work\_cw-validation\eunco\…\frames\` — 30 extracted color-deck slides (42 MB incl. source)
- v1 ground truth: `D:\SecondBrain\_assets\harness-engineering-2026-06-04\` (28 frames)
