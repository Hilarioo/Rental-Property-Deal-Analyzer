# /ship-done

**After a PR is merged, update docs + push to main + present the next batch of work options.**

Use this right after merging a PR. Replaces the manual "update the docs and push that to main with what was completed and provide next batch of tasks" prompt.

## When to use

Immediately after merging a PR. One invocation per merge — don't batch.

## What it does

1. **Syncs local with main:**
   ```
   git checkout main
   git pull origin main
   ```

2. **Identifies what shipped** — inspects the last merge commit and recent commits ahead of the previous state.

3. **Updates documentation based on the merged PR:**
   - **`handoff/CHANGELOG.md`** — add a new entry at the top with PR number, one-line outcome, and any user-visible behavior changes.
   - **`handoff/SPRINT_PLAN.md`** — move the completed task from In Progress to Done, or remove the row entirely if the whole sprint completed.
   - **`handoff/BACKLOG.md`** — remove items the PR resolved; add any tracked follow-ups (MEDIUM/LOW findings from `/review`).
   - **`handoff/TECHNICAL_ASSESSMENT.md`** / **`handoff/ACCEPTANCE_CRITERIA.md`** — if the PR flipped a verdict gate or closed an acceptance item, update the relevant row.
   - **`handoff/ADR-XXX-*.md`** — if the PR warranted a new architectural decision (e.g. math/threshold change, batch-pipeline shape change), drafts a new ADR (stops for approval before committing).
   - **`README.md`** / **`RUN_ME.md`** — only if user-facing setup or run instructions changed.

4. **Commits the doc updates** on main with a `docs:` message.

5. **Pushes to main.**

6. **Presents next-batch options** — based on `handoff/SPRINT_PLAN.md` (current sprint remaining tasks) and `handoff/BACKLOG.md`. Formats as:

   ```
   📦 Shipped in PR #<N>: <title>
   📄 Docs updated: <list of files>
   🚀 Pushed to main: <commit>

   Next batch options:
   1. **Continue current sprint** — <next task from SPRINT_PLAN.md>
   2. **High-priority backlog** — <top 1–3 items from BACKLOG.md>
   3. **Roadmap / ADR follow-ups** — <top 1 item>

   Which direction?
   ```

## Guardrails

- **Never force-pushes to main.** If main is ahead for any reason, fetch + inspect and present for user decision.
- **Never rewrites merged commits.**
- **Pauses before adding a new ADR** — if the merged PR introduced a non-obvious architectural decision, drafts the ADR and asks for approval before committing it.
- **Never bypasses the Sprint 0 test gate** — if `make test-parity` fails on main post-merge, stop and flag, do not "fix forward" silently.
