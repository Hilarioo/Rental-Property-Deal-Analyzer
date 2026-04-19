# BACKLOG — Post-V1 Hardening

Owner: Jose H Gonzalez
Updated: 2026-04-18
Source: consolidated from 5 audit lanes (security, code health, performance, architecture, docs). 9 audits still outstanding — backlog expected to grow in Sprint 10+.

Scope rule: Sprints 7A/7B/7C land THIS week. Sprint 8+ queued. Do not mix lanes.

---

## Sprint 7A — Security hotfix (THIS WEEK, ~3h)

Hard gate: nothing else ships until 7A is green. All four are regressions or unbounded-trust paths.

### 7A-1. Scrub `str(exc)` from `/api/analyze-ai` LM/Ollama branches
- Rationale: H-1. Regression from prior M1/M3 fix. Raw exception strings leak stack/path/config to client. Violates the error-sanitization invariant we already established.
- Files: `app.py:1702-1706`, `app.py:1729-1736`
- Fix: route through existing `_safe_error()` helper; log full exc server-side, return generic `{"error":"upstream_llm_failed"}` with 502.
- Effort: 20 min
- Deps: none
- Severity: HIGH

### 7A-2. Fix `_detect_source` suffix-match SSRF vector
- Rationale: H-2. `hostname.endswith("redfin.com")` matches `evilredfin.com`. On remote deploy, attacker-controlled hostname routes through our trusted-source branch. Same bug likely mirrors on zillow/realtor branches — audit all.
- Fix: compare against exact hostname OR `"." + domain` suffix. Prefer allowlist set: `{"redfin.com","www.redfin.com"}`.
- Effort: 30 min (incl. audit of sibling branches)
- Deps: none
- Severity: HIGH

### 7A-3. Clamp LLM-returned `rehabBand.*.mid` to non-negative
- Rationale: H-3. Negative `mid` flips verdict predicate (cost subtracted becomes added — deal looks better than reality). LLM can hallucinate any number; we must not trust the sign.
- Fix: `max(0, mid)` on ingest in `/api/analyze-ai` response parser. Also clamp `low`/`high` and enforce `low <= mid <= high`.
- Effort: 20 min + 1 unit test
- Deps: none
- Severity: HIGH

