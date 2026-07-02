# Dogfood: `--crop auto` field limits on real Zoom recordings (2026-06-28)

Honest negative result for the R04 `--crop auto` heuristic, recorded so the limit
is documented rather than rediscovered. `--crop auto` (motion-based edge-band
trimming) was implemented with synthetic-test coverage and a fail-open fallback;
this is what happened when it met two real seminar recordings.

## Subjects (both 2560×1600 screen recordings)

- **A — demo-heavy seminar** ("LLM Wiki…AI 에이전트 팀원 만들기", 155 min): a prepared
  deck interleaved with long live IDE/Notion/browser demos.
- **B — Zoom gallery forum** ("Beyond LLM Wiki Forum 2026", 217 min): a Zoom layout
  with a **top cam-gallery bar (several talking-head cams)**, a **right-side chat
  panel**, a bottom toolbar, and a **slide deck that changes continuously** in the
  centre.

## Result: `detect_slide_crop` returned `None` on both → safe fallback

Neither produced a crop; both correctly fell back to `--cam-corner/--caption`
(no wrong crop emitted). The interesting part is *why* — it exposes the heuristic's
operating envelope.

## Why motion alone fails here

The heuristic assumes **moving chrome (cam/chat/toolbar) around a static slide**.
Recording B inverts that. To understand the signal we examined per-pixel temporal
motion maps at two sampling scales (this is *diagnostic* reasoning — the shipped
`detect_slide_crop` runs a single 24-frame pass across the whole video, not a 60 s /
12 s analysis):

- At a **60 s scale** (several slides): the **centre slide text is the highest-motion
  region** (it keeps changing); the edges (cams, chat) are comparatively still. So
  "trim high-motion edges" has no high-motion edge to trim → full frame → `None`.
- At a **12 s scale** (between slide changes): only the **cams** move, but they are
  small *point* clusters (a few faces), so no row/column crosses the 12% hot-band
  threshold → nothing trimmed → `None`.

So the motion pattern flips with timescale, and neither scale gives the "static
slide vs moving border" contrast the trim relies on.

## Why edge + motion also fails (R05 prototype)

The slide region is obvious to a human eye scanning a motion map, so we prototyped
six separable-signal candidates. None worked; none shipped (prototype only):

| Signal | Outcome on recording B |
|---|---|
| motion edge-band trim | no high-motion edge → `None` |
| motion burst (0.5 s) | cams are point clusters, sub-threshold → trim 0 |
| motion active-content bbox | bbox includes the top cams |
| edge density alone | every region has edges (slide text, cam outlines, chat text, toolbar icons) |
| **edge × motion product** | suppresses chat / toolbar / background well, **but cams remain** |
| edge × motion row/col profile | **cam row ≈ 35% vs slide row ≈ 40%** — not separable |

The blocker is the **talking-head cam**: a moving face has both motion *and* contour
edges, so its `edge × motion` signal is on par with slide text. No zero-dependency
threshold separates them. Pulling the cam out would need face detection / skin-tone
(an external dependency we don't take) or a Zoom-layout assumption (a fixed top
cam-bar trim that wouldn't generalize — corner cams are already handled by
`--cam-corner`).

## Takeaway

- `--crop auto` is **best-effort**. It helps when the moving chrome forms a clear
  high-motion band and the slide is comparatively static; it **falls back (safely)
  on dynamic-slide + talking-head Zoom layouts**, where motion/edge signals can't
  separate cam from content.
- For those recordings, **set `--crop W:H:X:Y` from one frame** (by hand, or by a
  vision pass — see below). It stays the precise option; the auto path never emits a
  wrong crop, it declines.
