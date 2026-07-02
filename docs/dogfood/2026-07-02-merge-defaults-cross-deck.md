# Dogfood: time-aware merge defaults across deck styles (2026-07-02)

Honest field record for the R06–R09 time-aware merge arc. Everything in that arc
(`--merge-gap 15 --merge-dist 11`, the exact-threshold flagging of R08, the audit-line
filter of R09) was tuned and validated on **one** recording — the "Beyond LLM Wiki
Forum" Zoom clip. The R06 contract flagged this explicitly: "기본값 15/11은 단일 forum
클립 튜닝값 — 다른 발표 스타일에서 오작동 가능." This round tests that on three decks of
deliberately different style. The headline: **the merge default silently destroys real
slides on sparse-text, uniform-template decks — a common style — and R08's
exact-threshold safety net does not catch it.**

## Subjects (all local Korean free-lecture recordings)

| # | Style | Source | Window tested |
|---|---|---|---|
| A | **Dark minimalist deck** — black bg, centered white title, tiny gray subtitle, small top-right cam, page numbers, player chrome (1920×1080) | 열끈마케팅 마케팅 강의, 45 min | 12-min slice (15:00–27:00) |
| B | **Content-dense mixed** — infographic PPT slides + live browser/tool demos + notepad typing, bottom-right cam (1920×1080) | 알파남 AI 블로그 웹비나, 2h31m | 12-min slice (~1:09–1:21) |
| C | **Vertical short-form reel** — meme image + burned-in captions, fast flips (720×1280) | 자동화 GPT 릴, 37 s | full |

Slices were physically cut with ffmpeg (`-c copy`), because `--slides` cannot be
combined with `--start/--end` in v1. All runs used `--hold 3` and the default merge
(`--merge-gap 15 --merge-dist 11`), matching the forum-clip baseline. Crop was
per-deck: A used the default `--cam-corner tr --caption bottom` (no explicit crop
needed — freeze captured the slides cleanly, so the finding below is purely about the
merge pass, not crop quality); B used `--cam-corner br --caption none`; C used
`--cam-corner none --caption none`.

## Result A — dark minimalist deck: **10 / 10 merges were FALSE (≈29% of slides destroyed)**

`slides_extracted: 25`, 3 near-dup review lines, **10 `merged:` lines, 0
`review: merge-threshold` lines.** The 25 kept frames were read as a montage and are
all mutually distinct titled slides (no duplicates among them). And every one of the
10 merges folded away a **genuinely distinct slide**, confirmed by extracting each
merged-away frame from the source and reading it against its anchor. Where the
presenter's page number was legible (faint, bottom-centre — 6 of the 10 pairs) it
gives objective proof of a page jump; the other 4 rest on plainly different on-screen
titles:

| merged pair | dist / gap | anchor page → dropped page | verdict |
|---|---|---|---|
| 00:03 ~ 00:17 | 9 / 14.3s | p36 "무엇을 믿고 사야 할까요?" → **p37** "물론 이런 방식이 먹히는 상품도" | distinct — lost |
| 03:50 ~ 03:59 | 10 / 9.3s | p47 "그렇게 하면 안 됩니다" → **p48** "블로그도 마찬가지입니다" | distinct — lost |
| 04:21 ~ 04:35 | **5** / 13.8s | p49 "상위노출은 됐습니다" → **p50** "왜 이 업체에게 구매해야 할까요?" | distinct — lost |
| 04:58 ~ 05:03 | 8 / 5.4s | p52 "콘텐츠입니다" → **p53** "구조가 이렇게 바뀌어야" (flow) | distinct — lost |
| 05:17 ~ 05:26 | 8 / 8.9s | "광고는 사람을 데려옵니다" → "콘텐츠의 역할은 고객의 문제를 해결" | distinct — lost |
| 07:43 ~ 07:50 | 10 / 6.6s | p60 "실제 변화까지 보여줍니다" → **p61** "콘텐츠가 판매 확률을 올립니다" (diagram) | distinct — lost |
| 09:01 ~ 09:10 | 8 / 8.8s | "긁어줘야 합니다" → "그런 사람들이 모여야 콘텐츠로 설득" | distinct — lost |
| 09:01 ~ 09:16 | 9 / 14.5s | p64 "긁어줘야 합니다" → **p66** "이 차이를 이해해야" (contrast) | distinct — lost |
| 11:12 ~ 11:16 | 8 / **3.6s** | "상품 판매는 일어납니다" → "문제는 고가 상품입니다" | distinct — lost |
| 11:46 ~ 11:55 | 9 / 9.3s | "한 번에 설득하려고 하면 어렵습니다" → "고객이 부담 없이 한 단계씩" | distinct — lost |

The 25 kept slides are all distinct (montage-verified) and the 10 merged-away frames
are 10 further distinct slides, so **the merge pass alone silently deleted 10 real
slides from a ~35-slide window (≈29%).** (~35 counts only what this run surfaced;
`phash_dedup` may have dropped further frames upstream, so it is a *floor* on the deck's
true size — but 10 merge-deleted slides is a hard count regardless of the denominator.)
The two `09:01` rows above are the **same anchor with two different frames folded into
it** — a dropped frame never becomes the anchor, so a held screen can absorb several
neighbours. Even the shortest-gap merge (3.6s) and the lowest-distance merge (dist 5)
were false; not a single legitimate build-step appeared in this window.