### 7A-4. Port per-URL validation from async `/api/batch-analyze` to sync path
- Rationale: M-4. Sync path accepts unvalidated URLs — bypass for any input filter enforced on async. Drift in trust boundary.
- Fix: extract `_validate_url()` helper, call from both paths.
- Effort: 45 min
- Deps: none
- Severity: MEDIUM (upgraded — it's a bypass, not a gap)

Sprint 7A DoD: all four merged, smoke test shows no raw exc strings in response bodies, `evilredfin.com` rejected, negative mid rejected by analyzer unit test.

---

## Sprint 7B — Drift closure (~3h)

One-source-of-truth pass. Every verdict/constant lives in `spec/constants.json`. Everything else reads.

### 7B-1. Add `hardFailUnitsUnknown` branch to JS `computeJoseVerdict`
- Rationale: #1 BLOCKER. Python hard-fails when unit count is unknown; JS passes through. Any user hitting the JS path on an ambiguous listing gets a false GO verdict. Parity violation.
- Files: `static/calc.js` (or wherever `computeJoseVerdict` lives) + `batch/verdict.py` cross-check
- Effort: 45 min + parity test stub
- Deps: none (parity test itself is 7B-5 below, but fix ships first)
- Severity: HIGH

### 7B-2. Move rehab multipliers to `spec/constants.json`
- Rationale: Triplicated — spec, inline `index.html`, and `_effective_rehab` in Python. Three places to forget to update.
- Fix: single `spec.rehab.multipliers` object. JS fetches via `/spec/constants.json`; Python imports via existing spec loader.
- Effort: 45 min
- Deps: spec loader must already handle nested objects (verify)
- Severity: MEDIUM

### 7B-3. `batch/pipeline.py DEFAULTS` reads from spec
- Rationale: Duplicates `spec.constants.defaults`. This was called out as a drift lane that never closed. Kill the literal dict.
- Fix: module-level `DEFAULTS = load_spec()["defaults"]`; fail-loud if missing keys.
- Effort: 30 min
- Deps: 7B-2 pattern (same loader)
- Severity: MEDIUM

### 7B-4. Align `_looks_excluded` with `spec.zipTiers.excludedCities`
- Rationale: Hardcoded exclusion list in Python drifts from spec. Same class of bug as rehab multipliers.
- Fix: read from spec; remove inline list.
- Effort: 20 min
- Deps: 7B-2 pattern
- Severity: MEDIUM

### 7B-5. Verdict parity smoke test (not the full harness — just a canary)
- Rationale: Prevents 7B-1 regression. Full harness is Sprint 9; this is the cheap canary: 5 fixture inputs, run both, assert equal.
- Effort: 40 min
- Deps: 7B-1
- Severity: MEDIUM

Sprint 7B DoD: grep for hardcoded rehab multipliers returns zero hits outside spec. `DEFAULTS` literal deleted from `batch/pipeline.py`. Parity canary green in CI.

---

## Sprint 7C — Docs refresh (~3h)

Truth-up the handoff folder. Nothing in `handoff/` currently reflects what shipped.

### 7C-1. Rewrite `handoff/README.md`
- Rationale: Says Sprints 0-2 done, 3-5 remaining. All 5 shipped. README is first thing anyone reads — lying from the top.
- Fix: current status block, link to this BACKLOG, link to CHANGELOG (7C-6).
- Effort: 25 min
- Severity: MEDIUM

### 7C-2. Rewrite `handoff/TECHNICAL_ASSESSMENT.md`
- Rationale: Claims FHA MIP and rental offset are "missing" — both shipped in Sprint 1 (commit dd1737f).
- Fix: flip to "shipped", add as-built notes, update FIX verdict rationale.
- Effort: 30 min
- Severity: MEDIUM

### 7C-3. Rewrite `handoff/SPRINT_PLAN.md`
- Rationale: S3/S4/S5 marked remaining with unchecked DoD. All shipped.
- Fix: check boxes, add Sprint 6 scope-cut note (commit 01e0c50), append Sprint 7A/7B/7C from this backlog.
- Effort: 25 min
- Severity: LOW

### 7C-4. Rewrite `handoff/USER_FLOW.md`
- Rationale: No batch panel, no async mode, no tier banner, no spec.json fetch. User flow doc describes a version that no longer exists.
- Fix: rewrite around current UI. Screenshots deferred to Sprint 10.
- Effort: 40 min
- Severity: MEDIUM

### 7C-5. Flip ADRs from Proposed to Accepted
- Rationale: Code shipped; ADRs still say "Proposed". Anyone reading assumes decisions aren't final.
- Effort: 15 min (mechanical)
- Severity: LOW

### 7C-6. New files: `CHANGELOG.md`, `BATCH_USER_GUIDE.md`, `TROUBLESHOOTING.md`, `SCHEMA.md`
- Rationale: All four missing. BATCH_USER_GUIDE is the highest-value gap — users hit the batch panel with no docs.
- Effort: 45 min (CHANGELOG seeded from git log; BATCH_USER_GUIDE is the only one needing real writing; TROUBLESHOOTING + SCHEMA stubbed)
- Severity: MEDIUM

Sprint 7C DoD: no stale "remaining" or "missing" language in handoff/. BACKLOG.md (this file) linked from README.

---

## Sprint 8 — Perf optimization (queued, ~4-6h)

Not a hotfix. Ship when 7A/B/C green. Batch-URL cold-cache path is the pain point — 60s wall-clock is the user-visible symptom.

### 8-1. Playwright browser pool
- Rationale: ~800ms–1.5s cold launch per call. For 30-URL batch that's 24-45s of browser-launch overhead alone.
- Fix: process-lifetime singleton browser, new context per call.
- Effort: 90 min incl. lifecycle cleanup
- Severity: HIGH (for perf, not correctness)

### 8-2. Overpass coordinate-bucket cache + parallelize
- Rationale: 2s cooldown serializes ALL calls → 60s on cold 30-URL batch. Bucket by rounded lat/lon (e.g. 0.01 deg), cache 24h.
- Effort: 90 min
- Severity: HIGH (perf)

### 8-3. `executemany` for DB row writes
- Rationale: Per-row execute. Cheap win.
- Effort: 30 min
- Severity: MEDIUM

### 8-4. 15-min warm-cache scrape skip
- Rationale: We scrape even when cache is warm. Short-circuit before Playwright spin-up.
- Effort: 30 min
- Severity: MEDIUM

### 8-5. Trim system prompt below Anthropic cache threshold
- Rationale: ~1,100 tokens, 60 over the cache minimum. One prompt edit away from losing cache. Fragile.
- Fix: audit + trim, add a length-budget test.
- Effort: 30 min
- Severity: LOW (but one edit from HIGH)

Sprint 8 DoD: cold-cache 30-URL batch under 20s wall-clock (from ~60s baseline).

---

## Sprint 9 — Quality/test (scope-cut relaxation candidate, ~4h)

Sprint 6 explicitly cut tests/a11y to refocus on speed-to-offer. This is the unfreeze candidate. Do not start without product sign-off.

### 9-1. JS ↔ Python math parity harness (full)
- Rationale: 7B-5 is the canary. This is the harness: fixtures for all verdict inputs, run both paths, assert parity. Catches the next `hardFailUnitsUnknown`-class drift before it ships.
- Effort: 2h
- Deps: 7B-5 pattern
- Severity: MEDIUM

### 9-2. Cache staleness boundary tests (`>` vs `>=` for 3% / 14 DOM / 30d)
- Rationale: Audit flagged inconsistent comparators. Tests pin behavior; normalization can follow.
- Effort: 45 min
- Severity: MEDIUM

### 9-3. Circuit breaker across 5 external APIs
- Rationale: One flaky upstream currently takes the batch down. Need breaker + fallback + user-visible degradation banner.
- Effort: 90 min
- Severity: MEDIUM

Sprint 9 DoD: parity harness green in CI, breakers open/close under fault injection.

---

## Sprint 10+ — Future (stub; expand when remaining 9 audits return)

Placeholder. Concrete items after workflow/UX/UI/accessibility/ops/compliance/data/observability/DX audits land.

### 10-1. Remove `handoff/USER_PROFILE.md` from git / move to local-only
- Rationale: M-3. Jose's $85K cash + credit score tracked in git. If repo ever goes public, doxx. Move to `.gitignore`'d local file; keep a redacted template in git.
- Effort: 30 min + git history scrub decision
- Severity: MEDIUM (HIGH if repo goes public)

### 10-2. Gate `/spec/constants.json` or strip PII fields before serving
- Rationale: M-1. Endpoint serves Jose's W-2 income, credit, cash to any client. Split: public spec (multipliers, thresholds) vs private profile (income, cash).
- Effort: 45 min
- Severity: MEDIUM

### 10-3. Security headers / CSP / CORS policy
- Rationale: M-2. None set. Standard hardening.
- Effort: 45 min
- Severity: MEDIUM

### 10-4. Refresh Dockerfile (missing batch/scripts/spec/calc.js)
- Rationale: M-5. Image is stale — will not boot current app cleanly.
- Effort: 30 min
- Severity: MEDIUM (HIGH if anyone tries to deploy via Docker)

### 10-5. Scrub remaining 18+ endpoints of raw `str(exc)`
- Rationale: 7A-1 fixes the two regressed ones. The long tail is Sprint 10 work — mechanical but tedious.
- Effort: 2h
- Severity: MEDIUM

### 10-6. Resolve `window.__*` globals (11+) — module boundary cleanup
- Rationale: Classic/module bridge. Not broken, but every new global is a future refactor tax.
- Effort: 3h
- Severity: LOW

### 10-7. Remove private import `_process_url` from `app.py:2183-2189`
- Rationale: Reaches into batch module internals. Promote to public API or inline.
- Effort: 30 min
- Severity: LOW

### 10-8. Broaden CAPTCHA detection beyond "captcha"/"access denied" substrings
- Rationale: False negatives on Cloudflare/PerimeterX/hCaptcha variants — batch silently returns garbage.
- Effort: 60 min
- Severity: MEDIUM

### 10-9. Fix rent-comps in-flight future leak on owner cancellation
- Rationale: Future never resolves → memory/handle leak over long-running process. Not urgent but real.
- Effort: 45 min
- Severity: LOW

### 10-10. UX/workflow/a11y items
- Rationale: Stubbed — waiting on 9 remaining audit lanes. Expect batch-panel UX, error-state copy, keyboard nav, screen-reader labels, mobile layout.
- Effort: TBD
- Severity: TBD

---

## Ranking summary (top 12, cross-sprint)

1. 7A-1 — Scrub `str(exc)` in analyze-ai (HIGH, regression)
2. 7A-2 — Fix suffix-match SSRF (HIGH)
3. 7A-3 — Clamp LLM rehab mid (HIGH, verdict correctness)
4. 7B-1 — JS `hardFailUnitsUnknown` parity (HIGH, false GO)
5. 7A-4 — Sync batch URL validation (MEDIUM, trust-boundary bypass)
6. 7B-2 — Rehab multipliers to spec (MEDIUM, drift)
7. 7B-3 — `batch/pipeline.py DEFAULTS` from spec (MEDIUM, drift)
8. 7C-2 — Rewrite TECHNICAL_ASSESSMENT (MEDIUM, lies about shipped work)
9. 7C-6 — BATCH_USER_GUIDE (MEDIUM, user-facing gap)
10. 8-1 — Playwright pool (HIGH perf)
11. 8-2 — Overpass bucket cache (HIGH perf)
12. 9-1 — JS↔Py parity harness (MEDIUM, drift prevention)

---

## Notes

- 9 audit lanes still outstanding. Expect Sprint 10+ to grow.
- Sprint 7A is non-negotiable this week. 7B/7C can slip to next week if needed — 7A cannot.
- Scope-cut from commit 01e0c50 (tests + a11y frozen for speed-to-offer) still in force. Sprint 9 is the unfreeze gate; needs product sign-off before starting.
- All effort estimates are pessimistic-realistic. Add 15% buffer at sprint-commit time.
