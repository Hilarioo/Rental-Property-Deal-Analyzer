# Sprint Plan — Jose's Personal Rental Property Deal Analyzer

**Branch:** `feature/jose-profile`
**Owner:** Jose H Gonzalez (personal-use customization)
**Plan author:** Product Sprint Prioritizer agent
**Plan date:** 2026-04-17
**Status:** CLOSED — all sprints shipped. Retrospective record as of 2026-04-18. Post-V1 work tracked in `BACKLOG.md`.

> **Scope cut 2026-04-17:** tests, accessibility, and UI polish are frozen at current state for the remaining sprints. See README.md "V1 philosophy".

---

## 1. Overview

### Context

Jose is a first-time FHA homebuyer in Vallejo, CA evaluating 2–4 unit owner-occupied house-hack deals. He has already cloned a generic rental-property analyzer at `/Users/hilarioo/Documents/Projects/Rental-Property-Deal-Analyzer/` and confirmed it boots on `http://localhost:8000`. Base install + smoke tests are **DONE**. The remaining work is to retrofit the tool so it answers *Jose's* question — "does this specific listing clear my FHA-owner-occupied bar?" — not a generic investor question.

### Jose's profile (authoritative for all defaults)

| Field | Value |
| --- | --- |
| W-2 income | $54,080 / yr ($4,506 / mo gross) |
| Self-employment income | $0 for lender purposes (documentation gap) |
| Credit score | 780+ |
| Consumer debt | $0 |
| Liquid cash | $85K total, $40–45K earmarked for cash-to-close |
| Strategy | FHA 3.5% down, owner-occupied 2–4 unit house-hack |
| Target ZIPs (tier 1) | Vallejo 94590, 94591 |
| Target ZIPs (tier 2) | Hercules, Rodeo, Crockett, Pinole |
| Target ZIPs (tier 3) | Richmond 94801, 94804, 94805 |
| Price ceiling — duplex | $525,000 |
| Price ceiling — triplex | $650,000 |
| License | C-39 roofing — self-performs roof ~40% under retail |
| Max net PITI after 75% offset | ~$3,000 / mo |
| AI provider | Anthropic Claude (`.env` already populated) |

### FIX verdict

The existing codebase is functionally close but **quantitatively wrong for FHA owner-occupied math**. The biggest defects are (a) no FHA MIP in PITI, (b) zero support for the 75% rental offset rule that actually drives Jose's DTI approval, (c) rehab is a single scalar so his C-39 edge is invisible, and (d) the quick-score hardcodes 20% down. None of these are architectural problems; they are targeted retrofits. A test harness (Sprint 0) is the non-negotiable prerequisite before any math change.

### Effort rollup

| Sprint | Name | Hours | Status |
| --- | --- | ---: | --- |
| 0 | Foundation (branch + pins + tests) | 4.0 | **DONE** (609fb5d) |
| 1 | Core FHA math | 8.0 | **DONE** (dd1737f) |
| 2 | Jose profile defaults | 3.0 | **DONE** (b115e33) |
| 3 | Presets & market guardrails | 2.5 | **DONE** (9e892ad) |
| 4 | Contractor edge + Jose-tuned G/Y/R scorer | 6.0 | **DONE** (6559559) |
| 5 | Live deal run-through + RUN_ME | 1.0 | **DONE** (8523b4a) |
| | **Shipped total** | **24.5** | ✅ all sprints DONE |

Remaining scope was deliberately cut from 12.5h to 9.5h on 2026-04-17: Sprint 3–5 test tasks, a11y work, and UI polish were pulled out. Rationale: the north star is Jose making a real offer on a real Vallejo duplex, not a publication-quality codebase. Existing Sprint 0–2 tests (61) stay as a free regression net; `make test` still works; no new tests are gated.

### Sprint dependency graph

```
S0 ──► S1 ──► S2 ──► S3 ──► S4 ──► S5
         │                    ▲
         └────────────────────┘  (S1 math feeds S4 scorer)
```

S0 blocks everything that touches math. S2 can technically start in parallel with S1 (pure config), but do S1 first to avoid re-wiring defaults twice.

---

## 2. Sprint 0 — Foundation

**Duration:** 4 hours
**Goal:** Lock in a baseline test harness and dependency pins so every downstream math change is verifiable, not blind.

### User-visible outcome

Nothing visible in the UI. Under the hood: `pytest` runs green against current `app.py` behavior, a minimal JS unit-test runner exercises the calculation functions in `index.html`, and `requirements.txt` is pinned so a rebuild in 6 months still boots.

### Why this is sprint zero (not later)

