# Rental Property Deal Analyzer — Local Setup & Customization Handoff

## Mission

Clone, install, and customize the open-source `Rental-Property-Deal-Analyzer` (github.com/berkcankapusuzoglu/Rental-Property-Deal-Analyzer) for a specific user: **Jose**, a Vallejo, CA-based contractor running his first BRRRR-adjacent house-hack play. The tool must be tuned to his exact financial situation, target markets, and loan strategy before being considered "flawless."

You (the code agent) are being handed this doc to execute end-to-end. Read the entire document before running any commands. User context is authoritative — do not invent numbers or markets not specified here.

---

## User Context (authoritative — do NOT modify without explicit user approval)

### Financial profile
- **W-2 income:** $26/hr × 40 hrs × 52 wks = **$54,080/yr ($4,506/mo gross)**
- **Self-employment income:** Files Schedule C for roofing + Peritik businesses but writes off to near-zero net. **Lenders will count $0 from SE income.** Do not use SE income in any qualifying math.
- **Credit score:** 780+ (perfect)
- **Existing debt:** $0
- **Documented cash available:** $85,000 in savings/investment accounts in Jose's name
- **Employment tenure:** 1–2 years at current W-2 job
- **First-time homebuyer:** Yes, in California
- **Veteran status:** No (no VA eligibility)

### Strategy
- **Loan:** FHA 203(b) standard, 3.5% down, owner-occupied 2-4 unit (duplex/triplex)
- **CalHFA stacking:** Check MyHome Assistance availability for Solano and Contra Costa counties; include as optional field in tool
- **Rental offset:** 75% of projected rent from non-occupied unit(s) counts as qualifying income (FHA rule)
- **Max PITI target (net after rental offset):** ~$3,000/mo out-of-pocket
- **Rehab budget:** $40K-$75K over 12-24 months, moderate scope (cosmetic to mid-level)
- **License:** Jose holds a CSLB license for his roofing business but is NOT sure of classification (likely C-39 Roofing only). Assume C-39 unless otherwise confirmed. He can self-perform roofing only; all other trades require subcontractors at retail rates.
- **Occupancy plan:** Flexible — optimize for investment. Minimum 12 months owner-occupancy per FHA rule, then refi or rent out.
- **Path chosen:** Path A — move-in-ready duplex + cash-funded rehab after move-in. NOT 203(k) rehab loan.

### Target markets (priority order)
1. **Vallejo, CA** — zip codes 94590 (Old City, Heights), 94591 (East Vallejo, not Glen Cove/Hiddenbrooke)
2. **Hercules 94547, Rodeo 94572, Crockett 94525, Pinole 94564**
3. **Richmond, CA** — zip codes 94801, 94804, 94805 ONLY (exclude 94803 Point Richmond, 94806 Hilltop)

### Markets explicitly EXCLUDED
- Benicia (over budget — median $786K+)
- Glen Cove / Hiddenbrooke / Mare Island Vallejo (over budget)
- Oakland (rent control + price)
- Berkeley (rent control + price)
- Point Richmond 94803 (over budget)
- Sacramento (flat market, too far from Vallejo base)
- Peninsula / Redwood City (way over budget)

### Price parameters
- **Purchase price ceiling:** $525K for duplex, $650K for triplex
- **Ideal purchase sweet spot:** $425K-$525K duplex list price
- **Offer range:** Typically 5-8% below list on properties sitting 30+ days on market
- **Cash at close budget:** $40K-$45K (down + closing + reserves)
- **Reserve requirement:** Keep minimum 2 months PITI liquid after close

### Property profile requirements
- Legal 2-4 unit property (verify with permit history)
- Separate electric + gas meters preferred
- Separate entrances required
- Livable at appraisal (FHA requirement) — ugly/dated OK, gut job NOT OK
- Roof under 15 years old OR seller credit for replacement
- NO flat-roof commercial conversions
- NO unpermitted ADUs or garage conversions
- NO foundation cracks beyond hairline
- NO pre-1978 homes with original galvanized plumbing + knob-and-tube electrical
- NO properties in rent-controlled cities (Oakland, Berkeley)

---

## Repository to clone

**URL:** https://github.com/berkcankapusuzoglu/Rental-Property-Deal-Analyzer
**License:** MIT
**Stack:** Python (FastAPI-style `app.py`), HTML/JS frontend (`index.html`), Playwright for scraping fallback

