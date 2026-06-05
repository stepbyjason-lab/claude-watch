# Dogfood: `--slides` on a 54-min slide lecture (2026-06-05)

First real-world run of `--slides` + the concept-first note contract, compared honestly against a prior **manual** pass on the **same** video. This closes the pre-push validation gate in [`docs/plans/2026-06-04-note-quality-template-redesign.md`](plans/2026-06-04-note-quality-template-redesign.md) (Task 6).

## Subject

Korean marketing lecture "상세페이지 10단계" (sales detail-page copywriting), 54:04 — a prepared slide deck plus a late live browser demo. Two notes of the same video were produced and kept side by side:

- **v1** — manual `crop-scene` curation, **17 frames** hand-picked (the pre-fork workflow).
- **v2** — `watch.py --slides --cam-corner tr --caption none`, **138 frames** auto-extracted (`slides_extracted: 138`), synthesized under the concept-first contract.

## Where `--slides` helped (coverage widened)

`--slides` captured prepared slides the manual v1 pass had missed:

- Copywriting principle ③ **의외성 (unexpectedness)** and ⑥ **스토리텔링 (storytelling)** — v1 had only ①②.
- A **Kmong case-study** slide ("80% is self-praise, no detail images") — v1 missed.
- A **customer-qualification** tip slide — v1 missed.

Principle coverage went **2/6 → 4/6**, plus two case slides. The hypothesis "for lectures, capturing the whole deck beats manual curation" held — coverage is materially broader.

## Where `--slides` still missed (the honest part — it is **not** exhaustive)

- **Step-10 "scarcity" slide was dropped.** Visually similar to step-9, so the perceptual-hash dedup **merged** them. This slide is the lecture's punchline (a prospect paid *before* contacting because of it). v2 lost it; v1's manual pass had it.
- **Principles ④ 신뢰 (trust) and ⑤ 공감 (empathy)** were **fast-flipped** in ~13s, so no distinct frame landed. Even `--slides` could not recover them from frames — only the transcript had them.

Both failure modes — *visually-similar merge* and *fast-flip* — are exactly why the docs now describe slides mode as **high-recall, not exhaustive**, and why the transcript stays a first-class evidence source.

## Concept-first note contract: result

The v2 note followed the new contract end to end: TLDR → Core Thesis → Concept Map → Learning Path (concept-titled sections with inline evidence captions) → Frameworks/Methods/Decision Rules → Examples and Applications → Caveats → **Slide Coverage Ledger**. 23 slides inline; the ledger accounts for all 138 extracted frames as `inline` / `ledger` / **`gap`**, with the 3 missed slides recorded as honest `gap` rows (transcript-reconstructed).

### Rubric score ([`docs/fixtures/note-quality/rubric.md`](fixtures/note-quality/rubric.md))

| Criterion | Score | Notes |
|---|---|---|
| Concept organization | 2 | Fully concept-first; section titles are claims, not timestamps. |
| Synthesis depth | 2 | 6 principles + 10-step framework + decision rules + examples + caveats extracted. |
| Evidence use | 2 | 23 inline embeds with evidence captions; ledger accounts coverage; no duplicate embeds. |
| Slide coverage (`--slides`) | 1 | All **extracted** frames accounted for and grouped, but 3 prepared slides were genuinely missed by extraction (logged transparently as `gap`, reconstructed from the transcript). |
| Reader value | 2 | The lecture is learnable from the note alone. |
| Traceability | 2 | Every major claim carries a `[t=MM:SS]` and/or a frame link. |
| **Total** | **11 / 12** | **PASS** — ≥10/12 with no zero in Concept organization, Synthesis depth, or Slide coverage. |

## Verdict

- **Concept-first note contract: passes in the field.** The output reads as a study document, not a screen log.
- **`--slides`: useful, not magic.** It broadened coverage well beyond manual curation, but it missed a visually-similar slide (phash merge) and fast-flipped slides. The honest framing is **high-recall slide-deck mode** — transcript plus light human curation are still needed for completeness.
- **Follow-up recorded:** for decks with visually-similar consecutive slides, try `--phash-dist 2–3` (default 4) so steps like 9 vs 10 are not merged; keep the transcript as a parallel evidence source.

> The full source note and curated frames live in the operator's private notes vault; this file is the repo-side evidence of the run.