There are ~4,200 lines of calculation JS in `index.html` with zero tests. Every subsequent sprint rewrites financial math. Without a baseline snapshot test, you cannot tell whether a "fix" in Sprint 1 silently breaks an unrelated calculation in Sprint 4. **No math edits before this sprint completes.**

### Tasks

| # | Task | File / Location | Est. |
| --- | --- | --- | ---: |
| 0.1 | Create working branch `feature/jose-profile` off `main` | git | 0.1h |
| 0.2 | Pin every dependency in `requirements.txt` with `==` (currently unpinned) | `requirements.txt` | 0.5h |
| 0.3 | Add `requirements-dev.txt` with `pytest`, `pytest-cov`, `playwright` (for JS harness later) | new file | 0.2h |
| 0.4 | Create `tests/` dir with `tests/test_app_baseline.py` that snapshots current `/api/analyze` output for 3 fixture payloads (duplex $500K, triplex $650K, SFR $400K) | new | 1.0h |
| 0.5 | Extract JS calc functions (PITI, cashflow, DSCR, quick-score) into a testable shim — add a `<script type="module">` export block at the bottom of `index.html` OR mirror them into `static/calc.js` and import | `index.html:3131-3142`, `:3177`, `:2315-2373` | 1.2h |
| 0.6 | Add `tests/calc.test.mjs` running under `node --test` with ~8 unit tests covering known-good outputs (document each as "baseline — may be wrong, locked for regression detection") | new | 0.8h |
| 0.7 | Add `npm test` and `pytest` entries to a `Makefile` or `scripts/test.sh` so one command runs both | new | 0.2h |

### Acceptance criteria

- [x] `git rev-parse --abbrev-ref HEAD` returns `feature/jose-profile`.
- [x] `pip install -r requirements.txt` in a fresh venv succeeds with pinned versions; `pip freeze` diff is empty.
- [x] `pytest` runs and reports ≥3 passing tests with non-zero assertions (not just imports).
- [x] `node --test tests/calc.test.mjs` runs and reports ≥8 passing tests.
- [x] A single command (`make test` or `./scripts/test.sh`) runs both suites and exits 0.
- [x] Every baseline test has a comment `# BASELINE — pre-Jose-fix value. Expected to change in Sprint N.` flagging which sprint will intentionally break it.

### Dependencies

None. This is the root of the tree.

### Risks

- **JS extraction is more invasive than it looks.** The calc functions in `index.html` may capture closures over DOM state. If pure extraction is hard, fall back to Playwright-driven end-to-end tests against `http://localhost:8000` — slower but no refactor risk. Budget the 1.2h estimate on task 0.5 as a hard cap; switch strategy if over.
- **Pinning breaks something.** Running `pip-compile` or `pip freeze` on a working venv mitigates this. Test the install in a throwaway venv before committing.

---

## 3. Sprint 1 — Core FHA math

**Duration:** 8 hours
**Goal:** Make the tool produce lender-accurate numbers for an FHA owner-occupied 2–4 unit purchase: correct PITI (with MIP), correct qualifying income (with 75% rental offset), and a correct quick-score that respects the actual down-payment input.

### User-visible outcome

1. Review step shows a new **FHA MIP** line (upfront + annual) and the PITI total is ~$342/mo higher on a $500K / 3.5% / 6.5% case.
2. A new **Qualifying income & DTI** panel shows max PITI the borrower clears at 45%, 50%, and 55% back-end DTI after the 75% rental offset.
3. Quick-score no longer silently assumes 20% down — entering 3.5% actually flows through.

### Tasks

