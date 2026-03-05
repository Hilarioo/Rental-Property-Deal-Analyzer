# Rental Property Deal Analyzer v2 — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Rebuild the rental property deal analyzer with Zillow URL scraping, BiggerPockets-style 6-step wizard, comprehensive investment metrics (NOI, DSCR, 1%/50%/70% rules, 5-year projections), and Claude AI analysis — all powered by a Python FastAPI backend.

**Architecture:** FastAPI backend (`app.py`) serves a single `index.html` frontend and exposes two API endpoints: `/api/scrape` (Zillow data extraction) and `/api/analyze-ai` (Claude proxy). Zillow scraping uses `httpx` + `BeautifulSoup` to parse `__NEXT_DATA__` JSON. Frontend is a vanilla JS wizard with real-time calculations. API key stored server-side in `.env`.

**Tech Stack:** Python 3.13, FastAPI, uvicorn, httpx, BeautifulSoup4, lxml, python-dotenv | HTML5, CSS3 (custom properties), vanilla JavaScript, Anthropic Messages API (claude-sonnet-4-20250514)

---

## Task 1: Set up Python backend with project scaffolding

**Files:**
- Create: `requirements.txt`
- Create: `.env.example`
- Create: `.gitignore`
- Create: `app.py`

**Step 1: Create `requirements.txt`**

```
fastapi
uvicorn
httpx
beautifulsoup4
lxml
python-dotenv
```

**Step 2: Create `.env.example`**

```
ANTHROPIC_API_KEY=sk-ant-your-key-here
```

**Step 3: Create `.gitignore`**

```
.env
__pycache__/
*.pyc
.venv/
```

**Step 4: Create `app.py` with basic FastAPI server**

Minimal server that:
- Loads `.env` via `python-dotenv`
- Serves `index.html` at `GET /`
- Has placeholder `POST /api/scrape` endpoint (returns `{"status": "not implemented"}`)
- Has placeholder `POST /api/analyze-ai` endpoint (returns `{"status": "not implemented"}`)
- Runs with `uvicorn` on port 8000
- Opens browser automatically on startup

```python
import os, json, webbrowser, threading
from pathlib import Path
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
import uvicorn

load_dotenv()
app = FastAPI()

@app.get("/", response_class=HTMLResponse)
async def serve_frontend():
    return Path("index.html").read_text(encoding="utf-8")

@app.post("/api/scrape")
async def scrape_zillow(request: Request):
    body = await request.json()
    url = body.get("url", "")
    return JSONResponse({"status": "not implemented", "url": url})

@app.post("/api/analyze-ai")
async def analyze_ai(request: Request):
    body = await request.json()
    return JSONResponse({"status": "not implemented"})

def open_browser():
    webbrowser.open("http://localhost:8000")

if __name__ == "__main__":
    threading.Timer(1.5, open_browser).start()
    uvicorn.run(app, host="127.0.0.1", port=8000)
```

**Step 5: Install dependencies and verify server starts**

```bash
pip install -r requirements.txt
python app.py
# Verify: browser opens to http://localhost:8000, shows current index.html
# Ctrl+C to stop
```

**Step 6: Commit**

```bash
git add requirements.txt .env.example .gitignore app.py
git commit -m "feat: add FastAPI backend scaffolding with placeholder endpoints"
```

---

## Task 2: Implement Zillow scraping endpoint

**Files:**
- Modify: `app.py`

**Step 1: Implement the scraping function**

Replace the placeholder `/api/scrape` with a real implementation:

1. Accept `{"url": "https://www.zillow.com/homedetails/..."}` via POST
2. Validate it's a Zillow URL (must contain `zillow.com`)
3. Fetch the page with `httpx` using browser-like headers:
   ```python
   HEADERS = {
       "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
       "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
       "Accept-Language": "en-US,en;q=0.9",
       "Accept-Encoding": "gzip, deflate, br",
   }
   ```
4. Parse HTML with BeautifulSoup, find `<script id="__NEXT_DATA__">` tag
5. Parse the JSON, navigate to property data inside `gdpClientCache` or `apiCache`
6. Extract and return a flat JSON object:

```python
{
    "address": "123 Main St, City, ST 12345",
    "price": 200000,
    "beds": 3,
    "baths": 2,
    "sqft": 1500,
    "lotSize": 6000,
    "yearBuilt": 1985,
    "propertyType": "SINGLE_FAMILY",
    "zestimate": 210000,
    "rentZestimate": 1800,
    "taxHistory": [{"year": 2024, "amount": 3200}],
    "annualTax": 3200,
    "hoaFee": 0,
    "description": "...",
    "imageUrl": "https://..."
}
```

