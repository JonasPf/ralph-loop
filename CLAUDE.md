# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

This is the **Ralph Wiggum Loop** — a Python script (`loop.py`) that drives a coding agent iteratively in two modes:

- **Plan mode**: Reads specs and codebase, generates/updates `IMPLEMENTATION_PLAN.md` as a prioritized checkbox list
- **Build mode**: Implements items from the plan, then spawns reviewer and QA subagents to verify, commits and repeats

The agent backend is selectable with `--agent` (default `claude`, the Claude Code CLI; or `pi`, the [pi coding agent](https://github.com/badlogic/pi-mono)). Pi has no subagent tool, so it uses its own prompt variants that fold QA inline.

## Commands

```bash
# Initialize — generates PROMPT_plan.md, PROMPT_build.md, PROMPT_spec.md
#              plus pi variants PROMPT_plan_pi.md, PROMPT_build_pi.md
python loop.py init

# Plan mode (default 10 iterations)
python loop.py plan
python loop.py plan -n 5

# Build mode (default 20 iterations)
python loop.py build
python loop.py build -n 10
python loop.py build --no-stop        # don't stop when plan is complete
python loop.py build --model opus     # use a different model (build default: sonnet, plan default: opus)

# Use the pi agent instead of claude (uses the PROMPT_*_pi.md prompts)
python loop.py build --agent pi
python loop.py plan --agent pi
```

## Prerequisites

- The selected agent CLI must be installed and on PATH (`claude` by default, or `pi` with `--agent pi`) — checked in preflight
- Must be run inside a git repository with a clean worktree
- The prompt file for the chosen mode/agent must exist (claude: `PROMPT_plan.md` / `PROMPT_build.md`; pi: `PROMPT_plan_pi.md` / `PROMPT_build_pi.md`) — created by `init` or manually
- A `specs/` directory with application specifications is expected

## Key Behaviors

- Creates checkpoint commits after each iteration (`git add -A && git commit`)
- Stops after 2 consecutive iterations with no substantive changes (changes to the per-iteration log files `THINKING.md` / `stream.jsonl` don't count)
- In build mode, stops when all plan tasks are checked off (unless `--no-stop`)
- With `--agent claude`: disables Claude's auto-memory via `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`, and the prompts spawn reviewer/QA subagents (fresh context each) to verify work before marking tasks complete
- With `--agent pi`: drives the `pi` CLI (`--mode json`); the pi prompts run QA inline since pi has no subagent tool. Pi's JSON token/cost fields aren't yet parsed, so those metrics show as zero (see the `TODO(pi)` in `loop.py`)
