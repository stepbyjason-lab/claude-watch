# Note Quality Template Redesign Spec (2026-06-04)

## Context

The original claude-watch skill successfully extracts video evidence: frames, timestamps, and transcript. The observed quality problem is downstream: the original `SKILL.md` requires one section per scene with `On screen`, `Said`, and `Synthesis`. That makes agents produce a chronological screen log instead of a learning document.

This issue predates the slides-mode fork work. Slides mode improves visual coverage, but it does not by itself fix synthesis quality.

Core principle: formatting rules serve the learning goal. If a non-negotiable format quietly turns "study notes" into a scene log, the format is wrong.

## Decision

claude-watch notes must be **concept-first** by default.

The agent must read every frame and transcript segment, then reorganize the source into:

1. Core thesis
2. Concept map
3. Argument / lesson structure
4. Frameworks, methods, or decision rules
5. Examples and applications
6. Caveats, edge cases, and open questions
7. Inline visual evidence where it teaches the concept
8. Slide coverage ledger for dense decks and traceability

Frame descriptions are evidence, not the main narrative.

For slide lectures, extraction completeness is mandatory: every unique prepared slide captured by `--slides` must be accounted for in the note. Concept-first structure changes where the slide evidence appears; it does not authorize dropping slides to make the note look less chronological.

For short decks, accounting may mean embedding every slide inline or in the ledger. For long decks, accounting may mean embedding only the teaching-critical slides and listing the rest in a reference-only ledger with `[t]`, `frames/path`, and one-line "supports X" notes. Completeness means every slide is tracked; it does not mean every slide must consume image tokens twice.

## Output Modes

### Default Study Notes

Use for tutorials, lectures, talks, seminars, and conceptual videos.

The main body is organized by concepts. Timestamps and frames support claims.

For slide lectures, prefer `--slides` so the prepared deck is captured completely.

### Code Walkthrough Notes

Use when the video is mostly coding.

The main body may be organized by implementation milestones, but each section must still explain intent, design decisions, and reusable lessons. Code-on-screen must be transcribed where useful.

### Product / UI Demo Notes

Use when the video is mostly demonstrating a product.

The main body may be organized by workflow or feature area. Screen evidence stays attached to workflow analysis.

### Slide Coverage Ledger

Use when extra traceability or slide completeness is needed. It can preserve chronological details, but it must not replace the main concept-first notes. It is not the only place images may appear; important visual evidence should appear inline beside the concept it supports. For long decks, the ledger may be reference-only instead of embedding every image.

## Quality Bar

A note fails if:

- Most section headings are timestamps or slide titles.
- Most paragraphs start by describing what appears on screen.
- Old-template fields such as `On screen:` or `Synthesis:` appear in the main note body, indicating regression to the scene-log format.
- Evidence captions or visual descriptions are longer than the teaching prose across most sections.
- The reader cannot explain the lecture's core argument without the prose.
- Slide-mode notes fail to account for unique prepared slides that were extracted.
- The note preserves chronology but does not extract frameworks, decisions, examples, caveats, or application steps.

A note passes if:

- The reader can understand the core thesis and concept structure from the prose.
- Screenshots are used inline or in a ledger/appendix as evidence for concepts, examples, diagrams, UI states, or before/after comparisons.
- The document explains why the material matters and how to apply it.
- Timestamps make claims traceable.
- Slides/scenes are grouped when they support the same idea.

## Compatibility

The watch script output does not change. Existing cached libraries remain valid. This is a skill/template change only.
