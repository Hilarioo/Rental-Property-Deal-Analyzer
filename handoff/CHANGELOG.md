# Changelog

All notable changes. Dates in UTC. Newest first.

---

## 2026-04-19 — Scan ZIPs usability round (PRs #9 → #15)

Seven PRs merged in one session fixing the issues Jose hit during his first real Scan ZIPs runs. Main branch is now at commit `401e253`.

### Hotfix #11 — separate `batch_scrape` rate-limit bucket (534589f)

First Scan ZIPs run (9 ZIPs × 15 survivors = 126 URLs) hit **"RATE LIMITED — TRY AGAIN IN A MINUTE"** on URL #6 and skipped the other 121. Both `batch/pipeline.py::process_url` and `batch/async_pipeline.py::_prepare_url` were sharing the `scrape:{ip}` 5/min bucket with the manual `/api/scrape` endpoint — sized for a human pasting one URL at a time. Introduced a separate `batch_scrape:{ip}` bucket at 180/min (~3/sec, matches Sprint 8-1 browser-pool saturation). `/api/scrape` bucket unchanged. Outer batch endpoints still enforce their own 3/min at entry.

### Feat #12 — source pill + per-row delete + sync cap 100 (82505fb)

Three asks surfaced from the first scan:
- **Scan-vs-Paste differentiation:** envelope tagged `source: 'scan_zips'` / `'batch_paste'` client-side; colored pill renders next to the batch ID + scan-origin summary line ("From Scan ZIPs — 9/9 ZIPs, top 15 per ZIP, 126 survivors"). Source survives the async-poll round-trip via the pending record.
- **Row delete:** per-row **×** button + bulk "Clear N failed rows" + "Clear all" (with confirm). Only the batch-results card resets — SQLite-cached analyses stay.
- **Sync cap 30 → 100:** `_BATCH_MAX_URLS_SYNC` and `_BATCH_HARD_CAP` bumped (30→100, 50→150); JS `SYNC_MAX` 30→100; UI copy updated. ~170s wall-clock for 100 URLs at browser-pool saturation.

### Fix #13 — unit inference from APT/UNIT/# suffix + condo/townhouse type (38e3745)

User flagged: Zillow URL for a condo (`401-Stinson-St-APT-3-Vallejo-CA-94591`) returned RED with "Unit count not detected — re-scrape or enter manually" — but the property exists. Root cause: the scraper only checked JSON `numberOfUnits` + multifamily keywords. Extended the inference chain to infer `units=1` when:
- address matches `\b(apt|apartment|unit|suite|ste)\s*[#]?\s*\w+\b`
- address has a `#N` suffix
- URL slug contains `/APT-N/` etc.
- raw propertyType is "Condo" / "Townhouse" / "SingleFamily"

Plumbed `units_source` + `property_type_raw` through `verdict_ctx`. New RED reasons (JS + Python + parity-harness in lockstep):
- "Single condo unit — no other units to rent, 75% FHA offset unavailable"
- "Single townhouse unit — ..."
- "Address suffix (APT/UNIT/#) indicates one unit of a larger building — no 75% FHA rental offset possible"
- "SFR without legal ADU — no 75% rental offset possible" (retained for true SFR)

Also softened the genuinely-ambiguous copy: "Unit count not detected — re-scrape or enter manually" → "Unit count ambiguous — cannot confirm 2-4 unit eligibility; set units manually in the single-property wizard".

### Fix #14 — Force sync actually forces + Top-N cap 15→50 (9c1c112)

Force sync was a suggestion, not a rule — `mode == "sync" OR len > cap` silently flipped to async when survivors exceeded the sync cap. Now:
- `async` → always async
- `sync` → always sync, up to `_BATCH_HARD_CAP` (150). Above that: 400 "reduce or pick async".
- `""` (auto) → sync if ≤ cap, else async (unchanged).

Also raised `_SCAN_ZIPS_MAX_TOP_N` 15 → 50 with matching HTML `max` attr + JS clamp updates.

### Feat #15 — Min/Max Price inputs + profile-ceiling default on Scan ZIPs (4875409)