7. Handle errors gracefully:
   - Invalid URL → 400 error with message
   - Zillow blocks/captcha → 503 error with message suggesting retry
   - Missing data fields → return `null` for missing fields, don't crash

**Step 2: Add a fallback scraping path**

Zillow sometimes structures data differently. Implement two extraction attempts:
1. Primary: `__NEXT_DATA__` → `props.pageProps` → look for property data in `gdpClientCache` (parse the stringified JSON values)
2. Fallback: Look for `<script type="application/ld+json">` which contains structured data with `@type: "SingleFamilyResidence"` or `"Product"`

If both fail, return a clear error message.

**Step 3: Test the scraping endpoint**

```bash
# Start server
python app.py &

# Test with curl (use a real Zillow listing URL)
curl -X POST http://localhost:8000/api/scrape \
  -H "Content-Type: application/json" \
  -d '{"url": "https://www.zillow.com/homedetails/test/123_zpid/"}'

# Verify response contains property fields
# Stop server
```

**Step 4: Commit**

```bash
git add app.py
git commit -m "feat: implement Zillow scraping endpoint with __NEXT_DATA__ parsing"
```

---

## Task 3: Implement Claude AI analysis endpoint

**Files:**
- Modify: `app.py`

**Step 1: Implement the AI proxy endpoint**

Replace the placeholder `/api/analyze-ai` with:

1. Read `ANTHROPIC_API_KEY` from environment
2. If no key configured, return 400 with helpful message
3. Accept POST body with all property data and calculated metrics
4. Build the prompt (system + user message with all metrics)
5. Call `https://api.anthropic.com/v1/messages` via `httpx`:
   - Model: `claude-sonnet-4-20250514`
   - Max tokens: 1024
   - System: "You are a real estate investment analyst. Analyze this rental property deal and provide a plain-English investment summary with: 1) Overall Assessment, 2) Key Strengths, 3) Key Risks, 4) Recommendation. Be concise but thorough."
6. Return the response text as JSON: `{"analysis": "..."}`
7. Handle errors: missing key, API errors, network issues

```python
@app.post("/api/analyze-ai")
async def analyze_ai(request: Request):
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        return JSONResponse({"error": "ANTHROPIC_API_KEY not set in .env file"}, status_code=400)

    body = await request.json()
    metrics_text = body.get("metrics", "")

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
            json={
                "model": "claude-sonnet-4-20250514",
                "max_tokens": 1024,
                "system": "You are a real estate investment analyst...",
                "messages": [{"role": "user", "content": metrics_text}],
            },
            timeout=30.0,
        )

    if resp.status_code != 200:
        return JSONResponse({"error": f"API error: {resp.status_code}"}, status_code=502)

    data = resp.json()
    text = data["content"][0]["text"]
    return JSONResponse({"analysis": text})
```

**Step 2: Commit**

```bash
git add app.py
git commit -m "feat: implement Claude AI analysis proxy endpoint"
```

---

## Task 4: Rewrite frontend — HTML structure and wizard UI

**Files:**
- Rewrite: `index.html`

**Step 1: Build the complete HTML structure**

Rewrite `index.html` with the 6-step wizard layout:

**Header:**
- App title + subtitle
- Status indicator (shows "Connected to server" / API key status)

**Wizard Navigation:**
- Step indicators (1-6) with labels, clickable, shows current step highlighted
- Progress bar between steps
- Steps: Property → Loan → Income → Expenses → Review → Results

**Step 1 — Property URL & Purchase:**
- Zillow URL input with "Fetch Data" button + loading spinner
- Fetched property summary card (shows image, address, beds/baths/sqft after scrape)
- Purchase price (auto-filled, editable)
- After Repair Value (ARV) — manual, optional
- Closing costs (default 3% of price, editable)
- Rehab/repair budget (default $0)
- Property value growth %/yr (default 3%)

**Step 2 — Loan Details:**
- "Cash Purchase" toggle (hides other fields when on)
- Down payment % (default 20%) — show calculated dollar amount
- Interest rate % (default 7%)
- Loan term in years (default 30)
- Points (default 0)

**Step 3 — Rental Income:**
- Monthly rent (auto-filled from Rent Zestimate if scraped, editable)
- For multifamily: per-unit rent fields with unit count selector
- Other monthly income (default $0)
- Annual income growth % (default 2%)

