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
> 2. Read the E2E test files in `e2e/` and verify there is at least one E2E test for each acceptance criterion (AC) mentioned in the spec for this feature. If any AC lacks an E2E test, report it as a gap — do not pass QA until every AC has E2E coverage.
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
