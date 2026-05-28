#!/usr/bin/env python3
"""Ralph Wiggum Loop — Python implementation.

Drives a coding agent (Claude Code, or pi via --agent pi) in a loop, feeding it
a prompt file each iteration. Supports plan mode (generate/update
IMPLEMENTATION_PLAN.md) and build mode (one task per iteration with QA).

Usage:
    loop.py [plan|build|init] [OPTIONS]
    loop.py --help
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
import time
from datetime import datetime
from pathlib import Path

# ─── Constants ────────────────────────────────────────────────────────────────

PLAN_PROMPT = "PROMPT_plan.md"
BUILD_PROMPT = "PROMPT_build.md"
QA_PROMPT = "PROMPT_qa.md"
FIX_PROMPT = "PROMPT_fix.md"
SPEC_PROMPT = "PROMPT_spec.md"
IMPLEMENTATION_PLAN = "IMPLEMENTATION_PLAN.md"
LOGS_ROOT = Path(".ralph/logs")

PLAN_DEFAULT_ITERATIONS = 10
BUILD_DEFAULT_ITERATIONS = 20
QA_DEFAULT_MAX_ATTEMPTS = 3

# ─── Prompt templates ────────────────────────────────────────────────────────

BUILD_PROMPT_TEMPLATE = """\
Read `specs/*` and @IMPLEMENTATION_PLAN.md. Work on the **topmost unchecked (`- [ ]`) task** in the plan — that is the task the orchestrator has selected for this iteration.

## Implementation

Read the relevant specs in `specs/*`. Search the codebase before assuming anything is missing.

Implement the item fully — no placeholders or stubs. Fix any failures including pre-existing ones.

Use TDD — write tests first, then implement to make them pass. Keep code DRY and lean.

Build, test, and lint locally. All must pass before reporting completion. A separate QA pass will run after you finish; do not try to QA your own work here.

## Rules

- Do **not** edit any marker in `IMPLEMENTATION_PLAN.md`. The QA pass flips `- [ ]` → `- [x]` on success; the orchestrator flips to `- [B]` on terminal failure.
- If you discover out-of-scope work (a real prerequisite, a missing dependency, follow-up scope), append a new `- [ ]` line for it **below the in-flight task** (or at the end of the plan). Never insert above the in-flight task — that would change which task is current.
- Update @CLAUDE.md only with operational knowledge (e.g. correct build commands). Keep it brief.

When done, output your final message in this exact format, on two separate lines:

TITLE: <short headline, max 50 chars, e.g. "Add user authentication endpoint">
SUMMARY: <1-3 sentence description of what changed and why>\
"""

PLAN_PROMPT_TEMPLATE = """\
Plan only — do NOT implement anything.

Read the following inputs, in this order:
1. `specs/*` — the source of truth for what should exist.
2. `@IMPLEMENTATION_PLAN.md` (if present; it may be stale or wrong).
3. Any `- [B]` lines in the existing plan with their attached reasons — these flag work that needs human attention.

Search the codebase to verify what is and isn't implemented. Never assume something is missing — confirm with code search. Look for TODOs, placeholders, stubs, skipped/flaky tests, and incomplete implementations.

Produce/update `@IMPLEMENTATION_PLAN.md` as a prioritized checkbox list (`- [ ]` pending, `- [x]` done, `- [B] — <reason>` blocked).

Check that all dependencies required by the spec are available: command line tools, MCP servers, API keys, environment variables, etc. If anything is missing, create a task for it and mark it as `- [B]` (blocked) with what's needed.

For each acceptance criterion (AC) in the specs, ensure there is a task to add an E2E test if one doesn't already exist. Tests must never be skipped.

For each task, consider whether it can be fully executed by an LLM without human involvement. If a task requires human input (e.g. API keys, credentials, third-party account setup, ambiguous requirements that need a product decision, manual deployment steps), mark it as `- [B]` (blocked) with a brief reason. The goal is to surface blockers early so they can be resolved before build iterations start.

**Sort `- [B]` blocked tasks toward the top of the list where possible.** Build mode stops when the next available task is blocked, so frontloading blockers gets them resolved before iteration starts. If a `- [B]` is genuinely non-blocking for everything below it, leave it where it is so build can keep going.

When done, output your final message in this exact format:

TITLE: <short headline, max 50 chars, e.g. "Add user authentication endpoint">
SUMMARY: <1-3 sentence description of what changed and why>\
"""

QA_PROMPT_TEMPLATE = """\
You are the QA verifier. The task being verified is:

    {TASK}

Your job is to verify that the implementation in the current worktree adequately covers the spec for this task.

