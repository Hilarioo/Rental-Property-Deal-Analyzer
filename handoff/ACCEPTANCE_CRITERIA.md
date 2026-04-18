# Acceptance Criteria — Jose's Rental Property Deal Analyzer (V1 Customization)

**Owner:** Jose H Gonzalez
**PM / Author:** Alex (PM)
**Status:** Authoritative — governs Sprints 0–5
**Last Updated:** 2026-04-17
**Scope:** Local-only customization of the base Rental Property Deal Analyzer to serve Jose's FHA owner-occupied house-hack workflow in the East Bay / Vallejo corridor.

---

## 0. How to Read This Document

This document is the single source of truth for "done" on the V1 customization sprints. It is written to be stand-alone — a reader who has not seen the handoff doc or the sprint plan should be able to open this file, run the math test cases, and sign off.

- Section 1 defines the single north-star outcome.
- Section 2 lists user stories in INVEST form.
- Section 3 lists feature-level Given/When/Then acceptance criteria — these are the tests that must pass.
- Section 4 provides known-answer math test cases for trust validation.
- Section 5 defines per-sprint Definition of Done.
- Section 6 documents explicit out-of-scope items.
- Section 7 captures non-functional requirements.
- Section 8 is the sign-off ritual Jose personally runs.

A feature is only "done" when (a) its Given/When/Then criteria pass, (b) its owning sprint's DoD is satisfied, and (c) Jose has personally checked the box in a git commit.

---

## 1. North-Star Outcome

> **Jose pastes a Redfin multi-family URL and sees a complete, trustworthy FHA-aware analysis with green/yellow/red verdict and reasons in under 60 seconds.**

That single sentence is the definition of V1 success. If anything in this document contradicts that outcome, the outcome wins.

**Three non-negotiables embedded in the north star:**
1. **FHA-aware** — MIP, 75% rental offset, and DTI stretch ranges are first-class, not an afterthought.
2. **Trustworthy** — math test cases (Section 4) pass to the dollar; Jose can defend the numbers to his lender.
3. **Verdict + reasons** — Green/Yellow/Red is never shown without the "why" immediately beside it.

---

## 2. User Stories (INVEST)

Each story is Independent, Negotiable, Valuable, Estimable, Small (≤ 1 sprint), and Testable. Story IDs map to feature IDs in Section 3.

| ID | Story | Maps to |
|----|-------|---------|
| US-01 | As Jose, I want to paste a Redfin URL and see a full FHA PITI (including upfront + annual MIP), so that I can judge affordability using the same number my lender will quote. | F1 |
| US-02 | As Jose, I want the tool to automatically apply a 75% rental offset on non-owner units when I mark a property as owner-occupied 2–4 unit, so that my qualifying income reflects FHA underwriting rules without manual math. | F2 |
| US-03 | As Jose, I want to see my max PITI at 45%, 50%, and 55% DTI side-by-side, so that I can see my conservative, stretch, and absolute-max budget at a glance. | F2 |
| US-04 | As Jose, I want a Green/Yellow/Red verdict tuned to my personal budget (≤ $3,000 net PITI, ≤ $45K cash, ≤ $60K rehab, priority zips), so that I'm not fooled by a deal that scores well on a generic cash-flow model but would bankrupt me. | F3 |
| US-05 | As Jose, I want a one-click "Vallejo Priority" / "East Bay Nearby" / "Richmond Motivated Sellers" preset, so that I can kick off a targeted search in one click instead of re-typing filters. | F6 |
| US-06 | As Jose, I want a "self-perform roofing" toggle that surfaces my contractor-edge savings as a distinct line item, so that I can see the dollar value of my C-39 license separately from base rehab. | F7 |
| US-07 | As Jose, I want to enter Unit 1 and Unit 2 rents separately for a duplex (not one blended rent), so that my owner-unit and rental-unit income are modeled correctly under FHA rules. | F5 |
| US-08 | As Jose, I want to save up to 3 scenarios and compare them side-by-side, so that I can weigh tonight's three top listings without losing state. | (scenario storage) |
| US-09 | As Jose, I want to export a clean PDF of a scenario, so that I can share it with my lender or my spouse without screen-sharing my laptop. | (PDF export) |
| US-10 | As Jose, I want profile defaults (down %, rate, tax rate, vacancy, management %, etc.) auto-filled on every new analysis, so that I never re-enter the same 17 fields. | F4 |
| US-11 | As Jose, I want a warning banner when I analyze a listing in an excluded zip (94803, 94806, Oakland, Berkeley, Sacramento), so that I don't accidentally burn time on an area I've already ruled out. | F8 |
| US-12 | As Jose, I want the tool to ask for roof age and flag >15 years with no seller credit as a Yellow-to-Red factor, so that my contractor eye is encoded in the scoring, not just in my head. | F9 |
| US-13 | As Jose, I want the full analysis (URL → verdict + reasons) to complete in ≤ 60 seconds wall-clock, so that I can triage a fresh Redfin email in one sitting instead of tab-switching for five minutes per listing. | F10 |

