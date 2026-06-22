# Changelog

All notable changes to `claude-watch` are documented here.

## [Unreleased] — fork `stepbyjason-lab/claude-watch`

Additive changes on top of upstream `devinilabs/claude-watch`. Classic extraction and existing library caches are unchanged.

### Added
- **Local Whisper backend (preferred over the cloud).** When no native captions exist, claude-watch now uses a local Whisper before falling back to Groq/OpenAI. A `whisper` (openai-whisper) or `whisper-ctranslate2` (faster-whisper) on PATH is auto-detected (language auto-detected; `WHISPER_MODEL` picks the model, default `base`); an isolated venv or custom wrapper is wired via `WHISPER_LOCAL_CMD` (read from the OS environment or `~/.config/claude-watch/.env`, with `{audio}`/`{outdir}` placeholders — whichever is absent is appended). Resolution order: forced `--whisper local|groq|openai` > `WHISPER_LOCAL_CMD` > PATH CLI > Groq > OpenAI. If an auto-picked local backend fails, it falls back to the cloud (a forced backend does not). The command's `.srt` output is parsed by the existing VTT grammar. Failure surfaces (unrunnable binary, nonzero exit, no/empty/unreadable SRT, unparseable `WHISPER_LOCAL_CMD`, ffmpeg audio-extraction error) all degrade gracefully to a cloud fallback or frames-only notes instead of crashing; subprocess output is control-char-sanitized before it reaches stderr. `setup.py --check` reports `whisper_backend: local` and treats a local Whisper as satisfying the transcription requirement (no cloud key needed).
- **`--slides` mode** — high-recall capture of a lecture deck (aims for every prepared slide; fast-flips and visually-similar slides can still be missed — see [`docs/dogfood/2026-06-05-detailpage-slides.md`](docs/dogfood/2026-06-05-detailpage-slides.md)): crops out the presenter cam + caption, scene-detects on the slide region at a low threshold, and conservatively deduplicates near-identical frames via a zero-dependency `ffmpeg` 9×8 **difference (edge) hash** — chosen over an average hash because it tells apart monochrome text slides on white decks, where an average hash collapses them (borderline pairs are kept and flagged, not silently dropped). Native 720p extraction. Flags: `--slides`, `--cam-corner`, `--caption`, `--hi-res`, `--phash-dist`.
- **Slides end-of-video tail anchor** — `apply_coverage_floor(..., include_tail_anchor=True)` (slides mode opts in; classic mode keeps the upstream default, byte-identical) adds one extractable floor candidate near `duration − 0.5s`, so the final slide — shown past the last coverage step, too late to register as a scene cut — gets coverage. Validated on a 38-min white deck (last slide recovered, 25 → 26); for dense text decks `--scene-threshold 0.15 --phash-dist 2` lifts capture further (25 → 31) without ballooning. Evidence: [`docs/dogfood/2026-06-05-harness-dhash-validation.md`](docs/dogfood/2026-06-05-harness-dhash-validation.md).
- Slides cache identity: the full detection profile is folded into the slug (any slide flag change re-runs cleanly); default-mode slugs hash identically to upstream.
- Hardening: `-protocol_whitelist file` on all ffmpeg inputs, `urlparse`-based source-scheme allowlist, candidate-frame cap, UTF-8 stdout/stderr (no cp949 crash on Windows), frames-wipe containment guard (never deletes outside the resolved library root), and local-source validation (`copy_local` refuses non-files and non-video extensions before creating the library symlink).

### Changed
- **Library root relocated to the OS app-data dir.** The hardcoded `~/claude-watch/library`
  (a folder dumped into the home-directory root on every platform) is replaced by a resolver:
  Windows `%LOCALAPPDATA%\claude-watch\library`, macOS `~/Library/Application Support/claude-watch/library`,
  Linux `$XDG_DATA_HOME` (or `~/.local/share`) `/claude-watch/library`. Override order:
  `--out-dir` flag > `CLAUDE_WATCH_LIBRARY` env var > the same key in `~/.config/claude-watch/.env` >
  legacy `~/claude-watch/library` if it already exists (existing installs keep working untouched) >
  platform default. `setup.py` scaffolds the new `.env` key as a commented hint.
- **Notes are now concept-first.** The scene-first "one scene/slide = one section" template (On screen + Said + Synthesis per scene) was replaced by a concept-first study-notes contract: core thesis, concept map, learning path, frameworks/examples/caveats, inline visual evidence with captions, and a slide coverage ledger — gated by a pre-write quality check. Screenshots and timestamps are evidence, not the main structure. Script/extraction output is unchanged.

### Docs
- Design specs, TDD plans, an implementation log, and good/bad note fixtures + a scoring rubric under `docs/`.

## [0.1.0] — 2026-05-03

### Added
- `/claude-watch <url-or-path> [topic]` slash command that produces structured study notes.
- Scene-aware frame extraction: ffmpeg scene detection (default threshold 0.30) with a coverage floor (synthetic boundaries every 45s across long static gaps) and a budget cap (default 80 frames, drops lowest-scoring detected scenes first; floor boundaries are always preserved).
- Persistent library at `~/claude-watch/library/<slug>/` with cached download, transcript, and scenes — re-runs only regenerate frames + notes.
- Slug rule `YYYY-MM-DD-<title>-<sha1(source+focus)[:4]>` so chronological + collision-safe across focus-range re-watches.
- Native caption pull via yt-dlp (manual + auto-subs) with VTT dedupe.
- Whisper fallback: Groq `whisper-large-v3` (preferred), OpenAI `whisper-1` (alt). Stdlib HTTP clients — no SDKs.
- `--start`/`--end` focused mode with denser coverage floor (15s vs 45s default).
- `setup.py` preflight (`--check` / `--json`) with cross-platform installer (`brew` on macOS auto-runs; `apt`/`dnf`/`winget`/`pip` commands printed elsewhere).
- Three-surface distribution: Claude Code plugin, claude.ai `.skill` bundle (built by `scripts/build-skill.sh`), Codex skill.
- SessionStart hook prints a one-liner only when remediation is needed.
- Strict notes template baked into SKILL.md: TLDR, Key Concepts, per-scene Notes (On screen + Said + Synthesis), Code & Commands, Diagrams Referenced, Open Questions.
