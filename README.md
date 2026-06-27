# claude-watch

> **This is a fork** of [`devinilabs/claude-watch`](https://github.com/devinilabs/claude-watch)
> that adds a **`--slides` mode** for high-recall capture of a lecture deck, plus a
> **concept-first note contract**. Classic extraction and existing caches are unchanged. → [**Changes in this fork**](#changes-in-this-fork)

**Turn any tutorial or lecture video into structured study notes.** Paste a URL, walk away, come back to a markdown file with embedded screenshots, timestamped transcript, and Claude's synthesis — saved to a persistent library.

```
/claude-watch https://youtu.be/<lecture> backprop intuition
```

## Install

| Surface | Command |
|---|---|
| **Claude Code** | `/plugin marketplace add devinilabs/claude-watch` then `/plugin install claude-watch@claude-watch` |
| **claude.ai** (web) | Download `claude-watch.skill` from the latest release → Settings → Capabilities → Skills → `+` |
| **Codex** | `git clone https://github.com/devinilabs/claude-watch ~/.codex/skills/claude-watch` |

## What it does

1. Downloads via `yt-dlp` (or accepts a local file).
2. Detects scene changes with `ffmpeg`. Inserts coverage-floor frames every 45s across long static gaps so a lecture with one slide for 5 minutes still gets ~7 frames, not 1.
3. Pulls a timestamped transcript — captions first (free), then a local Whisper (if installed — free, offline, preferred), then the Whisper API (Groq, OpenAI) — only when missing.
4. Hands frames + transcript to Claude as raw evidence. Claude `Read`s every frame and writes `notes.md` as a **concept-first study document** — not a screen-by-screen log:
   - `## TLDR` + `## Core Thesis` — the main argument and why it matters
   - `## Concept Map` — concepts with timestamped evidence
   - `## Learning Path` — concepts/claims/frameworks explained, with inline screenshot evidence and captions
   - `## Frameworks…`, `## Examples and Applications`, `## Caveats and Open Questions`
   - `## Code & Commands` — code-on-screen transcribed into runnable blocks
   - `## Slide Coverage Ledger` — every extracted frame accounted for as `inline` / `ledger` / `non-slide` (held demos and non-content stills get a one-line reason), with `[t]` + frame links
5. Saves everything to the library at `<library-root>/<slug>/` — re-running the same URL is a cache hit.
   The library root defaults to the OS app-data dir (Windows `%LOCALAPPDATA%\claude-watch\library`,
   macOS `~/Library/Application Support/claude-watch/library`, Linux `$XDG_DATA_HOME` or
   `~/.local/share/claude-watch/library`). To override — highest priority first — use the
   `--out-dir` flag per run, the `CLAUDE_WATCH_LIBRARY` environment variable, or the same key in
   `~/.config/claude-watch/.env`. Installs that already have the legacy `~/claude-watch/library`
   keep using it untouched (any override above still wins).

## Why this exists

`claude-video`'s uniform frame sampling spends the budget poorly on long lectures with slow-changing slides. And answers live in chat, so you can't go back to "the notes from that video." `claude-watch` is opinionated for the tutorial workflow: scene-aware frames, persistent library, structured notes file.

## Changes in this fork

This fork (`stepbyjason-lab/claude-watch`) adds a **`--slides` mode** on top of upstream
`devinilabs/claude-watch`. Classic **extraction and caching are unchanged** (the `--slides`
flag and its pipeline are purely additive). The note-writing contract is now **concept-first**
for every mode — see [Note quality](#note-quality).

**New — `--slides`: high-recall capture of a lecture deck (aims for every prepared slide).**
- Crops out the presenter cam + burned-in caption, then scene-detects on the *slide region* at
  a low threshold — so slide→slide changes the whole-frame detector misses are caught.
- A tight coverage floor — with an **end-of-video tail anchor** so a slide shown only in the
  final seconds (too late to register as a scene cut, past the last coverage step) is still
  captured — plus a conservative **perceptual-hash dedup** (zero new dependencies —
  the 9×8 difference (edge) hash is computed via `ffmpeg` — an edge hash, not an average
  hash, so it tells apart monochrome text slides on white decks): near-identical frames are dropped, but
  borderline pairs are **kept and flagged**, not silently merged.
- Downloads 720p and extracts at native resolution; `--hi-res` for tiny-text decks.
- New flags: `--slides`, `--detect`, `--crop`, `--hold`, `--freeze-noise`, `--candidate-cap`, `--cam-corner`, `--caption`, `--hi-res`, `--phash-dist`.

> **Detection mode (default `--detect freeze`).** `--slides` now captures one frame per
> *held* (frozen) screen — a prepared slide is shown static for a few seconds, while a live
> demo (scrolling code/browser) never settles, so demo scroll-noise is skipped and the output
> count tracks held screens rather than video length (no candidate-cap blowups on multi-hour
> videos). For screen recordings where the cam/chat/taskbar isn't in a corner (e.g. Zoom),
> pass an explicit `--crop W:H:X:Y` for the slide region — freeze needs the moving chrome out
> of frame. `--hold N` (default 5s) is the min hold; lower = higher recall (also keeps held
> demo screens), higher = stricter. Freeze does **not** separate slides from held demo screens
> (that's a content judgment — leave it to the notes step); it removes the scroll-noise.
> The previous scene-cut + coverage-floor detector is still available via `--detect scene`.

> **High-recall, not exhaustive.** (`--detect scene`) Two real runs ([dogfood](docs/dogfood/2026-06-05-detailpage-slides.md), [white-deck validation](docs/dogfood/2026-06-05-harness-dhash-validation.md)) widened coverage well beyond manual sampling; the end-of-video anchor recovers the final slide, and on a 38-min white deck `--scene-threshold 0.15 --phash-dist 2` lifted capture from 25 → 31 slides. *Fast page-flips* and *near-identical build steps* can still merge, so keep the transcript as a parallel source and lower `--scene-threshold` / `--phash-dist` for dense text decks.

**Supporting changes (additive / backward-compatible):**
- `slug_for`: slides runs fold their full detection profile into the cache key, so changing any
  slide flag re-runs cleanly. **Default-mode slugs hash identically to upstream — existing caches keep hitting.**
- `detect_scenes` gains one optional `prefilter=` kwarg (default `""` → byte-identical output).
- `extract_frames` gains a `native=` option; `download_video` gains an enum `fmt=` selector (default unchanged).
- All `ffmpeg` inputs add `-protocol_whitelist file`; the source URL scheme is allow-listed (http/https/local only).
- `watch.py` forces UTF-8 stdout/stderr so non-ASCII output doesn't crash on legacy Windows codepages (e.g. cp949).

**Design & review trail:** [design spec](docs/specs/2026-06-04-slides-mode-design.md) ·
[TDD plan](docs/plans/2026-06-04-slides-mode.md) ·
[implementation + multi-lens review log](docs/2026-06-04-slides-mode-implementation-log.md).
Run the suite with `python -m pytest -m "not network"`.

*Offered for upstreaming — happy to open a PR if it's useful to the project.*

## Usage

```
/claude-watch <url-or-path> [topic]
/claude-watch ~/Lectures/cs231n.mp4 backpropagation derivation
/claude-watch https://youtu.be/<long> --start 5:00 --end 25:00
/claude-watch <url> --resolution 1024            # for slides with tiny code text
/claude-watch <url> --slides                     # lecture-deck mode
```

Flags: `--start/--end`, `--max-frames`, `--resolution`, `--scene-threshold`, `--max-gap`, `--whisper local|groq|openai`, `--no-whisper`, `--out-dir`.

Slides flags: `--slides`, `--detect freeze|scene` (default freeze), `--crop W:H:X:Y`, `--hold SECONDS` (default 5), `--freeze-noise -50dB`, `--candidate-cap N` (default 800), `--prefer-light` + `--light-threshold N` (default 80), `--cam-corner tr|tl|br|bl|none`, `--caption bottom|top|none`, `--hi-res`, `--phash-dist`.

> **`--prefer-light` (opt-in, freeze only).** After freeze capture, drops frames whose mean brightness is below `--light-threshold` (0–255, default 80) — a cheap way to discard dark IDE/terminal *demo* screens and cut downstream token cost further. Assumes light-background slides: **leave it off for dark-themed decks** (their slides would be dropped). It removes dark demos, not light-background ones — final slide selection still belongs to the notes step.

## Note quality

claude-watch extracts frames and transcript, but the final note should not be a screen-by-screen log. The skill now asks the agent to produce concept-first study notes: core thesis, concept map, frameworks, examples, applications, caveats, inline visual evidence, and a slide coverage ledger with timestamped frame references.

Use the scene/slide timeline as evidence, not as the main structure. For slide lectures, `--slides` should preserve every unique prepared slide; the note should account for those slides in the ledger and group them by concept rather than dropping them or listing them as one-slide-per-section.

## Bring your own keys

Captions cover the majority of public videos for free. Whisper only kicks in when a video has no caption track.

| Need | Cost |
|---|---|
| Download + native captions | free (`yt-dlp` + `ffmpeg`) |
| Whisper fallback (preferred) | **local Whisper — free, offline** (auto-used when available) |
| Whisper fallback (cloud) | Groq `whisper-large-v3` (cheap, fast), then OpenAI `whisper-1` |
| Disable Whisper | `--no-whisper` (frames-only when no captions) |

**Local Whisper takes precedence over the cloud automatically.** If `whisper` (`pip install openai-whisper`) or `whisper-ctranslate2` (faster-whisper) is on your PATH, it's auto-detected and used — no key, no upload. Language is auto-detected; set `WHISPER_MODEL` to pick the model (default `base`). For an isolated venv or a custom wrapper that isn't on PATH, point `WHISPER_LOCAL_CMD` at it (it's also read from the OS environment, so other tools can share the same variable):

```
# A command that, given an audio path, writes <stem>.srt into an output dir.
# {audio}/{outdir} are substituted; if absent, the audio path and `--outdir <dir>`
# are appended. Example (faster-whisper wrapper in its own venv):
WHISPER_LOCAL_CMD=/path/to/venv/python /path/to/transcribe.py {audio} --outdir {outdir}
```

If the local backend fails, claude-watch falls back to the cloud (unless you forced it with `--whisper local`). Cloud keys go in `~/.config/claude-watch/.env` (mode 0600 on Unix; Windows does not enforce this — restrict the file with filesystem ACLs yourself if the machine is shared).

## Re-running the same video

The library is keyed on `slug = YYYY-MM-DD-<title>-<short-hash>` where the short hash is `sha1(source + focus_range)[:4]`. Re-running the same URL with the same focus range hits the cache — no re-download, no re-transcribe, only frames + notes regenerate. Different focus range = different slug = a separate notes file.

To force a fresh run, delete the `meta.json` in the library dir.

## Limits

- **Best accuracy: under ~30 minutes** for a single notes pass. Past that, use `--start`/`--end` to focus.
- **Hard frame cap: 80** by default. Bump with `--max-frames` (token cost grows linearly).
- **Cloud Whisper upload limit: 25 MB** (~50 min mono 16 kHz). Longer videos need captions or a local Whisper (no upload limit).
- **No private platforms.** Public URLs and local files only.

## Develop

```bash
git clone https://github.com/devinilabs/claude-watch
cd claude-watch
python3 -m pytest                         # full suite
bash scripts/build-skill.sh               # → dist/claude-watch.skill (claude.ai bundle)
```

Releasing: tag `vX.Y.Z`, push the tag — CI builds and attaches `claude-watch.skill`.

## License

MIT. Built on `yt-dlp`, `ffmpeg`, and Claude's multimodal `Read` tool. Whisper transcription via a local install (openai-whisper / faster-whisper) or the [Groq](https://groq.com) / [OpenAI](https://openai.com) APIs.