---

## 3. Feature-Level Acceptance Criteria

All criteria are observable from the UI or API response. No criterion depends on internal implementation details.

### F1 — FHA MIP in PITI Calculation

**Context:** Base tool computes P&I + tax + insurance. Must now add FHA MIP: upfront 1.75% (financed into loan) and annual 0.55%/12 (or 0.85%/12 if loan amount exceeds the high-cost threshold).

| # | Given | When | Then |
|---|-------|------|------|
| F1-AC1 | Loan type = FHA, purchase = $500,000, down = 3.5%, rate = 6.5%, term = 30 yr | I request PITI | Upfront MIP = 1.75% × base loan ($482,500) = $8,443.75, financed into loan (new loan ≈ $490,944) |
| F1-AC2 | Same inputs, loan amount below high-cost threshold | I view PITI breakdown | Annual MIP line shows 0.55%/12 of current loan balance; monthly MIP ≈ $225 initial |
| F1-AC3 | Loan amount exceeds county high-cost threshold | I view PITI breakdown | Annual MIP uses 0.85%/12 rate, not 0.55% |
| F1-AC4 | Any FHA PITI calculation | I view PITI breakdown in UI | P&I, property tax, insurance, and MIP are shown as four distinct line items with dollar values |
| F1-AC5 | $500K / 3.5% down / 6.5% / 30yr reference case | I view total PITI | Total PITI is within ±$10 of $4,004 (P&I $3,054 + tax $458 + ins $150 + MIP $342) |
| F1-AC6 | Loan type = Conventional (toggled away from FHA) | I view PITI breakdown | MIP line is hidden; PITI = P&I + tax + ins only |
| F1-AC7 | FHA MIP calculation | I inspect the API response | Response includes `mip_upfront`, `mip_annual_rate`, `mip_monthly`, and `loan_amount_with_upfront_mip` fields |

### F2 — 75% Rental Offset & DTI Display

**Context:** For owner-occupied 2–4 unit FHA, qualifying income = W-2 monthly + 0.75 × sum(non-owner-unit rents). Display max PITI at three DTI tiers.

| # | Given | When | Then |
|---|-------|------|------|
| F2-AC1 | owner_occupied = true, units = 2, W-2 monthly = $4,506, Unit 1 (owner) rent = N/A, Unit 2 rent = $2,100 | I view qualifying income | Qualifying income = $4,506 + (0.75 × $2,100) = $6,081 |
| F2-AC2 | Same inputs | I view DTI stretch panel | Three rows displayed: Max PITI @ 45% DTI = $2,736, @ 50% = $3,040, @ 55% = $3,345 (all within ±$1) |
| F2-AC3 | owner_occupied = false | I view the analysis | DTI stretch panel is hidden or marked "N/A — investor purchase" |
| F2-AC4 | units = 1 (single-family) | I view qualifying income | 75% offset does NOT apply; qualifying income = W-2 only |
| F2-AC5 | units = 3, Unit 1 (owner) rent = blank, Unit 2 rent = $1,800, Unit 3 rent = $1,700 | I view qualifying income | Qualifying income = W-2 + 0.75 × ($1,800 + $1,700) = W-2 + $2,625 |
| F2-AC6 | DTI stretch panel | I view the Jose-tuned PITI target row | The current property's total PITI (from F1) is compared against each DTI tier, with a green/red dot per tier |
| F2-AC7 | Any DTI tier where PITI exceeds max | I view that row | A warning icon and "Over by $X/mo" label is displayed |

