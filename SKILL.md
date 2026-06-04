---
name: claude-watch
description: Watch a tutorial or lecture video (URL or local path) and produce concept-first study notes. Downloads with yt-dlp, detects scene or slide changes with ffmpeg, pulls a timestamped transcript (captions or Whisper API fallback), and writes synthesized markdown notes with inline visual evidence and timestamped traceability to ~/claude-watch/library/<slug>/.
argument-hint: "<video-url-or-path> [topic-or-question]"
allowed-tools: Bash, Read, Write, AskUserQuestion
homepage: https://github.com/devinilabs/claude-watch
repository: https://github.com/devinilabs/claude-watch
license: MIT
user-invocable: true
---

# /claude-watch — Claude turns a video into study notes

You don't have a video input. This skill gives you one *and* turns each viewing into a saved notes artifact.

## Step 0 — Setup preflight (silent on success)

Run on every `/claude-watch` invocation:

```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/setup.py" --check
```

Exit codes: `0` ready (silent — proceed), `2` missing binaries, `3` missing API key, `4` both. On non-zero, run the installer:

```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/setup.py"
```

On macOS this auto-`brew install`s ffmpeg + yt-dlp. On Linux/Windows it prints the right commands. It scaffolds `~/.config/claude-watch/.env` (mode 0600) with commented placeholders.

If a Whisper key is still missing afterwards, use `AskUserQuestion` to ask whether the user has a Groq key (preferred — cheaper, faster) or an OpenAI key, and write it to `~/.config/claude-watch/.env`. If they don't want to, run with `--no-whisper`; videos without native captions will come back frames-only.

## When to use

- User pastes a tutorial / lecture / talk URL and asks to study it
- User points at a local screen recording or video and wants notes
- User types `/claude-watch <url-or-path> [topic]`

## How to invoke

**Step 1 — parse input.** Separate the source (URL or path) from any topic the user mentioned. The topic shapes which sections you emphasize in the notes — pass it through to your synthesis, not to the script.

**Step 1.5 — classify the video and choose extraction flags (before running).** The `--slides` decision changes how frames are extracted, so make it **now**, not after the script has run:

- **Slide lecture / seminar (prepared deck):** run with `--slides` so every unique prepared slide is captured. Do **not** run plain scene mode on a slide deck — that defeats the whole point of slides mode. Group the slides by concept when you write the notes.
- **Conceptual talk without slides:** plain scene mode; organize the notes by thesis, concepts, arguments, examples, caveats, and applications.
- **Code walkthrough:** plain scene mode; organize the notes by implementation milestones, intent, design decisions, reusable patterns, and caveats.
- **Product / UI demo:** plain scene mode; organize the notes by workflow or feature area, with screenshots explaining UI states and decisions.

When you cannot tell whether a lecture uses a prepared deck, prefer `--slides`. This classification determines **both** the extraction flags here and the note structure in Step 5; after the script runs, the returned frames and transcript are treated as the raw evidence you synthesize.

**Step 2 — run the watch script** with the flags for the mode you chose in Step 1.5 (add `--slides` for slide lectures):

```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/watch.py" "<source>"           # scene mode
python3 "${CLAUDE_SKILL_DIR}/scripts/watch.py" "<source>" --slides   # slide lecture
```

Optional flags:
- `--start T` / `--end T` — focus on a section (`SS`, `MM:SS`, or `HH:MM:SS`)
- `--max-frames N` — lower budget (default 80)
- `--resolution W` — bump frame width to 1024 px when on-screen text is tiny
- `--scene-threshold X` — sensitivity (default 0.30; raise for fewer cuts, lower for more)
- `--max-gap S` — coverage floor in seconds (default 45)
- `--whisper groq|openai` — force backend
- `--no-whisper` — disable Whisper entirely
- `--out-dir DIR` — override library root
- `--slides` — **slide-deck mode**: capture every unique slide (see *Slides mode* below)
- `--cam-corner {tr,tl,br,bl,none}` — presenter-cam corner to exclude (slides mode; default `tr`)
- `--caption {bottom,top,none}` — burned-in caption band to exclude (slides mode; default `bottom`)
- `--hi-res` — slides mode: download 1080p instead of 720p (tiny-text decks)
- `--phash-dist N` — slides dedup distance; lower keeps more near-duplicates (default 4)