Do **not** read the implementation source code. Only read:
- the relevant files in `specs/*`
- the test files
- `@CLAUDE.md` for operational commands (build, test, lint, run)
- `@IMPLEMENTATION_PLAN.md` to locate the task

Then:

1. For each acceptance criterion (AC) named in the spec for this feature, confirm there is at least one E2E test covering it. If any AC lacks an E2E test, that is a QA failure.
2. Run the project's build, tests, and linter. Any failure is a QA failure.
3. Start the app and smoke-test the new feature end to end (real requests, real responses). Failure to start, or visibly incorrect behavior, is a QA failure.

Plan-file rule:

- **On PASS**: flip the in-flight `- [ ]` line for the verified task to `- [x]` in `IMPLEMENTATION_PLAN.md`. That is the only edit you make to the plan.
- **On FAIL**: do not edit the plan at all.

If you spot out-of-scope work (something this task didn't claim to do but should exist), append a new `- [ ]` line for it below the in-flight task in `IMPLEMENTATION_PLAN.md`.

When done, your final message must be **exactly one** of the two forms below, on its own lines, with no other text after:

QA: PASS

— or —

QA: FAIL
ISSUES:
- <one-line issue>
- <one-line issue>
DETAILS:
<freeform multi-paragraph explanation>\
"""

FIX_PROMPT_TEMPLATE = """\
You are continuing a task another agent started. The code currently in the worktree is their work; a separate QA pass has reported the issues below. Address them.

## The task

    {TASK}

## QA report (verbatim)

{QA_REPORT}

## Diff stat of the prior attempt

{LAST_DIFF}

## What to do

Read the relevant specs in `specs/*` and the failing tests. Address each issue in the QA report. Do not start from scratch — extend or fix what is already in the worktree.

Build, test, and lint locally. All must pass before reporting completion. A QA pass will re-run after you finish.

## Rules

- Do **not** edit any marker in `IMPLEMENTATION_PLAN.md`.
- If you discover out-of-scope work, append a new `- [ ]` line for it below the in-flight task (or at end of the plan).
- Update @CLAUDE.md only with operational knowledge (e.g. correct build commands).

When done, output your final message in this exact format, on two separate lines:

TITLE: <short headline, max 50 chars>
SUMMARY: <1-3 sentence description of what you fixed and how>\
"""

SPEC_PROMPT_TEMPLATE = """\
Help me write or update a specification for a software project. If there are already files in ./specs, study them first. Then interview me in detail, asking one focused question at a time about anything that needs clarification. Be very in-depth and continue interviewing me until you have all the information needed. Then create or update the specifications in ./specs/.

Rules for writing the specification:

0. Keep the specification as concise and succinct as possible. Avoid bloat.
1. Do not refer to the current state of the project, instead write or update the specs so they are self-contained and can be read and understood without prior knowledge of the project.
2. A specification is not a plan, do not include specific impmlementation steps.
3. Use OpenAPI 3.0 to design APIs.
4. Use single page html for visual content, otherwise use markdown
5. Use mermaid entity relation diagram to document databases and other data models.
6. Use mermaid flowcharts or message sequence diagrams to document data flows and system interaction.
7. Every feature must be documented with acceptance criteria.
8. Include a requirement for concise and succinct documentation and a getting started guide in README.md.
9. Include a requirement for linting the code base.
10. Include a requirement for e2e testing.

Once you've written the specification, study it again and point out any inconsistencies, gaps or blindspots. If there are any lets resolve them together.\
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

COLOR = bool(sys.stdout.isatty()) and not os.environ.get("NO_COLOR")


def c(code: str, text: str) -> str:
    return f"{code}{text}{RESET}" if COLOR else text


def banner(text: str) -> None:
    width = max(len(text) + 4, 50)
    print()
    print(c(CYAN, "=" * width))
    print(c(CYAN, f"  {text}"))
    print(c(CYAN, "=" * width))
    print()


def section(title: str) -> None:
    print()
    print(f"  {c(BOLD, title)}")
    print(f"  {c(DIM, '-' * len(title))}")


def info(msg: str) -> None:
    print(f"  {c(BLUE, '>')} {msg}")


def success(msg: str) -> None:
    print(f"  {c(GREEN, '+')} {msg}")


def warn(msg: str) -> None:
    print(f"  {c(YELLOW, '!')} {msg}")


def error(msg: str) -> None:
    print(f"  {c(RED, 'x')} {msg}")


# ─── Git helpers ──────────────────────────────────────────────────────────────


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], capture_output=True, text=True)


def git_head() -> str:
    r = _git("rev-parse", "--short", "HEAD")
    return r.stdout.strip() if r.returncode == 0 else "unknown"


