# Dogfood: `--slides` coverage on a 155-min demo-heavy seminar (2026-06-27)

Validation for the R03 notes-coverage contract change (embed-first asymmetric
accounting; `inline` / `ledger` / `non-slide`). This is the field evidence behind
the "155-min seminar embedded only 6 slides" report: R01 (crop+freeze) and R02
(`--prefer-light`) cleaned the *extraction*; R03 makes the *note* cover it. This
run measures whether a cleaned 90-frame extraction, accounted for under the new
contract, actually embeds the deck instead of collapsing to a handful.

## Subject

Korean seminar **"LLM Wiki를 활용해, 내 데이터를 제대로 활용하는 AI 에이전트 팀원 만들기"**,
155:34 — a Zoom-style screen recording that is **demo-heavy**: a prepared slide
deck interleaved with long live IDE/terminal/Notion/browser demos and a few
venue/break stills the camera held.

Command (read-only validation, frames only):

```bash
python scripts/watch.py "<...>.mp4" --slides --crop 1840:1180:160:320 --hold 6 --prefer-light --no-whisper
```

→ `slides_extracted: 90`. (For comparison, legacy `--detect scene` on the same
video extracts **465** frames — mostly demo scroll-noise.)

## Extraction quality (R01 + R02 effect)

The crop+freeze default plus the opt-in brightness filter brought a 155-min
recording down to **90 candidate frames** with almost no empty scroll-noise — the
precondition R03 needs. A 90-frame contact sheet (3 groups of 30) was read to
classify each frame.

## Applying the R03 contract: the Slide Coverage Ledger

Each of the 90 extracted frames was classified per the contract — a prepared
slide as `inline` (default) or `ledger` (a genuine near-duplicate), and a held
demo / non-content still as `non-slide` with a reason. Classification is from
contact-sheet review (visual layout: title/agenda/card/diagram decks vs. held
IDE/Notion/browser screens vs. venue stills); it is not transcript-verified, so
counts are ±2.

| Disposition | Count | What it covers |
|---|---|---|
| `inline` | ~40 | prepared slides embedded beside the concept they support, plus 1 representative of the held IDE-demo cluster (t≈31:00–36:09) |
| `ledger` | ~11 | near-duplicate build steps / repeated section dividers (e.g. agenda frames t01:11–02:33; "2부 세션1" repeats t62:15 ≈ t68:51) — covered, not re-embedded |
| `non-slide` | ~39 | held demo/app screens beyond the one representative (~35: IDE/terminal/Notion/browser the speaker drove live) + non-content stills (~4: venue/room shots the freeze detector caught at t≈118:51 / 139:53 / 145:12) |
| **total** | **90** | `inline + ledger + non-slide = extracted` ✓ |

Per-group breakdown (contact-sheet review):

- **Frames 1–30** (intro + framing deck, then a held IDE demo cluster): inline ~16, ledger ~5, non-slide ~9. The t≈31:00–36:09 purple-header IDE screens are ~10 near-identical frames → one `inline` representative, the rest `non-slide` pointing at it.
- **Frames 31–60** (concept slides interleaved with doc/IDE demos): inline ~14, ledger ~5, non-slide ~11. Repeated section dividers ("2부 세션1 시작", "읽을거리…") become `ledger`.
- **Frames 61–90** (hands-on / live-build heavy tail): inline ~10, ledger ~1, non-slide ~19. The tail is mostly live Notion/IDE work and 3–4 venue stills, so `non-slide` dominates *this group* — legitimately, per the contract's demo-heavy allowance.

## Result: the "6 slides" failure does not recur

- **~40 slides embed `inline`** vs. the **6** of the original complaint — a **~6.7×** increase in deck coverage. The deck is materially represented, not collapsed.
- The audit identity holds: every one of the 90 extracted frames lands in exactly one disposition (`40 + 11 + 39 = 90`), so nothing is silently dropped.
- `non-slide` is **39/90 (43%)** — high, but this is a genuinely demo-heavy seminar (a third of the runtime is live tooling), and the contract explicitly allows that (a held demo is content judgment, not a slide). It stays **under the gate's majority trip-wire (>50%)**, which is exactly the line R03 added to catch under-embedding. Had the writer dumped real slides into `non-slide` to shrink the note, this would have crossed 50% and failed the coverage check.

## What this validates (and what it does not)

- **Validates:** the asymmetric embed-first rule produces broad deck coverage on a
  demo-heavy source; the three dispositions partition cleanly; the majority
  trip-wire is a meaningful, not-yet-tripped guardrail at 43%.
- **Does not validate:** full concept-first *prose* quality — this run is
  frames-only (`--no-whisper`), so no `notes.md` narrative was scored against the
  rubric. A transcript-backed full note is a follow-up. The coverage claim
  ("6 → ~40 inline") stands on the frame accounting alone.

Field evidence companion to [`2026-06-05-detailpage-slides.md`](2026-06-05-detailpage-slides.md)
(coverage *widening* on a clean deck) and
[`2026-06-05-harness-dhash-validation.md`](2026-06-05-harness-dhash-validation.md)
(dedup tuning). This one covers the inverse risk: a noisy, demo-heavy source where
the danger was *under*-embedding.