Key files (from README):
- `app.py` — main server/API
- `index.html` — single-page frontend
- `requirements.txt` — Python dependencies
- `Dockerfile` — containerization option
- `render.yaml` — deploy config
- `.env.example` — env var template
- `examples/` — sample data
- `generate_examples.py` — script to create example scenarios

---

## Phase 1: Environment Setup

### Prerequisites check
Before installing, verify the dev environment has:
- Python 3.10+ (`python3 --version`)
- pip (`pip --version`)
- git (`git --version`)
- Node.js (for Playwright's browser dependencies, though Playwright installs chromium itself)

If any are missing, install them first. On macOS: `brew install python3 git`. On Linux: package manager.

### Clone and install

```bash
# Clone into Jose's preferred project directory
cd ~/projects  # or wherever Jose wants it
git clone https://github.com/berkcankapusuzoglu/Rental-Property-Deal-Analyzer.git
cd Rental-Property-Deal-Analyzer

# Create a virtual environment (recommended — do NOT skip)
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install Python dependencies
pip install -r requirements.txt

# Install Playwright's Chromium browser (required for scrape fallback)
python -m playwright install chromium

# Verify install succeeded
python -c "import playwright; print('Playwright installed')"
```

### Environment variables

Copy `.env.example` to `.env` and fill in:

```bash
cp .env.example .env
```

Open `.env` in an editor and populate required values. At minimum:
- `OPENAI_API_KEY` or `ANTHROPIC_API_KEY` — for AI analysis feature. Jose already has Anthropic API access; prefer Anthropic if the tool supports it. If it only supports OpenAI, note this and flag to Jose to decide.
- `FREDDIE_MAC_API_KEY` — likely not required (Freddie Mac rates are public). Leave blank if optional.

**Do not commit `.env` to any git repo. Verify it's in `.gitignore`.**

### First run — verify it works

```bash
# From the project root with venv activated
python app.py
```

Expected behavior: A local server starts, likely on `http://localhost:5000` or `http://localhost:8000`. Open in browser. The UI should load `index.html` and show the analyzer.

**If it fails:** Check the error, read `README.md`, and try again. Do not proceed to Phase 2 until a local instance runs end-to-end with a sample property analysis.

Test with a known-good Redfin URL like `https://www.redfin.com/CA/Vallejo/` to confirm scraping works. Redfin works. Zillow is likely blocked for datacenter IPs but should work from Jose's local machine.

---

## Phase 2: Configure for Jose's Profile

This is the critical customization phase. The stock tool assumes a generic investor. Jose has specific constraints. Update the tool so default values reflect his profile.

### 2.1 Default financial inputs

Find where the tool defines default values for the analysis form (likely in `index.html` as JavaScript variables, or in `app.py` as Python defaults). Update to:

| Field | Default Value | Notes |
|---|---|---|
| Buyer annual income (W-2) | `54080` | Jose's $26/hr × 40 × 52 |
| Buyer monthly income | `4506` | Gross monthly from W-2 |
| Credit score | `780` | Perfect tier |
| Monthly debts | `0` | Zero existing debt |
| Down payment % | `3.5` | FHA 3.5% |
| Loan type | `FHA` | If toggle exists |
| Interest rate | `6.5` | Current FHA 30-yr fixed (verify with Freddie Mac fetch) |
| Loan term (years) | `30` | Standard |
| Property tax rate | `1.1` | Solano County (verify for Contra Costa) |
| Insurance (annual) | `1800` | Typical duplex in target zips |
| HOA | `0` | Duplexes typically have no HOA |
| Vacancy rate | `5` | Conservative for Bay Area |
| Maintenance % | `5` | Of gross rent |
| Property management | `0` | Jose self-manages while owner-occupied |
| Owner-occupied | `true` | FHA requires |
| Units occupied by owner | `1` | He lives in one unit |
| FHA MIP rate | `0.85` | Annual MIP for 3.5% down loans over $625.5K |
| Closing costs % | `3` | Standard for California |

### 2.2 Add or adjust rental offset logic

The tool must treat 75% of projected rent from non-owner-occupied units as qualifying income. Locate the qualifying income / DTI calculation in the codebase.

**Required change:** If `owner_occupied === true` AND `units > 1`:
```
qualifying_income = W2_monthly + (0.75 × sum_of_non_owner_unit_rents)
```

Max PITI at 50% DTI (FHA max with Jose's credit profile — perfect credit pushes past 45%). Show explicit breakdown:
- W-2 income contribution
- Rental offset contribution (75% of each non-owner unit)
- Total qualifying income
- Max PITI at 45%, 50%, 55% DTI (display all three so Jose can see the stretch range)

### 2.3 Contractor-adjusted rehab mode

Add a toggle or input: **"Contractor self-performs roofing?"** (default: `true` for Jose)

When enabled, the tool should:
- Automatically reduce estimated roofing costs by ~40% (his in-house cost vs retail)
- Leave all other rehab line items at retail rates (he subs them)
- Show "Contractor edge savings" as a separate line item in the output so Jose sees the actual dollar benefit

If a rehab budget input exists, also add a companion field **"Expected retail rehab cost"** vs **"Your actual cost after contractor edge"** — this helps Jose see the equity capture advantage clearly.

### 2.4 Market-specific rent estimates

The tool scrapes Redfin for rental comps. For Jose's target zips, verify it's returning realistic numbers. Expected market rents as of April 2026:

| Area | 2BR/1BA unit rent | 3BR/2BA unit rent |
|---|---|---|
| Vallejo 94590 | $1,900-$2,300 | $2,500-$2,900 |
| Vallejo 94591 (non-Glen Cove) | $2,000-$2,400 | $2,600-$3,100 |
| Hercules/Rodeo/Crockett | $2,100-$2,500 | $2,700-$3,200 |
| Richmond 94801/94804/94805 | $1,900-$2,400 | $2,500-$3,000 |

If the scraped data is significantly off, either the rental scraper needs fixing OR the comp radius needs widening. Debug if necessary.

### 2.5 Go/No-go signal logic

The tool has a deal-scoring system. Tune the thresholds for Jose's situation:

**GREEN (pursue aggressively) criteria:**
- Purchase price ≤ $525K (duplex) or $650K (triplex)
- Net monthly PITI after rental offset ≤ $2,500
- Cash to close ≤ $45K
- Rehab budget needed ≤ $60K
- Located in priority zip list

**YELLOW (investigate further) criteria:**
- Net monthly PITI $2,500-$3,200
- Cash to close $45K-$60K
- Rehab needed $60K-$80K
- Located in secondary zip list

**RED (skip) criteria:**
- Net monthly PITI > $3,200
- Cash to close > $60K (eats his reserves)
- Rehab > $80K (exceeds his budget)
- Located in excluded market
- Flat roof OR unpermitted ADU OR pre-1978 with original plumbing
- SFR without ADU potential (no rental offset)

### 2.6 Saved searches / Neighborhood Search presets

The tool has a "Neighborhood Search" feature that accepts a zip code. Pre-configure these three searches as presets Jose can click:

**Preset 1: "Vallejo Priority"**
- Zip codes: 94590, 94591
- Property type: Multi-family 2-4 unit
- Price range: $400K-$550K
- Keywords: duplex, two on one, income property, fixer

**Preset 2: "East Bay Nearby"**
- Zip codes: 94547 (Hercules), 94572 (Rodeo), 94525 (Crockett), 94564 (Pinole)
- Property type: Multi-family 2-4 unit
- Price range: $400K-$600K

**Preset 3: "Richmond Motivated Sellers"**
- Zip codes: 94801, 94804, 94805
- Property type: Multi-family 2-4 unit
- Price range: $400K-$575K
- Filter: Days on market > 30 (motivated seller signal)

If the tool doesn't support preset-saving natively, add it. The user should click one button to load each preset, not re-enter criteria every time.

---

## Phase 3: Quality Gates — "Flawless Response" Criteria

The user defined "flawless" as: a property analysis run that produces a complete, accurate, decision-grade output aligned to his actual constraints. Verify each of these works end-to-end before handing the tool back to Jose.

### 3.1 Input flow test
- [ ] Paste a Redfin duplex listing URL (find any active Vallejo 94590 multi-family listing)
- [ ] Tool auto-fills: address, price, beds/baths/sqft, year built, photos
- [ ] Rent estimation scrapes nearby rentals and returns market rent band
- [ ] Mortgage rate button fetches current 30-yr fixed successfully

### 3.2 Calculation accuracy test
- [ ] PITI calculation matches hand math (verify with one known property):
  - $500K purchase, 3.5% FHA down, 6.5% rate, 30-yr → P&I should be ~$3,054
  - Taxes: 1.1% of $500K ÷ 12 = $458
  - Insurance: $150
  - FHA MIP: ~$342 (0.85% annual on $482,500 loan ÷ 12)
  - Total PITI: ~$4,004
- [ ] Rental offset reduces effective monthly cost correctly (75% of other unit rent)
- [ ] Max purchase price calculator respects $3,000/mo net out-of-pocket cap
- [ ] Cash-to-close includes down + closing + reserves, flags when over $45K

### 3.3 Contractor edge test
- [ ] Toggle "Contractor self-performs roofing" ON
- [ ] Rehab budget shows reduced roof cost + "Savings from contractor edge: $X" line
- [ ] Verify against retail benchmark: $15K retail roof → $9K contractor cost → $6K savings

### 3.4 Deal signal test
- [ ] Feed a known GREEN property (e.g., $475K duplex with $2,000/mo other unit rent) — expect GREEN
- [ ] Feed a known RED property (e.g., $650K duplex with $1,500/mo other unit rent) — expect RED
- [ ] Feed a YELLOW (borderline) property — expect YELLOW with specific reason

### 3.5 Comparison test
- [ ] Save 2-3 scenarios, open side-by-side comparison
- [ ] All metrics visible in comparison view
- [ ] Export to PDF or CSV works

### 3.6 Neighborhood search test
- [ ] Click "Vallejo Priority" preset
- [ ] Returns a table of current Vallejo multi-family listings
- [ ] Each listing has quick score
- [ ] "Analyze →" button opens full wizard with data pre-filled

### 3.7 Error handling test
- [ ] Paste an invalid URL → graceful error, allows manual entry
- [ ] Scraper fails → falls back to Playwright → falls back to manual entry
- [ ] Missing optional field (HOA) → tool doesn't crash, uses sensible default

---

## Phase 4: Deployment Decision

The repo supports multiple deployment modes. Recommended for Jose:

**Option A: Local-only (RECOMMENDED for now)**
- Run on Jose's Mac/PC when he wants to analyze a deal
- No hosting costs
- Full scraping capability (Zillow and Redfin both work from residential IP)
- Downside: must be on that machine to use it

**Option B: Self-hosted on a small cloud VPS**
- $5-12/mo DigitalOcean droplet
- Accessible from anywhere
- **Major issue:** Redfin and Zillow block datacenter IPs aggressively. Scraping will partially fail.
- Only do this if Jose specifically wants remote access.

**Option C: Render.com deploy (repo includes `render.yaml`)**
- Free tier available
- Same datacenter IP problem as B

**Recommendation:** Start with Option A. If Jose wants mobile access later, set up a small local machine at his home office that stays running, and use Tailscale or similar to access it from his phone. This gets residential IP + remote access without paying for a VPS.

---

## Phase 5: Extensions (Optional — Discuss With Jose Before Building)

These are not in scope for the initial setup but worth noting. Only build if Jose explicitly approves:

### 5.1 Daily cron alert pipeline
A GitHub Actions workflow (or local cron) that:
- Runs the 3 Neighborhood Search presets every 4 hours
- Compares results against the previous run
- Pushes new listings to Jose's phone via Twilio SMS or Telegram bot
- Auto-runs the deal math and includes green/yellow/red signal in the message

This pairs naturally with Jose's existing Twilio call recording project architecture he drafted previously.

### 5.2 BiggerPockets Agent Finder scraper
A script that pulls investor-friendly Realtor listings from BiggerPockets for Solano + Contra Costa, ranks by recent closed deals on multi-family, and generates an outreach list. Not supported by the base tool.

### 5.3 Solano County and Contra Costa County parcel data integration
Pull permit history, tax records, code violation notices directly from county systems. Useful for off-market deal hunting. High effort; each county has different APIs/no APIs.

### 5.4 Integration with Jose's Scrappy or SENTINEL repos
If he wants the deal analyzer to feed data into his existing projects (Vue 3 + Firebase stack), expose a simple REST endpoint from `app.py` and document it for his other agents to consume.

---

## Deliverables Checklist

When you're done, Jose should have:

1. ✅ Working local instance at `http://localhost:PORT`
2. ✅ `.env` file populated (but NOT committed to git)
3. ✅ Virtual environment installed and activation documented
4. ✅ Default financial inputs pre-filled with Jose's profile
5. ✅ Rental offset logic verified against FHA 75% rule
6. ✅ Contractor edge toggle working and reducing roof costs by 40%
7. ✅ Three Neighborhood Search presets saved and one-click loadable
8. ✅ Go/no-go signal thresholds tuned to his budget
9. ✅ All 7 quality gate tests from Phase 3 passing
10. ✅ A `RUN_ME.md` or equivalent quick-start doc for Jose explaining:
    - How to start the tool (`source venv/bin/activate && python app.py`)
    - How to stop it
    - How to update it (`git pull && pip install -r requirements.txt`)
    - Where to paste a Redfin URL to analyze a deal
    - How to interpret the output (what green/yellow/red mean for HIS deals specifically)
11. ✅ Known limitations documented (Zillow may partially work; rent estimates may need manual override for new/unusual areas)

---

## Things to Explicitly AVOID

- Do not modify core BRRRR math logic unless you've verified the original is wrong against BiggerPockets BRRRR calculator or similar known-good reference.
- Do not add paid API integrations (Zestimate API, ATTOM, Estated) without Jose's approval — they cost money.
- Do not deploy to a public URL or expose the tool to the internet. This is a personal tool.
- Do not commit `.env` or any API keys to git. Double-check `.gitignore`.
- Do not assume Jose wants to rebuild the tool from scratch in Vue/Firebase. This is a use-it-as-is customization, not a rewrite.
- Do not add features not listed in this handoff without asking first. Stick to the scope.
- Do not change the user interface layout dramatically. Minor CSS tweaks OK, complete redesign not in scope.

---

## Escalation / When to Stop and Ask

Pause and surface these to Jose (do not silently resolve):

1. **If the repo has been deleted, archived, or moved** — do not substitute a different repo. Stop and report.
2. **If `pip install` fails on a dependency** — report the specific error. Some Python packages need system libs (e.g., `lxml` needs libxml2) that may not be installed.
3. **If the scraping doesn't work on ANY Redfin URL** — stop and report. Likely means Redfin has updated their anti-scrape measures and the tool needs an update the maintainer hasn't shipped yet.
4. **If the tool requires an API key Jose doesn't have** — stop and ask which key he wants to use, or whether to disable that feature.
5. **If the default calculations produce results that don't match the expected numbers in Phase 3.2** — stop and audit the math before customizing. Better to fix a broken base than customize broken code.
6. **If customization requires rewriting more than 20% of a file** — stop and discuss. That's a signal the tool might not fit Jose's use case as cleanly as assumed, and he needs to know before you burn hours.

---

## Success Criteria (how Jose will know it's done right)

He should be able to:
1. See a new multi-family listing hit Redfin in Vallejo
2. Copy the URL
3. Paste it into his local tool
4. In under 60 seconds, see a complete analysis with:
   - His specific PITI at FHA 3.5% with rental offset
   - Cash required at close from his $85K
   - Rehab budget implications
   - Green/yellow/red signal
   - Why (specific reasons)
5. Make a go/no-go decision without running any additional numbers in his head

That's the flawless response bar. Anything less means the tool isn't ready yet.

---

## Appendix: Reference Data for Verification

### FHA 2025 loan limits (use in validation)
- Solano County: $1,149,825 for 1-unit; higher for 2-4 unit
- Contra Costa County: $1,149,825 for 1-unit; higher for 2-4 unit
- Both counties are "high-cost" areas, so Jose has headroom even on triplex

### CalHFA MyHome Assistance (verify current availability)
- URL: calhfa.ca.gov/homeownership/programs/myhome.htm
- Up to 3.5% of purchase price as deferred second mortgage
- Income limits apply — Jose is well under
- Status fluctuates based on state funding cycles; check this week

### Known good comparison property for validation
If you need to verify the tool's math against a real example, search for a recently-closed Vallejo 94590 duplex and run the math against the sale price. Example recent closing: **1035-1037 Virginia St, Vallejo, sold $375,000 on April 3, 2026** — 2BR/1BA + 2BR/1BA on one lot. Verify tool's PITI calc against this purchase price as a sanity check before final handoff.

---

## Questions to Confirm With Jose Before Starting

Only ask if you genuinely need an answer — if this doc covers it, don't ask:

1. Confirm which dev machine to install on (Mac, Windows, Linux)
2. Preferred Python version if multiple installed
3. Anthropic API key or OpenAI API key for AI analysis feature (and whether he wants AI analysis at all — some people prefer pure math)
4. Whether to set up a launcher script / desktop shortcut for easy startup
5. Confirm CSLB license classification if he's checked by now (changes contractor edge math if he has B license)
