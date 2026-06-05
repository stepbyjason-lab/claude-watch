# Note Quality Template Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace claude-watch's scene-by-scene default note contract with a concept-first study-note contract so outputs stop reading like screen descriptions.

**Architecture:** This is a documentation/skill-behavior change, not a video-extraction change. `watch.py` continues to provide frames, timestamps, and transcript as raw evidence; `SKILL.md` changes how the agent synthesizes that evidence into notes. For slide lectures, extraction completeness and concept-first note structure are separate requirements: capture every unique prepared slide, then account for every slide in a coverage ledger while embedding the most teaching-relevant slides inline beside their concepts. Verification is done through prompt-contract checks, fixture review, and required dogfood against one cached video.

**Tech Stack:** Markdown skill instructions, existing claude-watch CLI output, optional manual dogfood against cached library artifacts.

---

## Problem Statement

This plan addresses a quality issue observed in notes produced **before** the slides-mode fork changes. The issue is not that claude-watch failed to extract frames or transcript; it is that the current `SKILL.md` tells the agent to produce one section per scene/slide with required `On screen`, `Said`, and one-line `Synthesis` fields.

That contract naturally produces timeline logs:

- "At this timestamp, the screen shows X."
- "The speaker says Y."
- "This means Z."

The desired output is different: a concept-first learning document that reorganizes the lecture into arguments, frameworks, examples, caveats, and application steps, with screenshots and timestamps serving as evidence.

The important nuance from real usage: screenshots should not be exiled to a distant appendix. For visual teaching moments, the relevant frame belongs inline beside the concept it supports. The failure mode is not "images appear in the main body"; the failure mode is "image description becomes the main content."

For slide lectures, do not solve screen-log output by dropping slides. `--slides` exists because a prepared lecture deck should be captured completely. Concept-first means reordering/grouping evidence, not reducing coverage. For long decks, completeness is satisfied by a coverage ledger that accounts for every slide; not every slide needs a visible image embed in the main body.

## Non-Goals

- Do not change `watch.py`, frame extraction, transcript extraction, cache keys, or slides-mode detection.
- Do not add a new Python dependency.
- Do not remove frame/timestamp evidence.
- Do not reduce slide coverage for slide lectures merely to avoid a scene-log shape.
- Do not make the output less traceable to the source video.
- Do not force every video into the same high-level structure when it is clearly a code walkthrough or product demo.

## File Structure

- Modify: `SKILL.md`
  - Updates the frontmatter description away from "section-by-section".
  - Replaces the strict scene-first notes template.
  - Adds concept-first output contract.
  - Allows inline frame evidence in concept sections.
  - Adds a coverage-ledger model for dense slide decks: embed teaching-critical slides inline; account for all remaining slides with timestamp/path references.
  - Adds a mode-selection step before writing notes.
  - Adds a quality gate before writing `notes.md`.
- Modify: `README.md`
  - Briefly documents that claude-watch produces synthesized study notes, not only a frame log.
  - Adds a short example of the new output shape.
- Create: `docs/specs/2026-06-04-note-quality-template-redesign.md`
  - Records the rationale and accepted behavior.
- Create: `docs/fixtures/note-quality/bad-scene-first.md`
  - Minimal bad example showing the failure mode.
- Create: `docs/fixtures/note-quality/good-concept-first.md`
  - Minimal good example showing the expected output.
- Create: `docs/fixtures/note-quality/rubric.md`
  - Human-readable QA checklist for future dogfood.

## Acceptance Criteria

- `SKILL.md` no longer says `One scene = one section` as the default note rule.
- `SKILL.md` no longer requires `On screen` as the center of every main-section body.
- `SKILL.md` frontmatter no longer promises a "section-by-section" output as the core artifact.
- Main `notes.md` template starts from ideas, claims, frameworks, examples, and applications.
- Concept sections may include inline screenshot evidence with captions that explain what the image proves or exemplifies.
- Scene/slide evidence remains traceable with timestamps and frame links.
- Slides mode says "read every slide, preserve all unique prepared slides, group by concept" instead of "one slide = one section".
- Default scene mode may omit non-content noise, but slide-mode prepared slides must all be accounted for in the note.
- Long slide decks may use a reference-only coverage ledger for non-critical slides to avoid token-budget blowups.
- A quality gate explicitly fails outputs that are mostly screen description.
- Dogfood against one cached previously disappointing video is required before push.
- README and fixtures make the change understandable without reading this plan.