- **Field record: 2/2 correct dispositions** — both real recordings legitimately fell
  back (0 emitted crops, 0 wrong crops). There is **no real-world success case yet**,
  but both samples were the *hardest* layout (dynamic slides + talking-head cam); a
  recording where this succeeds — a large, actively-scrolling chrome band beside a
  static slide — wasn't in the sample set. Synthetic tests
  ([`tests/test_slides.py`](../../tests/test_slides.py), the `auto-crop` block) cover the
  working-envelope geometry.

## The real fix is a different layer (vision, not pixels)

The slide region is **obvious to a human — or a vision model — glancing at one frame**;
that is exactly how the manual `--crop` workflow already works ("measure the slide
rectangle from one extracted frame"). Sibling projects corroborate the layer choice:
`claude-video-vision` analyzes structure with ffmpeg filters and lets the model plan,
and **neither it nor the upstream `claude-video` attempts a pixel-heuristic spatial
crop** — auto-localization is left to the model. So the durable path for an ambiguous
crop is **LLM-assisted**: extract one frame, look at it, read off the slide rectangle,
re-run with explicit `--crop`. Pixel-motion can't separate a talking-head cam from
slide text; a vision pass on a single frame can. See the `--crop auto` notes in
SKILL.md for the step-by-step.

### Verified end to end on recording B — with two corrections from follow-up checks

This was run on the recording that defeated `--crop auto`, and the LLM-assisted
**workflow** (read one frame, measure the slide rectangle, re-run with `--crop
W:H:X:Y`) is confirmed valid and reproducible. Two follow-up verification passes,
though, found the first write-up of this result was imprecise on two separate
points. Both are corrected below rather than quietly re-asserted.

**Correction 1 — the crop coordinate itself had a small error.** The original
measurement extracted one frame (a 1280-wide grid overlay, each cell = 256 px of
source) and read the slide rectangle off the grid **by eye**: cam-gallery bar above
y≈256, chat panel right of x≈2048, toolbar below y≈1408 → `--crop 2048:1152:0:256`
(source is 2560×1600). That crop worked well enough to run, but it slightly
**over-includes chrome**: the top edge cuts ~50px into the cam-gallery bar, and the
right edge (x=2048) crosses ~40px past the real chat-panel boundary (the chat panel
actually starts at source x≈2008). A blind re-measurement by a Sonnet agent, using
per-pixel color-transition sampling instead of grid eyeballing, produced
**`--crop 2000:1122:0:308`**, which cleanly excludes the cam-bar, chat, and toolbar
(self-verified at 3 timepoints). Takeaway: **pixel-boundary sampling beats grid
eyeballing** for this measurement step, and the precise crop for recording B is
`~2000:1122:0:308`, not the earlier rounded `2048:1152:0:256`.

**Correction 2 — `slides_extracted: 15` is a raw extraction count, not the true
unique-slide count, and it was conflating two separate problems.** Running
`watch.py --slides --crop 2048:1152:0:256` on a 12-min slide-heavy clip reported
**`slides_extracted: 15`**. That number is close to correct by coincidence but is
the **wrong set**: dense 3-second-interval sampling (240 frames) across the same
clip established the true unique prepared-slide count is **~13-14**, and the
`--hold 6` run that produced 15 actually **missed 4 short slides** (each shown only
3-6s, at or under the hold threshold — "LLM Wiki 3-Layer" diagram, "Cross-Vault
마이그레이션", "~100 소스·40만 단어", "퀴리가 수집처럼 누적된다") while
**double-counting** some sub-slide states (cursor/bullet/build animations treated as
separate slides). Lowering `--hold` does not fix this — it overshoots the other
way: `--hold 3` → 22 slides, `--hold 2 --phash-dist 2` → 24 slides (animation
near-duplicates logged as separate slides each time). The root cause is that this
deck mixes very-short (3-6s) and very-long (~2min) slides, and **no single `--hold`
threshold handles both**.

**These are two separate problems, and the original write-up mushed them
together.** (a) LLM-assisted crop solves the **crop-geometry** (spatial) problem —
that part is still valid, now with a corrected coordinate. (b) The `--hold`
freeze-recall gap is a **separate, unsolved, temporal** extraction problem — an R01
`--detect freeze` flag limitation, not something the crop fix touches. The crop
being right does not mean recall is fine; treat them as independent follow-ups.

**Positive note: Sonnet is sufficient for the crop-measurement step.** The blind
re-measurement above was done by a Sonnet agent and was *more* precise than the
original (Opus) eyeball measurement. The LLM-assisted crop step can be delegated to
a Sonnet subagent — cheaper, and at least as accurate here.

So the same recording that returned `None` under `--crop auto` (motion heuristic)
yields cleanly-cropped slides under an LLM-measured `--crop` — that part of the
field record stands: **auto path 0/2 (correct safe declines); LLM-assisted crop
path 1/1 (recording B verified, coordinate since corrected)**. What's not yet
solved is freeze-hold recall on mixed-length decks — call that a known open
follow-up, not a solved problem.

### 2026-07-02 follow-up: the `--probe-frame` loop, validated end to end on recording B

The "get one frame" step above originally required running the full `--slides`
pipeline just to obtain a frame to measure from. R07 turned that step into a flag:
`--slides --probe-frame` downloads the source, extracts *one* native-resolution frame
(default 25% in, `--probe-at` to override), prints the frame path +
`source_resolution: WxH`, and exits — no detect, no extract, no transcribe. The full
loop was then re-run on recording B's 12-min clip (2560×1600):

1. **Probe:** `--slides --probe-frame` → one 2560×1600 frame at t=03:00.
2. **Blind measurement** (a Sonnet subagent, pixel-boundary sampling via ffmpeg
   grayscale strips + row/column mean transitions, *without* being shown the earlier
   manual value): **`--crop 2006:1130:0:306`** — top y=306 (row mean 21→254), right:
   white background holds through x=2007 then drops to ~21 (even-rounding the width
   lands at 2006), bottom y=1436 (254→21), left flush x=0. Self-verified clean at
   three timepoints (03:00, 60s, 600s).
3. **Agreement:** every edge within **≤6 px** of the earlier manual measurement
   (`2000:1122:0:308`; per-edge deltas 0/2/6/6) — two independent measurements of
   the same layout converge; the workflow is reproducible, not a one-off.
4. **Equivalence — checked at the frame level, and the honest answer is "mostly".**
   The full run `--slides --crop 2006:1130:0:306 --hold 3` (with R06's time-aware
   merge defaults, `--merge-gap 15 --merge-dist 11`) extracted **19 frames, the same
   count** as the R06 run with the manual crop. Note this 19 was measured against the
   corrected crop — not comparable to the `--hold 3` → 22 figure in Correction 2 above, which
   used the old imprecise `2048:1152:0:256` crop *without* the merge pass. But the
   count equality is partly coincidence. Comparing the two 19-frame manifests
   timestamp-by-timestamp: **17 of the 19 timestamps are identical across both
   runs**, one more pair matches modulo a 0.5 s detection-start jitter (t=289.1 vs
   289.6 — same held screen), and **the final slot differs: each run kept one frame
   the other merged away** — two borderline merge decisions flipped in opposite
   directions. The reconciling arithmetic: each run entered the merge pass with
   **22 post-dedup survivors and folded 3 of them** (probe run: 19 kept + the
   3-entry merge inventory below = 22, derivable from this section alone; manual
   run: 22 → 19 with 3 merges is the R06 round record). The probe run's complete
   merge inventory is `t=04:23 ~ 04:38 (dist 9, gap 14.6s)`, `t=04:58 ~ 05:03 (dist
   11, gap 5.1s)`, `t=07:45 ~ 07:50 (dist 11, gap 4.9s)`. Which specific pairs the
   manual run folded is partly sourced, partly inferred: its dist-9 pair is recorded
   in the R06 contract's spike diagnostics (the same pair at gap 14 s, dist 9 under
   the manual crop); the other two folds are inferred from its kept set (it kept
   t=07:50 and neither demo scroll state), not quoted from a preserved merge log —
   both folds land inside the 04:58–05:11 demo stretch. So the manual run folded
   both demo scroll states and kept page 16, while the probe run kept one demo
   scroll state and folded page 16. Net: exactly one kept-frame swap. Reading the four frames involved
   (not just the merge log) shows **each run false-merged one genuinely distinct
   screen the other run kept**:
   - The **probe-crop run lost a prepared slide**: it merged t=07:50 (deck page 16,
     "qmd 5 검색법") into t=07:45 (page 15, "Cross-Vault 비대칭") — `merged: t=07:45
     ~ t=07:50 (dist 11, gap 4.9s)`, *exactly at* the `--merge-dist 11` threshold.
     Two sparse white slides with near-identical card layouts hashed within 11 under
     this crop; under the manual crop the same pair was ≥12 and both were kept.
   - The **manual-crop run lost a distinct held demo state**: it merged the Obsidian
     qmd-note view (~t=05:11) into the Settings-dialog screen at t=04:58 — different
     demo content a notes writer would cite separately. The probe-crop run kept both.
   So **the merge pair-set is not identical, and neither run's set strictly contains
   the other** — a ~6 px crop change nudges hash distances at threshold-adjacent
   pairs across the line, in both directions. This is the `--merge-dist` margin
   fragility R06 shipped with as a known trade-off (`dist 11` sits above the flag
   threshold; the code's own docstring warns a genuinely distinct slide can be folded away),
   now observed in the field: on this deck, dist 11 contains *both* genuine
   build/scroll steps *and* one real slide transition, so no threshold separates
   them. (A related subtlety the frame reads surfaced: the merge pass compares each
   frame only to the *last kept* one, so a frame can fold into a temporally-adjacent
   but different-content parent. The probe run's *other*, unflipped merge in the same
   demo stretch — `merged: t=04:58 ~ t=05:03 (dist 11, gap 5.1s)` — folded a qmd-page
   scroll state into the Settings-dialog screen; the page itself stayed covered only
   because its next scroll state at t=05:11 independently survived.)
   Practical readings: (a) the probe-measured crop is a working replacement for the
   hand-measured one *for crop geometry* — neither measurement was wrong; but note
   the crop is not causally innocent in the merge outcome either: *which* borderline
   pairs land exactly on the threshold is crop-dependent (the ~6 px delta is what
   moved the page-15/16 pair from ≥12 to 11), so any two correct crops can fold
   different borderline pairs; (b) after any crop change, **read the `merged:` lines
   and spot-check every pair sitting exactly at the `--merge-dist` threshold** —
   that one-line audit is precisely what caught the lost slide here; (c) for sparse
   decks with repeating card layouts, consider `--merge-dist 10`: against the probe
   run's complete inventory above, the two dist-11 folds un-merge (page 16 kept, plus
   the t=05:03 scroll state surfacing as one extra near-dup for the notes step) and
   the dist-9 fold still merges → 21 frames — a cheap trade against losing a
   prepared slide.

One layout note surfaced by the measurement pass: this recording carries a
**picture-in-picture active-speaker box baked into the shared-screen stream itself**
(top-right, roughly x 1594–1990 / y 320–544 in source pixels — *inside* the slide
region). A rectangular crop cannot remove it without cutting slide content; both the
manual and the probe-measured crop include it, and freeze detection tolerated it on
this recording (the held-slide signal dominated at this PiP size and the default
`-50dB` noise threshold — a larger or busier PiP could plausibly eat into that
margin). Treat it as slide-content noise for the notes step, not as croppable chrome.

Companion to [`2026-06-27-demo-heavy-seminar-coverage.md`](2026-06-27-demo-heavy-seminar-coverage.md)
(notes-coverage on recording A).
