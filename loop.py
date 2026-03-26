#!/usr/bin/env python3
"""Ralph Wiggum Loop — Python implementation.

Drives Claude Code in a loop, feeding it a prompt file each iteration.
Supports plan mode (generate/update IMPLEMENTATION_PLAN.md) and build mode
(implement from plan, commit, repeat).

Usage:
    loop.py [plan|build] [OPTIONS]
    loop.py --help
"""

import argparse
import json
import os
import re
import subprocess
import sys
import textwrap
import time
from pathlib import Path

# ─── Constants ────────────────────────────────────────────────────────────────

PLAN_PROMPT = "PROMPT_plan.md"
BUILD_PROMPT = "PROMPT_build.md"
SPEC_PROMPT = "PROMPT_spec.md"
IMPLEMENTATION_PLAN = "IMPLEMENTATION_PLAN.md"

PLAN_DEFAULT_ITERATIONS = 10
BUILD_DEFAULT_ITERATIONS = 20

# ─── Prompt templates ────────────────────────────────────────────────────────

BUILD_PROMPT_TEMPLATE = """\
Read `specs/*` and @IMPLEMENTATION_PLAN.md. Pick the highest-priority unchecked item and implement it.

## Implementation

Read the relevant specs in `specs/*`. Search the codebase before assuming anything is missing. Use as many subagents as needed to parallelize work.

Implement the item fully — no placeholders or stubs. Fix any failures including pre-existing ones.

Use TDD — write tests first, then implement to make them pass. Keep code DRY and lean.

Build, test, and lint. All must pass before proceeding.

## QA

When implementation is complete, spawn a **QA** subagent with a fresh context. Include the task text from the plan in the subagent prompt so it knows what was implemented.

> You are the QA verifier. The task being verified is: `{TASK}`. Your job is to verify that the tests adequately cover the spec.
>
> Do NOT read the implementation code — only read the test files and the specs.
>
> 1. Read the relevant specs in `specs/*` to understand the expected behavior and acceptance criteria.
> 2. Read the test files to check they cover every acceptance criterion from the spec. Look for missing edge cases, untested error paths, and acceptance criteria without corresponding tests.
> 3. Run the build, tests, and linter to confirm they pass.
> 4. Start the app and smoke test the new feature(s) end to end — make real requests, verify responses, check that the feature works as a user would experience it. Don't rely solely on automated tests.
> 5. If there are gaps in test coverage or smoke test failures, report exactly what is missing or broken.
> 6. If everything is covered, passing, and works end to end, respond with: `QA PASS`
>
> You may add new items to @IMPLEMENTATION_PLAN.md if you discover things that need to be done. You may mark items as `- [B]` (blocked) if they need human intervention. You MUST mark the current item as `- [x]` (done) when all checks pass.

If QA reports failures, fix the issues and re-run the QA subagent (fresh context each time). Repeat until QA passes.

## Completion rules

- A task is only done when the QA subagent passes.
- Only the QA subagent may mark a task as `- [x]` in IMPLEMENTATION_PLAN.md.
- Any agent (you or QA) may mark a task as `- [B]` (blocked) with a reason.
- Any agent may add new items to IMPLEMENTATION_PLAN.md.
- If a task is blocked and needs human intervention (e.g. missing credentials, ambiguous spec), mark it `- [B]` with a brief reason and move on to the next unchecked item.
- If stuck in a fix/verify loop for more than 3 rounds, mark the task `- [B]` and move on.
- Update @CLAUDE.md only with operational knowledge (e.g. correct build commands). Keep it brief — progress belongs in IMPLEMENTATION_PLAN.md.

When done, output your final message in this exact format:

TITLE: <short headline, max 50 chars, e.g. "Add user authentication endpoint">
SUMMARY: <1-3 sentence description of what changed and why>
"""