---

### Task 1: Write the Spec for the Note-Quality Contract

**Files:**
- Create: `docs/specs/2026-06-04-note-quality-template-redesign.md`

- [ ] **Step 1: Create the spec file**

Use this exact content:

```markdown
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
```

- [ ] **Step 2: Verify the spec is readable**

Run:

```bash
python3 - <<'PY'
from pathlib import Path
p = Path("docs/specs/2026-06-04-note-quality-template-redesign.md")
text = p.read_text(encoding="utf-8")
assert "concept-first" in text
assert "Inline visual evidence" in text
assert "coverage ledger" in text.lower()
assert "every unique prepared slide" in text
assert "Old-template fields" in text
assert "watch script output does not change" in text
print("spec ok")
PY
```

Expected: `spec ok`

- [ ] **Step 3: Commit**

```bash
git add docs/specs/2026-06-04-note-quality-template-redesign.md
git commit -m "docs: specify concept-first claude-watch notes"
```

---

### Task 2: Replace the Main Notes Template in `SKILL.md`

**Files:**
- Modify: `SKILL.md`

- [ ] **Step 1: Update the frontmatter description**

Find the existing description:

```yaml
description: Watch a tutorial or lecture video (URL or local path) and produce structured study notes. Downloads with yt-dlp, detects scene changes with ffmpeg, pulls a timestamped transcript (captions or Whisper API fallback), and writes a section-by-section markdown notes file with embedded screenshots to ~/claude-watch/library/<slug>/.
```

Replace it with:

```yaml
description: Watch a tutorial or lecture video (URL or local path) and produce concept-first study notes. Downloads with yt-dlp, detects scene or slide changes with ffmpeg, pulls a timestamped transcript (captions or Whisper API fallback), and writes synthesized markdown notes with inline visual evidence and timestamped traceability to ~/claude-watch/library/<slug>/.
```

- [ ] **Step 2: Add a mode-selection step BEFORE running extraction**

The `--slides` decision changes extraction, so mode selection must come **before** the run step (Step 2), not after — otherwise a slide lecture could be captured in plain scene mode. Insert a `Step 1.5` between Step 1 and Step 2:

```markdown
**Step 1.5 — classify the video and choose extraction flags (before running).** The `--slides` decision changes how frames are extracted, so make it **now**, not after the script has run:

- **Slide lecture / seminar (prepared deck):** run with `--slides` so every unique prepared slide is captured. Do **not** run plain scene mode on a slide deck — that defeats the whole point of slides mode. Group the slides by concept when you write the notes.
- **Conceptual talk without slides:** plain scene mode; organize the notes by thesis, concepts, arguments, examples, caveats, and applications.
- **Code walkthrough:** plain scene mode; organize the notes by implementation milestones, intent, design decisions, reusable patterns, and caveats.
- **Product / UI demo:** plain scene mode; organize the notes by workflow or feature area, with screenshots explaining UI states and decisions.

When you cannot tell whether a lecture uses a prepared deck, prefer `--slides`. This classification determines both the extraction flags here and the note structure in Step 5; after the script runs, the returned frames and transcript are treated as the raw evidence you synthesize.
```

Then update the existing **Step 2** so it runs with the chosen mode's flags — show both the plain `"<source>"` command and a `"<source>" --slides   # slide lecture` variant, so the classification from Step 1.5 is actually applied at run time.

- [ ] **Step 3: Update Step 5 wording**

Find:

```markdown
**Step 5 — write `notes.md` to the library directory.** Use the **strict template** below.
```

Replace with:

```markdown
**Step 5 — write `notes.md` to the library directory.** Use the **concept-first study notes contract** below. The frames and transcript are raw evidence; your job is to synthesize them into a learning document. Save to `<library_dir>/notes.md`. Then print a 3-line summary to chat:
```

- [ ] **Step 4: Replace the `Notes template` section**

Replace the current `## Notes template (non-negotiable structure)` section and its markdown template with this:

````markdown
## Notes template (concept-first study notes contract)

The default output is **not** a scene-by-scene screen log. Read every frame and transcript segment, then reorganize the video into a study document. The prose should teach the core thesis and concepts; screenshots should appear inline where they prove, illustrate, or clarify the idea.

