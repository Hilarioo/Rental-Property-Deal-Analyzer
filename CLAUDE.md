# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Single-page rental property investment analyzer. FastAPI backend serves a self-contained HTML frontend with a 6-step wizard UI. Users can scrape Zillow listings or enter data manually, then get financial metrics and optional AI-powered analysis.

## Commands

```bash
# Install dependencies
pip install -r requirements.txt
python -m playwright install chromium

# Run the app (opens browser to http://localhost:8000)
python app.py

# Or run with uvicorn directly
uvicorn app:app --host 127.0.0.1 --port 8000

# Setup Ollama for free local AI analysis
ollama serve          # start the server
ollama pull llama3.2:3b  # pull a model
```

No tests, linter, or build step. The frontend is a single HTML file with inline CSS and JS.

## Architecture

**Two files carry all application logic:**

- `app.py` — FastAPI backend with three routes:
  - `GET /` — serves `index.html`
  - `POST /api/scrape` — fetches a Zillow URL, parses property data from `__NEXT_DATA__` JSON (4 extraction strategies: A=gdpClientCache, B=apiCache, C=direct pageProps, D=componentProps). Falls back from httpx to Playwright headless browser when blocked.
  - `POST /api/analyze-ai` — sends calculated metrics to an LLM. Auto-detects provider: Anthropic Claude API (paid) if `ANTHROPIC_API_KEY` is set, otherwise Ollama (free/local). Controlled by `AI_PROVIDER` env var (`auto`/`ollama`/`anthropic`).

- `index.html` — Self-contained SPA (~2000 lines). All CSS, HTML, and JS in one file. Organized as:
  - CSS variables for dark theme (`:root` custom properties)
  - HTML: 6 wizard steps (Property Info, Financing, Income, Expenses, Review, Results)
  - JS (inside IIFE): DOM helpers, state management, wizard navigation, Zillow scrape handler, calculation engine, amortization builder, results renderer, equity chart, review populator, AI analysis caller, event listeners

**Key JS patterns in index.html:**
  - `$()` is a shorthand for `document.getElementById()`
  - `lastCalcResults` holds the most recent calculation output (lives inside the IIFE closure, not on `window`)
  - `window.scrapedData` stores the last Zillow scrape response (on `window` for external access)
  - `window.runAI` is exposed globally for the onclick handler
  - Calculation runs in `calculateAll()` which populates `lastCalcResults` and calls `renderResults()`

## Configuration

Copy `.env.example` to `.env`. Key settings:
- `AI_PROVIDER` — `auto` (default), `ollama`, or `anthropic`
- `ANTHROPIC_API_KEY` — required only for Anthropic provider
- `OLLAMA_URL` — defaults to `http://localhost:11434`
- `OLLAMA_MODEL` — defaults to `llama3.2:3b`

## Zillow Scraping Limitations

Zillow aggressively blocks automated requests. The scraper tries httpx first, then Playwright headless Chromium as fallback. Both may still be blocked by CAPTCHA depending on network/IP. When scraping fails, users enter data manually.
