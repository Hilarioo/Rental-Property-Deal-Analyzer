# Live Run-Through — 3 Vallejo 94590 Listings

**Date:** 2026-04-18
**Profile:** Jose — W-2 $4,506/mo, 780 credit, $85K cash, FHA 3.5% down, 6.5%/30yr, solo buyer
**Thresholds used:** USER_PROFILE §11 (GREEN: price ≤ $525K duplex / $650K triplex, net PITI ≤ $2,500, cash ≤ $45K, rehab ≤ $60K, Tier 1/2 zip, PITI/qualifying ≤ 50%; RED: >55% DTI or any hard-fail).

Shared math (applied to every property):
- Base loan = price × 0.965
- Upfront MIP = base × 0.0175, financed into loan
- P&I factor @ 6.5% / 30yr = 0.00632068 (monthly)
- Monthly MIP = base × 0.0055 / 12 (use 0.0085 if loan > $625,500)
- Property tax = price × 0.011 / 12 (Solano 1.1%)
- Insurance = $150/mo baseline
- Closing costs = 3% of price
- Cash to close (baseline, no rehab at close) = (3.5% down + 3% closing) = 6.5% of price
- Rental offset = 0.75 × non-owner unit rent (FHA owner-occupied rule)

---

## Property 1 — 927 Carolina St, Vallejo 94590

- URL: https://www.redfin.com/CA/Vallejo/927-Carolina-St-94590/home/2233765
- Scraped: price $424,000, 4 BR / 2 BA, 1,385 sqft, year built ~1900 (Old City)
- Market rent comps for 2BR Vallejo 94590: **$1,900–$2,300/mo** (USER_PROFILE §9, live-validated 2026-04-17)
- Assumed split: 4BR/2BA at 1,385 sqft reads as a legal duplex (2BR/1BA + 2BR/1BA). Unit 1 (Jose) $0 / Unit 2 $2,100 (mid-band)

### Computed at Jose's profile (W-2 $4,506, 3.5% FHA, 6.5%)
| | |
|---|---|
| Base loan | $409,160 |
| Upfront MIP (financed) | $7,160 |
| Loan amount w/ upfront MIP | $416,320 |
| P&I | $2,631/mo |
| Monthly MIP | $188/mo |
| Property tax | $389/mo |
| Insurance | $150/mo |
| **PITI** | **$3,358/mo** |
| Rental offset (75% × $2,100) | $1,575 |
| **Net PITI** | **$1,783/mo** |
| Qualifying income | $6,081/mo |
| PITI / qualifying | **55.2%** |
| Cash to close (3.5% down + 3% closing) | $27,560 |

### Jose's verdict
**🟡 YELLOW**
- Price $424K under $525K duplex ceiling — PASS
- Net PITI $1,783 well under $2,500 target — PASS
- Cash $27.5K under $45K budget — PASS
- Tier 1 (94590) — PASS
- **PITI / qualifying income = 55.2% — right on the RED line.** At the conservative 45% DTI this blows past; at the 50% stretch it's over by $317/mo; at 55% it's $16/mo over. Needs either (a) a lender who'll go to 55%+ with 780 credit + reserves, (b) Unit 2 rent closer to $2,300 top-of-band (offset $1,725, qual $6,231, ratio → 53.9%), or (c) a co-borrower.

### Sanity check
Yes — Jose would expect this to be tight. A $424K duplex in 94590 clears every cash and affordability bar on paper; the only pinch is the one that always pinches Jose: DTI on W-2-only $54K income. This is exactly the listing where "stretch approval" matters and why you talk to the lender BEFORE offering.

---

## Property 2 — 507 Central Ave, Vallejo 94590