def git_branch() -> str:
    r = _git("branch", "--show-current")
    return r.stdout.strip() if r.returncode == 0 else "unknown"


def is_git_repo() -> bool:
    return _git("rev-parse", "--is-inside-work-tree").returncode == 0


def has_uncommitted_changes() -> bool:
    """True if there's anything to commit (ignored files don't count)."""
    r = _git("status", "--porcelain")
    return bool(r.stdout.strip())


def ensure_gitignore() -> None:
    """Ensure `.ralph/` is in .gitignore."""
    gi = Path(".gitignore")
    entry = ".ralph/"
    if gi.exists():
        lines = [l.rstrip() for l in gi.read_text().splitlines()]
        if entry in lines:
            return
        text = gi.read_text()
        if not text.endswith("\n"):
            text += "\n"
        gi.write_text(text + entry + "\n")
    else:
        gi.write_text(entry + "\n")


# ─── Plan parsing ─────────────────────────────────────────────────────────────


_PLAN_LINE_RE = re.compile(r'^[-*]\s+\[([ xXbB])\]\s+(.*?)\s*$')


def parse_plan_tasks(path: str = IMPLEMENTATION_PLAN) -> dict:
    """Return task statistics: total, done, pending, blocked, tasks (list)."""
    result = {"total": 0, "done": 0, "pending": 0, "blocked": 0, "tasks": []}
    p = Path(path)
    if not p.exists():
        return result
    for line in p.read_text().splitlines():
        m = _PLAN_LINE_RE.match(line.strip())
        if not m:
            continue
        marker = m.group(1).lower()
        text = m.group(2).strip()
        result["total"] += 1
        if marker == 'x':
            result["done"] += 1
            status = "done"
        elif marker == 'b':
            result["blocked"] += 1
            status = "blocked"
        else:
            result["pending"] += 1
            status = "pending"
        result["tasks"].append({"text": text, "status": status})
    return result


def next_pending_or_blocker(path: str = IMPLEMENTATION_PLAN) -> tuple[str, str]:
    """Walk the plan top to bottom, return the first non-`[x]` task.

    Returns ('pending', text) for a `- [ ]` row, ('blocker', text) for a `- [B]`
    row, or ('done', '') when nothing remains. The `text` for a blocker
    includes any ` — reason` suffix as written in the file.
    """
    p = Path(path)
    if not p.exists():
        return ('done', '')
    for line in p.read_text().splitlines():
        m = _PLAN_LINE_RE.match(line.strip())
        if not m:
            continue
        marker = m.group(1).lower()
        if marker == 'x':
            continue
        text = m.group(2).strip()
        if marker == ' ':
            return ('pending', text)
        if marker == 'b':
            return ('blocker', text)
    return ('done', '')


def _flip_pending_marker(task_text: str, new_marker: str, suffix: str = "") -> bool:
    """Flip the `- [ ]` line whose body is `task_text` to `- [<new_marker>] …`.

    Orchestrator-only writer for plan markers. Used for:
      - PASS fallback (`new_marker='x'`) if the QA agent forgot to flip.
      - Terminal QA fail (`new_marker='B'` + reason suffix).
    Byte-exact match is always expected — the caller just read the text from
    the plan. Returns False (with a stderr warning) if no match is found.
    """
    plan = Path(IMPLEMENTATION_PLAN)
    if not plan.exists():
        print(f"  ! flip_marker: {IMPLEMENTATION_PLAN} not found", file=sys.stderr)
        return False
    pattern = re.compile(
        r'^([-*])(\s+)\[ \](\s+)' + re.escape(task_text) + r'(\s*)$',
        re.MULTILINE,
    )
    new_text, n = pattern.subn(
        lambda m: f"{m.group(1)}{m.group(2)}[{new_marker}]{m.group(3)}{task_text}{suffix}",
        plan.read_text(),
        count=1,
    )
    if n == 0:
        print(f"  ! flip_marker: could not find: {task_text[:80]!r}", file=sys.stderr)
        return False
    plan.write_text(new_text)
    return True


def mark_task_done(task_text: str) -> bool:
    return _flip_pending_marker(task_text, "x")


def mark_task_blocked(task_text: str, reason: str) -> bool:
    suffix = f" — {reason}"
    if len(suffix) > 200:
        suffix = suffix[:197] + "..."
    return _flip_pending_marker(task_text, "B", suffix)


