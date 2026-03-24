# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

This is the **Ralph Wiggum Loop** — a Python script (`loop.py`) that drives Claude Code iteratively in two modes:

- **Plan mode**: Reads specs and codebase, generates/updates `IMPLEMENTATION_PLAN.md` as a prioritized checkbox list
- **Build mode**: Implements items from the plan, then spawns reviewer and QA subagents to verify, commits and repeats

## Commands

```bash
# Initialize — generates PROMPT_plan.md, PROMPT_build.md, updates .gitignore
python loop.py init

# Plan mode (default 10 iterations)
python loop.py plan
python loop.py plan -n 5

# Build mode (default 20 iterations)
python loop.py build
python loop.py build -n 10
python loop.py build --no-stop        # don't stop when plan is complete
python loop.py build --model sonnet   # use a different model (default: opus)
```

## Prerequisites

- Claude CLI must be installed and on PATH
- Must be run inside a git repository with a clean worktree
- Auto-compact must be disabled: `claude config set autoCompact false`
- Prompt files (`PROMPT_plan.md` / `PROMPT_build.md`) must exist (created by `init`)
- A `specs/` directory with application specifications is expected

## Key Behaviors

- Runs Claude in headless mode (`claude -p --dangerously-skip-permissions --output-format stream-json`)
- Creates checkpoint commits after each iteration (`git add -A && git commit`)
- Stops after 2 consecutive iterations with no changes
- In build mode, stops when all plan tasks are checked off (unless `--no-stop`)
- Disables Claude's auto-memory via `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`
- Spawns reviewer and QA subagents (fresh context each) to verify work before marking tasks complete