| # | Task | File / Location | Est. |
| --- | --- | --- | ---: |
| 1.1 | Add FHA MIP constants module: `MIP_UPFRONT = 0.0175`, `MIP_ANNUAL = 0.0055`, financed-upfront flag default `true` | `index.html` near top of calc block (~line 3100) | 0.3h |
| 1.2 | Update loan-amount calc to add financed upfront MIP: `loanAmount = basePrincipal * (1 + MIP_UPFRONT)` when `loanType === 'FHA'` | `index.html:~3120` | 0.5h |
| 1.3 | Update PITI formula to include monthly MIP: `pitiMonthly = PI + T + I + (baseLoan * MIP_ANNUAL / 12)` | `index.html:3131-3142` | 0.7h |
| 1.4 | Update baseline tests in `tests/calc.test.mjs` — the $500K / 3.5% / 6.5% case should now assert **~$4,004** (not ~$3,779). Mark the old value in a `// was:` comment. | `tests/calc.test.mjs` | 0.3h |
| 1.5 | Add owner-occupied branch detection: new input `ownerOccupied` (bool) + `units` (int). Default on for Jose. | `index.html` form + state | 0.5h |
| 1.6 | Implement `computeQualifyingIncome({ w2Monthly, units, perUnitRents, ownerOccupied })`: if `ownerOccupied && units > 1`, return `w2Monthly + 0.75 * sum(nonOwnerUnitRents)`; otherwise `w2Monthly + 0.75 * sum(allUnitRents)` for pure-investment branch | new JS function | 1.2h |
| 1.7 | Implement `maxPitiAtDti(qualifyingIncome, dtiPct, existingDebt)`: `qualifyingIncome * dtiPct - existingDebt` | new JS function | 0.4h |
| 1.8 | Add DTI display panel to Review step: three rows showing max PITI at 45%, 50%, 55% and a pass/fail check against computed PITI from 1.3 | `index.html` Review template | 1.2h |
| 1.9 | Fix quick-score 20%-down assumption: replace `price * 0.2` literal with `price * (downPaymentPct / 100)` | `index.html:2315` | 0.3h |
| 1.10 | Audit quick-score thresholds (`index.html:2320-2373`) for other hardcoded assumptions; document each in a code comment | `index.html:2320-2373` | 0.4h |
| 1.11 | Add `app.py` endpoint `/api/dti` OR extend `/api/analyze` response to include `qualifying_income`, `max_piti_45`, `max_piti_50`, `max_piti_55`, `fha_mip_upfront`, `fha_mip_annual_monthly` | `app.py` | 1.0h |
| 1.12 | Write unit tests for: (a) MIP upfront financed amount, (b) MIP monthly component, (c) qualifying income with owner-occupied duplex (1 rented unit), (d) qualifying income with owner-occupied fourplex (3 rented units), (e) max PITI at each DTI tier | `tests/` | 1.2h |

### Acceptance criteria

- [x] For fixture `price=$500,000, down=3.5%, rate=6.5%, term=30, taxes=$6,250/yr, ins=$1,800/yr`: PITI equals **$4,004 ± $5** (prior value $3,779).
- [x] Upfront MIP of $8,663 appears as a line item and is financed into the loan (loan amount = $490,088.38, not $482,500).
- [x] For fixture `W-2=$4,506/mo, duplex, non-owner rent=$2,000/mo`: qualifying income reads **$6,006/mo** (4,506 + 0.75 × 2000).
- [x] DTI panel shows three max-PITI rows. For qualifying income $6,006: 45% = $2,703, 50% = $3,003, 55% = $3,303.
- [x] Quick-score with `down=3.5%` entered no longer computes as if 20% was used — verified by diffing score before/after against a fixture.
- [x] All baseline tests from Sprint 0 updated to new expected values with a `// Sprint 1:` comment documenting the change.
- [x] `pytest` and `node --test` both pass.

### Dependencies

- **Sprint 0 must be green.** Without the harness, you cannot prove these changes don't break the DSCR, cash-on-cash, or IRR math elsewhere in `index.html`.

### Risks

- **FHA MIP annual rate is LTV-dependent.** 0.55% is the 2025 rate for >95% LTV, ≤$726K. If Jose's actual loan trips into the >$726.2K bucket (unlikely at his price ceilings) the rate shifts. Hardcode 0.55% for now; add a TODO for rate-table lookup.
- **The 75% rule has lender variations.** Some lenders require 2 years of landlord experience to count projected rents at all (Jose has none). If that gate matters for his pre-approval, we may need a separate "conservative" view that counts $0 of rental income for qualifying. Add as Sprint 4 optional toggle, not here.
- **Quick-score refactor is a stealth rewrite.** Lines 2320–2373 may interlock with the full-scorecard math. Strictly scope 1.9–1.10 to replacing the `0.2` literal; do not refactor thresholds (that is Sprint 4 work).

---

## 4. Sprint 2 — Jose profile defaults

**Duration:** 3 hours
**Goal:** Centralize every default currently scattered across `index.html` and wire them to Jose's numbers so a fresh page load pre-fills to his profile, not the generic investor profile.

### User-visible outcome

1. Page load shows: down payment **3.5%**, management fee **0%**, vacancy **5%**, rate **6.5%** (or current fetched), term **30 yr**, loan type **FHA**, owner-occupied **true**.
2. Duplex selection reveals **two rent inputs** (Unit 1 / Unit 2), not one combined rent.
3. Triplex selection reveals three, fourplex four.
4. Changing the profile is a one-line edit to `const DEFAULTS = {...}` in one place.

### Tasks

