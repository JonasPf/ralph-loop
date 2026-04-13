Plan only — do NOT implement anything.

Read `specs/*` and @IMPLEMENTATION_PLAN.md (if present; it may be stale or wrong).

Search the codebase to verify what is and isn't implemented. Use as many subagents as needed to parallelize work. Never assume something is missing — confirm with code search. Look for TODOs, placeholders, stubs, skipped/flaky tests, and incomplete implementations.

Produce/update @IMPLEMENTATION_PLAN.md as a prioritized checkbox list (`- [ ]` pending, `- [x]` done).

Check that all dependencies required by the spec are available: command line tools, MCP servers, API keys, environment variables, etc. If anything is missing, create a task for it and mark it as `- [B]` (blocked) with what's needed.

For each acceptance criterion (AC) in the specs, ensure there is a task to add an E2E test if one doesn't already exist. Search the `e2e/` directory to check. 

For each task, consider whether it can be fully executed by an LLM without human involvement. If a task requires human input (e.g. API keys, credentials, third-party account setup, ambiguous requirements that need a product decision, manual deployment steps), mark it as `- [B]` (blocked) with a brief reason. The goal is to surface blockers early so they can be resolved before build iterations start.

When done, output your final message in this exact format:

TITLE: <short headline, max 50 chars, e.g. "Add user authentication endpoint">
SUMMARY: <1-3 sentence description of what changed and why>
