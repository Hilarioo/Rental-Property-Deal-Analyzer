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

# Generate example reports (server must be running)
python generate_examples.py              # PDF + HTML only
python generate_examples.py --with-ai    # PDF + HTML + AI analysis
```

No tests, linter, or build step. The frontend is a single HTML file with inline CSS and JS.

## Architecture

**Two files carry all application logic:**

- `app.py` — FastAPI backend with three routes:
  - `GET /` — serves `index.html`
  - `POST /api/scrape` — fetches a Zillow URL, parses property data from `__NEXT_DATA__` JSON (4 extraction strategies: A=gdpClientCache, B=apiCache, C=direct pageProps, D=componentProps). Falls back from httpx to Playwright headless browser when blocked.
  - `POST /api/analyze-ai` — sends calculated metrics to an LLM. Supports 3 providers: LM Studio (GPU-accelerated, free), Ollama (free/local), Anthropic Claude API (paid). Controlled by `AI_PROVIDER` env var. Includes `_strip_thinking()` to remove reasoning tokens from thinking models (qwen3, deepseek-r1).

- `index.html` — Self-contained SPA (~2400 lines). All CSS, HTML, and JS in one file. Organized as:
  - CSS variables for dark theme (`:root` custom properties) + print media queries for light-theme PDF export
  - HTML: 6 wizard steps (Property Info, Financing, Income, Expenses, Review, Results)
  - JS (inside IIFE): DOM helpers, state management, wizard navigation, Zillow scrape handler, calculation engine, amortization builder, results renderer, equity chart, investment summary (pillars + scorecard + strategy fit), review populator, AI analysis caller, event listeners

- `generate_examples.py` — Playwright script to generate example PDF + HTML reports for 3 scenarios (good/mediocre/bad deal). Optionally runs AI analysis with `--with-ai` flag.

**Key JS patterns in index.html:**
  - `$()` is a shorthand for `document.getElementById()`
  - `lastCalcResults` holds the most recent calculation output (lives inside the IIFE closure, not on `window`)
  - `window.scrapedData` stores the last Zillow scrape response (on `window` for external access)
  - `window.runAI` is exposed globally for the onclick handler
  - Calculation runs in `calculateAll()` which populates `lastCalcResults` and calls `renderResults()`

**Key calculation concepts:**
  - 5-Year Total Return = Cash Flow + Appreciation + Debt Paydown + Tax Benefits (4 pillars)
  - Deal Score: 7 factors (CoC, Cap Rate, DSCR, Cash Flow, BEO, OER, GRM), each scored 0/1/2 points, max 14
  - Depreciation: 27.5-year straight-line, building value % configurable (default 80%)
  - Strategy Fit cards: Cash Flow, Wealth Building, Low Risk, BRRRR

## Configuration

Copy `.env.example` to `.env`. Key settings:
- `AI_PROVIDER` — `auto` (default), `lmstudio`, `ollama`, or `anthropic`
- `LMSTUDIO_URL` — defaults to `http://localhost:1234`
- `LMSTUDIO_MODEL` — model ID from LM Studio (e.g. `qwen/qwen3.5-9b`), or empty for auto
- `OLLAMA_URL` — defaults to `http://localhost:11434`
- `OLLAMA_MODEL` — defaults to `llama3.2:3b`
- `ANTHROPIC_API_KEY` — required only for Anthropic provider

### AI Provider Notes
- **LM Studio** (recommended for AMD GPUs): Uses Vulkan for GPU acceleration. Works with any GPU. OpenAI-compatible API on port 1234.
- **Ollama**: Uses ROCm (AMD) or CUDA (NVIDIA). RDNA1 cards (RX 5600/5700) need the [ollama-for-amd](https://github.com/likelovewant/ollama-for-amd) fork. CPU-only fallback is slow with thinking models.
- **Anthropic**: Paid cloud API. Best quality but requires API key and costs money.
- Thinking models (qwen3, deepseek-r1) output is auto-stripped of `<think>` blocks and reasoning prefixes.

## Zillow Scraping Limitations

Zillow aggressively blocks automated requests. The scraper tries httpx first, then Playwright headless Chromium as fallback. Both may still be blocked by CAPTCHA depending on network/IP. When scraping fails, users enter data manually.

## Git Conventions

- No co-author tags — commits are authored by the repo owner only
- Commit messages follow conventional commits (feat/fix/docs prefix)