| # | Task | File / Location | Est. |
| --- | --- | --- | ---: |
| 2.1 | Create `const DEFAULTS` block at the top of the JS section consolidating all 15+ scattered defaults | `index.html` (new block, replaces usages at `:1539, :2297, :2308, :2315, :4185-4193`) | 0.8h |
| 2.2 | Replace each scattered literal with `DEFAULTS.<key>` reference; search for any missed spots | `index.html` (grep pass) | 0.5h |
| 2.3 | Set Jose's values in DEFAULTS: `downPct: 3.5, mgmtPct: 0, vacancyPct: 5, loanType: 'FHA', ownerOccupied: true, termYears: 30, w2MonthlyGross: 4506, existingDebt: 0, creditScore: 780, cashBudget: 42500` (midpoint) | `index.html` DEFAULTS block | 0.2h |
| 2.4 | Add `units` selector (1/2/3/4) that dynamically renders 1–4 rent input rows. Label each "Unit 1 rent (owner-occupied)", "Unit 2 rent", etc. | `index.html` form | 0.9h |
| 2.5 | Wire per-unit rents into the 75% offset calc from Sprint 1.6 — unit index 0 is always owner-occupied when `ownerOccupied=true` | `index.html` | 0.3h |
| 2.6 | Add baseline tests asserting `DEFAULTS` object shape and values so future refactors can't silently drift | `tests/calc.test.mjs` | 0.3h |

### Acceptance criteria

- [x] A fresh page load (no localStorage) pre-fills all 10 fields listed in 2.3 to Jose's values.
- [x] Selecting "Duplex" renders exactly 2 rent inputs; "Triplex" renders 3; "Fourplex" renders 4.
- [x] Entering rents `[0, 2000]` for a duplex with `ownerOccupied=true` produces qualifying income = `w2Monthly + 0.75 * 2000`.
- [x] `grep -n "0.2" index.html` shows no remaining down-payment-as-decimal literals outside the DEFAULTS block.
- [x] A single diff changing `DEFAULTS.downPct: 3.5` to `25` changes every downstream calc — no orphan literals.

### Dependencies

- Sprint 1.6 (qualifying income function) needs per-unit rent inputs to be real.

### Risks

- **Hidden defaults.** The 15+ number estimate came from the prior agent's audit; there may be more. Budget an extra 0.5h mentally for a grep sweep with `\b(0\.\d|10|8|20)\b` as sanity check.
- **localStorage collision.** Existing users (Jose himself after the smoke test) may have stale localStorage that shadows the new DEFAULTS. Add a one-time `localStorage.removeItem('deal-defaults')` migration on first load of the new branch.

---

## 5. Sprint 3 — Presets & market guardrails

**Duration:** 2.5 hours
**Goal:** Support multiple named presets so Jose can jump between "Vallejo Priority," "East Bay Nearby," and "Richmond Motivated Sellers" with one click, and warn when a property's ZIP isn't in his target list.

### User-visible outcome

1. A **Presets** dropdown in the header with three options pre-populated.
2. Clicking a preset overwrites the form with that preset's market assumptions (e.g., Vallejo taxes vs. Richmond taxes, different rent benchmarks).
3. If the analyzed property's ZIP is not in any tier, a yellow banner appears: "ZIP 94804 is Tier 3 (Richmond motivated sellers). Underwrite conservatively."
4. If the ZIP is outside all three tiers entirely, a red banner: "ZIP 95670 is outside your target markets."

### Tasks

| # | Task | File / Location | Est. |
| --- | --- | --- | ---: |
| 3.1 | Refactor localStorage from single-preset to keyed map: `presets: { [name]: {...defaults overrides} }` | `index.html:2856-2882` | 0.8h |
| 3.2 | Define three hardcoded presets as JS consts, each a partial override of DEFAULTS | `index.html` near DEFAULTS | 0.5h |
| 3.3 | Preset dropdown UI + "Apply preset" button in header | `index.html` header | 0.6h |
| 3.4 | ZIP-tier constants: `TIER_1 = ['94590','94591']`, `TIER_2 = [...Hercules/Rodeo/Crockett/Pinole ZIPs...]`, `TIER_3 = ['94801','94804','94805']` | `index.html` | 0.2h |
| 3.5 | Look up tier from analyzed property ZIP; render banner with matching severity | `index.html` Review step | 0.6h |

(Task 3.6 removed 2026-04-17 under scope cut — no new tests required. Existing Sprint 0–2 tests continue to run.)

### Preset contents (starter values — refine after first real scrape)

| Preset | Tax rate | Insurance | Vacancy | Rate source |
| --- | --- | --- | --- | --- |
| Vallejo Priority | 1.25% | $1,800/yr | 5% | Live fetch, fallback 6.5% |
| East Bay Nearby | 1.15% | $1,900/yr | 5% | Live fetch, fallback 6.5% |
| Richmond Motivated | 1.35% | $2,100/yr | 8% | Live fetch, fallback 6.75% |