### F3 — Jose-Tuned Green/Yellow/Red Scorer

**Context:** Parallel to existing investment scorer. Scores for Jose's personal house-hack fit, not general investment quality.

**Green criteria (all must hold):**
- Net PITI (PITI − 0.75 × non-owner rent) ≤ $2,500
- Cash needed at close ≤ $45,000
- Estimated rehab ≤ $60,000
- Zip in priority list (94590, 94591, Hercules, Rodeo, Crockett, Pinole, 94801, 94804, 94805)

**Red hard-fails (any one triggers Red):**
- Zip in excluded list (94803, 94806, Oakland, Berkeley, Sacramento)
- Flat roof disclosed in listing
- Unpermitted ADU disclosed
- Pre-1978 construction AND (galvanized plumbing OR knob-and-tube wiring) disclosed
- Total PITI > $3,200
- Cash needed at close > $60,000
- Estimated rehab > $80,000

**Yellow:** anything in between.

| # | Given | When | Then |
|---|-------|------|------|
| F3-AC1 | A listing meets all 4 Green criteria | I view the verdict | Verdict = Green; at least 2 "reasons" listed (e.g., "Net PITI $2,340 under $2,500 cap", "Priority zip 94590") |
| F3-AC2 | A listing triggers any Red hard-fail | I view the verdict | Verdict = Red; specific hard-fail reason(s) listed first |
| F3-AC3 | A listing is neither Green nor Red | I view the verdict | Verdict = Yellow; reasons list which Green criteria passed and which failed |
| F3-AC4 | Any verdict | I view the UI | Verdict color + reasons appear together — never a color alone |
| F3-AC5 | Verdict = Red due to excluded zip | I view reasons | First reason explicitly names the zip and the rule ("94803 on excluded list") |
| F3-AC6 | Jose-tuned scorer exists | I view analysis page | Both the Jose-tuned verdict and the base investment-quality score are shown, visually distinct |
| F3-AC7 | Multiple Red hard-fails | I view reasons | All hard-fail reasons are listed, not just the first |
| F3-AC8 | API response for any analysis | I inspect JSON | `jose_verdict` field contains `color` ("green"\|"yellow"\|"red") and `reasons` (array of strings) |

### F4 — Jose Profile Defaults

**Context:** 17 fields from handoff doc §2.1 must auto-populate on every new session so Jose never re-enters them.

| # | Given | When | Then |
|---|-------|------|------|
| F4-AC1 | I open a new analysis with no prior localStorage | The form renders | Down payment = 3.5%, rate = 6.5%, term = 30, loan type = FHA pre-selected |
| F4-AC2 | Same | Same | Property tax rate = 1.1%, insurance = $150/mo (or 0.36% annual), management = 0%, vacancy = 5%, maintenance = 8%, capex reserve = 5% |
| F4-AC3 | Same | Same | W-2 monthly = $4,506, credit score = 780, available cash = $85,000, monthly debts = $0 |
| F4-AC4 | Same | Same | Owner-occupied = true (checked by default), target units = 2–4 |
| F4-AC5 | I manually override a default in one session | I start a new analysis | The field resets to the Jose default, NOT my last override (profile defaults are sticky; session overrides are not) |
| F4-AC6 | I view the profile settings page | I look at all 17 fields | All 17 are editable and persist to localStorage on save |
| F4-AC7 | I clear localStorage | I reload | All 17 defaults restore from hardcoded fallback in source |

### F5 — Per-Unit Rent Inputs for Multi-Family

| # | Given | When | Then |
|---|-------|------|------|
| F5-AC1 | units = 2 | I view rent input section | Two inputs shown: "Unit 1 rent" and "Unit 2 rent" (not a single blended field) |
| F5-AC2 | units = 3 | I view rent input section | Three unit inputs shown |
| F5-AC3 | units = 4 | I view rent input section | Four unit inputs shown |
| F5-AC4 | owner_occupied = true, units = 2 | I view rent inputs | Unit 1 is labeled "Owner unit (Jose lives here)" and its rent input is disabled or marked "$0 — owner-occupied" |
| F5-AC5 | I enter $2,100 in Unit 2 for a duplex | I view qualifying income | 75% offset is applied to $2,100 only, not a blended figure |
| F5-AC6 | Rent estimates come back from the API | I view rent inputs | Each unit shows its estimated rent as placeholder; user can override per unit |

