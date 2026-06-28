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

Companion to [`2026-06-27-demo-heavy-seminar-coverage.md`](2026-06-27-demo-heavy-seminar-coverage.md)
(notes-coverage on recording A).