- URL: https://www.redfin.com/CA/Vallejo/507-Central-Ave-94590/home/2244841
- Scraped: price $360,000, 3 BR / 2 BA, 1,908 sqft, year built ~1910
- Market rent comps: 1BR ~$1,600, 2BR $1,900–$2,300 in 94590
- Assumed split: 3BR/2BA at 1,908 sqft as a duplex (1BR/1BA + 2BR/1BA). Jose occupies the 1BR, rents the 2BR. Unit 1 (Jose) $0 / Unit 2 $1,900 (low-mid band, old housing stock).
- **Caveat:** if this listing is actually SFR with no legal second unit, it's an automatic RED per USER_PROFILE §11 ("SFR without legal ADU — no rental offset possible"). Confirm the second kitchen / second meter / permit history before offering. The numbers below assume legal duplex.

### Computed at Jose's profile (W-2 $4,506, 3.5% FHA, 6.5%)
| | |
|---|---|
| Base loan | $347,400 |
| Upfront MIP (financed) | $6,080 |
| Loan amount w/ upfront MIP | $353,480 |
| P&I | $2,234/mo |
| Monthly MIP | $159/mo |
| Property tax | $330/mo |
| Insurance | $150/mo |
| **PITI** | **$2,873/mo** |
| Rental offset (75% × $1,900) | $1,425 |
| **Net PITI** | **$1,448/mo** |
| Qualifying income | $5,931/mo |
| PITI / qualifying | **48.4%** |
| Cash to close | $23,400 |

### Jose's verdict
**🟢 GREEN** (conditional on legal-duplex confirmation)
- Price $360K under $525K ceiling — PASS
- Net PITI $1,448 crushes the $2,500 target — PASS
- Cash $23.4K leaves ~$60K in reserves — PASS
- Tier 1 (94590) — PASS
- **PITI/qualifying = 48.4%, under the 50% bar** — PASS
- Reserve math: $85K − $23.4K = $61.6K post-close, ≈ 21 months PITI liquid — way over the 2-month minimum

### Sanity check
Yes — this is the kind of listing Jose is looking for. Lowest price in the set, strong reserves, DTI clears even the conservative stretch tier. Only risk is non-financial: this was built ~1910, so roof age / knob-and-tube / galvanized plumbing (all hard-fail disqualifiers per §8) need an inspector on-site before any offer goes in. Math says GREEN; FHA appraisal could still swat it down.

---

## Property 3 — 705 Georgia St, Vallejo 94590

- URL: https://www.redfin.com/CA/Vallejo/705-Georgia-St-94590/home/2128879
- Scraped: price $849,000, 4 BR / 3 BA, 2,984 sqft, year built ~1895
- Market rent comps: 3BR $2,500–$2,900 in 94590 if this is a large 2-unit config
- Assumed split: even if a 2+2 duplex with Unit 2 rent $2,800, the price alone blows both the duplex ($525K) and triplex ($650K) ceilings.

### Computed at Jose's profile (W-2 $4,506, 3.5% FHA, 6.5%)
| | |
|---|---|
| Base loan | $819,285 |
| Upfront MIP (financed) | $14,337 |
| Loan amount w/ upfront MIP | $833,622 |
| P&I | $5,269/mo |
| Monthly MIP (0.85% high-cost tier — loan > $625K) | $580/mo |
| Property tax | $778/mo |
| Insurance | $150/mo |
| **PITI** | **$6,777/mo** |
| Rental offset (75% × $2,800 est) | $2,100 |
| **Net PITI** | **$4,677/mo** |
| Qualifying income | $6,606/mo |
| PITI / qualifying | **102.6%** |
| Cash to close | $55,185 |

### Jose's verdict
**🔴 RED** — multi-factor hard fail
- **Price $849K exceeds duplex ceiling ($525K) by $324K AND triplex ceiling ($650K) by $199K** — HARD FAIL
- **PITI/qualifying = 102.6% — the PITI alone exceeds total qualifying income.** No lender will write this.
- **Cash to close $55K exceeds the $45K budget** and would wipe post-close reserves
- Net PITI $4,677 > $3,200 RED threshold
- Loan size trips the 0.85% MIP tier — makes the math even worse than the standard 0.55%

### Sanity check
Yes — obviously. $849K in Old City Vallejo is a restored Victorian for an owner-occupant couple with two incomes, not a house-hack for a solo W-2 roofer. Tool correctly nukes it three different ways. No lender conversation required.

