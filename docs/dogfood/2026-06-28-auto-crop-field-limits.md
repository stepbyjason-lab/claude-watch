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

Companion to [`2026-06-27-demo-heavy-seminar-coverage.md`](2026-06-27-demo-heavy-seminar-coverage.md)
(notes-coverage on recording A).
