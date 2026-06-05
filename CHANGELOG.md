# Changelog

All notable changes to `claude-watch` are documented here.

## [Unreleased] — fork `stepbyjason-lab/claude-watch`

Additive changes on top of upstream `devinilabs/claude-watch`. Classic extraction and existing library caches are unchanged.

### Added
- **`--slides` mode** — high-recall capture of a lecture deck (aims for every prepared slide; fast-flips and visually-similar slides can still be missed — see [`docs/dogfood/2026-06-05-detailpage-slides.md`](docs/dogfood/2026-06-05-detailpage-slides.md)): crops out the presenter cam + caption, scene-detects on the slide region at a low threshold, and conservatively deduplicates near-identical frames via a zero-dependency `ffmpeg` 8×8 average hash (borderline pairs are kept and flagged, not silently dropped). Native 720p extraction. Flags: `--slides`, `--cam-corner`, `--caption`, `--hi-res`, `--phash-dist`.
- Slides cache identity: the full detection profile is folded into the slug (any slide flag change re-runs cleanly); default-mode slugs hash identically to upstream.
- Hardening: `-protocol_whitelist file` on all ffmpeg inputs, `urlparse`-based source-scheme allowlist, candidate-frame cap, and UTF-8 stdout/stderr (no cp949 crash on Windows).

### Changed
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