### Acceptance criteria

All checks are visual / manual in the browser.

- [x] Dropdown shows 3 presets; selecting each overwrites the relevant DEFAULTS overrides and re-renders.
- [x] Analyzing a property at 94590 shows a green "Tier 1 — Vallejo priority" banner.
- [x] Analyzing 94804 shows yellow "Tier 3 — Richmond motivated."
- [x] Analyzing 95670 shows red "Outside target markets."
- [x] localStorage supports saving a fourth custom preset without overwriting the three built-ins.

### Dependencies

- Sprint 2 DEFAULTS block — presets are overrides of that object.

### Risks

- **ZIP list for Hercules/Rodeo/Crockett/Pinole.** Jose named cities, not ZIPs. Quick lookup: Hercules 94547, Rodeo 94572, Crockett 94525, Pinole 94564. Confirm before shipping.
- **Storage schema migration.** Existing single-preset localStorage will not match the new keyed map. Add a one-time migration reader that detects the old shape and wraps it under `presets.legacy`.

---

## 6. Sprint 4 — Contractor edge + Jose-tuned G/Y/R scorer

**Duration:** 6 hours
**Goal:** Model Jose's C-39 roofing self-perform discount as a rehab line item, and replace the generic quick-score with a Green/Yellow/Red overlay calibrated to his specific budget and tolerance — not investor-quality scoring.

### User-visible outcome

1. Rehab section becomes a **category table** with rows for Roof, Plumbing, Electrical, Cosmetic, HVAC, Other. Each row has a "Self-perform" checkbox that applies a discount multiplier.
2. With Roof self-perform checked, a $20,000 roof entry shows $12,000 effective (40% off).
3. Next to the existing investor-quality score (kept for reference), a new **Jose Verdict** badge: GREEN, YELLOW, or RED with up to 3 reasons listed.

### Tasks

| # | Task | File / Location | Est. |
| --- | --- | --- | ---: |
| 4.1 | Replace single rehab scalar with category array state `rehabItems: [{category, retailCost, selfPerform}]` | `index.html:1307-1308` + state | 1.0h |
| 4.2 | Render rehab category table with 6 seed rows (editable) and a "+Add row" button | `index.html` form | 1.3h |
| 4.3 | Per-category self-perform multiplier map: `{ roof: 0.60, plumbing: 1.0, electrical: 1.0, cosmetic: 0.80, hvac: 1.0, other: 1.0 }` — roof reflects C-39 40% off; cosmetic gets a 20% sweat-equity discount; others retail | `index.html` constants | 0.3h |
| 4.4 | Compute `effectiveRehab = sum(item.retailCost * (item.selfPerform ? multipliers[item.category] : 1))` | `index.html` | 0.4h |
| 4.5 | Propagate `effectiveRehab` through cash-to-close and cashflow calcs (replacing the old scalar) | `index.html` | 0.5h |
| 4.6 | Define Jose's GREEN criteria as explicit predicate functions, all must pass: `netPiti <= 2500`, `cashToClose <= 45000`, `effectiveRehab <= 60000`, `zipTier in ['tier1','tier2','tier3']`, `dti_at_50 >= computedPiti` | `index.html` new scorer module | 1.0h |
| 4.7 | Define YELLOW as: GREEN with 1 criterion missed by ≤10%, OR tier 3 ZIP | same | 0.5h |
| 4.8 | Define RED as: any GREEN criterion missed by >10%, OR ZIP outside tiers, OR PITI exceeds 55% DTI max | same | 0.5h |
| 4.9 | Render verdict badge with up to 3 reasons (e.g., "Net PITI $2,780 exceeds $2,500 target by $280") | `index.html` Review step | 1.2h |
| 4.10 | Keep the original quick-score visible but label it "Generic investor score (reference)" to avoid confusion | `index.html:2320-2373` | 0.3h |

(Task 4.11 removed 2026-04-17 under scope cut — no new predicate tests required. A regression test in `tests/calc.test.mjs` is encouraged but NOT acceptance-gated.)

### Jose verdict predicates (table form)

| Criterion | GREEN | YELLOW edge | RED |
| --- | --- | --- | --- |
| Net PITI (post 75% offset) | ≤ $2,500 | $2,501–$2,750 | > $2,750 |
| Cash to close | ≤ $45,000 | $45,001–$49,500 | > $49,500 |
| Effective rehab | ≤ $60,000 | $60,001–$66,000 | > $66,000 |
| ZIP tier | Tier 1 or 2 | Tier 3 | Outside tiers |
| PITI vs 50% DTI | ≤ 50% DTI | 50–55% DTI | > 55% DTI |

