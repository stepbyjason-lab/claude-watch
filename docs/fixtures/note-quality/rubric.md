# claude-watch Note Quality Rubric

Score each output from 0 to 2.

| Criterion | 0 | 1 | 2 |
|---|---|---|---|
| Concept organization | Mostly timestamp/slide order | Some concept grouping | Main structure is concept-first |
| Synthesis depth | Screen/transcript recap | Some interpretation | Reusable claims, frameworks, examples, caveats |
| Evidence use | Screenshots are the content, duplicated wastefully, or exiled to an unused appendix | Mixed | Screenshots support claims inline, while the ledger accounts for coverage without duplicate embeds |
| Slide coverage (`--slides`) | Unique prepared slides are missing | All slides accounted for but not well grouped | All slides accounted for and grouped by concept |
| Reader value | Must watch video again | Partial summary | Can learn from the note alone |
| Traceability | Few timestamps | Some timestamps | Major claims are timestamped |

Passing score: 10/12 or higher, with no zero in Concept organization, Synthesis depth, or Slide coverage when `--slides` is used.
