# Changelog

All notable changes. Dates in UTC. Newest first.

---

## 2026-04-18 — Post-V1 audit pass (15 agents)

### Security hotfixes (inline, pre-Sprint 7A)

- `fix(sec): H-2 close SSRF via loose hostname endswith match`
  — `_detect_source` now requires exact match or `.redfin.com` / `.zillow.com`
    suffix. Rejects `evilredfin.com` and `redfin.com.attacker.tld`.
- `fix(sec): H-1 close str(exc) leak on LM Studio + Ollama branches`
  — Both branches now use `_error_envelope` + `logger.exception`, matching
    the Anthropic branch M1/M3 fix.
- `fix(sec): H-3 clamp LLM rehabBand + roofAgeYears to prevent negative
  values from prompt injection flipping verdicts`.
- `fix(sec): M-4 defense-in-depth for sync batch — same per-URL validation
  as async endpoint via shared _validate_batch_urls helper`.

### Docs

- New: `handoff/BACKLOG.md` (Sprint 7A/B/C / 8 / 9 / 10)
- New: `handoff/CHANGELOG.md`
- New: `handoff/TROUBLESHOOTING.md`
- Refreshed: `handoff/README.md` — post-V1 reading order + shipped status
- Refreshed: `handoff/SPRINT_PLAN.md` — all sprints flipped to DONE with commit hashes; post-V1 section added
- Refreshed: `handoff/TECHNICAL_ASSESSMENT.md` — post-V1 epilogue prepended; original preserved
- ADR-001 + ADR-002 flipped to Accepted with sign-off lines

---

## 2026-04-18 — 809c9cb — refactor: collapse index.html math into calc.js imports

ADR-002 Phase B. `index.html` now imports `computeFhaPITI`, `computeQualifyingIncome`, `maxPitiAtDti`, `computeJoseVerdict` from `calc.js` via `<script type="module">`. Duplicate inline bodies deleted. Global onclick handlers re-attached to `window` where needed. Drift engine fully killed across browser and Node test runtimes.

---

## 2026-04-18 — ff5fbdf — refactor: extract shared constants to spec/constants.json

ADR-002 Phase A. New `spec/constants.json` (FHA rates, Jose thresholds, TOPSIS weights, insurance heuristic, presets, ZIP tiers, rehab categories, defaults). New `spec/__init__.py` Python loader. `calc.js` + `index.html` + `batch/*.py` all read one file. Hard-fail loader on missing file or malformed JSON. All 61 tests still green post-refactor.

---

## 2026-04-18 — 7e4c5e8 — feat(batch): wire real Redfin rent-comp medians into ranking

Scrape real rent comps from Redfin; use median when ≥2 comps returned; fall back to `tier_default` otherwise. `rent_source` field surfaced in UI so Jose can see when he's looking at real comps vs. tier fallback. Improves TOPSIS input quality on well-trafficked ZIPs.

---

## 2026-04-18 — a2bb5c4 — feat(batch): Phase B drift-kill preparation

Ported `computeJoseVerdict` from inline `index.html` into `calc.js` as a new export. ~80-line lift with hardcoded threshold refs swapped to `SPEC.jose.*`. Unblocks Phase B module-script conversion.

---

## 2026-04-18 — 10ab110 — feat(batch): Phase A enrichment + LLM extraction

External enrichment wired in with 8s hard-cap per property: FEMA flood risk, Cal Fire WUI severity, OSM Overpass amenity counts, Census geocoder for lat/lon normalization. Consolidated structured-extraction LLM call per property (Claude Sonnet 4.5 + Vision on primary listing image) returns one JSON blob covering rehabBand, motivationSignals, riskFlags, insuranceUplift, aduPotential. Per-URL SQLite cache with explicit invalidation (price >3%, DOM ≥14 days, age >30 days). Per-field fallback defaults when JSON malformed.

---

## 2026-04-18 — 23b352f — feat(batch): async Message Batches endpoints

`POST /api/batch-submit-async` + `GET /api/batch-status/{batchId}`. Delegates LLM narration to Anthropic Message Batches API (50% cheaper, 24h SLA). UI unlocks async toggle when URL count exceeds 20. `localStorage.pendingBatchId` for client-side state recovery across reloads.

---

## 2026-04-18 — 5fa53a3 — feat(batch): SQLite + TOPSIS ranking MVP

ADR-001 Commit 1. `POST /api/batch-analyze` sync endpoint. 8 SQLite tables (`properties`, `scrape_snapshots`, `batches`, `rankings`, `claude_runs`, plus enrichment tables). WAL journal mode. `BEGIN IMMEDIATE` critical sections with 3-attempt retry/backoff. TOPSIS scoring on 13 criteria + Pareto non-dominance filter + hard-fail gate from `computeJoseVerdict`. UI collapsible batch panel with ranked table. Includes security fixes M1/M3 (exception leaks on Anthropic branch).

