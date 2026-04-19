# BACKLOG — Post-V1 Hardening

Owner: Jose H Gonzalez
Updated: 2026-04-19
Source: consolidated from 5 audit lanes (security, code health, performance, architecture, docs). Sprints 11 / 11.5 / 12 landed 2026-04-19 driven by Jose's stated workflow ask + Lane 3 decisions.

Scope rule: Sprints 7A/7B/7C/8/9/10A/10B/10-6/11/11.5/12 SHIPPED. Sprint 13 (automated per-ZIP data puller) is next. Do not mix lanes.

**Hotfixes landed 2026-04-19 (post-Sprint-12):**
- **#6** — Anthropic model IDs bumped to Claude 4.X (Opus 4.7 / Sonnet 4.6 / Haiku 4.5 / batch default Sonnet 4.6). Unblocked the AI analysis final page that was 404ing.
- **#7** — Promoted Sprint 12 onto main (stacked-PR #5 had merged into `feature/sprint-11-automation` instead of `main`).
- **#8** — Scan ZIPs UX: clamp Top-N 1-15 on blur + reflect back, auto-expand Batch panel + scroll on submit, show chosen mode (sync / async) in scan summary.
- **#9** — `_coerce_narrative` helper to stop `sqlite3.ProgrammingError: type 'dict' is not supported` at rankings INSERT when `llm_analysis.narrativeForRanking` holds a dict instead of a string. Was blocking `reconcile_pending_batches_on_startup` from completing on server restart.

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

## Sprint 11 — Profile-driven automation + ZIP-scan overnight flow (SHIPPED 2026-04-19, PR #4 commit 7d1f676)

**Driver:** Jose's 2026-04-19 ask. Screenshot of Neighborhood Search tab required him to type "Max Price" and "Target Monthly Rent" for ZIP 94590, even though those values already exist in his profile + active preset. The form validation currently hard-fails with "Please enter a target monthly rent." — the tool has the data and is still asking for it.

**North star:** Jose opens `http://localhost:8000`, clicks a preset (or none — profile picks one), pastes a list of ZIPs into a textarea, walks away. He returns to a ranked list of exceptional FHA 2–4 unit candidates, all scored through the full TOPSIS + Jose-verdict pipeline, with failed/excluded URLs in a separate pill.

**Hard gate:** no new feature wiring until audit of `str(exc)` scrub + profile PII endpoint is confirmed complete for new endpoints (11-5 below).

### 11-1. Auto-populate Neighborhood Search form from profile + active preset on load
- **Rationale:** Today `__applySpec__()` + `initDefaults()` only populate the single-property analyzer form. Search form fields (`searchTargetRent`, `searchMinPrice`, `searchMaxPrice`, `searchLocation`, `searchPropType`, `searchMinBeds`) fall through — user re-types on every page load.
- **Files:** `index.html` — extend `initDefaults()` (~line 2665) with `initSearchDefaults()`; pull from `DEFAULTS.defaultPreset` + `PRESETS[name].search` + `DEFAULTS.targetMonthlyRent`.
- **Fix:**
  1. Add `defaults.defaultPreset: "Vallejo Priority"` and `defaults.targetMonthlyRent: <n>` to `spec/profile.example.json` and `profile.local.json`.
  2. On profile-load completion (index.html:2835), if `DEFAULTS.defaultPreset` exists, auto-apply the preset (hit `applyPreset(name)`).
  3. Populate `searchTargetRent` from `DEFAULTS.targetMonthlyRent` (or from `computeRentEstimate(zip, beds)` if present).
  4. Persist last-used search filters to localStorage on every change; restore on init (existing `saveSearchFilters`/`loadSearchFilters` already half-wired — finish the loop).
- **Effort:** 90 min
- **Severity:** HIGH (the actual user complaint)

### 11-2. Add `targetMonthlyRent` + derivation fallback to profile schema
- **Rationale:** Target rent is Jose's deal-quality yardstick — every scored listing compares price→rent to it. Currently user-entered per search; should be a profile default with a sensible fallback when blank.
- **Fix:**
  1. Extend `spec/profile.example.json` → `defaults.targetMonthlyRent` (default `0` = "derive").
  2. Server fallback: if blank, compute from `rent_comps_cache` median for the ZIP + bed count.
  3. UI: show the value as a pre-filled but editable input with a tiny hint: "from profile — click to override".
- **Effort:** 60 min
- **Deps:** 11-1
- **Severity:** MEDIUM