For slide lectures, account for every unique prepared slide captured by `--slides`. Group slides by concept; do not drop slides just to make the note less chronological. Avoid duplicate embeds: if a slide is embedded inline, the coverage ledger can reference it without embedding it again.

````markdown
# <Video Title>

**Source:** <URL or path>  ·  **Duration:** MM:SS  ·  **Watched:** YYYY-MM-DD

## TLDR
<3-5 sentences: the core thesis, why it matters, and the most useful takeaway.>

## Core Thesis
<The main argument or lesson of the video in plain language.>

## Concept Map
- **<concept>** — <definition and why it matters> · Evidence: `[t=MM:SS]`
- **<concept>** — <definition and relationship to another concept> · Evidence: `[t=MM:SS]`

## Learning Path

### <Concept / claim / framework name>

<Explain the idea. Start from what the speaker is teaching, not from what appears on screen.>

![](frames/0001_t00-04.jpg)

**Evidence caption:** <What this frame proves, illustrates, contrasts, or makes concrete. Do not merely describe what is visible.>

**Why it matters:** <Practical or strategic importance.>

**How to apply it:** <Concrete usage guidance, checklist, or decision rule.>

**Traceability:** `[t=MM:SS]`; transcript segment `<short excerpt or paraphrase>`.

**Additional supporting slides:** `[t=MM:SS]` `frames/0002_t00-31.jpg`; `[t=MM:SS]` `frames/0003_t01-12.jpg`. <Use this when multiple slides support the same concept but do not all need inline embeds.>

<The Slide Coverage Ledger is the single source of truth for slide accounting. `Additional supporting slides` is only a convenience pointer inside a concept section; it must not replace or duplicate ledger accounting.>

### <Next concept / claim / framework name>
...

## Frameworks, Methods, and Decision Rules
- **<framework>** — <steps, when to use it, when not to use it> · Evidence: `[t=MM:SS]`

## Examples and Applications
- **<example>** — <what it demonstrates and how to reuse it> · Evidence: `[t=MM:SS]`

## Code & Commands
<Every important code-on-screen frame's content as a runnable fenced block, language-tagged, with [t=MM:SS] back-link. Omit this section if no code appears.>

```python
# [t=03:45]
def forward(x):
    return x @ W + b
```

## Caveats and Open Questions
- <Things mentioned but not fully covered, risks, assumptions, or follow-ups.>

## Slide Coverage Ledger

<Recommended for slide lectures and dense demos. Use this to prove coverage and traceability, not as the main narrative. If using `--slides`, every unique prepared slide should be accounted for exactly once as either `inline` or `ledger`. For long decks, prefer reference-only ledger rows instead of embedding every image.>

| Time | Frame | Status | Supports |
|---|---|---|---|
| `[t=00:04]` | `frames/0001_t00-04.jpg` | inline | <concept/claim this embedded frame supports> |
| `[t=00:31]` | `frames/0002_t00-31.jpg` | ledger | <concept/claim this non-embedded slide supports> |

### Optional Embedded Ledger Detail

Use this only for short decks or visually critical slides that were not already embedded inline.

![](frames/0002_t00-31.jpg)

**Evidence caption:** <What this visual supports; avoid pure screen description.>

**Transcript anchor:** <Relevant transcript excerpt, lightly cleaned.>

**Supports:** <Which concept/claim this evidence supports.>
````
````

- [ ] **Step 5: Verify old failure-inducing phrases are gone**

Run:

```bash
python3 - <<'PY'
from pathlib import Path
text = Path("SKILL.md").read_text(encoding="utf-8")
assert "One scene = one section" not in text
assert "The \"On screen\" block is required even for title slides" not in text
assert "concept-first study notes contract" in text
assert "concept-first study notes" in text.splitlines()[2]
assert "Step 1.5" in text
assert "Evidence caption" in text
assert "Slide Coverage Ledger" in text
assert "Avoid duplicate embeds" in text
assert "single source of truth for slide accounting" in text
assert "preserve every unique prepared slide" in text
assert "every unique prepared slide should be accounted for exactly once" in text
print("template ok")
PY
```

Expected: `template ok`

- [ ] **Step 6: Commit**

```bash
git add SKILL.md
git commit -m "docs(skill): make claude-watch notes concept-first"
```

---

### Task 3: Rewrite Slides-Mode Reading Guidance