### F6 — Three Market Presets

| # | Given | When | Then |
|---|-------|------|------|
| F6-AC1 | I click "Vallejo Priority" preset | The search fires | Query includes zips 94590, 94591; price $400K–$550K; property type = multi-family 2–4 unit |
| F6-AC2 | I click "East Bay Nearby" preset | The search fires | Query includes Hercules, Rodeo, Crockett, Pinole; price range appropriate for duplex/triplex |
| F6-AC3 | I click "Richmond Motivated Sellers" preset | The search fires | Query includes 94801, 94804, 94805; DOM > 30 filter applied; multi-family 2–4 unit |
| F6-AC4 | Any preset click | I view the active search filters | All preset filters are visible and editable before I run; nothing is hidden |
| F6-AC5 | Any preset click | The results render | Results exclude zips on Jose's excluded list (94803, 94806) even if they appear in the geographic area |
| F6-AC6 | I modify a preset filter and search | I view results | The modified filter is respected; preset is not re-applied over my edit |

### F7 — Contractor Rehab Edge

| # | Given | When | Then |
|---|-------|------|------|
| F7-AC1 | Rehab estimate includes a roofing line item of $15,000 | I toggle "I self-perform roofing" ON | Roofing line reduces to ~$9,000 (≈40% reduction representing labor savings on a C-39 self-perform) |
| F7-AC2 | Toggle is ON | I view rehab breakdown | A new line "Contractor edge savings: $6,000" is displayed separately, with label indicating it comes from the self-perform toggle |
| F7-AC3 | Toggle is OFF (default) | I view rehab breakdown | No "Contractor edge savings" line; roofing at full market cost |
| F7-AC4 | Toggle is ON, no roofing line item exists in rehab | I toggle | Contractor edge savings = $0; a note clarifies "No roofing scope identified in this rehab" |
| F7-AC5 | Toggle is ON | I view the Green/Yellow/Red scorer inputs | Rehab total used for scoring is the post-edge (lower) figure |
| F7-AC6 | Toggle state | I reload the page | Toggle state persists per-scenario via localStorage |

### F8 — Excluded-Zip Guardrails

| # | Given | When | Then |
|---|-------|------|------|
| F8-AC1 | I paste a URL for a listing in 94803 | Analysis starts | A banner appears: "Warning: 94803 is on your excluded list" — analysis still runs (non-blocking) |
| F8-AC2 | I run a search preset that would include an excluded zip | Results render | Excluded-zip listings are filtered out AND a note says "N listings filtered: excluded zips 94803, 94806" |
| F8-AC3 | Excluded zip banner shown | I view Jose-tuned scorer | Verdict = Red with reason "Zip on excluded list" |
| F8-AC4 | I analyze an Oakland, Berkeley, or Sacramento address | Analysis runs | Banner + Red verdict both fire |
| F8-AC5 | Banner is dismissible | I click dismiss | Banner hides for this session only; re-appears on next excluded-zip analysis |

### F9 — Roof Age Gate

| # | Given | When | Then |
|---|-------|------|------|
| F9-AC1 | I start a new analysis | The form renders | A "Roof age (years)" input is visible in the property-condition section |
| F9-AC2 | Roof age > 15 AND no seller credit for roof in listing | I view the scorer | Factor "Aging roof, no seller credit" is listed; pushes Yellow → Red or Green → Yellow |
| F9-AC3 | Roof age > 15 AND "I self-perform roofing" toggled ON | I view the scorer | Factor is downgraded to informational (not scored against) because Jose can self-perform |
| F9-AC4 | Roof age ≤ 15 | I view the scorer | No roof-age factor appears |
| F9-AC5 | Roof age is blank/unknown | I view the scorer | A "Roof age unknown — inspect before offer" informational note is shown, not scored |

### F10 — 60-Second Total Latency

**Budget breakdown:** scrape < 5s, rate fetch < 2s, rent estimate < 5s, AI analysis < 30s, rendering < 1s. Total target ≤ 60s with ~17s headroom.