### 11-3. "Analyze all N results" button on Neighborhood Search
- **Rationale:** Today the 20–25 quick-scored listings require per-row "Analyze" clicks to run the full pipeline (FEMA/fire/Overpass/LLM/TOPSIS). User complaint: "I want the automation to find exceptional investment property potential" — that means running the real pipeline without manual per-row clicks.
- **Fix:**
  1. Add button "Run full analysis on all results" below search table (index.html:3383 area).
  2. Collect `searchResults[*].listingUrl`, POST to `/api/batch-analyze` (sync ≤25) or `/api/batch-submit-async` (>25).
  3. Render the batch envelope in the existing batch-results area (reuse `renderResults(envelope)`).
  4. Gate: respect the existing sync-batch URL cap (25). Above 25, force async.
- **Effort:** 2h
- **Deps:** 11-1 (so URLs come from pre-populated search)
- **Severity:** HIGH

### 11-4. ZIP-scan orchestrator: paste list of ZIPs → auto-fan-out → ranked list
- **Rationale:** The user's actual workflow: "paste 5 ZIPs, come back to ranked list." Today you'd have to run 5 separate searches, click "Analyze all" 5 times, and manually merge the rankings. Need a single orchestrator endpoint.
- **Fix:**
  1. New endpoint `POST /api/scan-zips` accepting `{ zips: [...], preset: "Vallejo Priority", topNPerZip: 10 }`.
  2. Fan out: for each ZIP, call existing Redfin search; take top N by quick-score.
  3. Dedup URLs, check ZIP-tier exclusions (`spec.zipTiers.excludedZips`/`excludedCities`), auto-reject.
  4. Submit survivors to `_run_async_batch()` (batch already handles concurrency, browser pool, Overpass cache from Sprint 8).
  5. Return `batchId`; frontend polls `/api/batch-status/{batchId}` (existing auto-poll from Sprint 10B).
  6. New UI tab or section: "Scan ZIPs" with textarea for ZIP list, preset selector, topN slider, "Scan" button.
- **Effort:** 3h
- **Deps:** 11-3 (reuses batch pipeline); Sprint 8 browser pool + Overpass cache (already shipped)
- **Severity:** HIGH

### 11-5. Security audit: new endpoints on the Sprint 10A allowlist
- **Rationale:** Sprint 10A scrubbed `str(exc)`, locked `_detect_source` to exact hostname, gated `/spec/profile.json` to loopback. Sprint 11 adds `/api/scan-zips` — must inherit the same invariants, not be a new attack surface.
- **Fix:**
  1. `/api/scan-zips` MUST route errors through `_safe_error()` (no raw exc leaks).
  2. Validate every ZIP is 5-digit numeric; cap `zips.length ≤ 20`, `topNPerZip ≤ 10`.
  3. Re-use the `_validate_url()` helper from Sprint 7A-4 on every survivor URL before batch submission.
  4. Do NOT echo profile fields (income, cash) in any response — even indirectly via computed fields. Audit the response shape.
  5. Rate-limit: internal semaphore cap of 1 concurrent `/api/scan-zips` call per process (it already fans out N searches).
- **Effort:** 60 min
- **Deps:** 11-4
- **Severity:** HIGH (hard gate — ship after 11-4 code lands, before merge)

### 11-6. Perf guard: scan-orchestrator reuses browser pool + Overpass bucket cache
- **Rationale:** 10 ZIPs × 10 listings = 100 properties through the pipeline. At cold-cache 2s/listing for Overpass that's 200s serialized; with Sprint 8 bucket cache + Playwright pool it should stay under 60s wall-clock.
- **Fix:**
  1. Confirm `/api/scan-zips` path calls into the same `batch/pipeline.py` codepath that Sprint 8 optimized (no new scrape codepath).
  2. Add a wall-clock budget log line per scan: `scan_zips zip_count=N survivors=M elapsed=X.Xs` → DB or stdout.
  3. Acceptance: 5 ZIPs × 10 listings cold-cache finishes ≤ 90s; warm-cache ≤ 30s.
- **Effort:** 45 min
- **Deps:** 11-4
- **Severity:** MEDIUM

### 11-7. UI: remove "Please enter a target monthly rent" blocker when profile supplies it
- **Rationale:** Tiny fix but it's the thing in the screenshot. If target rent auto-populates (11-1/11-2), the red validation message should never appear on a happy path.
- **Fix:** index.html:3112 — change the "required" check to soft-warn if `DEFAULTS.targetMonthlyRent > 0` supplies it.
- **Effort:** 10 min
- **Deps:** 11-1, 11-2
- **Severity:** LOW (but user-visible polish)