**Files:**
- Modify: `SKILL.md`

- [ ] **Step 1: Replace the slides-mode reading paragraph**

Find the paragraph beginning:

```markdown
**Reading slides-mode output:** treat **one slide = one section**
```

Replace it with:

```markdown
**Reading slides-mode output:** read every extracted slide, preserve every unique prepared slide, but do **not** make "one slide = one section" the default note structure. Frames are ordered by timestamp = deck order, and `slides_extracted: N` tells you how many distinct slides were captured. Use the slides as evidence, then group adjacent slides into the concepts, claims, frameworks, examples, or caveats they support.

Completeness and structure are separate: `--slides` protects coverage; concept-first writing protects learning quality. Do not drop prepared slides just to avoid a chronological-looking note. Instead, move each slide under the concept it supports and use an evidence caption that explains what the slide proves or exemplifies.

The stdout may print `review: near-dup t=A ~ t=B (dist D)` lines. These are borderline pairs the tool deliberately **kept** rather than risk dropping a real slide. Glance at both frames and merge their evidence only if they are genuinely the same slide.
```

- [ ] **Step 2: Verify slides guidance changed**

Run:

```bash
python3 - <<'PY'
from pathlib import Path
text = Path("SKILL.md").read_text(encoding="utf-8")
assert "do **not** make \"one slide = one section\"" in text
assert "group adjacent slides into the concepts" in text
assert "preserve every unique prepared slide" in text
assert "Do not drop prepared slides" in text
assert "each slide as a `### [t=MM:SS]` section" not in text
print("slides guidance ok")
PY
```

Expected: `slides guidance ok`

- [ ] **Step 3: Commit**

```bash
git add SKILL.md
git commit -m "docs(skill): group slides by concept in notes"
```

---

### Task 4: Add the Note Quality Gate

**Files:**
- Modify: `SKILL.md`

- [ ] **Step 1: Add a quality gate before `## Re-runs`**

Insert this section before `## Re-runs`:

```markdown
## Quality gate before writing `notes.md`

Before finalizing, check the draft against this gate:

- The reader can understand the video's core thesis and concept structure from the prose; screenshots add concrete evidence and detail.
- Main sections are organized by concepts, claims, workflows, frameworks, examples, or applications — not by timestamp.
- Screenshots and transcript excerpts are evidence for ideas, not the main narrative. Important visual evidence may appear inline beside the concept it supports.
- If most paragraphs begin with what is visible on screen, rewrite the note.
- If old-template fields such as `On screen:` or `Synthesis:` appear in the main note body, rewrite the note using the concept-first contract.
- If evidence captions or visual descriptions are longer than the teaching prose across most of the document, rewrite the note.
- If slide titles became section titles without interpretation, rewrite the section titles as claims or lessons.
- If a section title is only a timestamp or a copied slide title, rewrite it as a concept, claim, workflow, or decision rule.
- If using `--slides`, verify every unique prepared slide is accounted for exactly once in the Slide Coverage Ledger as either `inline` or `ledger`. Do not reduce slide coverage to avoid a scene-log shape, and do not duplicate image embeds for the same slide.
- Every major claim has at least one timestamp or frame reference.
- Code, diagrams, UI states, and slide text are transcribed only when they materially support the learning goal.

If the source is genuinely a linear code walkthrough or UI demo, the main sections may follow the workflow order, but they still must explain intent, design decisions, reusable patterns, and caveats.
```

- [ ] **Step 2: Verify the gate exists**

Run:

```bash
python3 - <<'PY'
from pathlib import Path
text = Path("SKILL.md").read_text(encoding="utf-8")
assert "Quality gate before writing `notes.md`" in text
assert "If most paragraphs begin with what is visible on screen, rewrite the note." in text
assert "old-template fields such as `On screen:` or `Synthesis:`" in text
assert "evidence captions or visual descriptions are longer" in text
assert "If a section title is only a timestamp or a copied slide title" in text
assert "accounted for exactly once in the Slide Coverage Ledger" in text
assert "Every major claim has at least one timestamp or frame reference." in text
print("quality gate ok")
PY
```

Expected: `quality gate ok`

- [ ] **Step 3: Commit**

```bash
git add SKILL.md
git commit -m "docs(skill): add note synthesis quality gate"
```

---

### Task 5: Add Good/Bad Fixtures for Future Review