### Acceptance criteria

All checks are manual in the browser against a real Vallejo listing (Jose's choice — typically a current duplex he is actively considering).

- [x] Rehab table with 6 seed categories renders; each row has retail-cost input and self-perform checkbox.
- [x] Setting roof = $20,000, self-perform = true, shows effective = $12,000 and feeds downstream cash-to-close.
- [x] Jose eyeballs a real Vallejo listing he believes should be GREEN (priority ZIP, within budget) and the badge comes back GREEN with plausible reasons.
- [x] Jose eyeballs a real listing he believes should be YELLOW (one criterion marginal) and the badge comes back YELLOW; the reason names the criterion he expected.
- [x] Jose eyeballs a real listing at an outside-tier ZIP (e.g. Oakland / 95670 / Sacramento) and the badge comes back RED with reason "ZIP outside target markets."
- [x] The generic investor score is still visible, labeled as reference only.
- [x] Verdict matches Jose's gut within his tolerance ("yes I'd offer" / "no I wouldn't"). If it disagrees, the predicate table gets re-tuned before merge.

### Dependencies

- Sprint 1 (net PITI + DTI math feeds predicate 1 and 5).
- Sprint 2 (DEFAULTS feeds cash budget thresholds).
- Sprint 3 (ZIP tiering feeds predicate 4).

### Risks

- **Self-perform multipliers are estimates.** 40% off roof is Jose's own number and load-bearing. 20% off cosmetic is a guess — make the multipliers constants, not magic numbers, so Jose can tune after his first real project.
- **Predicate creep.** Resist adding a 6th or 7th criterion now. Three reasons max in the badge; more than that is noise.
- **Double-counting with DTI.** Predicate 1 (net PITI ≤ $2,500) and predicate 5 (PITI ≤ 50% DTI) can both fire. That is intentional — they measure different things (absolute affordability vs. lender formula). Document this in a code comment so future-Jose doesn't "simplify" them.

---

## 7. Sprint 5 — Live deal run-through + RUN_ME

**Duration:** 1 hour
**Goal:** Jose personally runs 2–3 currently-listed Vallejo properties through the tool, confirms the numbers pass his sniff test, and writes a one-page quick-start so he can re-derive the workflow in six months.

### User-visible outcome

1. Jose has analyzed 2–3 real Vallejo/East Bay listings end-to-end and the numbers match his own back-of-napkin math.
2. A `RUN_ME.md` at repo root tells Jose how to start the server, paste a URL, and interpret the verdict.

### Tasks

| # | Task | Est. |
| --- | --- | ---: |
| 5.1 | Jose picks 2–3 currently-active Vallejo / East Bay / Richmond listings, runs each through the tool end-to-end | 0.3h |
| 5.2 | Jose confirms PITI, qualifying income, net PITI, cash-to-close, and verdict pass his sniff test; if anything looks off, re-tune predicates or defaults before merge | 0.3h |
| 5.3 | Write `RUN_ME.md` — start server, paste URL, read verdict, tune presets | 0.4h |

### Acceptance criteria

- [x] Jose has run ≥2 live listings through the tool and the numbers match his gut / spreadsheet within his own tolerance.
- [x] Any discrepancy Jose flagged is either fixed or explicitly deferred with a one-line note.
- [x] `RUN_ME.md` exists at repo root and is ≤1 page.

### Dependencies

- Every prior sprint. This is the gate.

### Risks

- **Scraper drift.** Live listings depend on Redfin still working. If it breaks mid-sprint, fall back to a saved HTML fixture or manual entry so the math chain can still be validated.
- **Jose's gut disagrees with the predicates.** This is the whole point of the sprint — catch the mismatch here, not after an offer goes out. Plan for one re-tune pass.

---

## 8. Out of scope for now

These items belong to Phase 5 / future iterations. Do **not** touch in this effort.

- CalHFA down-payment assistance integration (MyHome, ZIP, Forgivable Equity Builder).
- BiggerPockets forum / deal scraper.
- Solano / Contra Costa County property-records API integration.
- SMS or email alert system for new listings.
- Multi-user auth, cloud sync, mobile app.
- Full retail cost database for rehab categories.
- Live construction-permit lookup by address.

If any of these feel tempting mid-sprint, write a line in `BACKLOG.md` and move on.

---

## 9. Risk register (top 5)

| # | Risk | Likelihood | Impact | Mitigation |
| --- | --- | --- | --- | --- |
| R1 | Scraper breaks mid-sprint (listing source changes HTML) | Med | High — blocks T1, demo flow | Save 2–3 reference HTML fixtures in `tests/fixtures/`; scraper tests run against fixtures, not live |
| R2 | No new tests in Sprints 3–5 — regressions may slip past the landed Sprint 0–2 harness | Med | Med | **Accepted risk under 2026-04-17 scope cut.** Sprint 0–2 tests (61) still run on every `make test`; new predicates and preset logic ship unverified. Jose catches misfires in Sprint 5 live run-through, not in CI. |
| R3 | FHA MIP rate table changes (HUD updates) | Low | Med | Rates are constants in one place; TODO-comment the annual rate so re-tuning is trivial |
| R4 | Lender rejects 75% offset without landlord history | Med | High — changes Jose's real approval path | Note in RUN_ME that figures assume the offset is accepted; no new test will verify a "conservative" toggle — if it matters, Jose runs the math with rent = $0 by hand. |
| R5 | Claude API costs spike during dev if tests call it live | Low | Low | Mock Claude responses in tests; only call live API from the UI, never from `pytest` |

---

## 10. Definition of Done (whole effort)

The north star: **Jose pastes a Redfin URL of a real Vallejo duplex and, in ≤60 seconds, gets a GREEN / YELLOW / RED verdict he trusts enough to make an offer decision.** Everything else is scope creep.

The effort is DONE when Jose can do the following, unassisted, in under 60 seconds per listing:

1. Open `http://localhost:8000` on `feature/jose-profile`.
2. Paste a Vallejo/East Bay/Richmond duplex or triplex URL.
3. See, without touching any form field:
   - FHA-correct PITI including MIP.
   - Qualifying income and max-PITI at 45/50/55% DTI, using the 75% rental offset.
   - Cash-to-close vs. his $40–45K budget.
   - Effective rehab with any applicable C-39 self-perform discount.
   - A Green/Yellow/Red verdict badge with up to 3 plain-English reasons.
   - A ZIP-tier banner telling him whether this is a priority market.
4. Do all of the above without editing any code, because defaults and presets land him on Jose's profile automatically.
5. Decide — in that ≤60s window — whether to walk away or keep digging.

**Under the hood (cut scope, 2026-04-17):**

- [x] Sprint 0–2 landed: `pytest` + `node --test` cover FHA MIP, DTI, per-unit rent, DEFAULTS (61 tests total).
- [x] Every default lives in one `DEFAULTS` block — a profile swap is a single-file edit. (landed Sprint 2; confirmed post-Sprints 3–4; later moved to `spec/constants.json` per ADR-002)
- [x] `RUN_ME.md` exists and is current.
- [x] Jose has personally run ≥2 live listings end-to-end and the numbers match his gut.

No assertion on test count for Sprints 3–5. No a11y assertion beyond Sprint 2's `aria-live`. No UI-polish assertion.

When all of the above are true, merge `feature/jose-profile` to `main`, tag `v1.0-jose`, and stop. Future work is Phase 5 scope and waits for a real deal to motivate it.

**2026-04-18 update:** All six sprints shipped. Merged to `main`. V1 closed. See `Post-V1 Sprints` below for follow-up work that landed on top.

---

## 11. Post-V1 Sprints

After V1 shipped, Jose's real shopping workflow surfaced the "40 tabs on a weekend" problem. That drove a batch/ranking + persistence track that was not originally in scope. It is fully shipped; the hardening backlog is tracked separately in [`BACKLOG.md`](./BACKLOG.md).

### Post-V1 Batch — SHIPPED (commits 5fa53a3, 23b352f, 10ab110, a2bb5c4, 7e4c5e8)

- SQLite persistence (8 tables, WAL mode, `BEGIN IMMEDIATE` critical sections)
- `/api/batch-analyze` sync endpoint with TOPSIS + Pareto + hard-fail gates (ADR-001)
- `/api/batch-submit-async` + `/api/batch-status/{batchId}` (Anthropic Message Batches, 50% cheaper)
- Consolidated structured-extraction LLM call per property (Claude Sonnet 4.5 + Vision)
- External enrichment: FEMA flood, Cal Fire WUI, OSM Overpass amenities, Census geocoder (8s hard-cap)
- Real Redfin rent-comp medians wired into ranking

### Post-V1 Calc Drift — SHIPPED (commits ff5fbdf Phase A, 809c9cb Phase B per ADR-002)

- Extracted all numeric constants to `spec/constants.json`
- Collapsed `index.html` inline math into ESM imports from `calc.js`
- Three-runtime parity: browser, Node tests, Python batch pipeline all read one JSON file

### Post-V1 Security Hotfixes — SHIPPED (inline, 2026-04-18)

- H-1: scrubbed `str(exc)` leak on LM Studio + Ollama branches
- H-2: closed SSRF via loose hostname endswith match in `_detect_source`
- H-3: clamped LLM `rehabBand` + `roofAgeYears` to non-negative
- M-4: defense-in-depth for sync batch URL validation

### Queued post-V1 work

See [`BACKLOG.md`](./BACKLOG.md) for:

- **Sprints 7A/7B/7C/8/9/10A/10B/10-6** — SHIPPED (security hotfixes, drift closure, docs refresh, perf, parity harness, security/quality hygiene, UX wins, window-global consolidation)
- **Sprint 11 — SHIPPED 2026-04-19** (PR #4, commit 7d1f676) — profile-driven auto-populate, "Analyze all" batch-from-search, `POST /api/scan-zips` orchestrator with loopback-only PII + rate limiting + browser-pool reuse. 20-ZIP / 15-top-N caps.
- **Sprint 11.5 — SHIPPED 2026-04-19** (same PR #4) — Redfin search-filter bugfix: Python-side post-filter (min/max/beds/property-type re-enforced), "likely lot" heuristic drops beds+sqft-missing rows, multi-ZIP in Location field redirects to Scan ZIPs panel, `computeQuickScore` zero-stars for likely lots. Also landed schema groundwork for Sprint 12 (explicit Yellow thresholds, `location`/`rentalStrategy`/`selfManagement`/`contractorStretch` blocks, Sacramento `conditionalCities` rule).
- **Sprint 12 — SHIPPED 2026-04-19** (PR #5 merged into sprint-11 branch, promoted to main via PR #7) — layered Yellow classifier (explicit thresholds OR 10% rule, whichever is more forgiving), geospatial gating (`maxMilesHard` hard cap + `conditionalCities` threshold, Haversine from Pittsburg 38.028/-121.8847), auto-PM injection at units >= 4, `matchPresetByZip` per-listing tax/insurance/vacancy overrides. 12-3 (rentalStrategy per-unit UI) and 12-6 (203(k) stretch) deferred.
- **Hotfixes + follow-up feats (2026-04-19)** — all merged:
  - **#6** Anthropic model IDs bumped to Claude 4.X family (`claude-sonnet-4-6` / `claude-opus-4-7` / `claude-haiku-4-5-20251001`) after 404 on retired `claude-sonnet-4-20250514`.
  - **#7** promoted Sprint 12 onto main (stacked-PR base mishandling).
  - **#8** Scan ZIPs UX: clamp Top-N on blur, auto-expand + scroll to Batch panel on submit, show chosen mode (sync / async) in scan summary.
  - **#9** `_coerce_narrative` — stops `sqlite3.ProgrammingError: type 'dict' is not supported` at rankings INSERT when `llm_analysis.narrativeForRanking` holds a dict. Unblocked `reconcile_pending_batches_on_startup`.
  - **#10** docs truth-up for Sprint 11 / 11.5 / 12 + hotfixes #6/#7/#8/#9.
  - **#11** separate `batch_scrape:{ip}` rate-limit bucket (180/min) so Scan ZIPs doesn't self-DoS against the `/api/scrape` 5/min human-facing cap.
  - **#12** scan-vs-paste source pill, per-row `×` delete + "Clear N failed rows" bulk, sync cap 30 → 100.
  - **#13** unit inference from APT/UNIT/# address suffix + condo/townhouse type; differentiated RED copy for condo vs townhouse vs SFR.
  - **#14** Force sync actually forces sync (previously silently flipped to async above cap); Top-N per ZIP cap 15 → 50.
  - **#15** Min/Max Price inputs on Scan ZIPs panel; Max defaults to `profile.jose.priceCeilingDuplex` so "(none)" preset scans no longer surface $49K lots or $645K over-ceiling listings.
- **Sprint 13 — NEXT** — automated per-ZIP data puller (county assessor tax rates incl. Mello-Roos, DOM from Redfin market data, GreatSchools ratings, Rentometer/Redfin rent comps; `scripts/generate_presets.py --city X --zip Y` writes new `presets[*]` blocks with `_source: auto` flag). Replaces the per-city agent questionnaire Jose declined.
- **Sprint 14 — QUEUED** — Neighborhood Search UX polish: collapse the single-ZIP search form into its own `<details>` accordion, move all results (single search + batch + scan-zips) into a unified region below every control panel, bump Max Results dropdown 25 → 500 (raise server-side cap at `_search_redfin_page`), add desktop-notification + tab-title completion cue when an async scan/batch flips from pending → complete.