**Step 4 — Expenses:**
- Property taxes/yr (auto-filled from scrape, editable)
- Insurance/yr (default 0.5% of price, editable)
- Repairs & maintenance % of rent (default 5%)
- Vacancy rate % (default 8%)
- CapEx reserve % of rent (default 5%)
- Property management % (default 10%)
- HOA/month (auto-filled if scraped, default $0)
- Utilities/month (default $0)
- Other expenses/month (default $0)
- Annual expense growth % (default 2%)

**Step 5 — Review:**
- Two-column summary: all inputs organized by category
- Highlight auto-filled vs manual values (subtle badge)
- "Edit" links that jump back to relevant step
- "Finish Analysis" button (proceeds to Step 6)

**Step 6 — Results Dashboard:**
- Overview metric cards (4-column grid): Monthly Cash Flow, CoC Return, Cap Rate, 5yr Annualized ROI
- Quick Rules section: 1% Rule (pass/fail), 50% Rule (actual vs 50%), 70% Rule (if rehab entered)
- Detailed metrics cards: NOI, Monthly PITI, Total Monthly Expenses, GRM, Break-even Occupancy, DSCR, Total Cash to Close
- Deal Score banner (green/yellow/red)
- 5-Year Projection table: Year, Cash Flow, Property Value, Equity, Loan Balance, Cumulative ROI
- Amortization table (30-year, scrollable)
- Equity growth visualization (CSS bar chart)
- AI Analysis section: "Run AI Analysis" button + output area
- "Download Report" button (triggers `window.print()`)

**CSS:**
- Keep same dark theme variables from current `index.html`
- Wizard step indicator styling (circles with numbers, connected by lines)
- Active step highlighted with accent color
- Step content panels (show/hide based on current step)
- Responsive: works on mobile (steps stack vertically)
- Print styles (`@media print`): white background, hide navigation/buttons, show all results

**Step 2: Commit**

```bash
git add index.html
git commit -m "feat: rewrite frontend with 6-step wizard UI and results dashboard"
```

---

## Task 5: Implement frontend JavaScript — wizard logic, scraping, and calculations

**Files:**
- Modify: `index.html` (the `<script>` section)

**Step 1: Wizard navigation logic**

- Track `currentStep` (1-6)
- Next/Previous buttons advance/retreat steps
- Step indicator clicks jump to that step (only if step <= furthest visited)
- Show/hide step content panels
- "Finish Analysis" on Step 5 jumps to Step 6 and triggers `calculate()`

**Step 2: Zillow scrape integration**

- "Fetch Data" button on Step 1 calls `POST /api/scrape` with the URL
- Show loading spinner on button during fetch
- On success: auto-fill fields across all steps:
  - Step 1: price, property image + address card
  - Step 3: monthly rent (from rentZestimate)
  - Step 4: property taxes (from taxHistory), HOA
  - Determine SFH vs multi from propertyType
  - Calculate insurance estimate (0.5% of price)
- On error: show error message below URL input, don't crash

**Step 3: Real-time calculation engine**

All calculations in a single `calculate()` function, called whenever any input changes and on Step 6 load:

```
Core Calculations (same as current, plus new ones):

Monthly P&I:
  loanAmount = price * (1 - downPct/100)
  monthlyRate = rate / 100 / 12
  n = termYears * 12
  M = loanAmount * (monthlyRate * (1+monthlyRate)^n) / ((1+monthlyRate)^n - 1)
  (If cash purchase: M = 0)

PITI = M + taxes/12 + insurance/12

Total Monthly Rent = sum of all rent inputs + other income

Monthly Operating Expenses (excluding debt service):
  maintenance = totalRent * maintPct / 100
  vacancy = totalRent * vacPct / 100
  capex = totalRent * capexPct / 100
  management = totalRent * mgmtPct / 100
  opex = maintenance + vacancy + capex + management + taxes/12 + insurance/12 + hoa + utilities + other

Monthly Cash Flow = totalRent - opex - M (mortgage P&I)
Annual Cash Flow = monthlyCF * 12

NOI = (totalRent * 12) - (opex * 12 - vacancy * 12) ... actually:
NOI = (totalRent - vacancy) * 12 - (taxes + insurance + maintenance*12 + capex*12 + management*12 + hoa*12 + utilities*12 + other*12)
Simplify: NOI = annual gross rent - annual vacancy - annual operating expenses (no debt service)

Cap Rate = NOI / price * 100
Cash on Cash = annualCashFlow / totalCashInvested * 100
  where totalCashInvested = downPayment + closingCosts + rehabBudget

GRM = price / annualRent
Break-even Occupancy = (opex + M) / totalRent * 100 (excl vacancy from opex for this calc)
DSCR = NOI / (M * 12)  (if M > 0, else "N/A - cash purchase")
Total Cash to Close = downPayment + closingCosts + rehabBudget

1% Rule: totalRent >= price * 0.01 → Pass/Fail
50% Rule: (opex excluding debt) / totalRent → show actual % vs 50%
70% Rule: (price + rehab) <= ARV * 0.70 → Pass/Fail (only show if rehab > 0)

Deal Score:
  Great: CoC >= 8% AND capRate >= 6% AND DSCR >= 1.25
  Borderline: CoC >= 4% OR capRate >= 4%
  Pass: below borderline thresholds
```