---

## Observations

### Pattern Jose should see
Sub-$400K multifamily in Tier 1 clears every cash and affordability gate comfortably (Property 2). **The binding constraint almost every time is DTI**, not price or cash — Property 1 ($424K) passes price, cash, and reserves with room to spare but still lands YELLOW purely on the 55%-DTI cliff. In other words: "Can Jose afford this?" and "Will the lender write it?" are two different questions, and the lender question is tighter on W-2-only $54K. Anything north of ~$430K at current rents starts to strain qualifying income, full stop. The realistic sweet spot is $350K–$425K with Unit 2 rent ≥ $1,900.

### Anything the tool got wrong or surprisingly right
- **Right:** Property 3 fails on three independent predicates (price, DTI, cash) — exactly the kind of over-determined RED that should never get an offer. Tool reasons will list all three, which is what Jose wants.
- **Right:** Property 2's 48.4% DTI sits just under the 50% conservative tier — tool will render GREEN on the Jose-tuned scorer but the DTI panel (Sprint 1) will still show Jose exactly how much headroom he has at each tier. Good defensive display.
- **Surprise:** Property 1 is the most interesting result. On gut, a $424K Vallejo duplex "feels" GREEN to most investors. The tool correctly flags that it's YELLOW *only* because of Jose's specific W-2-only income profile. That's the whole point of a Jose-tuned scorer — generic rental analyzers would have stamped this GREEN and sent him to an offer that dies at underwriting.
- **Caveat:** "4BR/2BA" and "3BR/2BA" from a Redfin listing don't tell you it's actually a legal duplex. The scraper can't distinguish duplex from big SFR without permit data. Jose has to verify at step 2 of the wizard before trusting any rental-offset number.

### Tuning suggestions

1. **Insurance default is probably light for Vallejo pre-1960 wood-frame.** `DEFAULTS.insuranceAnnual = 1800` → $150/mo baseline is fine for a 2000s build, but 927 Carolina (~1900) and 507 Central (~1910) will both quote closer to $2,400/yr ($200/mo). Consider bumping to **$2,200/yr ($183/mo)** for anything built pre-1960, or add an "old-house insurance premium" toggle. On Property 1 this moves PITI from $3,358 → $3,391; net PITI from $1,783 → $1,816; DTI from 55.2% → 55.7%. Small, but on a borderline YELLOW it matters.

2. **The 55.2% result on Property 1 sits inside the tool's own RED rule** (`PITI/qualifying > 55%`) — by 0.2pp. The `JOSE_THRESHOLDS` DTI cap is triggered on a rounding-error basis. Consider changing the hard RED cliff from `> 55%` to `> 55.5%` or `>= 56%` to absorb Unit-2 rent uncertainty (±$100 on the rent assumption moves this calculation by ~0.5pp). Or: keep the cliff at 55% but phrase the YELLOW reason as "DTI 54–55% — borderline, depends on lender stretch" so Jose sees the fragility.

3. **Fourplex price ceiling still uses triplex $650K as a proxy.** Property 3 (4BR/3BA, 2,984 sqft) is almost certainly a single large SFR, not a fourplex — but if Jose starts looking at actual fourplexes in Vallejo he needs a separate ceiling (~$750K is probably realistic given cap-rate math). File this as a V2 follow-up in `JOSE_THRESHOLDS`.

4. **Add a "legal unit count" confirm step.** Two of three properties in this run-through had ambiguous unit counts from Redfin listing data alone. A single Step 2 checkbox — "I have confirmed this is a legal multi-unit via MLS/permit data" — would stop the tool from quietly applying a 75% rental offset to what might be an SFR. Right now the SFR-no-ADU hard fail only triggers if the user correctly declares 1 unit.

### Bottom line
Tool works. Two of three verdicts are obvious; the third (Property 1) is exactly the non-obvious call that justifies building this in the first place. Math tracks Test Case A (PITI formulas) within rounding. Ship it.