def print_plan_summary(tasks: dict) -> None:
    if tasks["total"] == 0:
        info("No tasks found in IMPLEMENTATION_PLAN.md")
        return
    pct = (tasks["done"] / tasks["total"]) * 100
    blocked = f" / {c(RED, str(tasks['blocked']))} blocked" if tasks["blocked"] else ""
    info(f"Tasks: {c(GREEN, str(tasks['done']))} done / {c(YELLOW, str(tasks['pending']))} pending{blocked} / {tasks['total']} total ({pct:.0f}%)")


# ─── Agent invocation ────────────────────────────────────────────────────────


def _parse_claude_log(log_path: Path) -> dict:
    """Read a claude stream-json log; return result_text + token totals.

    The terminal `result` event carries `.result` (final assistant text) and
    `.modelUsage` (per-model token counts). Everything else in the stream is
    just thinking/tool transcripts we don't need to interpret.
    """
    result_text = ""
    tokens_in = tokens_out = 0
    found_result = False
    if not log_path.exists():
        return {"result_text": "", "tokens_in": 0, "tokens_out": 0, "found_result": False}
    with open(log_path) as f:
        for line in f:
            s = line.strip()
            if not s:
                continue
            try:
                ev = json.loads(s)
            except json.JSONDecodeError:
                continue
            if ev.get("type") == "result":
                found_result = True
                result_text = ev.get("result", "") or result_text
                for mu in ev.get("modelUsage", {}).values():
                    tokens_in += mu.get("inputTokens", 0)
                    tokens_out += mu.get("outputTokens", 0)
    return {
        "result_text": result_text,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "found_result": found_result,
    }


def _parse_pi_log(log_path: Path) -> dict:
    """Aggregate text_delta events from a pi `--mode json` log into a string.

    Pi has no terminal "result" event and its token/cost fields aren't yet
    documented — see the TODO in CLAUDE.md.
    """
    result_text = ""
    if not log_path.exists():
        return {"result_text": "", "tokens_in": 0, "tokens_out": 0, "found_result": False}
    with open(log_path) as f:
        for line in f:
            s = line.strip()
            if not s:
                continue
            try:
                ev = json.loads(s)
            except json.JSONDecodeError:
                continue
            if ev.get("type") == "message_update":
                ame = ev.get("assistantMessageEvent", {})
                if ame.get("type") == "text_delta":
                    result_text += ame.get("delta", "")
    return {"result_text": result_text, "tokens_in": 0, "tokens_out": 0, "found_result": bool(result_text)}


def run_agent(
    prompt_text: str,
    log_path: Path,
    model: str,
    agent: str,
) -> dict:
    """Spawn the agent CLI with stdout streamed straight to `log_path`.

    No event loop, no live terminal output: the user can `tail -f` the log file
    in another terminal if they want to watch. After the process exits, the log
    is parsed to extract the agent's final assistant text + token totals.

    Returns: {success, tokens_in, tokens_out, duration_s, result_text, error}.
    """
    if agent == "pi":
        cmd = ["pi", "-p", prompt_text, "--mode", "json", "--model", model]
        env = {**os.environ, "PI_SKIP_VERSION_CHECK": "1"}
        stdin_input = None
    else:
        cmd = [
            "claude", "-p",
            "--permission-mode", "acceptEdits",
            "--output-format", "stream-json",
            "--model", model,
            "--verbose",
        ]
        env = {**os.environ, "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1"}
        stdin_input = prompt_text

    out = {
        "success": False, "tokens_in": 0, "tokens_out": 0,
        "duration_s": 0, "result_text": "", "error": None,
    }

    start = time.monotonic()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(log_path, "w") as logf:
            proc = subprocess.run(
                cmd,
                input=stdin_input,
                stdout=logf,
                stderr=subprocess.PIPE,
                text=True,
                env=env,
            )
    except FileNotFoundError:
        out["duration_s"] = time.monotonic() - start
        out["error"] = f"{agent} CLI not found. Is it installed and on PATH?"
        return out

    out["duration_s"] = time.monotonic() - start

    parsed = (_parse_pi_log if agent == "pi" else _parse_claude_log)(log_path)
    out["result_text"] = parsed["result_text"]
    out["tokens_in"] = parsed["tokens_in"]
    out["tokens_out"] = parsed["tokens_out"]

    # Claude: trust the `result` event. Pi: trust the exit code.
    if agent == "claude":
        out["success"] = parsed["found_result"] and proc.returncode == 0
    else:
        out["success"] = proc.returncode == 0

    if not out["success"]:
        stderr = (proc.stderr or "").strip()
        out["error"] = stderr or f"{agent} exited with code {proc.returncode}"

    return out


# ─── Prompt rendering / output parsing ───────────────────────────────────────


