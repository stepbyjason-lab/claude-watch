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
