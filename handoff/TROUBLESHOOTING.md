# Troubleshooting

Known failure modes and their fixes. Organized by where the symptom first surfaces (startup → server runtime → browser → batch pipeline → dev environment).

When adding a new entry, follow the Symptom / Cause / Fix / Code ref format so future-Jose can skim.

---

## Startup failures

### Symptom: Server won't start — `FileNotFoundError: spec/constants.json`
**Cause:** The shared constants file is missing, or the server was launched from a subdirectory so the relative path doesn't resolve.
**Fix:** Run from the repo root: `cd ~/Documents/Projects/Rental-Property-Deal-Analyzer && python app.py`. If the file itself is missing, `git status` will show it deleted — restore via `git checkout HEAD -- spec/constants.json`. The loader is intentionally fail-loud per ADR-002 §8.
**Code ref:** `spec/__init__.py:~15`, `app.py` (static route registration)

### Symptom: `ImportError: cannot import name 'FHA' from 'spec'`
**Cause:** `spec/__init__.py` parsed the JSON but a top-level key is missing from `constants.json` (schema drift between reader and file).
**Fix:** Check `spec/constants.json` has all seven top-level keys: `_meta`, `fha`, `jose`, `topsisWeights`, `insuranceHeuristic`, `presets`, `zipTiers`, `rehabCategories`, `defaults`. If any are missing, restore from git or bump `_meta.version` after a coordinated schema edit.
**Code ref:** `spec/__init__.py:~20`

### Symptom: `make test` fails on `fastapi` import
**Cause:** venv architecture mismatch — x86_64 wheels installed on an arm64 (Apple Silicon) host, or vice versa.
**Fix:** Recreate the venv with the correct arch:
```
rm -rf venv
arch -arm64 python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
```
**Code ref:** `requirements.txt`, `Makefile`

### Symptom: Port 8000 already in use
**Cause:** A prior `python app.py` didn't shut down cleanly (likely Ctrl-Z instead of Ctrl-C, or a crashed process holding the port).
**Fix:** `kill $(lsof -iTCP:8000 -sTCP:LISTEN -t)` then restart. If that returns nothing, the port is held by a different service — either change the `PORT` env var or shut down the other service.
**Code ref:** `app.py` (uvicorn startup)

---

## AI / provider runtime failures

### Symptom: `/api/models` returns 502 — "No AI provider available"
**Cause:** `ANTHROPIC_API_KEY` is empty in `.env`, or the shell exported an empty `ANTHROPIC_API_KEY` which overrode the `.env` value. This is the #1 false alarm after shell-profile edits.
**Fix:** Two options:
1. Ensure `.env` load takes precedence: the server uses `load_dotenv(override=True)`. Verify that's in place.
2. Unset the shell var before starting: `unset ANTHROPIC_API_KEY && python app.py`.
Confirm the key works standalone: `curl -s -H "x-api-key: $ANTHROPIC_API_KEY" -H "anthropic-version: 2023-06-01" https://api.anthropic.com/v1/models | head`.
**Code ref:** `app.py` (provider-wiring block, `_select_provider`)

### Symptom: Anthropic returns 401 but the same key works via curl
**Cause:** Same as above — the env var wasn't loaded into the Python process. Almost always `.env` precedence.
**Fix:** Covered above. If `load_dotenv(override=True)` is already in place, double-check the `.env` file is in the repo root (not in `~/.env`) and has no leading/trailing whitespace on the key value.
**Code ref:** `app.py` near `load_dotenv(...)` call

---

## Browser-side failures

### Symptom: Red banner "Spec failed to load"
**Cause:** The browser fetched `/spec/constants.json` and got a non-2xx or invalid JSON.
**Fix:**
1. Check browser network tab for the actual status on `GET /spec/constants.json`.
2. If 404, the static route for `/spec/*` isn't registered in `app.py` or the file is missing.
3. If 500, check server logs for a permission error on the file.
4. If 200 but malformed, the loader's `_meta.version` check failed — validate JSON with `python -m json.tool < spec/constants.json`.
**Code ref:** `index.html` bootstrap script (`await fetch('/spec/constants.json')`), `app.py` static-mount

### Symptom: Red banner "/calc.js failed to load"
**Cause:** Same class of issue as spec load — route/permission/path.
**Fix:** Check that `calc.js` sits at repo root and the static route covers it. Since ADR-002 Phase B, the browser imports `calc.js` as an ES module, so MIME type matters — the route must return `Content-Type: text/javascript` (or `application/javascript`), not `text/plain`.
**Code ref:** `app.py` static-mount for `calc.js`, `index.html` `<script type="module">` block

