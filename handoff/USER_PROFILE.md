# Jose — User Profile (Authoritative)

**Last updated:** 2026-04-17
**Use:** canonical reference for all customization work. Every hardcoded default, scoring threshold, and UI copy choice in the tool must trace back to a line in this doc.

Do NOT edit numbers here without explicit user approval. Treat as contract.

---

## 1. Identity

| Field | Value |
|---|---|
| Name | Jose |
| Location | Vallejo, CA (Solano County) |
| Role | W-2 employee + CSLB-licensed roofing contractor (C-39 assumed) |
| Other businesses | Roofing (Schedule C) + Peritik (Schedule C) — both written off to near-zero net |
| First-time homebuyer | Yes (California) |
| Veteran | No |
| Co-borrower | **None — solo buyer** (no spouse/parent/partner on the loan) |
| Lender relationship | **None yet** — not pre-approved, not actively shopping lenders |

---

## 2. Financial profile — qualifying

These are the numbers lenders will underwrite against.

| Field | Value | Source |
|---|---|---|
| W-2 income | **$54,080/yr** | $26/hr × 40 × 52 |
| W-2 monthly gross | **$4,506/mo** | Annual ÷ 12 |
| Self-employment income (for qualifying) | **$0** | Schedule C written off — lenders count zero |
| Credit score | **780+** | Perfect tier — allows 50%+ DTI on FHA |
| Monthly debts | **$0** | No car loan, no student loan, no revolving balance |
| Documented cash (Jose's accounts) | **$85,000** | Savings + investment |
| Employment tenure at W-2 job | **1–2 years** | Sufficient for FHA |

**Rule:** do NOT include any Schedule C / 1099 / SE income in qualifying math. Lenders will see near-zero net on those.

---

## 3. Loan strategy

| Field | Value |
|---|---|
| Loan program | **FHA 203(b) standard** (NOT 203(k) rehab loan) |
| Down payment | **3.5%** |
| Occupancy | **Owner-occupied 2–4 unit** (duplex/triplex) |
| Rental offset rule | **75%** of projected rent from non-owner-occupied units counts as qualifying income |
| Default DTI ceiling (no lender yet) | **45%** — conservative default until a lender commits |
| Stretch DTI context | Display 50% and 55% alongside 45% so Jose can see range once a lender is selected |
| Loan term | 30-year fixed |
| Interest rate (default) | **6.5%** (verify with Freddie Mac button each session) |
| FHA upfront MIP | **1.75%** of base loan — financed into loan amount |
| FHA annual MIP | **0.55%** for standard; **0.85%** if loan > ~$625K (high-cost threshold) |
| CalHFA MyHome | **Optional field** — check availability at time of offer |
| Path chosen | **Path A**: move-in-ready duplex + cash-funded rehab after move-in |

---

## 4. Budget parameters — hard limits

Jose's scoring thresholds must enforce these as hard gates.

| Field | Value |
|---|---|
| **Max net monthly PITI after rental offset** | **$3,000/mo** out-of-pocket |
| Max cash at close | **$45,000** (down + closing + reserves) |
| Reserve requirement | **≥ 2 months PITI liquid** after close |
| Max rehab budget | **$40K–$75K** over 12–24 months |
| Rehab scope | Cosmetic to mid-level (no gut jobs, no major structural) |
| Purchase price ceiling — duplex | **$525,000** |
| Purchase price ceiling — triplex | **$650,000** |
| Ideal duplex sweet spot | **$425K–$525K** list price |
| Offer range on stale listings | 5–8% below list on DOM > 30 |

---

## 5. Contractor profile (C-39 edge)

Jose holds a CSLB license, classification **C-39 (Roofing) — confirmed**. Self-performs roofing only; every other trade is subcontracted at retail rates.

| Category | Self-performs? | Cost multiplier vs retail |
|---|---|---|
| Roofing | **YES** (C-39) | **0.60** (40% savings) |
| Plumbing | No | 1.0 (retail) |
| Electrical | No | 1.0 |
| HVAC | No | 1.0 |
| Cosmetic (paint, floor, fixtures) | Partial | 0.80 (20% savings — some DIY) |
| Framing/structural | No | 1.0 |
| Other | No | 1.0 |

(If Jose ever upgrades to B-General or a multi-trade classification, revisit this table.)

---

## 6. Target markets — priority tiers

All tools (scorer, presets, excluded-zip warnings) reference these tiers.

### Tier 1 — Vallejo Priority

- **94590** (Old City, Heights)
- **94591** (East Vallejo — EXCLUDING Glen Cove and Hiddenbrooke)

Price range: **$400K–$550K**. Keywords for scraper: "duplex", "two on one", "income property", "fixer".

### Tier 2 — East Bay Nearby

- **94547** — Hercules
- **94572** — Rodeo
- **94525** — Crockett
- **94564** — Pinole

Price range: **$400K–$600K**.

### Tier 3 — Richmond Motivated Sellers

- **94801**, **94804**, **94805** — Richmond ONLY
- Filter: **Days on market > 30** (motivated seller signal)

Price range: **$400K–$575K**.

---

## 7. Excluded markets — hard NO

Tool must warn or block when user enters any of these.

| Excluded | Reason |
|---|---|
| Benicia | Over budget (median $786K+) |
| Glen Cove / Hiddenbrooke / Mare Island (Vallejo) | Over budget |
| Oakland | Rent control + price |
| Berkeley | Rent control + price |
| Point Richmond (**94803**) | Over budget |
| Hilltop Richmond (**94806**) | Outside target sub-market |
| Sacramento | Flat market, too far from Vallejo base |
| Peninsula / Redwood City | Far over budget |

---

## 8. Property profile requirements

For a listing to qualify for FHA appraisal and Jose's strategy:

**Required:**
- Legal 2–4 unit (verify via permit history)
- Separate entrances
- Livable at appraisal (ugly/dated OK; gut job NOT OK)
- Roof < 15 years old OR seller credit for replacement negotiated

**Preferred:**
- Separate electric meters
- Separate gas meters

**Disqualifying (hard RED gate):**
- Flat-roof commercial conversion
- Unpermitted ADU or garage conversion
- Foundation cracks beyond hairline
- Pre-1978 home with BOTH original galvanized plumbing AND knob-and-tube electrical
- Located in rent-controlled city (Oakland, Berkeley)

---

## 9. Expected market rents (as of April 2026)

Used as sanity-check bands for the rent estimator. If scraped comps fall far outside these bands, widen the comp radius or prompt manual override.

| Area | 2BR/1BA unit | 3BR/2BA unit |
|---|---|---|
| Vallejo 94590 | **$1,900–$2,300** | $2,500–$2,900 |
| Vallejo 94591 (non-Glen Cove) | $2,000–$2,400 | $2,600–$3,100 |
| Hercules / Rodeo / Crockett | $2,100–$2,500 | $2,700–$3,200 |
| Richmond 94801/94804/94805 | $1,900–$2,400 | $2,500–$3,000 |

Validated live 2026-04-17: Vallejo 94590 2BR returned $2,026–$2,599 from `/api/rent-estimate` — in band.

---

## 10. Tool default field values

These are the authoritative defaults for the central `DEFAULTS` config to be built in Sprint 2.

| Field | Default | Rationale |
|---|---|---|
| `buyerAnnualIncomeW2` | `54080` | W-2 gross |
| `buyerMonthlyIncomeW2` | `4506` | W-2 gross / 12 |
| `seIncomeForQualifying` | `0` | Schedule C zeroed |
| `creditScore` | `780` | Documented tier |
| `monthlyDebts` | `0` | $0 existing debt |
| `downPaymentPct` | `3.5` | FHA minimum |
| `loanType` | `FHA` | |
| `loanTerm` | `30` | years |
| `interestRate` | `6.5` | Override with Freddie Mac fetch button |
| `fhaUpfrontMipPct` | `1.75` | Financed into loan |
| `fhaAnnualMipPct` | `0.55` | Use `0.85` if loan > $625,500 |
| `propertyTaxRatePct` | `1.1` | Solano County (verify for Contra Costa) |
| `insuranceAnnual` | `1800` | Typical duplex in target zips |
| `hoaMonthly` | `0` | Duplexes rarely have HOA |
| `vacancyPct` | `5` | Conservative Bay Area |
| `maintenancePct` | `5` | Of gross rent |
| `propertyManagementPct` | `0` | Self-manages while owner-occupied |
| `ownerOccupied` | `true` | FHA requirement |
| `unitsOccupiedByOwner` | `1` | Lives in one unit |
| `closingCostsPct` | `3` | California standard |
| `rentalOffsetPct` | `75` | FHA rule |
| `maxDtiPct` | `45` | Conservative default — no lender selected yet; stretch to 50/55 when lender commits |
| `maxNetPitiTarget` | `3000` | Hard budget |
| `maxCashToClose` | `45000` | Hard budget |
| `reserveMonths` | `2` | Minimum liquid after close |
| `selfPerformRoofing` | `true` | C-39 |
| `roofingCostMultiplier` | `0.60` | 40% savings |

---

## 11. Green / Yellow / Red signal thresholds

Used by the Jose-tuned scorer (parallel to the existing investment-quality scorer).

### GREEN — pursue aggressively

**ALL of:**
- Purchase price ≤ $525K (duplex) or $650K (triplex)
- **Net monthly PITI after 75% rental offset ≤ $2,500**
- Cash to close ≤ $45,000
- Rehab needed ≤ $60,000
- Located in Tier 1 or Tier 2 zip

### YELLOW — investigate further

**Any of:**
- Net monthly PITI $2,500–$3,200
- Cash to close $45K–$60K
- Rehab needed $60K–$80K
- Located in Tier 3 zip
- Roof 10–15 years old (nearing FHA appraisal concern)

### RED — skip

**Any of:**
- Net monthly PITI > $3,200
- Cash to close > $60K (depletes reserves)
- Rehab > $80K (exceeds budget)
- Located in excluded market (see §7)
- Flat roof commercial conversion
- Unpermitted ADU or garage conversion
- Pre-1978 with original galvanized plumbing AND knob-and-tube
- SFR without legal ADU (no rental offset possible)

---

## 12. Non-negotiable product principles

1. **Decision-grade output in ≤ 60 seconds** from URL paste.
2. **Explain every color.** G/Y/R must always include the reasons (which thresholds hit/missed).
3. **Local-only.** No public deploy, no sharing of Jose's financial inputs.
4. **Defaults are a starting point, not a cage.** Every field editable.
5. **Math correctness > feature count.** Sprint 0 (tests) before any math changes.
6. **No unit-based abstractions pretending to know things we don't.** If unit count is unknown for a listing, ASK — don't assume.

---

## 13. Sign-off

Jose owns this doc. Any field change requires his explicit approval. This is the contract every sprint ships against.

Related docs:
- [`HANDOFF.md`](./HANDOFF.md) — full mission brief
- [`TECHNICAL_ASSESSMENT.md`](./TECHNICAL_ASSESSMENT.md) — current tool state + gaps
- [`SPRINT_PLAN.md`](./SPRINT_PLAN.md) — how we close the gaps
- [`USER_FLOW.md`](./USER_FLOW.md) — session flow + branch conditions
- [`ACCEPTANCE_CRITERIA.md`](./ACCEPTANCE_CRITERIA.md) — testable DoD per sprint