**Files:**
- Create: `docs/fixtures/note-quality/bad-scene-first.md`
- Create: `docs/fixtures/note-quality/good-concept-first.md`
- Create: `docs/fixtures/note-quality/rubric.md`

- [ ] **Step 1: Add the bad example**

Create `docs/fixtures/note-quality/bad-scene-first.md`:

```markdown
# Bad Example: Scene-First Screen Log

## Notes

### [t=00:04] Title slide

![](frames/0001_t00-04.jpg)

**On screen:** The title says "AI Agent Basics" with a diagram of a model and tools.

**Said:** The speaker says agents are becoming important.

**Synthesis:** This introduces the topic.

### [t=00:31] Model plus tools

![](frames/0002_t00-31.jpg)

**On screen:** The slide says "Model + Tools + Memory".

**Said:** The speaker says tools let the model act.

**Synthesis:** Tools are part of agents.

## Why This Fails

- The section structure is just timestamps.
- Screen description is the main content.
- The reader does not get a reusable framework.
- There is no clear thesis, decision rule, caveat, or application path.
- If this came from `--slides`, it also fails to prove that every prepared slide was accounted for conceptually.
```

- [ ] **Step 2: Add the good example**

Create `docs/fixtures/note-quality/good-concept-first.md`:

```markdown
# Good Example: Concept-First Study Notes

## TLDR

The lecture argues that an AI agent is not just a stronger model; it is a model embedded in an execution harness. The useful design question is therefore not "Which model is best?" but "What tools, memory, permissions, evaluation loop, and failure recovery does the model operate inside?"

## Core Thesis

An agent's practical capability comes from the whole operating system around the model.

## Concept Map

- **Model** — Generates plans and language, but does not by itself guarantee reliable action. Evidence: `[t=00:31]`
- **Harness** — The tool, memory, permission, and evaluation layer that turns model output into controlled action. Evidence: `[t=00:31]`
- **Evaluation loop** — The feedback mechanism that catches failed actions before they become user-visible failures. Evidence: `[t=01:12]`

## Learning Path

### Agent design starts with the harness, not the model

The speaker's important move is to define an agent as a system: model plus tools, memory, and control loop. That shifts the implementation question away from prompt cleverness and toward operational reliability.

![](frames/0002_t00-31.jpg)

**Evidence caption:** This slide turns "agent" from a vague label into a system boundary: model output only becomes useful action when tools and memory are inside a controlled harness.

**Why it matters:** A better model can still fail if it has unsafe tools, no memory boundary, or no verification loop.

**How to apply it:** When designing an agent, list the allowed actions, required context, failure checks, and rollback path before tuning prompts.

**Traceability:** `[t=00:31]`; the slide groups model, tools, and memory as one architecture.

**Additional supporting slides:** `[t=01:12]` `frames/0003_t01-12.jpg` shows the evaluation loop that checks whether tool actions worked.

## Slide Coverage Ledger

Use this ledger to prove coverage without duplicating image embeds. `frames/0002_t00-31.jpg` is already embedded inline, so it is referenced here as `inline`; `frames/0003_t01-12.jpg` is accounted for as a ledger-only supporting slide.

| Time | Frame | Status | Supports |
|---|---|---|---|
| `[t=00:31]` | `frames/0002_t00-31.jpg` | inline | Agent design starts with the harness, not the model. |
| `[t=01:12]` | `frames/0003_t01-12.jpg` | ledger | Evaluation loop as the reliability layer around tool actions. |
```

- [ ] **Step 3: Add the rubric**

Create `docs/fixtures/note-quality/rubric.md`:

```markdown
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
```

- [ ] **Step 4: Verify fixture files**

Run:

```bash
python3 - <<'PY'
from pathlib import Path
base = Path("docs/fixtures/note-quality")
for name in ["bad-scene-first.md", "good-concept-first.md", "rubric.md"]:
    p = base / name
    assert p.exists(), p
    assert p.read_text(encoding="utf-8").strip(), p
good = (base / "good-concept-first.md").read_text(encoding="utf-8")
rubric = (base / "rubric.md").read_text(encoding="utf-8")
assert "Evidence caption" in good
assert good.count("![](frames/0002_t00-31.jpg)") == 1
assert "Slide Coverage Ledger" in good
assert "Slide coverage" in rubric
print("fixtures ok")
PY
```