**Sprint 11 DoD:**
- Fresh page load with `profile.local.json` present → search form pre-filled, no red error banner, no typing required.
- "Run full analysis on all results" button posts all URLs from a search to batch-analyze and renders ranked output.
- `POST /api/scan-zips` with 5 ZIPs returns a batchId; polling yields a single ranked list with excluded-ZIP rejects in a separate pill.
- 10-ZIP × 10-listing cold scan under 90s wall-clock; no `str(exc)` in any response body; profile PII not echoed.
- Existing V1 flow (paste 1 URL) unchanged.

---

## Sprint 11.5 — Search-filter bugfix (SHIPPED 2026-04-19, same PR #4)

Driver: Vallejo 94591 search returned $95K and $64.9K properties under a $400K min + Multi-family filter. Two bugs compounding: multi-ZIP in the Location field silently dropped filters, and nothing post-filtered what Redfin returned.

### 11.5-1. Python-side post-filter in `_search_redfin_page`
- Min/max price, min beds, property-type re-enforced after Redfin returns.
- "Likely lot" heuristic: `beds==0 AND sqft==0 AND (price<200K OR no address)` → drop.
- Files: `app.py:_search_redfin_page` (around line 1218).
- Severity: HIGH (data-quality + false-positive ★★★★★ scores).

### 11.5-2. Reject comma-separated multi-ZIP in `/api/search`
- If Location field contains 2+ ZIPs, 400 with message redirecting to the Scan ZIPs panel.
- Files: `app.py:search_neighborhood` (around line 1238).
- Severity: MEDIUM (user-visible silent-failure path).

### 11.5-3. Quick-score lot detection
- `computeQuickScore` short-circuits to zero stars when beds+sqft both missing and price < $200K. Returns "Likely land only" warning.
- Files: `index.html:computeQuickScore` (around line 3268).
- Severity: HIGH (was showing ★★★★★ on vacant lots).

---

## Sprint 12 — Profile schema extensions + per-ZIP preset matching (SHIPPED 2026-04-19)

Status: 12-1, 12-2, 12-4, 12-5 shipped on branch `feature/sprint-12-per-zip-matching`. 12-3 (rentalStrategy per-unit UI) and 12-6 (203(k) stretch) deferred — revisit when Jose wants MTR support or walks into a heavy-rehab deal.

Parity harness 27/28 (pre-existing DTI 49.9% stale fixture unchanged). All 111 pytest pass apart from that same fixture. JS 43/43.

---

## Sprint 12 — Profile schema extensions + per-ZIP preset matching (ORIGINAL BRIEF, ~6–8h)

Driver: Jose's 2026-04-19 Lane 3 decisions. Three-tier scoring, geospatial gating, per-strategy vacancy, self-manage auto-PM. Gated on 3 open questions (see README section "Blocking questions" — answered before sprint opens).

### 12-1. Explicit Yellow thresholds in verdict code
- Add `netPitiYellow`, `rehabYellow` to `computeJoseVerdict` (JS + Python). Replaces the "miss Green by ≤10%" logic. Requires answer on Q3 above.
- Files: `calc.js`, `batch/verdict.py`, parity fixtures.
- Effort: 90 min + update parity harness.
- Severity: HIGH.

### 12-2. Geospatial gating: `location.maxMilesHard` + `zipTiers.conditionalCities`
- Haversine from `profile.location.homeBase` to listing lat/lng (Census geocoder already provides it). Reject beyond `maxMilesHard`. Conditionally allow cities in `zipTiers.conditionalCities` when within threshold.
- Files: `batch/verdict.py`, scoring context assembly, JS mirror.
- Effort: 2h.
- Severity: HIGH (Sacramento rule, Tracy/Stockton natural exclusion).

### 12-3. `rentalStrategy` per-unit: LTR vacancy 5% / MTR vacancy 12% / MTR rent × 1.35
- Unit 2+ rent inputs get a per-unit strategy toggle. Vacancy + rent multiplier pulled from `profile.rentalStrategy`. Block MTR if landlord months < `mtrMinLandlordMonths`.
- Files: `index.html` form + calc, `batch/pipeline.py` context.
- Effort: 2h.
- Severity: MEDIUM.

### 12-4. Self-manage cap → auto-PM injection
- When `units >= propertyManagementTriggerUnits` and user didn't manually override PM %, inject `propertyManagementFallbackPct` into PITI math. Flag in UI ("PM 9% auto-added — uncheck self-manage to hide").
- Files: `index.html`, `batch/pipeline.py`.
- Effort: 60 min.
- Severity: MEDIUM.