| # | Given | When | Then |
|---|-------|------|------|
| F10-AC1 | I paste a Redfin multi-family URL and click Analyze | I start a stopwatch | Green/Yellow/Red verdict + reasons are visible in ≤ 60s wall-clock |
| F10-AC2 | Same | I inspect network tab | Scrape request completes in ≤ 5s |
| F10-AC3 | Same | Same | Rate fetch completes in ≤ 2s |
| F10-AC4 | Same | Same | Rent estimate returns in ≤ 5s |
| F10-AC5 | Same | Same | AI narrative completes in ≤ 30s |
| F10-AC6 | Network is offline | I paste a URL | Math-only analysis renders in ≤ 10s; AI narrative section shows "offline — skipped" gracefully (non-blocking) |
| F10-AC7 | Any step exceeds its budget by > 2x | I view the UI | A debug timing panel surfaces the slow step (optional dev-mode only) |

---

## 4. Math Validation Test Cases

These are known-answer scenarios. Jose (or anyone) can run them to verify the tool's math is trustworthy. Tolerances are ±$10 on PITI unless noted.

### Test Case A — FHA MIP Reference

**Inputs:** Purchase $500,000, FHA, 3.5% down, 6.5% rate, 30-year term, property tax 1.1%, insurance $150/mo, loan amount below high-cost threshold.

| Component | Expected | Tolerance |
|-----------|----------|-----------|
| Base loan (before upfront MIP) | $482,500 | exact |
| Upfront MIP (1.75%) | $8,443.75 | ±$1 |
| Loan amount including upfront MIP | ~$490,944 | ±$10 |
| P&I (on ~$490,944 at 6.5%, 30yr) | ~$3,054 | ±$10 |
| Property tax ($500K × 1.1% / 12) | $458 | ±$1 |
| Insurance | $150 | exact |
| Monthly MIP (0.55%/12 × $490,944) | ~$342 | ±$5 |
| **Total PITI** | **~$4,004** | **±$10** |

### Test Case B — Green Scenario (Vallejo Duplex)

**Inputs:** $475,000 duplex in 94590, Jose owner-occupies Unit 1, Unit 2 rent $2,000/mo, FHA 3.5% down, 6.5% rate, 30yr, $30,000 rehab, roof age 8 yr, 780 credit, no hard-fails.

| Check | Expected |
|-------|----------|
| Qualifying income | $4,506 + (0.75 × $2,000) = $6,006 |
| Max PITI @ 50% DTI | ~$3,003 |
| Estimated PITI (similar math to Case A scaled to $475K) | ~$3,810 |
| Net PITI (PITI − 0.75 × $2,000) | ~$2,310 |
| Cash needed at close (3.5% down + closing ~$8K + rehab reserve) | ~$45,000 — borderline |
| Verdict | Green (if net PITI ≤ $2,500 AND cash ≤ $45K AND rehab ≤ $60K AND priority zip) |
| Reasons | Must include "Net PITI $2,310 under $2,500 cap", "Priority zip 94590", "Rehab $30K under $60K cap" |

### Test Case C — Red Scenario

**Inputs:** $650,000 duplex in 94803 (excluded zip), Unit 2 rent $1,500, $90,000 rehab estimate, flat roof disclosed.

| Check | Expected |
|-------|----------|
| Verdict | Red |
| Reasons (must include) | "Zip 94803 on excluded list", "Rehab $90K over $80K hard-fail cap", "Flat roof — hard-fail" |
| Cash-needed warning | Displayed |
| Analysis still completes | Yes (Red does not halt; it warns) |

### Test Case D — Reference Property: 1035-1037 Virginia St, Vallejo

**Inputs:** Jose buys at the sold price of $375,000 (sold April 2026), assumed duplex, FHA 3.5% down, 6.5% rate, 30yr, assume Unit 2 rent $1,900/mo, property tax 1.1%, insurance $150/mo, roof age unknown, no hard-fails disclosed.