**Step 3 — read every frame.** The script ends with a structured `=== frames ===` block listing each frame's path and timestamp. `Read` them all in parallel — they render as images in your context.

**Step 4 — load the transcript.** The `=== transcript ===` block points to `transcript.json` (or `transcript.window.json` for focused mode). `Read` it — it's a list of `{t_start, t_end, text, speaker_break}`.

**Step 5 — write `notes.md` to the library directory.** Use the **concept-first study notes contract** below. The frames and transcript are raw evidence; your job is to synthesize them into a learning document. Save to `<library_dir>/notes.md`. Then print a 3-line summary to chat:
1. Title and slug
2. Number of sections + key concepts
3. Path to the notes file

Do **not** delete the library dir. It is the artifact.

## Slides mode (`--slides`)

For lecture/seminar videos where the speaker presents slides, add `--slides`:

```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/watch.py" "<source>" --slides
```

This captures **every unique slide** (page 1 → last): downloads 720p, detects slide
changes on the slide region (excluding the presenter cam + burned-in caption),
deduplicates near-identical frames, and extracts at native 720p.

- `--cam-corner {tr,tl,br,bl,none}` (default `tr`) — which corner the presenter cam occupies; `none` if there is no cam.
- `--caption {bottom,top,none}` (default `bottom`) — burned-in caption band to ignore; `none` if there are no captions.
- `--hi-res` — download 1080p (only for decks with very small text).
- `--phash-dist N` (default 4) — dedup aggressiveness; lower keeps more near-duplicates.
- `--slides` **cannot** be combined with `--start`/`--end` in v1 (the script errors out).

**Reading slides-mode output:** read every extracted slide, preserve every unique prepared slide, but do **not** make "one slide = one section" the default note structure. Frames are ordered by timestamp = deck order, and `slides_extracted: N` tells you how many distinct slides were captured. Use the slides as evidence, then group adjacent slides into the concepts, claims, frameworks, examples, or caveats they support.

Completeness and structure are separate: `--slides` protects coverage; concept-first writing protects learning quality. Do not drop prepared slides just to avoid a chronological-looking note. Instead, move each slide under the concept it supports and use an evidence caption that explains what the slide proves or exemplifies.

The stdout may print `review: near-dup t=A ~ t=B (dist D)` lines. These are borderline pairs the tool deliberately **kept** rather than risk dropping a real slide. Glance at both frames and merge their evidence only if they are genuinely the same slide.

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
| `[t=01:12]` | `frames/0003_t01-12.jpg` | ledger | <every slide referenced above (e.g. in "Additional supporting slides") must also appear here> |

### Optional Embedded Ledger Detail

Use this only for short decks or visually critical slides that were not already embedded inline.

![](frames/0002_t00-31.jpg)

**Evidence caption:** <What this visual supports; avoid pure screen description.>

**Transcript anchor:** <Relevant transcript excerpt, lightly cleaned.>

**Supports:** <Which concept/claim this evidence supports.>
````

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

## Re-runs

If the user re-watches the same URL, the script reuses the cached download, transcript, and scenes. Only frames + notes regenerate. To force a full re-run, delete `<library_dir>/meta.json` first.

## Failure modes

- **Setup preflight non-zero** → run `setup.py`, then ask for a key via `AskUserQuestion`.
- **No transcript** → script emits `transcript_source: none`. Generate notes frames-only and tell the user.
- **Long video sparse-scan warning** → offer to re-run with `--start`/`--end` focused on the part the user cares about.
- **Whisper failure** → retry with `--whisper openai` (if Groq failed) or vice versa.

## Token budget

Frames dominate cost (~50-80k input tokens for 60 frames at 512 px). Transcripts are cheap. `--resolution 1024` quadruples per-frame cost — only when the user must read tiny on-screen text.

If the user asks a follow-up about a video you already watched in this session, do NOT re-run the script. The library directory is on disk; re-`Read` only the frames you need.

## Security

- Runs `yt-dlp`, `ffmpeg`, `ffprobe` locally
- Sends extracted mono 16 kHz audio to Groq (preferred) or OpenAI Whisper API only when captions are missing
- Reads/writes `~/.config/claude-watch/.env` (mode 0600) for keys
- Persists artifacts to `~/claude-watch/library/<slug>/` — review the directory after first run if you're cautious
- Does NOT log or transmit API keys, video files, or the original URL outside the audio-to-Whisper call