PLAN_PROMPT_TEMPLATE = """\
Plan only — do NOT implement anything.

Read `specs/*` and @IMPLEMENTATION_PLAN.md (if present; it may be stale or wrong).

Search the codebase to verify what is and isn't implemented. Use as many subagents as needed to parallelize work. Never assume something is missing — confirm with code search. Look for TODOs, placeholders, stubs, skipped/flaky tests, and incomplete implementations.

Produce/update @IMPLEMENTATION_PLAN.md as a prioritized checkbox list (`- [ ]` pending, `- [x]` done).

Check that all dependencies required by the spec are available: command line tools, MCP servers, API keys, environment variables, etc. If anything is missing, create a task for it and mark it as `- [B]` (blocked) with what's needed.

For each task, consider whether it can be fully executed by an LLM without human involvement. If a task requires human input (e.g. API keys, credentials, third-party account setup, ambiguous requirements that need a product decision, manual deployment steps), mark it as `- [B]` (blocked) with a brief reason. The goal is to surface blockers early so they can be resolved before build iterations start.

When done, output your final message in this exact format:

TITLE: <short headline, max 50 chars, e.g. "Add user authentication endpoint">
SUMMARY: <1-3 sentence description of what changed and why>
"""

SPEC_PROMPT_TEMPLATE = """\
Help me write or update a specification for a software project. If there are already files in ./specs, study them first. Then interview me in detail using the AskUserQuestionTool about anything. Be very in-depth and continue interviewing me until you have all the information needed. Then create a new branch and name it after the features or project I want to build. Then create or update the specifications in ./specs/.

Rules for writing the specification:

0. Keep the specification as concise and succinct as possible. Avoid bloat.
1. Do not refer to the current state of the project, instead write or update the specs so they are self-contained and can be read and understood without prior knowledge of the project.
2. Keep the what (Functionality, Use Cases, Acceptance Criteria) seperate from the how (Tech Stack, Architecture). IMPORTANT: A specification is not a plan, do not include specific impmlementation steps.
3. Use OpenAPI 3.0 to design APIs.
4. Use mermaid entity relation diagram to document data bases and other data models.
5. Use mermaid flowcharts or message sequence diagrams to document data flows and system interaction.
6. Every feature must be documented with acceptance criteria.
7. Include a requirement for a good test suite consisting of unit tests, integration and e2e tests.
8. Include a requirment for concise and succinct documentation and a getting started guide in README.md.
9. Include a requirement for linting the code base.

Once you've written the specification, study it again and point out any inconsistencies, gaps or blindspots. If there are any lets resolve them together.
"""

# ─── Terminal helpers ─────────────────────────────────────────────────────────

BOLD = "\033[1m"
DIM = "\033[2m"
RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
BLUE = "\033[34m"
CYAN = "\033[36m"
RESET = "\033[0m"


def _supports_color() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    return hasattr(sys.stdout, "isatty") and sys.stdout.isatty()


COLOR = _supports_color()


def c(code: str, text: str) -> str:
    return f"{code}{text}{RESET}" if COLOR else text


def banner(text: str) -> None:
    width = max(len(text) + 4, 50)
    print()
    print(c(CYAN, "=" * width))
    print(c(CYAN, f"  {text}"))
    print(c(CYAN, "=" * width))
    print()


def info(msg: str) -> None:
    print(f"  {c(BLUE, '>')} {msg}")


def success(msg: str) -> None:
    print(f"  {c(GREEN, '+')} {msg}")


def warn(msg: str) -> None:
    print(f"  {c(YELLOW, '!')} {msg}")


def error(msg: str) -> None:
    print(f"  {c(RED, 'x')} {msg}")


def section(title: str) -> None:
    print()
    print(f"  {c(BOLD, title)}")
    print(f"  {c(DIM, '-' * len(title))}")


# ─── Precondition checks ─────────────────────────────────────────────────────


def check_git_repo() -> bool:
    """Verify we are inside a git repository."""
    result = subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"],
        capture_output=True, text=True,
    )
    return result.returncode == 0


def check_clean_worktree() -> bool:
    """Return True if there are no uncommitted or unstaged changes."""
    # Check for staged changes
    staged = subprocess.run(
        ["git", "diff", "--cached", "--quiet"],
        capture_output=True,
    )
    # Check for unstaged changes
    unstaged = subprocess.run(
        ["git", "diff", "--quiet"],
        capture_output=True,
    )
    # Check for untracked files
    untracked = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard"],
        capture_output=True, text=True,
    )

    clean = staged.returncode == 0 and unstaged.returncode == 0 and not untracked.stdout.strip()

    if not clean:
        if staged.returncode != 0:
            warn("There are staged (uncommitted) changes")
        if unstaged.returncode != 0:
            warn("There are unstaged modifications")
        if untracked.stdout.strip():
            warn("There are untracked files:")
            for f in untracked.stdout.strip().splitlines()[:10]:
                print(f"      {c(DIM, f)}")

    return clean