Expected: `fixtures ok`

- [ ] **Step 5: Commit**

```bash
git add docs/fixtures/note-quality
git commit -m "docs: add note quality examples and rubric"
```

---

### Task 6: Update README and Perform Manual Contract Review

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add a short quality note to README**

Add this section near the usage documentation:

```markdown
## Note quality

claude-watch extracts frames and transcript, but the final note should not be a screen-by-screen log. The skill now asks the agent to produce concept-first study notes: core thesis, concept map, frameworks, examples, applications, caveats, inline visual evidence, and a slide coverage ledger with timestamped frame references.

Use the scene/slide timeline as evidence, not as the main structure. For slide lectures, `--slides` should preserve every unique prepared slide; the note should account for those slides in the ledger and group them by concept rather than dropping them or listing them as one-slide-per-section.
```

- [ ] **Step 2: Run manual contract checks**

Run:

```bash
python3 - <<'PY'
from pathlib import Path
skill = Path("SKILL.md").read_text(encoding="utf-8")
readme = Path("README.md").read_text(encoding="utf-8")
assert "concept-first study notes contract" in skill
assert "Quality gate before writing `notes.md`" in skill
assert "Slide Coverage Ledger" in skill
assert "One scene = one section" not in skill
assert "concept-first study notes" in readme
assert "preserve every unique prepared slide" in readme
assert "slide coverage ledger" in readme
print("contract review ok")
PY
```

Expected: `contract review ok`

- [x] **Step 3: Required dogfood against one cached video**

> ✅ **Done (2026-06-05):** ran on a 54-min slide lecture — see [`../dogfood/2026-06-05-detailpage-slides.md`](../dogfood/2026-06-05-detailpage-slides.md). Rubric **11/12 (PASS)**. Confirmed the concept-first contract holds in the field, and that `--slides` is **high-recall, not exhaustive** (a visually-similar step slide was merged; two fast-flipped slides were missed) — which is why the docs now say "high-recall" and keep the transcript as a parallel source.

Pick one previously disappointing cached library artifact. Re-read its frames and transcript without rerunning `watch.py`, then draft a new `notes.md` using the new contract.

Recommended targets:

- Harness / 전현준 note, if cached, because it previously showed scene-log residue.
- 김효율 note, if cached, because it previously showed screen-description drift.

Compare the new output against the previous note and confirm the ledger/inline split feels natural in real reading, not only in the template.

Manual expected result:

- Main headings are not timestamps.
- The first page explains the core thesis.
- The note contains at least one framework or decision rule.
- Important screenshots appear inline beside the concept they support.
- If the source is a slide lecture and `--slides` output is available, every unique prepared slide is accounted for in the Slide Coverage Ledger as either `inline` or `ledger`.
- The Slide Coverage Ledger preserves frame/timestamp traceability without becoming the main narrative.
- No frame is embedded twice unless there is an explicit teaching reason.
- Rubric score is at least 10/12, with no zero in Concept organization, Synthesis depth, or Slide coverage when applicable.

- [ ] **Step 4: Commit only this README change**

If `README.md` already has unrelated dirty changes, do **not** stage the whole file. Either commit/resolve the pre-existing README changes first, or interactively stage only the new "Note quality" section:

```bash
git add -p README.md
git commit -m "docs: document concept-first note output"
```

---

## Self-Review

**Spec coverage:** The plan covers the observed failure mode, the new note contract, frontmatter description, mode selection, inline evidence, slide completeness, slides-mode guidance, quality gate, fixtures, README update, and required manual dogfood path. It explicitly avoids changing video extraction code.

**Placeholder scan:** No `TBD`, `TODO`, or "fill in details" placeholders are present. Each task includes concrete file paths, exact text, commands, expected output, and commit messages.

**Type/term consistency:** The plan consistently uses "concept-first", "inline evidence", "Slide Coverage Ledger", "quality gate", "slide completeness", and "scene/slide evidence". It does not introduce Python APIs or code types.

## Execution Recommendation

Implement this as a documentation-only branch segment after the current dirty docs are reviewed or intentionally included. Because this changes the behavior contract of a user-invocable skill, run at least one manual dogfood before pushing. The dogfood must check both synthesis quality and, for slide lectures, slide coverage. If README is already dirty, preserve session isolation by staging only the note-quality hunk or by landing the existing README work separately first.
