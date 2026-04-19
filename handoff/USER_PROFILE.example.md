# User Profile (Example — Redacted Template)

**Last updated:** `<YYYY-MM-DD>`
**Use:** copy this file to `USER_PROFILE.md` and fill in your own values. The real
`USER_PROFILE.md` is gitignored (Sprint 10A §10-1) — do NOT commit a filled copy.

Treat this as the contract every sprint ships against. Every hardcoded default,
scoring threshold, and UI copy choice in the tool must trace back to a line in
this doc.

---

## 1. Identity

| Field | Value |
|---|---|
| Name | `<first name>` |
| Location | `<city, county, state>` |
| Role | `<W-2 role + side businesses>` |
| Other businesses | `<Schedule C entities — rolled to near-zero net?>` |
| First-time homebuyer | `<Yes/No>` |
| Veteran | `<Yes/No>` |
| Co-borrower | `<None, or relationship>` |
| Lender relationship | `<None yet / pre-approved with X>` |

---

## 2. Financial profile — qualifying

These are the numbers lenders will underwrite against.

| Field | Value | Source |
|---|---|---|
| W-2 income | `<$annual>` | `<derivation>` |
| W-2 monthly gross | `<$monthly>` | Annual ÷ 12 |
| Self-employment income (for qualifying) | `<$ — usually $0 if Schedule C written off>` | |
| Credit score | `<e.g. 780>` | |
| Monthly debts | `<$>` | |
| Documented cash | `<$ total in accounts>` | |
| Employment tenure at W-2 job | `<years>` | |

**Rule:** do NOT include any Schedule C / 1099 / SE income in qualifying math
unless you have two full years of tax returns showing positive net.

---

## 3. Loan strategy

| Field | Value |
|---|---|
| Loan program | `<FHA 203(b) / Conventional / VA / etc.>` |
| Down payment | `<%>` |
| Occupancy | `<Owner-occupied 2–4 unit / SFR / investment>` |
| Rental offset rule | `<%>` |
| Default DTI ceiling | `<% — conservative default until lender commits>` |
| Loan term | `<years>` |
| Interest rate (default) | `<% — verify each session>` |
| FHA upfront MIP | `<% if applicable>` |
| FHA annual MIP | `<% if applicable>` |
| Path chosen | `<A/B/C per your plan doc>` |

---

## 4. Budget parameters — hard limits

| Field | Value |
|---|---|
| Max net monthly PITI after rental offset | `<$/mo>` |
| Max cash at close | `<$>` |
| Reserve requirement | `<months liquid PITI>` |
| Max rehab budget | `<$ range>` |
| Purchase price ceiling — duplex | `<$>` |
| Purchase price ceiling — triplex | `<$>` |

---

## 5. Contractor profile (if applicable)

| Category | Self-performs? | Cost multiplier vs retail |
|---|---|---|
| Roofing | `<Y/N>` | `<0.0–1.0>` |
| Plumbing | `<Y/N>` | `<0.0–1.0>` |
| Electrical | `<Y/N>` | `<0.0–1.0>` |
| HVAC | `<Y/N>` | `<0.0–1.0>` |
| Cosmetic | `<Y/N>` | `<0.0–1.0>` |

---

## 6. Target markets — priority tiers

### Tier 1
- `<ZIP>` (`<neighborhood>`)

### Tier 2
- `<ZIP>` (`<city>`)

### Tier 3
- `<ZIP>` (`<city>`) — optional filter: `<DOM > N>`

---

## 7. Excluded markets — hard NO

| Excluded | Reason |
|---|---|
| `<city or ZIP>` | `<over budget / rent control / etc.>` |

---

## 8. Property profile requirements

Required / Preferred / Disqualifying — fill per your strategy.

---

## 9. Expected market rents

| Area | 2BR/1BA | 3BR/2BA |
|---|---|---|
| `<ZIP>` | `<$low–$high>` | `<$low–$high>` |

---

## 10. Tool default field values

These are the authoritative defaults for the central `DEFAULTS` config
(mirrored in `spec/profile.local.json` — also gitignored).

| Field | Default | Rationale |
|---|---|---|
| `buyerAnnualIncomeW2` | `<N>` | |
| `buyerMonthlyIncomeW2` | `<N>` | |
| `creditScore` | `<N>` | |
| `monthlyDebts` | `<N>` | |
| `downPaymentPct` | `<N>` | |
| (etc. — see real USER_PROFILE.md §10 for full field list) | | |

---

## 11. Green / Yellow / Red signal thresholds

Fill per `spec/profile.local.json → jose` block.

---

## 12. Non-negotiable product principles

1. Decision-grade output in ≤ 60 seconds from URL paste.
2. Explain every color. G/Y/R must always include reasons.
3. Local-only. No public deploy, no sharing of financial inputs.
4. Defaults are a starting point, not a cage.
5. Math correctness > feature count.
6. No unit-based abstractions pretending to know things we don't.