def check_auto_compact() -> bool:
    """Return True if auto-compact is disabled (safe to proceed).

    Checks ~/.claude.json for autoCompactEnabled. When false, auto-compact
    is disabled. Any other value (or missing key) means it's enabled and
    will interfere with the loop.
    """
    path = Path.home() / ".claude.json"
    try:
        data = json.loads(path.read_text())
        if data.get("autoCompactEnabled") is False:
            return True
    except (json.JSONDecodeError, OSError, FileNotFoundError):
        pass

    error("Auto-compact is enabled (or not explicitly disabled)")
    print()
    info("Auto-compact interferes with the ralph wiggum loop by")
    info("compacting context mid-iteration, which can cause the")
    info("agent to lose track of what it was doing.")
    print()
    info("Disable it by running:")
    print(f"      {c(BOLD, 'claude config set autoCompact false')}")
    print()
    return False

def check_prompt_file(prompt_file: str) -> bool:
    """Verify the prompt file exists."""
    if not Path(prompt_file).exists():
        error(f"Prompt file not found: {prompt_file}")
        info("Expected file in the current directory.")
        return False
    return True


# ─── Plan parsing ─────────────────────────────────────────────────────────────


def parse_plan_tasks(path: str = IMPLEMENTATION_PLAN) -> dict:
    """Parse IMPLEMENTATION_PLAN.md and return task statistics.

    Returns dict with keys: total, done, pending, blocked, tasks (list of dicts).
    Tasks are identified by markdown checkbox syntax: - [ ], - [x], or - [B].
    """
    result = {"total": 0, "done": 0, "pending": 0, "blocked": 0, "tasks": []}

    plan_path = Path(path)
    if not plan_path.exists():
        return result

    text = plan_path.read_text()
    for line in text.splitlines():
        stripped = line.strip()
        # Match markdown checkboxes:  - [ ] / - [x] / - [B]  or  * [x] etc.
        m = re.match(r'^[-*]\s+\[([ xXbB])\]\s+(.*)', stripped)
        if m:
            marker = m.group(1)
            task_text = m.group(2).strip()
            result["total"] += 1
            if marker.lower() == 'x':
                result["done"] += 1
                result["tasks"].append({"text": task_text, "status": "done"})
            elif marker.lower() == 'b':
                result["blocked"] += 1
                result["tasks"].append({"text": task_text, "status": "blocked"})
            else:
                result["pending"] += 1
                result["tasks"].append({"text": task_text, "status": "pending"})

    return result


def print_plan_summary(tasks: dict) -> None:
    """Print a human-readable summary of the implementation plan."""
    if tasks["total"] == 0:
        info("No tasks found in IMPLEMENTATION_PLAN.md")
        return

    pct = (tasks["done"] / tasks["total"]) * 100 if tasks["total"] > 0 else 0
    bar_width = 30
    filled = int(bar_width * tasks["done"] / tasks["total"])
    bar = c(GREEN, "#" * filled) + c(DIM, "-" * (bar_width - filled))

    info(f"Progress: [{bar}] {pct:.0f}%")
    blocked_str = f" / {c(RED, str(tasks['blocked']))} blocked" if tasks["blocked"] else ""
    info(f"Tasks: {c(GREEN, str(tasks['done']))} done / {c(YELLOW, str(tasks['pending']))} pending{blocked_str} / {tasks['total']} total")


# ─── Claude interaction ───────────────────────────────────────────────────────


def get_git_head() -> str:
    """Return the current HEAD commit hash (short)."""
    result = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        capture_output=True, text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else "unknown"