94565 scan with preset=(none) returned $49K lots through $645K over-ceiling listings. Scan ZIPs panel had Preset / Top-N / Mode inputs but no Min/Max Price — so "(none)" preset meant no price filter. Added two inputs ("Min Price (optional, overrides preset)" + "Max Price (defaults to profile duplex ceiling)"); `runScanZips` sends both as `min_price` / `max_price` to `/api/scan-zips` (endpoint already accepted them, they just weren't surfaced). Max Price pre-populates from `jose.priceCeilingDuplex` on load. Preset change pushes `preset.search.minPrice/maxPrice` into the inputs.

### Docs #10 — handoff/ truth-up for Sprint 11 / 11.5 / 12 + hotfixes #6/#7/#8/#9 (merge commit 0543ce5)

README / SPRINT_PLAN / BACKLOG / CHANGELOG all caught up. Status flipped "all post-V1 through Sprint 12 shipped"; test count updated to 111 pytest + 43 JS + 27/28 parity; new "What shipped 2026-04-19" section in README.

---

## 2026-04-19 — Sprint 11 + 11.5 + 12 + three hotfixes (PRs #4 / #5 / #6 / #7 / #8)

### Sprint 11 — profile-driven automation + ZIP-scan overnight flow (PR #4, 7d1f676)

Driver: eliminate the manual-form-entry bottleneck and add an "overnight ZIP scan" flow so Jose can paste a list of ZIPs and walk away.

- **Search form auto-populates on load** from `profile.defaults.defaultPreset` + `preset.search` (zips / price / property type) and `profile.defaults.targetMonthlyRent`. `initSearchDefaults()` fills blanks only — user's last-used values (via `loadSearchFilters`) still take priority. Fixes "why am I typing Max Price and Target Rent every time" complaint.
- **"Analyze all" button** on the Neighborhood Search results table pipes every URL from the current result set through the full batch pipeline. Auto-selects sync (≤30 URLs) or async. `analyzeAllSearchResults()` at `index.html:3451`.
- **New `POST /api/scan-zips` orchestrator** (`app.py:2715`). Fans out Redfin searches across N ZIPs (bounded concurrency of 3), picks top-K cheapest per ZIP, dedups, applies `zipTiers.excludedZips` + `excludedCities` filters, revalidates URLs via `_validate_batch_urls`, submits survivors to the existing batch pipeline. Inherits every Sprint 10A invariant: `_error_envelope`, rate-limited 2/min, process-lifetime semaphore (1 concurrent scan), strict 5-digit ZIP regex, caps at 20 ZIPs / 15 top-N.
- **Scan ZIPs UI panel** (`index.html:1418`) with preset + top-N + mode selectors, inline summary of per-ZIP keep/found counts + a rejected-pills section.
- **New profile schema fields:** `defaults.targetMonthlyRent`, `defaults.defaultPreset`, `defaults.scanTopNPerZip`.

### Sprint 11.5 — filter bugfix (PR #4, same commit)

Driver: Vallejo 94591 search returned $95K / $64.9K vacant lots with ★★★★★ quick-scores under a $400K + Multi-family filter. Two bugs compounding.

- **Python post-filter in `_search_redfin_page`**. Min/max price, beds, and property-type re-enforced after Redfin returns (Redfin's URL filter silently no-ops in multi-ZIP + search-bar-fallback paths).
- **"Likely lot" heuristic**: drops rows with `beds == 0 AND sqft == 0 AND (price < 200K OR no address)`.
- **Reject comma-separated multi-ZIP in `/api/search`** with a 400 redirecting to the Scan ZIPs panel. Root cause: `_build_redfin_search_url`'s `^\d{5}$` regex silently fell through to the search-bar path on multi-ZIP input, losing all filters.
- **`computeQuickScore` short-circuits** to zero stars + "Likely land only" warning when beds+sqft both missing and price < $200K.

Sprint 11.5 also landed schema groundwork for Sprint 12: `jose.netPitiYellow`, `jose.rehabYellow`, school-rating gates + new top-level `location`, `rentalStrategy`, `selfManagement`, `contractorStretch` blocks in `spec/profile.example.json`. Removed Sacramento from `excludedCities`, added `zipTiers.conditionalCities.Sacramento` with `maxMilesFromHomeBase` rule.

### Sprint 12 — layered Yellow + geospatial + auto-PM + per-ZIP preset matching (PR #5, 5e1e38f; promoted to main via PR #7 afc14bc)

Driver: Jose's Lane 3 decisions — 35-mi commute radius from Pittsburg, 30-mi Sacramento conditional, three-tier scoring (Yellow threshold added), auto-PM at 4+ units, per-ZIP tax rates.

- **12-1 layered Yellow classifier** (`batch/verdict.py:_classify_overage`, `index.html:_classifyOverage`, `scripts/verdict_parity_check.mjs` mirror). Yellow fires if EITHER an explicit Yellow threshold (`netPitiYellow`, `rehabYellow`, `cashCloseYellow` when set) allows it OR the legacy 10%-overage rule allows it. Red only when both fail. Backward-compatible when the explicit key is missing.
- **12-2 geospatial gating** (`batch/verdict.py:_haversine_miles` + `_geospatial_fail`, mirrored in `index.html:_geospatialFail`, plumbed lat/lng/address into `verdict_ctx` in `batch/pipeline.py`). `profile.location.maxMilesHard` is a hard cap; `zipTiers.conditionalCities` threshold gates individual cities (Sacramento today). Fires as RED ahead of numeric predicates. No-ops when lat/lng or homeBase are missing (preserves parity for pre-12-2 fixtures).
- **12-4 auto-PM injection** (`batch/pipeline.py:_auto_pm_pct`). When `units >= profile.selfManagement.propertyManagementTriggerUnits`, inject `propertyManagementFallbackPct` into opex. Surfaced as `metrics.auto_pm_pct`.
- **12-5 matchPresetByZip** (`batch/pipeline.py:_preset_defaults_for_zip`). Each listing's ZIP scans `spec.presets[*].search.zips`; matched preset's `propertyTaxRatePct` / `insuranceAnnual` / `vacancyPct` are used for that row's PITI. Vallejo + Richmond in the same batch now use their own rates. Surfaced as `metrics.matched_preset` + `metrics.applied_tax_pct`.
- **12-3** (rentalStrategy per-unit LTR/MTR toggles) and **12-6** (203(k) contractor-stretch scenario) — deferred.
- **Tests:** 111/112 pytest pass, 43/43 JS calc, parity 27/28 (pre-existing `DTI 49.9%` stale fixture unchanged). 2 new parity fixtures for 12-2 Sacramento + Vallejo coordinates. 2 existing fixtures updated to layered-Yellow semantics.

### Hotfix #6 — Anthropic model IDs (d9a2807, merged via aa3251c)

Anthropic returned `404 "model: claude-sonnet-4-20250514"` — that ID was retired. Bumped hardcoded refs to current Claude 4.X family:
- `claude-sonnet-4-20250514` → `claude-sonnet-4-6`
- `claude-haiku-4-20250414` → `claude-haiku-4-5-20251001`
- `claude-opus-4-20250514` → `claude-opus-4-7`
- `BATCH_LLM_MODEL` default was `claude-sonnet-4-5`, now `claude-sonnet-4-6`

Model-selector dropdown reordered Opus > Sonnet > Haiku.

### Hotfix #7 — promote Sprint 12 to main (afc14bc)

Follow-up. PR #5 merged into `feature/sprint-11-automation` instead of `main` because GitHub didn't auto-rebase its base after PR #4 merged. #7 fast-forwarded the Sprint 12 commit onto main — no code changes beyond what was already in #5.

### Hotfix #8 — Scan ZIPs UX (aebc6f9)

First real run surfaced three UX gaps:
- Top-N field accepted `550000` (paste of max-price into wrong input). Now clamps to 1-15 on blur + reflects clamped value back.
- Pending card + ranked results render in the Batch Analyze `<details>` panel above Scan ZIPs. Now auto-expands the Batch panel and scrolls to `batch-pending` (async) or `batch-results` (sync) on submission.
- Summary now shows the chosen mode — green "sync" vs amber "async (overnight)" — with an inline status line pointing at the Batch panel.

### Hotfix #9 — coerce `narrativeForRanking` to str before sqlite bind

Server startup crashed `reconcile_pending_batches_on_startup` with:

```
sqlite3.ProgrammingError: Error binding parameter 11: type 'dict' is not supported
```

Root cause: rankings INSERT param 11 is `claude_narrative`, sourced from `llm_analysis.narrativeForRanking`. The LLM schema declares that a string but an in-flight async batch (e.g. `msgbatch_01JrGCszpYRNkesDBwm5T2j2`) held a dict — sqlite3 rejects those at bind and `poll_async_batch` never recovers, leaving the batch stuck pending forever.

Fix: new `_coerce_narrative(value)` helper in `batch/pipeline.py` — str/None pass through, dict/list become a compact JSON dump, anything else becomes `str(value)`. `async_pipeline.py` imports it so both paths serialize identically. Applied at every bind/response site (two rankings INSERTs + two response-builders).

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