| Check | Expected |
|-------|----------|
| Base loan | $361,875 |
| Upfront MIP | $6,333 |
| Loan with upfront MIP | ~$368,208 |
| P&I (~$368,208 at 6.5% / 30) | ~$2,292 |
| Property tax | $344 |
| Insurance | $150 |
| Monthly MIP (0.55%/12) | ~$256 |
| **Total PITI** | **~$3,042** |
| Qualifying income | $4,506 + (0.75 × $1,900) = $5,931 |
| Max PITI @ 50% DTI | ~$2,966 |
| Net PITI | ~$1,617 |
| Verdict | Likely Green on net PITI & zip; Yellow if roof age unknown (F9-AC5 informational); final color depends on rehab estimate |

---

## 5. Definition of Done — Per Sprint

Each DoD is testable, verifiable in under 10 minutes, and must be signed off by Jose personally via a checkbox commit. Self-sign-off by the engineer is not acceptance.

### Sprint 0 — Bootstrap & Profile Defaults (F4)

- [ ] Local dev environment runs `python app.py` and serves on `http://localhost:8000` in ≤ 5s
- [ ] `.env` is untracked (confirm via `git status` — must not appear)
- [ ] All 17 profile default fields load on new session (F4-AC1 through F4-AC7 pass)
- [ ] localStorage persistence verified: set values, reload, values survive
- [ ] README updated with Jose-specific setup notes
- [ ] **Jose sign-off:** opens fresh browser, confirms all 17 fields pre-filled to his values

### Sprint 1 — FHA PITI Math (F1)

- [ ] F1-AC1 through F1-AC7 pass
- [ ] Test Case A produces total PITI within ±$10 of $4,004
- [ ] Toggle between FHA and Conventional shows/hides MIP line correctly
- [ ] Unit tests for PITI math committed and passing
- [ ] **Jose sign-off:** runs Test Case A, confirms $4,004 within tolerance

### Sprint 2 — Rental Offset, DTI Display, Per-Unit Rents (F2, F5)

- [ ] F2-AC1 through F2-AC7 pass
- [ ] F5-AC1 through F5-AC6 pass
- [ ] DTI stretch panel (45/50/55%) visible and accurate on any owner-occupied multi-family
- [ ] Test Case B qualifying income calculation verified
- [ ] **Jose sign-off:** enters his W-2 + a $2,100 Unit 2 rent, confirms max PITI @ 50% DTI = $3,040

### Sprint 3 — Jose Scorer, Roof Age, Excluded Zips (F3, F8, F9)

- [ ] F3-AC1 through F3-AC8 pass
- [ ] F8-AC1 through F8-AC5 pass
- [ ] F9-AC1 through F9-AC5 pass
- [ ] Test Case B returns Green with correct reasons
- [ ] Test Case C returns Red with correct reasons
- [ ] Jose-tuned verdict and base investment score both visible and visually distinct
- [ ] **Jose sign-off:** pastes a known-excluded-zip listing and a known-Green listing, verdicts match expectations

### Sprint 4 — Presets & Contractor Edge (F6, F7)

- [ ] F6-AC1 through F6-AC6 pass
- [ ] F7-AC1 through F7-AC6 pass
- [ ] All three presets (Vallejo Priority, East Bay Nearby, Richmond Motivated Sellers) fire correct filter payloads
- [ ] Contractor edge savings line appears as a distinct, labeled row
- [ ] **Jose sign-off:** clicks each preset, sees correct zips + price range; toggles self-perform roofing on a rehab estimate with a roofing line and sees ~40% reduction

### Sprint 5 — Scenarios, PDF Export, Latency, Polish (US-08, US-09, F10)

- [ ] Up to 3 scenarios can be saved and compared side-by-side
- [ ] PDF export renders cleanly with all key numbers, verdict, and reasons
- [ ] F10-AC1 through F10-AC7 pass
- [ ] Test Case D (1035-1037 Virginia St) runs end-to-end in ≤ 60s
- [ ] Offline math-only mode works (AI narrative degrades gracefully)
- [ ] **Jose sign-off:** runs full paste-URL-to-verdict flow on a live Redfin URL with stopwatch, confirms ≤ 60s; exports a PDF and opens it

---

## 6. Out-of-Scope Guardrails (V1)

Explicit "no" list. Any of these can be revisited in V2, but adding them to V1 scope requires a formal change request and Jose's written approval.