def render_prompt(template_path: str, **vars: str) -> str:
    """Literal `{KEY}` substitution. Placeholder values may contain braces."""
    text = Path(template_path).read_text()
    for key, value in vars.items():
        text = text.replace("{" + key + "}", value)
    return text


def parse_title_summary(text: str) -> tuple[str, str]:
    """Pull TITLE: and SUMMARY: lines. Defaults to ('', '') if missing."""
    title = ""
    summary = ""
    for line in text.strip().splitlines():
        s = line.strip()
        u = s.upper()
        if u.startswith("TITLE:"):
            title = s[6:].strip()
        elif u.startswith("SUMMARY:"):
            summary = s[8:].strip()
    return title, summary


def parse_qa_result(text: str) -> tuple[str, str]:
    """Return (verdict, report) where verdict is 'PASS', 'FAIL', or 'UNKNOWN'.

    `report` is everything from the first `QA:` line to the end of the message;
    it's what we hand to the fix agent verbatim.
    """
    lines = text.splitlines()
    for idx, line in enumerate(lines):
        s = line.strip()
        if s.upper().startswith("QA:"):
            tail = s[3:].strip().upper().split()
            verdict = tail[0] if tail else ""
            if verdict not in ("PASS", "FAIL"):
                verdict = "UNKNOWN"
            return verdict, "\n".join(lines[idx:]).strip()
    return "UNKNOWN", text.strip()


def extract_first_issue(qa_report: str) -> str:
    """The first `- ...` bullet under ISSUES:, used as the `[B]` reason."""
    in_issues = False
    for line in qa_report.splitlines():
        s = line.strip()
        if not in_issues and s.upper().startswith("ISSUES:"):
            in_issues = True
            continue
        if in_issues:
            if s.startswith("- "):
                return s[2:].strip()
            if s.upper().startswith(("DETAILS:", "QA:")):
                break
    for line in qa_report.splitlines():
        s = line.strip()
        if s and not s.upper().startswith("QA:"):
            return s[:160]
    return "QA failed"


# ─── Formatting helpers ──────────────────────────────────────────────────────


def fmt_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)


def fmt_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.0f}s"
    m, s = divmod(int(seconds), 60)
    if m < 60:
        return f"{m}m {s}s"
    h, m = divmod(m, 60)
    return f"{h}h {m}m"


# ─── QA cycle ─────────────────────────────────────────────────────────────────


def _git_diff_stat() -> str:
    r = _git("diff", "--stat", "HEAD")
    text = (r.stdout or "").strip()
    if not text:
        return "(no changes vs HEAD)"
    if len(text) > 4000:
        text = text[:4000] + "\n... (truncated)"
    return text


def _sum_metrics(dest: dict, src: dict) -> None:
    for k in ("tokens_in", "tokens_out", "duration_s"):
        dest[k] = dest.get(k, 0) + src.get(k, 0)


def run_qa_cycle(
    task: str,
    model: str,
    agent: str,
    max_attempts: int,
    iter_log_dir: Path,
) -> dict:
    """One task end-to-end: impl → QA → (fix → QA){0..N-1}.

    `task` is the topmost `- [ ]` text the orchestrator picked. Returns:
      outcome: 'pass' | 'blocked' | 'agent_error'
      attempts: total impl+fix invocations (does not count QA calls)
      title, summary: from the latest impl-or-fix call
      last_qa_report, first_issue
      totals: aggregated tokens/duration across all calls
      error: brief message on agent_error
    """
    totals = {"tokens_in": 0, "tokens_out": 0, "duration_s": 0}
    result = {
        "outcome": "agent_error", "attempts": 0,
        "title": "", "summary": "",
        "last_qa_report": "", "first_issue": "",
        "totals": totals, "error": None,
    }

    info(f"Running {agent} (impl)…")
    impl = run_agent(
        Path(BUILD_PROMPT).read_text(),
        iter_log_dir / "impl.jsonl",
        model, agent,
    )
    _sum_metrics(totals, impl)
    if not impl["success"]:
        result["error"] = impl.get("error") or "impl call failed"
        return result
    result["attempts"] = 1
    result["title"], result["summary"] = parse_title_summary(impl.get("result_text", ""))

    for attempt in range(1, max_attempts + 1):
        info(f"Running {agent} (qa, attempt {attempt}/{max_attempts})…")
        qa = run_agent(
            render_prompt(QA_PROMPT, TASK=task),
            iter_log_dir / f"qa.{attempt}.jsonl",
            model, agent,
        )
        _sum_metrics(totals, qa)
        if not qa["success"]:
            result["error"] = qa.get("error") or "qa call failed"
            return result

        verdict, qa_report = parse_qa_result(qa.get("result_text", ""))
        result["last_qa_report"] = qa_report
        result["first_issue"] = extract_first_issue(qa_report)

        if verdict == "PASS":
            result["outcome"] = "pass"
            return result

        if attempt >= max_attempts:
            break

        info(f"QA: {verdict}. Running {agent} (fix, attempt {attempt}/{max_attempts - 1})…")
        fix = run_agent(
            render_prompt(
                FIX_PROMPT,
                TASK=task,
                QA_REPORT=qa_report,
                LAST_DIFF=_git_diff_stat(),
            ),
            iter_log_dir / f"fix.{attempt}.jsonl",
            model, agent,
        )
        _sum_metrics(totals, fix)
        result["attempts"] += 1
        if not fix["success"]:
            result["error"] = fix.get("error") or "fix call failed"
            return result
        new_title, new_summary = parse_title_summary(fix.get("result_text", ""))
        if new_title:
            result["title"] = new_title
        if new_summary:
            result["summary"] = new_summary

    result["outcome"] = "blocked"
    return result


