# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

This is the **Ralph Wiggum Loop** — a Python script (`loop.py`) that drives a coding agent iteratively in two modes:

- **Plan mode**: Reads specs and codebase, generates/updates `IMPLEMENTATION_PLAN.md` as a prioritized checkbox list. Frontloads `- [B]` blocked tasks so they get resolved before build starts.
- **Build mode**: For each iteration, the orchestrator picks the topmost `- [ ]` task in the plan and runs it end-to-end as an `impl → QA → fix → QA` cycle (up to `--qa-max-attempts`). The QA agent flips `- [ ]` → `- [x]` on PASS; the orchestrator flips to `- [B] — reason` only on terminal QA failure. A single squashed commit at the end of the cycle carries the worktree changes and the marker flip.

The agent backend is selectable with `--agent` (default `claude`, the Claude Code CLI; or `pi`, the [pi coding agent](https://github.com/badlogic/pi-mono)). Both agents use the same prompt files.

## Commands

```bash
# Initialize — generates the five prompt files + adds .ralph/ to .gitignore.
python loop.py init

# Plan mode (default 10 iterations)
python loop.py plan
python loop.py plan -n 5

# Build mode (default 20 iterations; 3 QA attempts per task by default)
python loop.py build
python loop.py build -n 10
python loop.py build --model opus               # different model (build default: sonnet, plan default: opus)
python loop.py build --qa-max-attempts 5        # allow more fix attempts before [B]

# Use the pi agent instead of claude (same prompts; orchestrator drives QA the same way)
python loop.py build --agent pi
python loop.py plan --agent pi
```

## Prerequisites

- The selected agent CLI must be installed and on PATH (`claude` by default, or `pi` with `--agent pi`) — checked in preflight.
- Must be run inside a git repository with a clean worktree.
- The prompt files for the chosen mode must exist — created by `init`:
  - plan mode: `PROMPT_plan.md`
  - build mode: `PROMPT_build.md`, `PROMPT_qa.md`, `PROMPT_fix.md`
- A `specs/` directory with application specifications is expected.

## Key Behaviors

- **One outer iteration = one task.** The orchestrator picks the topmost `- [ ]` task in `IMPLEMENTATION_PLAN.md` and passes it as `{TASK}` to QA / fix prompts. The impl agent doesn't echo the task — convention is that whatever's topmost is current.
- **Marker discipline.** Build / fix agents never touch markers. The QA agent flips `- [ ]` → `- [x]` on PASS. The orchestrator flips `- [ ]` → `- [B] — reason` only after a terminal QA fail. A safety-net flip covers the case where QA passed but forgot to update the marker.
- **No DISCOVERED.md sidecar.** Build / QA / fix agents add discovered scope by appending new `- [ ]` lines *below* the in-flight task in `IMPLEMENTATION_PLAN.md` directly. They must never insert above it (that would change which task is current).
- **Stop when the next task is blocked.** Build mode (and preflight) walks the plan top to bottom; if the first non-`[x]` line is `- [B]`, the loop stops and the user must resolve the blocker. Plan mode is instructed to frontload blockers so they surface before build starts.
- **Per-call logs go to `.ralph/logs/<run-id>/iter-NN/<role>.<attempt>.jsonl`** and are gitignored. They are never committed. After each successful commit, `.ralph/logs/<run-id>/iter-NN/commit.txt` records the short SHA so logs can be cross-referenced to commits.
- **No live agent output.** Agent stdout streams straight to the log file. Watch with `tail -f` in another terminal if needed.
- Stops after 2 consecutive iterations with no commit (i.e. no worktree changes at all); in build mode, also stops on plan completion or a top-of-plan blocker.
- With `--agent claude`: disables Claude's auto-memory via `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`. Each role runs in a fresh context — the QA prompt instructs the agent to read `@CLAUDE.md` for operational commands since auto-memory is off.
- With `--agent pi`: drives the `pi` CLI (`--mode json`). Pi's token counts aren't yet parsed, so those metrics show as zero.