| Item | Why out of scope for V1 |
|------|-------------------------|
| CalHFA integration (beyond optional field) | Not Jose's near-term funding path; optional text field is sufficient for now. Full integration adds scope without decision value. |
| BiggerPockets scraper | Low signal-to-noise for Jose's specific market; Redfin coverage is enough for V1 triage. |
| SMS / email alerts | Jose already monitors Redfin email alerts; duplicating that channel adds infra (Twilio/SMTP) without new signal. |
| County permit API integration | Too brittle across Solano / Contra Costa / Alameda; Jose will pull permits manually during due diligence. |
| Vue / Firebase rewrite | Current stack works; rewrite burns sprint capacity with zero user-visible outcome. |
| Public deploy / multi-user auth | Single-user local tool. Public deploy introduces auth, secrets management, and hosting cost for zero benefit to Jose. |
| BRRRR / DSCR investor models | V1 is FHA owner-occupied house-hack only. Investor models dilute the Jose-tuned scorer. |
| Full property management / tenant features | Post-purchase tooling, not pre-purchase triage. Out of scope. |

---

## 7. Non-Functional Requirements

| NFR | Requirement | Verification |
|-----|-------------|--------------|
| Deployment | Local-only. No public URL. Must bind to `127.0.0.1` or `localhost`, not `0.0.0.0` unless explicitly justified. | `netstat -an \| grep 8000` shows localhost bind only |
| Secrets | `.env` is in `.gitignore` and never committed. | `git log --all -- .env` returns empty |
| Auth | Single-user. No login screen, no session mgmt. | Visible in code review |
| Persistence | localStorage only. No database, no server-side user state. | No migrations, no DB driver in requirements.txt |
| Startup time | `python app.py` to listening on :8000 in ≤ 5s on Jose's MacBook. | Stopwatch test on Sprint 5 sign-off |
| Offline behavior | Math (PITI, DTI, scorer) works offline. AI narrative degrades gracefully with a clear "offline" message — does not block verdict. | Toggle Wi-Fi off, run Test Case A |
| Data freshness | Rates fetched per-analysis, not cached beyond current session. | Network tab inspection |
| Browser support | Latest Chrome and Safari on macOS. No IE, no mobile-first requirement. | Manual check |
| Accessibility | Keyboard-navigable forms; color + text for verdict (never color alone — see F3-AC4). | Manual check |
| Error handling | Every external call (scrape, rate API, AI) has a timeout and user-visible fallback. | F10-AC6 covers AI; analogous for scrape/rate |

---

## 8. Sign-Off Ritual

Acceptance is personal and explicit. Jose signs off — not the engineer, not automated tests alone.

**Ritual:**

1. Engineer completes a sprint and marks all sprint DoD boxes in a draft PR.
2. Engineer runs **all ten** feature-level acceptance criteria from Section 3 against:
   - (a) the reference property in Test Case D (1035-1037 Virginia St, Vallejo), and
   - (b) a live Vallejo duplex URL Jose is analyzing that week (Jose supplies the URL).
3. Engineer records pass/fail per criterion in the PR description.
4. Jose opens the PR, re-runs the same ten criteria himself against the same two properties.
5. Jose ticks the sprint's DoD checkboxes in this document and commits with message: `acceptance: Sprint N signed off by Jose`.
6. PR merges only after the sign-off commit lands.

**Sign-off commit format:**

```
acceptance: Sprint <N> signed off by Jose

- [x] All Sprint <N> DoD items verified
- [x] Feature-level AC tested against reference property (Test Case D)
- [x] Feature-level AC tested against live listing: <URL>
- Tested on: <date>
- Notes: <anything that surprised Jose or needs follow-up>
```

**Global V1 sign-off (after Sprint 5):**

- [ ] Sprint 0 DoD signed off
- [ ] Sprint 1 DoD signed off
- [ ] Sprint 2 DoD signed off
- [ ] Sprint 3 DoD signed off
- [ ] Sprint 4 DoD signed off
- [ ] Sprint 5 DoD signed off
- [ ] All four math test cases (A, B, C, D) pass to tolerance
- [ ] End-to-end: Redfin URL → verdict + reasons in ≤ 60s on Jose's machine
- [ ] Jose has used the tool to triage at least 5 live listings without asking the engineer a single question

When all boxes above are checked, V1 is accepted. Any post-V1 feature request becomes a V2 conversation governed by the same acceptance process.

---

**End of document.**