---

## 2026-04-18 — 01e0c50 — docs: scope cut — freeze tests + a11y, refocus on speed-to-offer

Documented 2026-04-17 scope cut removing tests/a11y/UI polish from remaining sprints. `README.md` + `SPRINT_PLAN.md` updated with V1 philosophy section.

---

## 2026-04-18 — 8523b4a — feat(sprint5): live run-through + RUN_ME

Sprint 5. Jose ran 3 real Vallejo/East Bay/Richmond listings end-to-end; numbers matched his spreadsheet within tolerance. `RUN_ME.md` added at repo root (≤1 page quick-start). V1 north star met: paste URL → ≤60s → verdict Jose trusts.

---

## 2026-04-18 — 6559559 — feat(sprint4): C-39 rehab edge + Jose G/Y/R verdict

Sprint 4. Rehab scalar replaced with category array (Roof/Plumbing/Electrical/Cosmetic/HVAC/Other), each with self-perform checkbox. Per-category multipliers: roof 0.60 (C-39), cosmetic 0.80, others retail. New `computeJoseVerdict` predicate engine with 5 criteria: net PITI, cash-to-close, effective rehab, ZIP tier, PITI vs 50% DTI. GREEN/YELLOW/RED badge with up to 3 plain-English reasons. Generic investor score kept visible as reference.

---

## 2026-04-18 — 9e892ad — feat(sprint3): market presets + ZIP-tier banner

Sprint 3. localStorage refactored from single-preset to keyed map. Three hardcoded presets (Vallejo Priority, East Bay Nearby, Richmond Motivated) with per-market tax/insurance/vacancy/rate overrides. ZIP-tier constants + banner on Review step (green tier1/2, yellow tier3, red outside).

---

## 2026-04-18 — b115e33 — feat(sprint2): central DEFAULTS config + per-unit rent inputs

Sprint 2. Single `DEFAULTS` block at top of JS section consolidating 15+ scattered literals, pre-filled to Jose's profile (down 3.5%, mgmt 0%, vacancy 5%, loan FHA, owner-occupied true, W-2 $4,506/mo, etc.). `units` selector dynamically renders 1–4 rent input rows. Unit 0 = owner-occupied when `ownerOccupied=true`; wired into Sprint 1.6 qualifying-income calc. localStorage migration for stale pre-Sprint-2 shape.

---

## 2026-04-18 — 861293a — merge: land Jose FHA customization through Sprint 1

Merge commit landing `feature/jose-profile` Sprint 0 + Sprint 1 work to `main`.

---

## 2026-04-18 — dd1737f — feat(sprint1): FHA MIP in PITI + 75% rental offset + DTI panel

Sprint 1. Core FHA math. `FHA_MIP_UPFRONT_RATE = 0.0175` financed into loan amount; `FHA_MIP_ANNUAL_RATE = 0.0055` monthly component added to PITI formula. New `computeQualifyingIncome({w2Monthly, units, perUnitRents, ownerOccupied})` applying 0.75 × sum(non-owner rents) offset. New `maxPitiAtDti(income, dtiPct, existingDebt)`. Review-step DTI panel shows max PITI at 45/50/55% back-end DTI with pass/fail check. Quick-score 20%-down literal fixed to respect actual `downPaymentPct` input. `$500K / 3.5% / 6.5%` fixture now asserts $4,004 PITI (up from $3,779).

---

## 2026-04-18 — 609fb5d — feat(sprint0): pin deps and add pytest + node --test baseline harness

Sprint 0. `requirements.txt` pinned with `==`. New `requirements-dev.txt` (pytest, pytest-cov, playwright). `tests/` directory with `test_app_baseline.py` (snapshot tests for `/api/analyze` across duplex/triplex/SFR fixtures). JS calc functions extracted into testable shim (`calc.js` ESM) with `tests/calc.test.mjs` under `node --test` (8+ unit tests). `Makefile` target `make test` runs both suites. Every baseline test carries `BASELINE — pre-Jose-fix value` comment flagging the sprint that will intentionally change it.

---

## 2026-04-17 — 78260ea — initial handoff docs land

Initial commit of `handoff/` folder: `USER_PROFILE.md`, `HANDOFF.md`, `TECHNICAL_ASSESSMENT.md`, `SPRINT_PLAN.md`, `USER_FLOW.md`, `ACCEPTANCE_CRITERIA.md`, `README.md`. FIX verdict issued. Sprint 0 gated as hard prerequisite to any math change.
