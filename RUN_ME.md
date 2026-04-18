# RUN_ME — Jose's FHA Deal Analyzer

One-page quick-start. If it's been 6 months and you forgot how this works, start here.

---

## 1. Start the tool

```bash
cd ~/Documents/Projects/Rental-Property-Deal-Analyzer
venv/bin/python app.py
# open http://localhost:8000
```

To stop: `Ctrl-C` in the terminal, or from another shell:

```bash
kill $(lsof -iTCP:8000 -sTCP:LISTEN -t)
```

If the venv is missing: `python3 -m venv venv && venv/bin/pip install -r requirements.txt`.

---

## 2. Analyze a listing (the 60-second workflow)

1. **Step 1 — URL.** Paste a Redfin multi-family URL into the URL field.
2. **Step 2 — Confirm scrape.** Verify price, beds/baths/sqft. Fix anything Redfin got wrong. **Critical:** confirm this is actually a legal duplex/triplex — a "4BR/2BA" SFR is not a duplex, and the rental offset only applies to real multi-unit.
3. **Step 3 — Rents + loan.** Click "Estimate rent" or type comps yourself. Set Unit 1 (owner, $0) and Unit 2+ rents. Loan section is auto-filled from DEFAULTS (FHA 3.5%, 6.5% — hit the Freddie Mac button to refresh rate).
4. **Step 4 — Rehab.** Add any rehab line items by category. **Check "Self-perform" on the Roof row** — that's your C-39 40%-off edge.
5. **Step 5 — Review.** Toggle the hard-fail flags if you saw any in person: flat roof, unpermitted ADU, pre-1978 galvanized + knob-and-tube.
6. **Finish.** The **Jose's Verdict** badge appears at the top with up to 3 reasons.

---

## 3. Read the verdict

- **🟢 GREEN:** Pursue. Consider making an offer in your target range (5–8% under list on stale DOM).
- **🟡 YELLOW:** Underwrite carefully. One number is close but not ideal — the reason will tell you which. Usually fixable with lender stretch, co-borrower, or a price drop.
- **🔴 RED:** Skip, or solve the specific reason before offering. Common RED causes and fixes:
  - **"PITI X% of qualifying income exceeds 55% DTI"** → Your most likely RED. Need higher projected rents (check top of Vallejo 2BR band $2,300), a co-borrower, or a lender willing to stretch past 55% on 780 credit + reserves. Have that conversation before offering.
  - **"Price exceeds ceiling"** → Above duplex $525K or triplex $650K cap. Skip unless you bring more down payment (not the FHA play).
  - **"Excluded market"** → Oakland / Berkeley / Benicia / Mare Island / Glen Cove / Hiddenbrooke / 94803 / 94806 / Sacramento. Hard-stop per USER_PROFILE §7. Don't argue with it.
  - **"Flat roof / unpermitted ADU / pre-1978 galvanized + K&T"** → FHA appraiser will kill it. Don't waste an offer.
  - **"SFR with no legal ADU"** → No rental offset possible. Only works as a solo-occupancy purchase — which makes the DTI math collapse. Switch your search back to legal duplexes.

---

## 4. Use a preset

Preset dropdown in the header. Three built in:
- **Vallejo Priority** — 94590/94591, 1.25% tax, $1,800 ins, 5% vacancy
- **East Bay Nearby** — Hercules/Rodeo/Crockett/Pinole, 1.15% tax, $1,900 ins
- **Richmond Motivated Sellers** — 94801/94804/94805 only, DOM > 30 filter, 1.35% tax, $2,100 ins, 8% vacancy

Clicking applies market-specific defaults and pre-fills Neighborhood Search filters.

---

## 5. Neighborhood search (finding listings)

Step 0 has a ZIP/city search. Enter a zip → get a table of current multi-family listings with quick scores. "Analyze" button on each row opens the wizard pre-filled.

---

## 6. Save & compare scenarios

Wizard → "Save" button stores the current scenario to localStorage. The Scenarios dropdown loads saved ones. Compare up to 3 side-by-side.

---

## 7. When the tool breaks

- **Scraper returns empty / wrong address:** Redfin or Zillow changed their HTML. Scraper tries `__NEXT_DATA__` → `ld+json` → DOM → Playwright in order. If all fail, enter data manually at Step 2.
- **AI analysis spins forever:** Anthropic API key in `.env` is wrong or rate-limited. Skip AI — the math still works without it.
- **Freddie Mac rate fetch fails:** Use the last cached rate or type one manually. Default is 6.5%.
- **"No module named X" on startup:** `venv/bin/pip install -r requirements.txt` — the pins are exact.

---

## 8. Updating your profile

All 27 defaults live in the `DEFAULTS` block near the top of the `<script>` tag in `index.html` (around line 2001). One-file edit, no rebuild. Fields include: W-2 income, credit score, DTI ceiling, interest rate, tax rate, insurance, vacancy, rehab categories, contractor multipliers.

After editing, hard-refresh the browser or clear localStorage to bypass cached state.

---

## 9. Updating your target markets

ZIP tiers and excluded cities live near line 2087 in `index.html` (`ZIP_TIERS` object). Presets live near line 2050 (`PRESETS` array). Edit the arrays directly; no rebuild.

---

## 10. Known limitations

- **Zillow scraping is flaky** (PerimeterX bot detection). Redfin is reliable — stick to Redfin URLs.
- **Local-only by design.** Do NOT expose to the public internet. The rental offset and C-39 edge rules are specific to you; leaking the tool leaks your profile.
- **75% rental offset assumes lender cooperation.** Some FHA lenders require 2 years of documented landlord experience before counting projected rents. You have none. Confirm with the actual lender before relying on a GREEN verdict on a project-rent basis.
- **Roof age is a manual input.** Scraper does not auto-extract it. Enter it at Step 5.
- **Fourplex ceiling uses triplex $650K as a proxy.** If you start looking at real fourplexes, bump this in `JOSE_THRESHOLDS` first.
- **Unit-count confirmation is on you.** A Redfin "4BR/2BA 1,385 sqft" could be a legal duplex OR a big SFR. The tool can't tell. Verify before trusting the rental offset.

---

## 11. Git workflow

```bash
git status
git diff
git add .
git commit -m "your message"
git push origin main
```

Fork: https://github.com/Hilarioo/Rental-Property-Deal-Analyzer

---

## 12. Reference docs

- `handoff/USER_PROFILE.md` — your authoritative numbers (W-2, thresholds, markets)
- `handoff/LIVE_RUNTHROUGH.md` — 3 real Vallejo listings run through the tool, with expected outputs
- `handoff/ACCEPTANCE_CRITERIA.md` — per-sprint DoD + Test Cases A–D for math validation

If the numbers ever stop matching your spreadsheet, re-run Test Case A (FHA MIP reference, `ACCEPTANCE_CRITERIA.md` §4) — if that still produces $4,004 ± $10 on a $500K / 3.5% / 6.5% case, the PITI math is fine and the drift is in defaults or scraping.