### Why every merge is false here — and why no threshold fixes it

The deck's template is near-identical across slides: black background, a small green
corner square, one centered white title line, one faint gray subtitle, a small cam in
the corner. Two **completely different** slides therefore differ only in the title text
— a small fraction of the frame's pixels — so their **dhash (edge-difference) distance
is just 5–10**, comfortably inside the merge band (`< 11`). The merge's core
assumption, *"close hash ⇒ same screen's build-step,"* is simply false for this deck
class.

Crucially, **no single dhash threshold separates the two cases**, and the proof is a
head-to-head across decks: a **dist-5** merge here in Deck A is *false* (distinct slide
p49 → p50), while a **dist-5** merge in Deck B below is a *correct* build-step. One
threshold cannot both accept B's dist-5 and reject A's dist-5. So lowering `--merge-dist`
can't fix it, and raising it makes the forum clip's real build-steps stop merging — the
discriminating signal (the title line) is too small a pixel fraction for a whole-frame
structural hash to weigh. And note this failure mode is invisible to R08's safety net
*by construction*: merge fires at `dist < 11` while the exact-threshold flag fires only
at `dist == 11`, so a sub-threshold over-merge (dist 5–10) can never be flagged. Zero of
these 10 were flagged — and none could have been.

## Result B — content-dense mixed deck: **2 / 2 merges were LEGITIMATE (no loss)**

`slides_extracted: 18`, 3 near-dup lines, **2 `merged:` lines, both correct:**

- 02:53 ~ 03:03 (dist 5): the same notepad, mid-typing — the anchor has 5 lines, the
  dropped frame has 8 (more text added). A genuine build-step. ✓
- 11:13 ~ 11:22 (dist 9): the same Gemini demo screen (prompt entry → image
  generating). Same screen. ✓

The dense infographic slides (each a distinct multi-card layout) and varied demo
screens hash far apart, so the merge pass only fired on true same-screen progressions.
Note also a near-dup **chain** (03:35 ~ 04:01 ~ 04:39 ~ 05:06, dist 5–7): the same PPT
slide held ~90 s while the presenter talked, captured 4× and **kept** (gaps > 15 s, so
the merge pass correctly declined) and surfaced as near-dup review lines — the
recoverable path, exactly as intended.

## Result C — vertical reel: merge does not engage

Freeze mode captured **0 slides** (a 37-s reel never holds a screen for 3 s). The
SKILL-recommended reel path (`--detect scene --scene-threshold 0.15 --phash-dist 2`)
captured 22 — and scene mode does not run the time-aware merge at all. So short-form
never exercises the merge machinery; no merge finding applies.

## The failure boundary, stated plainly

| Deck style | Merges | False | Slide loss from merge |
|---|---|---|---|
| A — dark minimalist, sparse large-title | 10 | **10 / 10** | **≈29%** |
| B — content-dense infographic + demos | 2 | 0 / 2 | 0 |
| C — vertical reel (scene mode) | — | — | n/a |

**The default time-aware merge is safe on content-dense / visually varied material and
destructive on sparse-text, uniform-template decks.** The dark-minimalist style — one
big title line per slide on a fixed template — is a common enough presentation format
that it turned up on the first non-forum lecture tried here, and on that deck the
*default* `--slides` run silently lost roughly a third of the slides (10 of ~35), with
no signal the user would notice (the `merged:` lines report the folds, but a user who
trusts the defaults won't be auditing 10 of them frame-by-frame). Whether the failure is
this frequent across the tool's real input population is not something three clips can
establish — but one clip is enough to show the *default* can silently destroy a large
fraction of a legitimate deck, which is the R06 contract's own accepted risk, now
observed and quantified in the field.

## Recommendation (a fix round, not done here — needs a decision)

This dogfood only *characterizes* the defect; the fix is a separate round. The
recommended direction, for discussion:

- **Make the time-aware merge opt-in (default off), or flip its default to a
  conservative no-op.** Rationale: the project is recall-first — losing a real slide is
  unrecoverable at the notes step, while an un-merged build-step merely shows up as an
  extra near-dup-flagged frame (the *recoverable* case the near-dup review already
  handles). Merge remains available (`--merge-gap`/`--merge-dist`) for animation-heavy
  decks where the user has verified it helps, as on the forum clip. The cost —
  more frames to read on build-heavy decks — is strictly cheaper than silently deleting
  distinct slides.
- Threshold-tuning is a dead end: Result A shows no single dhash threshold separates a
  build-step from a distinct sparse slide, so neither raising nor lowering the default
  is a fix.
- A weaker alternative — keep merge on but emit a loud warning when many merges fire at
  low distance (a likely uniform-template over-merge) — only mitigates; it still starts
  from a lossy default.

Reversing the R06 default is a product decision, so it is left for the user rather than
changed unilaterally here.

Companion to [`2026-06-28-auto-crop-field-limits.md`](2026-06-28-auto-crop-field-limits.md)
(the `--crop`/`--probe-frame` field record on the forum clip).