**Step 4: 5-Year Projection table**

For each year 1-5:
- Rent grows by incomeGrowthPct
- Expenses grow by expenseGrowthPct
- Property value grows by valueGrowthPct
- Calculate: annual cash flow, property value, loan balance (from amortization), equity (value - balance), cumulative ROI

**Step 5: Amortization table**

Same logic as current implementation — yearly summary for full loan term.

**Step 6: Equity growth chart (CSS-based)**

Simple horizontal bar chart showing for years 1, 5, 10, 15, 20, 25, 30:
- Property value (full bar width)
- Loan balance (overlay bar)
- Equity = difference
Use CSS width percentages relative to max property value (year 30).

**Step 7: AI Analysis integration**

"Run AI Analysis" button:
- Calls `POST /api/analyze-ai` with all metrics as formatted text
- Shows spinner during request
- Displays response in styled output area
- Handles errors (no API key, network issues)

**Step 8: PDF/Print export**

"Download Report" button triggers `window.print()`. The `@media print` CSS (from Task 4) handles the styling — white background, hide wizard nav/buttons, expand all results sections.

**Step 9: Commit**

```bash
git add index.html
git commit -m "feat: implement wizard logic, Zillow integration, calculations, and AI analysis"
```

---

## Task 6: Polish, validate, and finalize

**Files:**
- Modify: `index.html`
- Modify: `app.py`

**Step 1: Input validation**

- Clamp numeric inputs on blur (same as current)
- Validate Zillow URL format before fetching
- Disable "Next" button on Step 1 until either URL is fetched or price is manually entered
- Show inline validation messages for required fields

**Step 2: Error handling polish**

- Scrape failures show user-friendly messages ("Zillow may be blocking requests. Try again in a minute, or enter data manually.")
- Network errors on AI analysis show retry option
- If server is not running, show connection error banner

**Step 3: UX polish**

- Flash animation on metric value changes (keep from current)
- Smooth step transitions (CSS opacity/transform)
- Auto-scroll to top on step change
- Keyboard navigation (Enter to advance steps)
- Loading states on all async operations

**Step 4: Test end-to-end**

1. Start server: `python app.py`
2. Paste a Zillow URL → verify data auto-fills across steps
3. Adjust assumptions → verify calculations update
4. Click through all 6 steps → verify review shows correct data
5. Verify all metrics calculate correctly on results page
6. Verify deal score changes color appropriately
7. Verify 5-year projection accounts for growth rates
8. Verify amortization table is correct
9. Test AI analysis (requires valid API key in `.env`)
10. Test print/PDF export
11. Test mobile responsiveness
12. Test with cash purchase (no loan)
13. Test with multifamily (multiple units)

**Step 5: Final commit**

```bash
git add index.html app.py
git commit -m "feat: polish UI, add validation, and finalize v2"
```

---

## Verification Checklist

1. `pip install -r requirements.txt && python app.py` — server starts, browser opens
2. Paste Zillow URL → property data auto-fills (price, rent, taxes, beds/baths)
3. Step through wizard — all fields editable, defaults sensible
4. Review step shows all inputs organized
5. Results page shows all metrics: Cash Flow, CoC, Cap Rate, NOI, DSCR, GRM, Break-even
6. Quick rules (1%, 50%, 70%) display correctly
7. Deal Score shows correct color (green/yellow/red)
8. 5-year projection table shows growth-adjusted values
9. Amortization table shows 30 rows with correct values
10. Equity chart visualizes growth over time
11. AI Analysis button works (with valid API key in `.env`)
12. "Download Report" produces clean print layout
13. Mobile responsive — wizard steps stack, cards reflow
14. Cash purchase mode skips loan fields and calculations
15. Manual entry works without scraping (all fields editable)
