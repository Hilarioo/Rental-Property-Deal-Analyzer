# /review

**Run the standard 8-agent pre-PR review in parallel, iterate until no BLOCKER/HIGH findings remain, then create a PR.**

Use this when a feature or fix is implemented and ready for validation. Automates the review → implement fixes → re-validate → PR-create loop.

## When to use

After you've completed a chunk of work and want multi-agent validation before opening a PR. Do not run on partial/in-progress work — agents assume the code is intended to be final.

## What it does

1. **Inspect the diff** — run `git diff main...HEAD --stat` to understand scope; confirm the branch is `feature/*`, `fix/*`, `docs/*`, or `chore/*`.

2. **Deploy 8 review agents in parallel** (single message, 8 Agent tool calls):
   - **Code Reviewer** — correctness, error handling, Python/JS type safety, calc.js ↔ app.py parity
   - **Security Engineer** — input validation, SSRF, scraper safeguards, secret handling, Flask route auth, prompt injection where LLM is involved
   - **Frontend Developer** — `index.html` + `calc.js` interactions, DOM safety, event wiring, localStorage persistence
   - **Backend Architect** — Flask route design, `app.py` structure, `batch/` pipeline, data flow, error boundaries
   - **Performance Benchmarker** — O(n²) patterns in batch scans, memory leaks, scan throughput, browser render cost on large result sets
   - **Accessibility Auditor** — WCAG 2.1 AA: focus, ARIA, keyboard, contrast, announcements on the deal analyzer UI
   - **UX Architect** — user flow, error/empty states, destructive-action confirmations (Clear All / Clear Red), verdict clarity
   - **DevOps Automator** — Dockerfile, `render.yaml`, Makefile targets, env/secrets, `requirements.txt` changes

   Each agent classifies findings as **BLOCKER / HIGH / MEDIUM / LOW** and ends with a verdict (PASS / FAIL / APPROVE / REQUEST CHANGES).

3. **Triage findings:**
   - **BLOCKER + HIGH, clearly-scoped:** implement automatically.
   - **BLOCKER + HIGH, judgment call:** pause and present via `/ask`.
   - **MEDIUM + LOW:** file as tracked follow-ups in `handoff/BACKLOG.md`. Do not block on these.

4. **Commit fixes** — one commit per iteration: `fix: resolve <N> agent review findings (iteration <N>)`.

5. **Re-deploy the 8 agents** on the updated diff to validate fixes and catch regressions.

6. **Iterate** until:
   - (a) Zero BLOCKER + HIGH findings across all 8 agents, OR
   - (b) Maximum 3 iterations reached (safety cap).

7. **Run final local validation:**
   - `make test-py` (pytest)
   - `make test-js` (node --test)
   - `make test-parity` (verdict JS↔Python parity — hard gate per Sprint 0)
   - If any touched Python file has obvious syntax risk: `venv/bin/python -m py_compile <file>`

8. **Create the PR** using `.github/pull_request_template.md`.

## Guardrails

- **Never bypasses hooks** (`--no-verify` is prohibited).
- **Pauses before destructive operations:** `git reset --hard`, `git push --force`, data deletions, any change to math constants in `spec/constants.json` without explicit approval.
- **Never ships math/threshold changes without the parity check passing** — see `handoff/USER_PROFILE.example.md` for the authoritative numbers.

## Manual override

If an agent finding is misguided, say `skip this finding: <description>` and the command continues past it.