def get_git_branch() -> str:
    """Return the current branch name."""
    result = subprocess.run(
        ["git", "branch", "--show-current"],
        capture_output=True, text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else "unknown"



def run_claude_iteration(prompt_file: str, model: str = "opus") -> dict:
    """Run a single Claude CLI iteration and parse the streaming JSON output.

    Returns dict with: success, tokens_in, tokens_out, duration_s, error.
    """
    prompt_text = Path(prompt_file).read_text()

    start = time.monotonic()
    result_data = {
        "success": False,
        "tokens_in": 0,
        "tokens_out": 0,
        "cache_read": 0,
        "cache_creation": 0,
        "duration_s": 0,
        "cost_usd": 0,
        "result_text": "",
        "error": None,
    }

    try:
        env = {
            **os.environ,
            "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
        }
        proc = subprocess.Popen(
            [
                "claude", "-p",
                "--dangerously-skip-permissions",
                "--output-format", "stream-json",
                "--model", model,
                "--verbose",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )

        stdout, stderr = proc.communicate(input=prompt_text)
        result_data["duration_s"] = time.monotonic() - start

        # Parse streaming JSON — each line is a JSON object
        for line in stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue

            etype = event.get("type", "")

            # Collect assistant text output
            if etype == "assistant" and "message" in event:
                for block in event["message"].get("content", []):
                    if block.get("type") == "text":
                        result_data["result_text"] += block["text"]

            # Collect usage from the result event
            if etype == "result":
                result_data["cost_usd"] = event.get("total_cost_usd", 0)
                result_data["result_text"] = event.get("result", result_data["result_text"])
                result_data["success"] = True

                # modelUsage has cumulative per-model totals
                model_usage = event.get("modelUsage", {})
                tokens_in = 0
                tokens_out = 0
                cache_read = 0
                cache_creation = 0
                for model_stats in model_usage.values():
                    tokens_in += model_stats.get("inputTokens", 0)
                    tokens_out += model_stats.get("outputTokens", 0)
                    cache_read += model_stats.get("cacheReadInputTokens", 0)
                    cache_creation += model_stats.get("cacheCreationInputTokens", 0)
                result_data["tokens_in"] = tokens_in
                result_data["tokens_out"] = tokens_out
                result_data["cache_read"] = cache_read
                result_data["cache_creation"] = cache_creation

        if proc.returncode != 0 and not result_data["success"]:
            result_data["error"] = stderr.strip() or f"Claude exited with code {proc.returncode}"

    except FileNotFoundError:
        result_data["error"] = "Claude CLI not found. Is it installed and on PATH?"
    except Exception as e:
        result_data["error"] = str(e)

    return result_data


# ─── Token formatting ────────────────────────────────────────────────────────


def fmt_tokens(n: int) -> str:
    """Format token count in a human-readable way."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)


def fmt_duration(seconds: float) -> str:
    """Format seconds into human-readable duration."""
    if seconds < 60:
        return f"{seconds:.0f}s"
    minutes = int(seconds // 60)
    secs = int(seconds % 60)
    if minutes < 60:
        return f"{minutes}m {secs}s"
    hours = minutes // 60
    mins = minutes % 60
    return f"{hours}h {mins}m"


def fmt_cost(usd: float) -> str:
    """Format USD cost."""
    if usd < 0.01:
        return f"${usd:.4f}"
    return f"${usd:.2f}"


def print_running_totals(iteration: int, totals: dict) -> None:
    """Print running totals across all iterations."""
    print()
    info(f"Running totals ({iteration} iteration{'s' if iteration != 1 else ''}):")
    print(f"      Input:  {c(CYAN, fmt_tokens(totals['tokens_in']))} tokens")
    print(f"      Output: {c(CYAN, fmt_tokens(totals['tokens_out']))} tokens")
    print(f"      Cost:   {c(DIM, fmt_cost(totals['cost_usd']))}")
    print(f"      Time:   {fmt_duration(totals['duration_s'])}")
    print()


# ─── Prompt generation ─────────────────────────────────────────────────────────


def init_project() -> int:
    """Generate prompt files for loop usage."""
    files = {
        BUILD_PROMPT: BUILD_PROMPT_TEMPLATE,
        PLAN_PROMPT: PLAN_PROMPT_TEMPLATE,
        SPEC_PROMPT: SPEC_PROMPT_TEMPLATE,
    }

    for filename, content in files.items():
        path = Path(filename)
        if path.exists():
            warn(f"{filename} already exists, overwriting")
        path.write_text(content)
        success(f"Generated {filename}")

    print()
    info("Next steps:")
    info(f"  1. Review and customise the generated prompts")
    info(f"  2. Create specs/ directory with your application specifications")
    info(f"  3. Run: python loop.py plan")
    print()

    return 0


def has_uncommitted_changes() -> bool:
    """Return True if there are any staged, unstaged, or untracked changes."""
    staged = subprocess.run(["git", "diff", "--cached", "--quiet"], capture_output=True)
    unstaged = subprocess.run(["git", "diff", "--quiet"], capture_output=True)
    untracked = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard"],
        capture_output=True, text=True,
    )
    return staged.returncode != 0 or unstaged.returncode != 0 or bool(untracked.stdout.strip())


def parse_title_summary(text: str) -> tuple[str, str]:
    """Parse TITLE: and SUMMARY: from LLM output. Falls back gracefully."""
    title = ""
    summary = ""
    for line in text.strip().splitlines():
        if line.strip().upper().startswith("TITLE:"):
            title = line.strip()[6:].strip()
        elif line.strip().upper().startswith("SUMMARY:"):
            summary = line.strip()[8:].strip()
    # Fallback: use first line as title, rest as summary
    if not title:
        lines = text.strip().splitlines()
        title = lines[0].strip() if lines else "No summary"
        summary = " ".join(l.strip() for l in lines[1:]).strip() if len(lines) > 1 else ""
    return title, summary


def build_commit_message(
    mode: str,
    iteration: int,
    max_iterations: int,
    result_text: str,
    iter_result: dict,
    model: str,
    stop_reason: str = "",
) -> str:
    """Build a structured commit message with metrics."""
    label = mode.capitalize()

    title, summary = parse_title_summary(result_text)

    # Truncate title to keep commit subject readable
    if len(title) > 50:
        title = title[:47] + "..."

    subject = f"{label} ({iteration}/{max_iterations}) - {title}"

    parts = [subject, ""]

    if stop_reason:
        parts.append(stop_reason)
        parts.append("")

    # Summary
    if summary:
        parts.append("## Summary")
        parts.append("")
        parts.append(summary)
        parts.append("")

    # Metrics
    plan_tasks = parse_plan_tasks()
    parts.append("## Metrics")
    parts.append("")

    if plan_tasks["total"] > 0:
        pct = (plan_tasks["done"] / plan_tasks["total"]) * 100
        bar_width = 20
        filled = int(bar_width * plan_tasks["done"] / plan_tasks["total"])
        bar = "#" * filled + "-" * (bar_width - filled)
        blocked_str = f" / {plan_tasks['blocked']} blocked" if plan_tasks["blocked"] else ""
        parts.append(f"Progress: [{bar}] {pct:.0f}%")
        parts.append(f"Tasks:    {plan_tasks['done']} done / {plan_tasks['pending']} pending{blocked_str} / {plan_tasks['total']} total")

    parts.append(f"Duration: {fmt_duration(iter_result['duration_s'])}")
    parts.append(f"Model:    {model}")
    if iter_result.get("cost_usd"):
        parts.append(f"Cost:     {fmt_cost(iter_result['cost_usd'])}")
    parts.append(f"Tokens:   {fmt_tokens(iter_result['tokens_in'])} in / {fmt_tokens(iter_result['tokens_out'])} out / {fmt_tokens(iter_result.get('cache_read', 0))} cache")

    return "\n".join(parts)



# ─── Main loop ────────────────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="loop.py",
        description="Ralph Wiggum Loop — drive Claude Code iteratively.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            examples:
              %(prog)s init               Generate prompts and update .gitignore
              %(prog)s build              Build mode, 20 iterations (default)
              %(prog)s build -n 10        Build mode, max 10 iterations
              %(prog)s build --no-stop    Build mode, don't stop when plan is complete
              %(prog)s build --model opus    Build mode with opus model
              %(prog)s plan               Plan mode, 10 iterations (default)
              %(prog)s plan -n 5          Plan mode, 5 iterations
        """),
    )

    subparsers = parser.add_subparsers(dest="mode", help="Operating mode")

    # Build mode
    build_parser = subparsers.add_parser("build", help="Implement from IMPLEMENTATION_PLAN.md")
    build_parser.add_argument(
        "-n", "--max-iterations", type=int, default=BUILD_DEFAULT_ITERATIONS,
        help=f"Max iterations (default: {BUILD_DEFAULT_ITERATIONS})",
    )
    build_parser.add_argument(
        "--no-stop", action="store_true",
        help="Don't stop when all plan tasks are complete",
    )
    build_parser.add_argument(
        "--model", default="sonnet",
        help="Claude model to use (default: sonnet)",
    )

    # Plan mode
    plan_parser = subparsers.add_parser("plan", help="Generate/update IMPLEMENTATION_PLAN.md")
    plan_parser.add_argument(
        "-n", "--max-iterations", type=int, default=PLAN_DEFAULT_ITERATIONS,
        help=f"Max iterations (default: {PLAN_DEFAULT_ITERATIONS})",
    )
    plan_parser.add_argument(
        "--model", default="opus",
        help="Claude model to use (default: opus)",
    )

    # Init mode
    subparsers.add_parser(
        "init",
        help="Generate prompt files and update .gitignore",
    )

    args = parser.parse_args()

    if args.mode is None:
        parser.print_help()
        return 1

    # Handle init early — it doesn't need the loop infrastructure
    if args.mode == "init":
        return init_project()

    mode = args.mode
    max_iterations = args.max_iterations
    model = args.model
    stop_on_complete = mode == "build" and not getattr(args, "no_stop", False)

    prompt_file = PLAN_PROMPT if mode == "plan" else BUILD_PROMPT

    # ── Precondition checks ──────────────────────────────────────────────

    banner(f"Ralph Wiggum Loop — {mode.upper()} mode")

    section("Preflight Checks")

    # 1. Git repo
    if not check_git_repo():
        error("Not inside a git repository.")
        info("Run this from within a git project directory.")
        return 1
    success("Git repository detected")

    # 2. Clean worktree
    if not check_clean_worktree():
        print()
        error("Working tree is not clean.")
        info("Commit or stash your changes before starting the loop.")
        return 1
    success("Working tree is clean")

    # 3. Auto-compact
    if not check_auto_compact():
        return 1
    success("Auto-compact is not enabled")

    # 4. CLAUDE.md
    if not Path("CLAUDE.md").exists():
        error("CLAUDE.md not found.")
        info("Create a CLAUDE.md file before starting the loop.")
        return 1
    success("CLAUDE.md found")

    # 5. Prompt file
    if not check_prompt_file(prompt_file):
        return 1
    success(f"Prompt file found: {prompt_file}")

    # ── Show initial state ───────────────────────────────────────────────

    branch = get_git_branch()
    head = get_git_head()

    section("Configuration")
    info(f"Mode:       {c(BOLD, mode.upper())}")
    info(f"Model:      {c(BOLD, model)}")
    info(f"Branch:     {c(CYAN, branch)}")
    info(f"Head:       {c(DIM, head)}")
    info(f"Prompt:     {prompt_file}")
    info(f"Max iters:  {max_iterations}")
    if mode == "build":
        info(f"Stop on completion: {c(GREEN, 'yes') if stop_on_complete else c(DIM, 'no')}")

    # Show plan status if in build mode
    if mode == "build":
        plan_tasks = parse_plan_tasks()
        if plan_tasks["total"] > 0:
            section("Implementation Plan")
            print_plan_summary(plan_tasks)

            # Check for blocked tasks
            if plan_tasks["blocked"] > 0:
                print()
                error(f"{plan_tasks['blocked']} task(s) are blocked and need human intervention:")
                for task in plan_tasks["tasks"]:
                    if task["status"] == "blocked":
                        print(f"      {c(RED, '[B]')} {task['text']}")
                print()
                info("Resolve blocked tasks before starting build iterations.")
                return 1
        else:
            print()
            warn("No IMPLEMENTATION_PLAN.md found or no tasks in it.")
            info("Consider running in plan mode first.")

    # ── Run loop ─────────────────────────────────────────────────────────

    totals = {
        "tokens_in": 0,
        "tokens_out": 0,
        "cost_usd": 0,
        "duration_s": 0,
    }

    start_time = time.monotonic()
    iteration = 0
    consecutive_no_changes = 0

    try:
        while True:
            iteration += 1

            # Check iteration limit
            if iteration > max_iterations:
                print()
                success(f"Reached max iterations ({max_iterations}). Stopping.")
                break

            # Check if no changes for 2 consecutive iterations
            if consecutive_no_changes >= 2:
                print()
                warn("No changes detected in the last 2 iterations. Stopping.")
                break

            # In build mode, check if plan is complete or only blocked items remain
            if stop_on_complete and iteration > 1:
                plan_tasks = parse_plan_tasks()
                if plan_tasks["total"] > 0 and plan_tasks["pending"] == 0:
                    print()
                    if plan_tasks["blocked"] > 0:
                        banner("No actionable tasks remain")
                        print_plan_summary(plan_tasks)
                        warn(f"{plan_tasks['blocked']} task(s) blocked — needs human intervention.")
                    else:
                        banner("All tasks complete!")
                        print_plan_summary(plan_tasks)
                        success("Implementation plan is fully checked off.")
                    info("Stopping.")
                    break

            # ── Iteration header ─────────────────────────────────────────

            iter_label = f"Iteration {iteration} / {max_iterations}"

            print()
            print(c(CYAN, f"  {'─' * 50}"))
            print(c(BOLD, f"  {iter_label}  ({mode.upper()})  model={model}"))
            print(c(CYAN, f"  {'─' * 50}"))

            if mode == "build":
                plan_tasks = parse_plan_tasks()
                if plan_tasks["total"] > 0:
                    print_plan_summary(plan_tasks)

            # ── Run Claude ───────────────────────────────────────────────

            info("Running Claude...")
            print()

            iter_result = run_claude_iteration(prompt_file, model)

            if not iter_result["success"]:
                error(f"Claude iteration failed: {iter_result.get('error', 'unknown error')}")
                if iteration == 1:
                    return 1
                warn("Continuing to next iteration...")
                continue

            # Update totals
            totals["tokens_in"] += iter_result["tokens_in"]
            totals["tokens_out"] += iter_result["tokens_out"]
            totals["cost_usd"] += iter_result["cost_usd"]
            totals["duration_s"] += iter_result["duration_s"]

            # ── Build report & commit ─────────────────────────────────────

            msg = build_commit_message(mode, iteration, max_iterations, iter_result.get("result_text", ""), iter_result, model)

            # Print the same message that goes into the commit
            print()
            print(c(CYAN, f"  {'─' * 50}"))
            for line in msg.splitlines():
                print(f"  {line}")
            print(c(CYAN, f"  {'─' * 50}"))

            print_running_totals(iteration, totals)

            # Commit
            if has_uncommitted_changes():
                subprocess.run(["git", "add", "-A"], capture_output=True)
                subprocess.run(["git", "commit", "-m", msg], capture_output=True)
                consecutive_no_changes = 0
                success("Committed.")
            else:
                consecutive_no_changes += 1
                info(f"No changes to commit ({consecutive_no_changes} consecutive iteration{'s' if consecutive_no_changes != 1 else ''} without changes)")


    except KeyboardInterrupt:
        print()
        print()
        warn("Interrupted by user.")

    # ── Final summary ────────────────────────────────────────────────────

    wall_time = time.monotonic() - start_time

    banner("Loop Complete")

    info(f"Iterations completed: {c(BOLD, str(iteration - 1))}")
    info(f"Wall time: {fmt_duration(wall_time)}")

    section("Total Token Usage")
    print(f"      Input:  {c(CYAN, fmt_tokens(totals['tokens_in']))}")
    print(f"      Output: {c(CYAN, fmt_tokens(totals['tokens_out']))}")
    if totals["cost_usd"]:
        print(f"      Cost:   {c(DIM, fmt_cost(totals['cost_usd']))}")

    if mode == "build":
        plan_tasks = parse_plan_tasks()
        if plan_tasks["total"] > 0:
            section("Final Plan Status")
            print_plan_summary(plan_tasks)

    head_final = get_git_head()
    info(f"HEAD: {c(DIM, head_final)}")
    print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