### Symptom: "Unit count not detected — re-scrape or enter manually"
**Cause:** Scraper's unit-count regex didn't match any known pattern in the listing HTML. This is **intentional fail-loud** per the scraper design — Jose enters the number rather than the tool silently guessing 1.
**Fix:** Click the manual-entry input and type the unit count. If this happens on the majority of listings, the scraper's regex list needs updating for a Redfin HTML change. File in `BACKLOG.md`.
**Code ref:** `app.py` (scraper's `_extract_units` fallback chain)

### Symptom: Rent shows `rent_source: tier_default` instead of real comps
**Cause:** Redfin rent-comps scrape failed or returned fewer than 2 comps (the minimum for a defensible median).
**Fix:** Not a bug — this is the documented fallback (commit 7e4c5e8). The TOPSIS input quality degrades on that property, but ranking still completes. If a known-good ZIP consistently falls back, the rent-comp scraper needs debugging.
**Code ref:** `batch/pipeline.py` (`_median_rent_comps`), UI rent-source badge

---

## Batch pipeline failures

### Symptom: Batch returns 502 immediately
**Cause:** One of: missing `ANTHROPIC_API_KEY`, hitting Anthropic rate limits, or an invalid URL format slipping past validation.
**Fix:** Check server logs (`logs/app.log`). The error envelope from the H-1 fix gives a generic client message; the log has the full stack. Rate-limit errors surface as 429 upstream; validation errors as 400.
**Code ref:** `app.py:/api/batch-analyze`, `batch/pipeline.py:_validate_batch_urls`

### Symptom: Batch returns `partial_failures`
**Cause:** Anthropic LLM response truncated or malformed on some URLs; per-field fallback defaults fired for those properties (see ADR-001 §3.5). Batch still completes; those properties rank with conservative values and a badge.
**Fix:** Not necessarily a bug. Check `claude_runs.status` in SQLite for the affected batch:
```
sqlite3 data/analyzer.db "SELECT url_hash, status, error FROM claude_runs WHERE batch_id='<batchId>' AND status != 'ok'"
```
If a specific URL reliably fails, test it through the single-URL wizard first.
**Code ref:** `batch/pipeline.py` (LLM extraction + fallback block)

### Symptom: Async batch stays "pending" forever
**Cause:** Either the Anthropic batch expired (24h SLA exceeded) or `ANTHROPIC_API_KEY` went missing mid-run.
**Fix:** Check `logs/app.log` for the last poll attempt on that `batchId`. If Anthropic returned "expired", the batch is dead — re-submit. If no polls happened, the key is gone or the server restarted mid-batch.
**Code ref:** `batch/async_pipeline.py` (`_poll_batch_status`)

### Symptom: `localStorage.pendingBatchId` sticks forever after server deletion
**Cause:** The server-side batch row was wiped (manual SQLite delete, DB file swap, etc.) but the browser still has the ID in localStorage.
**Fix:** The 404 path on `/api/batch-status/{batchId}` now auto-clears the stale ID (post-V1 fix). If it doesn't, click "Dismiss" in the UI or run `localStorage.removeItem('pendingBatchId')` in the browser console.
**Code ref:** `index.html` batch-poll handler

### Symptom: `OperationalError: database is locked`
**Cause:** Concurrent SQLite writers under contention. WAL mode + `BEGIN IMMEDIATE` mitigates most cases; the retry wrapper (3 attempts, 100/300/900ms backoff) handles transient locks.
**Fix:** If persistent, you have a stale Python process holding a write lock. Find it:
```
lsof data/analyzer.db
```
Kill the process. If the issue recurs with only one process running, check that no external tool (DB Browser, another shell with `sqlite3 data/analyzer.db`) has an open transaction.
**Code ref:** `batch/pipeline.py` (`_retry_on_locked`), `scripts/init_db.py` (WAL pragma)

---

## Dev environment quirks

### Symptom: Tests pass locally but fail in CI (or vice versa)
**Cause:** Usually `.env` differences or un-pinned transitive deps. `requirements.txt` is pinned but transitive wheels can still differ by platform.
**Fix:** Reproduce locally by recreating the venv from scratch in the CI's arch. If stubborn, add the transitive dep explicitly to `requirements.txt` with `==`.
**Code ref:** `requirements.txt`

### Symptom: `node --test tests/calc.test.mjs` fails with `Cannot use import statement outside a module`
**Cause:** Missing `"type": "module"` in `package.json`, or the test file extension is `.test.js` instead of `.test.mjs`.
**Fix:** Check that `package.json` has `"type": "module"` and test files use `.mjs`.
**Code ref:** `package.json`, `tests/calc.test.mjs`

### Symptom: Changes to `spec/constants.json` don't show up in the browser
**Cause:** Browser cache. The fetch uses default caching.
**Fix:** Hard reload (Cmd-Shift-R). For persistent dev-time avoidance, consider a cache-busting query param on the fetch — deferred since Jose is a single user and hard-reload is fine.
**Code ref:** `index.html` bootstrap `fetch('/spec/constants.json')`

### Symptom: `make test` runs pytest but skips node tests (or vice versa)
**Cause:** Makefile target order or a missing `set -e` equivalent.
**Fix:** Check `Makefile` — the `test` target should invoke both `pytest` and `node --test tests/` sequentially with an explicit error check.
**Code ref:** `Makefile`

---

## When in doubt

1. Check `logs/app.log` — every known error path logs with enough context to identify the failure mode.
2. Run `make test` — if it's red, fix that first; downstream symptoms are usually noise until the tests are green.
3. Check `git status` — a locally-modified or deleted file is the most common cause of "it used to work."
4. If adding a new entry here, match the Symptom / Cause / Fix / Code ref format and link to the relevant `BACKLOG.md` item if the fix is deferred.
