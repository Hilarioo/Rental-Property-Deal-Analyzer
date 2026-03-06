# Rental Property Deal Analyzer

A web-based tool that helps you evaluate rental property investments. Enter property details (or scrape them from Zillow), and get a full financial breakdown with cash flow projections, rule-of-thumb checks, and optional AI-powered investment analysis.

![Dark-themed single-page app with a 6-step wizard]

## Quick Start

### 1. Install dependencies

```bash
pip install -r requirements.txt
python -m playwright install chromium
```

### 2. Configure AI (optional)

Copy the example env file and edit it:

```bash
cp .env.example .env
```

**Option A: Free local AI with Ollama (recommended)**

Install [Ollama](https://ollama.com), then:

```bash
ollama pull llama3.2:3b
ollama serve
```

That's it — the app auto-detects Ollama when no Anthropic key is set.

**To use a different model** (e.g. `qwen3.5:4b`):

```bash
# Pull the model first
ollama pull qwen3.5:4b

# Then either set it in .env:
OLLAMA_MODEL=qwen3.5:4b

# Or pass it as an environment variable when running:
OLLAMA_MODEL=qwen3.5:4b python app.py
```

Any model available on Ollama works — just `ollama pull <model>` and set `OLLAMA_MODEL`.

**Option B: Anthropic Claude API (paid)**

Set your API key in `.env`:

```
AI_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-your-key-here
```

### 3. Run the app

```bash
python app.py
```

Opens automatically at **http://localhost:8000**. No build step required.

### Environment Variables

| Variable | Default | Description |
|---|---|---|
| `AI_PROVIDER` | `auto` | `auto` (Anthropic if key set, else Ollama), `ollama`, or `anthropic` |
| `ANTHROPIC_API_KEY` | — | Anthropic API key (required for `anthropic` provider) |
| `OLLAMA_URL` | `http://localhost:11434` | Ollama server URL |
| `OLLAMA_MODEL` | `llama3.2:3b` | Any Ollama model name (e.g. `qwen3.5:4b`, `mistral`, `phi3`) |

## How It Works

The app is a **6-step wizard**:

### Step 1: Property Info
Enter the property address, purchase price, type (single-family or multifamily), ARV (after-repair value), and rehab budget. You can also paste a Zillow URL to auto-fill these fields.

### Step 2: Financing
Set your down payment percentage, interest rate, loan term, points, and closing costs. There's a "Cash Purchase" toggle if you're buying without a loan.

### Step 3: Income
Enter monthly rent (with support for multiple units on multifamily properties), other income, and expected annual income growth rate.

### Step 4: Expenses
Enter property taxes, insurance, HOA, utilities, and percentage-based expenses (maintenance, vacancy, CapEx, management). Also set expected annual expense growth.

### Step 5: Review
See a summary of everything before calculating. Go back to any step to adjust.

### Step 6: Results
Full financial dashboard with all metrics, a 5-year projection, amortization schedule, equity growth chart, and optional AI analysis.

## Metrics Explained

### Core Metrics

| Metric | What It Means | Good Target |
|---|---|---|
| **Monthly Cash Flow** | Rent income minus ALL expenses (operating + mortgage). This is money in your pocket each month. | > $100-200/unit |
| **Annual Cash Flow** | Monthly cash flow x 12. | > $1,200/unit |
| **Cash-on-Cash Return (CoC)** | Annual cash flow divided by total cash you invested (down payment + closing costs + rehab + points). Measures the return on YOUR money, not the property's total value. | > 8% |
| **Cap Rate** | Net Operating Income (NOI) divided by purchase price. Measures the property's return independent of financing. Useful for comparing properties regardless of how you finance them. | > 5-6% |
| **NOI (Net Operating Income)** | Annual rental income minus annual operating expenses (taxes, insurance, maintenance, vacancy, CapEx, management, HOA, utilities). Does NOT include mortgage payments. | Positive |
| **DSCR (Debt Service Coverage Ratio)** | NOI divided by annual mortgage payments. Tells you if the property's income covers its debt. Banks typically require 1.25+. Below 1.0 means you're losing money. | > 1.25 |
| **GRM (Gross Rent Multiplier)** | Purchase price divided by annual rent. Lower = better. It's a quick-and-dirty comparison tool — how many years of gross rent to pay off the price. | < 12-15 |
| **Break-Even Occupancy** | The percentage of time the property must be occupied just to cover all expenses + mortgage. Over 85% is risky — too little margin for vacancies. | < 85% |

### Rule-of-Thumb Checks

| Rule | How It Works | What It Tells You |
|---|---|---|
| **1% Rule** | Monthly rent should be >= 1% of purchase price. A $200K property should rent for $2,000+/month. | Quick filter to see if the numbers could work. |
| **50% Rule** | Operating expenses (excluding mortgage) typically run about 50% of gross rent. If yours are much higher, expenses may eat your cash flow. | Reality check on your expense estimates. |
| **70% Rule** | Purchase price + rehab should be <= 70% of ARV. Only shown when ARV or rehab is entered. Used for flips and BRRRR deals. | Checks if you're paying too much relative to the improved value. |

### Deal Score

The app gives an overall verdict based on combined metrics:

- **Great Deal**: CoC >= 8%, Cap Rate >= 6%, and DSCR >= 1.25
- **Borderline Deal**: CoC >= 4% or Cap Rate >= 4%, but doesn't hit all thresholds
- **Pass on This Deal**: Both CoC and Cap Rate below minimum thresholds

### 5-Year Projection

Projects cash flow, property value, loan balance, equity, and cumulative ROI over 5 years, factoring in your income growth, expense growth, and property value appreciation rates. Cash flows are color-coded: green for positive, red for negative.

### Amortization Schedule

Full year-by-year breakdown of annual payment, principal, interest, remaining balance, and total equity for the entire loan term.

### Equity Growth Chart

Visual bar chart showing how your equity grows over time (years 1, 5, 10, 15, 20, 25, 30) as you pay down the loan and the property appreciates.

### AI Investment Analysis

Click "Run AI Analysis" to get a plain-English assessment from a local LLM (Ollama) or Claude API. The AI reviews all your calculated metrics and provides:
1. Overall assessment
2. Key strengths
3. Key risks
4. Buy/pass recommendation

## Zillow Scraping

The app can attempt to auto-fill property data from a Zillow listing URL. It tries:

1. **httpx** (fast direct HTTP request)
2. **Playwright** (headless Chromium browser, if httpx is blocked)

**Important**: Zillow aggressively blocks automated requests. Scraping may fail with CAPTCHA depending on your network/IP. When it fails, simply enter the property data manually — all fields in Steps 1-4 are editable.

The scraper extracts: address, price, beds, baths, sqft, lot size, year built, property type, Zestimate, rent Zestimate, tax history, HOA fee, description, and a photo.

## Tech Stack

- **Backend**: Python, FastAPI, uvicorn, httpx, BeautifulSoup, Playwright
- **Frontend**: Vanilla HTML/CSS/JS (single file, no frameworks, no build step)
- **AI**: Ollama (local, free) or Anthropic Claude API (cloud, paid)