### 12-5. `matchPresetByZip()` — per-listing defaults
- After scrape/geocode, resolve each listing's ZIP against `presets[*].search.zips`. Use matched preset's defaults (taxes, insurance, vacancy) for that row's PITI. Unmatched → global default preset.
- Files: `batch/pipeline.py`.
- Effort: 90 min.
- Severity: HIGH (core of "bundle cities" ask).

### 12-6. 203(k) contractor-stretch scenario
- When `rehab > rehabRed` AND self-perform share ≥ `contractorStretch.selfPerformMinPct`, run a parallel 203(k) scenario (`loanType: FHA-203k`, rehab financed). Show side-by-side verdict on cash-funded vs 203(k).
- Files: FHA math module, UI badge.
- Effort: 2h.
- Severity: LOW (appetite-driven; kill if over budget).

Sprint 12 DoD: all 6 shipped; parity harness green; Jose runs 3 real listings across Vallejo + Pittsburg + conditional-Sac and verdicts reflect the new tier/strategy/geospatial logic.

---

## Sprint 13 — Automated per-ZIP data puller (queued, ~8–10h)

Driver: Jose declined to ask his agent to fill a per-city table ("prefer pulling programmatically over agent memory"). Sprint 13 builds the puller so preset blocks self-populate from public data, not tribal knowledge.

### 13-1. County assessor tax-rate scraper
- Target: Solano, Contra Costa, Alameda, Sacramento county assessors. Parse base rate + Mello-Roos/CFD overlays per ZIP. Cache 90 days.
- Writes: `presets[city].defaults.propertyTaxRatePct`.

### 13-2. Days-on-market + inventory velocity from Redfin market-data pages
- Scrape `/city/<id>/CA/<city>/housing-market` for median DOM, inventory count, price/sqft. Cache 14 days.
- Writes: `presets[city].search.minDom` guidance + a new `marketTemp` enum (hot/warm/cool).

### 13-3. GreatSchools API integration
- Paid but cheap. Pull per-address school scores. Feed into `jose.schoolRating*` gate.
- Writes: stored on the listing, not the preset.

### 13-4. Rent-comp refinement via Rentometer or Redfin rentals
- Median rent by (ZIP, beds) with confidence interval. Already half-wired; extend to cover all tier-2/3 ZIPs + new preset cities.
- Writes: `rent_comps_cache` populated for every preset ZIP.

### 13-5. Preset auto-generator CLI
- `python scripts/generate_presets.py --city "Pittsburg, CA" --zip 94565`
- Pulls 13-1..13-4, writes a new `presets[*]` block to `spec/constants.json` with a `"_source": "auto"` flag + timestamp. Human reviews + commits.
- Severity: HIGH (the whole sprint's UX payoff).

Sprint 13 DoD: 6+ auto-generated city preset blocks committed; each traces back to source URLs in frontmatter; parity harness unchanged.

---

## Ranking summary (Sprint 13 → next)

Sprint 11 / 11.5 / 12 all shipped 2026-04-19 (PRs #4 #5 #7 plus hotfixes #6 #8 #9). Sprint 13 is queued and unblocked. Deferred from Sprint 12: **12-3** (rentalStrategy per-unit LTR/MTR toggles — UI work, ~2h) and **12-6** (203(k) contractor-stretch scenario — new loan-math branch, ~2h). Revisit when Jose wants MTR support or walks into a heavy-rehab deal.

### Carry-over verification after this week's merges
- 7A-1/7A-2/7A-3/7A-4 — Sprint 10A invariants still hold in `/api/scan-zips`: `_error_envelope` on every path, loopback-only profile, per-URL validation via shared `_validate_batch_urls`, negative-mid clamp in LLM output.
- 8-1/8-2 — Sprint 8 browser pool + Overpass bucket cache still the active path; `/api/scan-zips` delegates to `batch/pipeline.py` so it inherits them.
- 9-1 — parity harness 27/28 (the 1 fail is the pre-existing `DTI 49.9%` stale fixture, JS+Py agree).
- 10A §10-1/10-2 — `profile.local.json` still gitignored, `/spec/profile.json` still loopback-only.

---

## Notes

- Scope-cut from commit 01e0c50 (tests + a11y frozen for speed-to-offer) still in force. Sprint 9 was the unfreeze gate and shipped; explicit-Yellow thresholds added in Sprint 12-1 layered on top of the 10%-rule without breaking the harness.
- All effort estimates are pessimistic-realistic. Add 15% buffer at sprint-commit time.
- Stacked-PR gotcha: when PR #5 had base `feature/sprint-11-automation`, merging it after #4 did NOT auto-rebase onto main — it landed on the old base branch. PR #7 was the cleanup. If another stacked PR is opened, update its base to `main` manually after the parent merges, before hitting Squash and Merge.