# ─── Commit messages ─────────────────────────────────────────────────────────


def build_commit_message(
    mode: str,
    iteration: int,
    max_iterations: int,
    title: str,
    summary: str,
    iter_metrics: dict,
    model: str,
    agent: str,
    qa_attempts: int = 0,
    qa_outcome: str = "",
) -> str:
    label = mode.capitalize()
    title = title or "(no title)"
    if len(title) > 50:
        title = title[:47] + "..."
    subject = f"{label} ({iteration}/{max_iterations}) - {title}"
    parts = [subject, ""]

    if summary:
        parts += ["## Summary", "", summary, ""]

    if qa_attempts > 0 and qa_outcome:
        fix_attempts = max(0, qa_attempts - 1)
        parts += [
            f"## QA: {qa_outcome} ({qa_attempts} attempt{'s' if qa_attempts != 1 else ''}, 1 impl + {fix_attempts} fix)",
            "",
        ]

    parts += ["## Metrics", ""]
    plan_tasks = parse_plan_tasks()
    if plan_tasks["total"] > 0:
        pct = (plan_tasks["done"] / plan_tasks["total"]) * 100
        blocked = f", {plan_tasks['blocked']} blocked" if plan_tasks["blocked"] else ""
        parts.append(f"Tasks:    {plan_tasks['done']}/{plan_tasks['total']} done ({pct:.0f}%), {plan_tasks['pending']} pending{blocked}")
    parts.append(f"Duration: {fmt_duration(iter_metrics['duration_s'])}")
    parts.append(f"Agent:    {agent}")
    parts.append(f"Model:    {model}")
    parts.append(f"Tokens:   {fmt_tokens(iter_metrics['tokens_in'])} in / {fmt_tokens(iter_metrics['tokens_out'])} out")
    return "\n".join(parts)


# ─── init mode ───────────────────────────────────────────────────────────────


def init_project() -> int:
    files = {
        BUILD_PROMPT: BUILD_PROMPT_TEMPLATE,
        PLAN_PROMPT: PLAN_PROMPT_TEMPLATE,
        QA_PROMPT: QA_PROMPT_TEMPLATE,
        FIX_PROMPT: FIX_PROMPT_TEMPLATE,
        SPEC_PROMPT: SPEC_PROMPT_TEMPLATE,
    }
    for filename, content in files.items():
        path = Path(filename)
        if path.exists():
            warn(f"{filename} already exists, overwriting")
        path.write_text(content)
        success(f"Generated {filename}")
    ensure_gitignore()
    success("Ensured `.ralph/` is in .gitignore")
    print()
    info("Next steps:")
    info("  1. Review and customise the generated prompts")
    info("  2. Create specs/ directory with your application specifications")
    info("  3. Run: python loop.py plan")
    print()
    return 0


# ─── Main ─────────────────────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="loop.py",
        description="Ralph Wiggum Loop — drive a coding agent iteratively.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            examples:
              %(prog)s init               Generate prompt files + .gitignore
              %(prog)s build              Build mode, 20 iterations (default)
              %(prog)s build -n 10        Build mode, max 10 iterations
              %(prog)s build --model opus    Build mode with opus model
              %(prog)s plan               Plan mode, 10 iterations (default)
              %(prog)s plan -n 5          Plan mode, 5 iterations
        """),
    )
    sub = parser.add_subparsers(dest="mode", help="Operating mode")

    bp = sub.add_parser("build", help="Implement from IMPLEMENTATION_PLAN.md")
    bp.add_argument("-n", "--max-iterations", type=int, default=BUILD_DEFAULT_ITERATIONS)
    bp.add_argument("--model", default="sonnet")
    bp.add_argument("--agent", choices=["claude", "pi"], default="claude")
    bp.add_argument("--qa-max-attempts", type=int, default=QA_DEFAULT_MAX_ATTEMPTS,
                    help=f"Max impl+fix attempts per task before marking `- [B]` (default: {QA_DEFAULT_MAX_ATTEMPTS}, min 1)")

    pp = sub.add_parser("plan", help="Generate/update IMPLEMENTATION_PLAN.md")
    pp.add_argument("-n", "--max-iterations", type=int, default=PLAN_DEFAULT_ITERATIONS)
    pp.add_argument("--model", default="opus")
    pp.add_argument("--agent", choices=["claude", "pi"], default="claude")

    sub.add_parser("init", help="Generate prompt files and update .gitignore")

    args = parser.parse_args()
    if args.mode is None:
        parser.print_help()
        return 1
    if args.mode == "init":
        return init_project()

    mode = args.mode
    max_iterations = args.max_iterations
    model = args.model
    agent = args.agent
    qa_max_attempts = getattr(args, "qa_max_attempts", QA_DEFAULT_MAX_ATTEMPTS)

    if mode == "build" and qa_max_attempts < 1:
        error("--qa-max-attempts must be at least 1.")
        return 1

    required_prompts = (
        [BUILD_PROMPT, QA_PROMPT, FIX_PROMPT] if mode == "build" else [PLAN_PROMPT]
    )

    # ── Preflight ────────────────────────────────────────────────────────
    banner(f"Ralph Wiggum Loop — {mode.upper()} mode")
    section("Preflight Checks")

    if not is_git_repo():
        error("Not inside a git repository.")
        return 1
    success("Git repository detected")

    if has_uncommitted_changes():
        error("Working tree is not clean. Commit or stash first.")
        return 1
    success("Working tree is clean")

    if shutil.which(agent) is None:
        error(f"{agent} CLI not found. Is it installed and on PATH?")
        return 1
    success(f"{agent} CLI found")

    for pf in required_prompts:
        if not Path(pf).exists():
            error(f"Prompt file not found: {pf}")
            info("Run `loop.py init` to (re)generate prompt files.")
            return 1
        success(f"Prompt file found: {pf}")

    ensure_gitignore()

    # ── Configuration block ─────────────────────────────────────────────
    section("Configuration")
    info(f"Mode:       {c(BOLD, mode.upper())}")
    info(f"Agent:      {c(BOLD, agent)}")
    info(f"Model:      {c(BOLD, model)}")
    info(f"Branch:     {c(CYAN, git_branch())}")
    info(f"Head:       {c(DIM, git_head())}")
    info(f"Prompts:    {', '.join(required_prompts)}")
    info(f"Max iters:  {max_iterations}")
    if mode == "build":
        info(f"QA attempts: {qa_max_attempts} (impl + up to {qa_max_attempts - 1} fix)")

    # Preflight blocker check — only relevant in build mode.
    if mode == "build":
        kind, task = next_pending_or_blocker()
        if kind == "done":
            section("Implementation Plan")
            warn("No tasks in plan, or all already done.")
        elif kind == "blocker":
            section("Implementation Plan")
            print_plan_summary(parse_plan_tasks())
            print()
            error(f"Next task is blocked — resolve before running build:")
            print(f"      {c(RED, '[B]')} {task}")
            return 1

    plan_tasks = parse_plan_tasks()
    if plan_tasks["total"] > 0:
        section("Implementation Plan")
        print_plan_summary(plan_tasks)

    # ── Run loop ────────────────────────────────────────────────────────
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_log_dir = LOGS_ROOT / run_id
    run_log_dir.mkdir(parents=True, exist_ok=True)
    info(f"Logs:       {run_log_dir}/iter-NN/")

    totals = {"tokens_in": 0, "tokens_out": 0, "duration_s": 0}
    start_time = time.monotonic()
    iteration = 0
    consecutive_no_changes = 0

    try:
        while True:
            iteration += 1
            if iteration > max_iterations:
                print()
                success(f"Reached max iterations ({max_iterations}). Stopping.")
                break

            if consecutive_no_changes >= 2:
                print()
                warn("No changes in the last 2 iterations. Stopping.")
                break

            # Stop condition: topmost non-done is blocker or nothing left.
            if mode == "build":
                kind, task = next_pending_or_blocker()
                if kind == "done":
                    print()
                    banner("All tasks complete!")
                    print_plan_summary(parse_plan_tasks())
                    break
                if kind == "blocker":
                    print()
                    banner("Next task is blocked — human intervention needed")
                    print(f"      {c(RED, '[B]')} {task}")
                    break

            iter_label = f"Iteration {iteration} / {max_iterations}"
            print()
            print(c(CYAN, f"  {'─' * 50}"))
            print(c(BOLD, f"  {iter_label}  ({mode.upper()})  agent={agent} model={model}"))
            print(c(CYAN, f"  {'─' * 50}"))
            if mode == "build":
                print_plan_summary(parse_plan_tasks())
                info(f"Task: {task[:100]}")

            iter_log_dir = run_log_dir / f"iter-{iteration:02d}"
            iter_log_dir.mkdir(parents=True, exist_ok=True)
            print()

            qa_attempts = 0
            qa_outcome = ""

            if mode == "build":
                cycle = run_qa_cycle(task, model, agent, qa_max_attempts, iter_log_dir)
                metrics = cycle["totals"]
                qa_attempts = cycle["attempts"]

                if cycle["outcome"] == "agent_error":
                    error(f"{agent} call failed: {cycle.get('error', 'unknown error')}")
                    if iteration == 1:
                        return 1
                    warn("Continuing to next iteration...")
                    continue

                if cycle["outcome"] == "pass":
                    qa_outcome = "PASS"
                    # Belt-and-braces: if the QA agent forgot to flip the
                    # marker, do it ourselves so the next iteration doesn't
                    # repeat the same task.
                    still_pending, still_text = next_pending_or_blocker()
                    if still_pending == "pending" and still_text == task:
                        warn("QA passed but did not flip the marker — flipping now.")
                        mark_task_done(task)
                    success(f"Marked task done: {task[:60]}")
                else:  # blocked
                    qa_outcome = "BLOCKED"
                    reason = cycle.get("first_issue") or "QA failed"
                    if mark_task_blocked(task, reason):
                        warn(f"Marked task blocked: {task[:60]} — {reason}")
                title = cycle["title"]
                summary = cycle["summary"]
            else:
                info(f"Running {agent} (plan)…")
                plan_log = iter_log_dir / "plan.jsonl"
                res = run_agent(
                    Path(PLAN_PROMPT).read_text(),
                    plan_log, model, agent,
                )
                metrics = {
                    "tokens_in": res["tokens_in"],
                    "tokens_out": res["tokens_out"],
                    "duration_s": res["duration_s"],
                }
                if not res["success"]:
                    error(f"{agent} iteration failed: {res.get('error', 'unknown error')}")
                    if iteration == 1:
                        return 1
                    warn("Continuing to next iteration...")
                    continue
                title, summary = parse_title_summary(res.get("result_text", ""))

            for k in totals:
                totals[k] += metrics[k]

            msg = build_commit_message(
                mode, iteration, max_iterations,
                title, summary, metrics, model, agent,
                qa_attempts=qa_attempts, qa_outcome=qa_outcome,
            )

            print()
            print(c(CYAN, f"  {'─' * 50}"))
            for line in msg.splitlines():
                print(f"  {line}")
            print(c(CYAN, f"  {'─' * 50}"))
            print()
            info(f"Totals: {fmt_tokens(totals['tokens_in'])} in / {fmt_tokens(totals['tokens_out'])} out / {fmt_duration(totals['duration_s'])}")

            if has_uncommitted_changes():
                _git("add", "-A")
                _git("commit", "-m", msg)
                sha = git_head()
                (iter_log_dir / "commit.txt").write_text(f"{sha} {msg.splitlines()[0]}\n")
                success(f"Committed {sha}.")
                consecutive_no_changes = 0
            else:
                consecutive_no_changes += 1
                info(f"No changes ({consecutive_no_changes} consecutive iteration{'s' if consecutive_no_changes != 1 else ''} without changes)")

    except KeyboardInterrupt:
        print()
        print()
        warn("Interrupted by user.")

    # ── Final summary ───────────────────────────────────────────────────
    wall = time.monotonic() - start_time
    banner("Loop Complete")
    info(f"Iterations completed: {c(BOLD, str(iteration - 1))}")
    info(f"Wall time: {fmt_duration(wall)}")
    section("Total Token Usage")
    print(f"      Input:  {c(CYAN, fmt_tokens(totals['tokens_in']))}")
    print(f"      Output: {c(CYAN, fmt_tokens(totals['tokens_out']))}")
    if mode == "build":
        plan_tasks = parse_plan_tasks()
        if plan_tasks["total"] > 0:
            section("Final Plan Status")
            print_plan_summary(plan_tasks)
    info(f"HEAD: {c(DIM, git_head())}")
    info(f"Logs: {run_log_dir}/")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
