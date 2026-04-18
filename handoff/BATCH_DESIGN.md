# Batch Analysis & Ranking — Design Specification

**Companion to:** `ADR-001-batch-ranking.md`
**Status:** Implementation-ready
**Date:** 2026-04-18
**Scope:** Two-commit delivery covering batch URL analysis, TOPSIS ranking, SQLite
persistence, a collapsible Step-0 UI, and two bundled security fixes.

This document is the plan a Senior Developer agent executes. The ADR owns the "why";
this doc owns the "what" and "how."

---

## Table of contents

- [A. SQLite schema](#a-sqlite-schema)
- [B. HTTP endpoint contracts](#b-http-endpoint-contracts)
- [C. Ranking algorithm — 13 criteria, TOPSIS, Pareto, hard-fails](#c-ranking-algorithm)
- [D. Frontend spec](#d-frontend-spec)
- [E. Structured-extraction LLM prompt + cache plan](#e-prompt-cache-plan)
- [F. Async Message Batches integration](#f-async-message-batches-integration)
- [G. Security fix plan — M1/M3](#g-security-fix-plan)
- [H. Operation lock strategy](#h-operation-lock-strategy)
- [I. Phasing — two commits, justified](#i-phasing)
- [K. External data integrations — FEMA, Cal Fire, OSM Overpass](#k-external-data-integrations)
- [L. Cache invalidation policy](#l-cache-invalidation-policy)
- [M. Insurance heuristic and breakdown](#m-insurance-heuristic)
- [N. End-to-end per-URL enrichment flow](#n-enrichment-flow)

---

## A. SQLite schema

**File location:** `./data/analyzer.db` (gitignored).
**Journal mode:** WAL (set once on DB init, persists in the file header).
**Foreign keys:** ON (must be enabled per-connection — SQLite default is OFF).
**Access layer:** stdlib `sqlite3` module. No ORM for V1.

### A.1 Initialization DDL

Run once at first server start. Idempotent — all `CREATE` statements use
`IF NOT EXISTS`.

```sql
-- Per-connection pragmas (WAL + FK + NORMAL sync + 5s busy timeout):
PRAGMA journal_mode = WAL; PRAGMA foreign_keys = ON;
PRAGMA synchronous = NORMAL; PRAGMA busy_timeout = 5000;

-- 1) properties: one row per unique listing URL
CREATE TABLE IF NOT EXISTS properties (
    url_hash                    TEXT PRIMARY KEY,         -- SHA-256 hex of normalized URL
    canonical_url               TEXT NOT NULL,
    address                     TEXT,                     -- denormalized from latest scrape
    zip_code                    TEXT,                     -- denormalized; used for tier lookup
    first_seen_at               TEXT NOT NULL,            -- ISO-8601 UTC
    last_scraped_at             TEXT NOT NULL,            -- ISO-8601 UTC
    scrape_count                INTEGER NOT NULL DEFAULT 1,
    -- Cached denormalized scrape values used by the invalidation policy (§L):
    last_price                  INTEGER,                  -- integer dollars, latest scrape
    last_dom                    INTEGER,                  -- days on market, latest scrape
    -- Consolidated structured-extraction LLM output. JSON blob matching §E.2 schema.
    llm_analysis                TEXT,                     -- JSON; null until first extraction
    llm_analyzed_at             TEXT,                     -- ISO-8601 UTC; null if llm_analysis null
    llm_model                   TEXT,                     -- e.g. 'claude-sonnet-4-5'
    llm_input_tokens            INTEGER,
    llm_cached_input_tokens     INTEGER,
    llm_output_tokens           INTEGER,
    -- Derived insurance fields, computed from heuristic + enrichment + llm uplift (§M).
    cached_insurance            INTEGER,                  -- annual $, integer
    cached_insurance_breakdown  TEXT,                     -- JSON: base, age_mult, flood_mult, fire_mult, llm_mult, total
    -- Free-form reason we re-ran (or did not re-run) the LLM on the last batch pass.
    cache_stale_reason          TEXT                      -- enum in §L.2; null if cache was used
);

CREATE INDEX IF NOT EXISTS idx_properties_zip ON properties(zip_code);
CREATE INDEX IF NOT EXISTS idx_properties_last_scraped ON properties(last_scraped_at);
CREATE INDEX IF NOT EXISTS idx_properties_llm_analyzed_at ON properties(llm_analyzed_at);

-- 2) scrape_snapshots: append-only log of every scrape attempt
CREATE TABLE IF NOT EXISTS scrape_snapshots (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    url_hash       TEXT NOT NULL,
    scraped_at     TEXT NOT NULL,
    price          INTEGER,                           -- null if scrape failed
    beds           INTEGER,
    baths          REAL,
    sqft           INTEGER,
    year_built     INTEGER,
    units          INTEGER,                           -- 1-4
    dom            INTEGER,
    description    TEXT,
    image_url      TEXT,
    raw_json       TEXT,                              -- full payload for debug
    scrape_ok      INTEGER NOT NULL DEFAULT 1,
    error_reason   TEXT,
    FOREIGN KEY (url_hash) REFERENCES properties(url_hash) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_snapshots_urlhash_time ON scrape_snapshots(url_hash, scraped_at DESC);
CREATE INDEX IF NOT EXISTS idx_snapshots_zip_time ON scrape_snapshots(url_hash, scraped_at);

-- 3) batches: one row per batch submission
CREATE TABLE IF NOT EXISTS batches (
    batch_id          TEXT PRIMARY KEY,                  -- uuid4 hex
    created_at        TEXT NOT NULL,
    completed_at      TEXT,
    mode              TEXT NOT NULL CHECK (mode IN ('sync','async')),
    input_count       INTEGER NOT NULL,
    status            TEXT NOT NULL CHECK (status IN ('pending','running','complete','failed','partial')),
    external_batch_id TEXT,                              -- provider batch_id for async
    preset_name       TEXT,
    error_reason      TEXT
);

CREATE INDEX IF NOT EXISTS idx_batches_created ON batches(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_batches_status ON batches(status);

-- 4) rankings: one row per (batch_id, url_hash)
CREATE TABLE IF NOT EXISTS rankings (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id            TEXT NOT NULL,
    url_hash            TEXT NOT NULL,
    rank                INTEGER NOT NULL,              -- 1-indexed
    topsis_score        REAL NOT NULL,                 -- [0,1]; 0 for hard-fails
    pareto_efficient    INTEGER NOT NULL DEFAULT 0,
    verdict             TEXT NOT NULL,                 -- 'green'|'yellow'|'red'
    hard_fail           INTEGER NOT NULL DEFAULT 0,
    reasons_json        TEXT NOT NULL,                 -- JSON array
    criteria_json       TEXT NOT NULL,                 -- 13 criterion values
    derived_metrics_json TEXT NOT NULL,                -- 5 historical metrics
    claude_narrative    TEXT,
    narrative_status    TEXT NOT NULL DEFAULT 'pending' CHECK (narrative_status IN ('pending','ok','failed','skipped')),
    FOREIGN KEY (batch_id) REFERENCES batches(batch_id) ON DELETE CASCADE,
    FOREIGN KEY (url_hash) REFERENCES properties(url_hash) ON DELETE CASCADE
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_rankings_batch_url ON rankings(batch_id, url_hash);
CREATE INDEX IF NOT EXISTS idx_rankings_batch_rank ON rankings(batch_id, rank);

-- 5) claude_runs: ledger of every LLM call (extraction or narration)
CREATE TABLE IF NOT EXISTS claude_runs (
    run_id              TEXT PRIMARY KEY,
    batch_id            TEXT NOT NULL,
    url_hash            TEXT,
    mode                TEXT NOT NULL CHECK (mode IN ('sync','async')),
    external_batch_id   TEXT,
    prompt_cache_hit    INTEGER,
    input_tokens        INTEGER,
    cached_input_tokens INTEGER,
    output_tokens       INTEGER,
    cost_usd            REAL,
    created_at          TEXT NOT NULL,
    completed_at        TEXT,
    status              TEXT NOT NULL CHECK (status IN ('pending','ok','failed')),
    error_reason        TEXT,
    FOREIGN KEY (batch_id) REFERENCES batches(batch_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_claude_runs_batch ON claude_runs(batch_id);
CREATE INDEX IF NOT EXISTS idx_claude_runs_created ON claude_runs(created_at DESC);

-- 6) property_enrichment: one row per url_hash; external data fetched once, never
--    refetched automatically. Delete the row by hand to force a re-fetch.
CREATE TABLE IF NOT EXISTS property_enrichment (
    url_hash           TEXT PRIMARY KEY,
    lat                REAL,                           -- WGS84
    lng                REAL,
    geocode_source     TEXT,                           -- 'scrape' | 'nominatim' | 'census'
    flood_zone         TEXT,                           -- FEMA code: 'AE', 'X', 'VE', 'A', etc.; null = not in DB
    flood_zone_risk    TEXT,                           -- 'high' | 'moderate' | 'low' | 'unknown'
    fire_zone          TEXT,                           -- 'LRA-moderate' | 'LRA-high' | 'LRA-very-high' | 'none' | null
    fire_zone_risk     TEXT,                           -- 'high' | 'moderate' | 'low' | 'unknown'
    amenity_counts     TEXT,                           -- JSON object; per-category counts within radius (see §K.3)
    walkability_index  INTEGER,                        -- 0-100 derived from amenity_counts; null if Overpass unavailable
    enriched_at        TEXT NOT NULL,                  -- ISO-8601 UTC
    fetch_errors_json  TEXT,                           -- JSON object; keys = 'fema'|'calfire'|'overpass'|'geocode'
    FOREIGN KEY (url_hash) REFERENCES properties(url_hash) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_enrichment_flood ON property_enrichment(flood_zone_risk);
CREATE INDEX IF NOT EXISTS idx_enrichment_fire ON property_enrichment(fire_zone_risk);

-- 7) rent_comps_cache: cached /api/rent-estimate responses keyed by (zip, beds, baths).
--    TTL 24h; re-fetch if older. See §N.4 for the lookup path.
CREATE TABLE IF NOT EXISTS rent_comps_cache (
    zip_code       TEXT NOT NULL,
    beds           INTEGER NOT NULL,
    baths          REAL NOT NULL,                      -- 1, 1.5, 2, etc.
    payload_json   TEXT NOT NULL,                      -- full /api/rent-estimate response
    fetched_at     TEXT NOT NULL,                      -- ISO-8601 UTC
    PRIMARY KEY (zip_code, beds, baths)
);

CREATE INDEX IF NOT EXISTS idx_rent_comps_fetched ON rent_comps_cache(fetched_at);
```

### A.2 Invariants and rationale

- **`url_hash` is SHA-256 of a normalized URL** (lowercased scheme+host, path with
  trailing slash stripped, query params sorted, fragment removed). This matches the
  Scrappy pattern and guarantees the same listing pasted with different tracking
  params dedups to one row.
- **`scrape_snapshots` is append-only.** Never UPDATE a snapshot; always INSERT a new
  one. This is what makes the derived metrics in §C.3 work — we need historical
  prices to compute price velocity.
- **`rankings.criteria_json` and `rankings.derived_metrics_json` are stored.** That
  is deliberate. If we change the weights later, we can re-rank historical batches
  from these values without re-scraping. This is the cheapest possible audit trail.
- **No FTS5.** Full-text search on listings is out of scope for V1.
- **No TTL or pruning job.** Historical rows stay forever. At Jose's volume (~500
  listings/year) the DB stays under 50 MB for a decade.
- **`property_enrichment` is fetched once per `url_hash` and never re-fetched
  automatically.** FEMA/Cal Fire/OSM Overpass change on a yearly cadence. Manual
  refresh via `DELETE FROM property_enrichment WHERE url_hash = ?`.
- **`properties.llm_analysis` is the cache surface for structured extraction.**
  Invalidation per §L; UPDATE-in-place (no LLM-output history — `claude_runs`
  captures per-call metadata for auditing).
- **`rent_comps_cache` has a 24h TTL** keyed on `(zip, beds, baths)`. Lookup
  checks `fetched_at`; re-fetch and UPSERT if stale.

### A.3 Migrations

No framework. Schema changes ship a one-off `scripts/migrate_NNN.py`
documented in `RUN_ME.md`. Accepted debt.

---

## B. HTTP endpoint contracts

All endpoints live in `app.py` alongside the existing `/api/analyze` route. All
request and response bodies are JSON. All error responses use the uniform shape in
§B.7. All requests and responses use `snake_case` field names to match existing
conventions.

### B.1 POST /api/batch-analyze — synchronous mode

**Purpose:** Analyze and rank a batch of URLs, return ranked results in one response.

**Request body:**
```json
{
  "urls": ["https://www.redfin.com/CA/Vallejo/123-Main-St/...", "..."],
  "preset_name": "vallejo-priority",
  "include_narrative": true
}
```

- `urls`: required. Array of 1–30 URLs. Server rejects with 400 if 0 or >30 (hard cap 50 for async mode).
- `preset_name`: optional. One of `vallejo-priority`, `east-bay-nearby`,
  `richmond-motivated`, or null for DEFAULTS-only. Applied per-property.
- `include_narrative`: optional, default `true`. If `false`, skip the LLM call and
  return with `claude_narrative: null` on every row. Useful for fast re-rank after
  weight tuning.

**Response body (200 OK):** envelope `{ batch_id, created_at, mode, input_count,
status, rankings: [...] }`. Each ranking row:
```json
{
  "rank": 1, "url_hash": "a1b2...", "canonical_url": "...", "address": "1035 Virginia St, Vallejo CA 94590",
  "price": 475000, "topsis_score": 0.832, "pareto_efficient": true,
  "verdict": "green", "hard_fail": false,
  "reasons": ["Net PITI $2,310 under $2,500 cap", "Priority zip 94590", "Rehab $30K under $60K cap"],
  "criteria": { /* all 13 criterion values from the §C.1 table */ },
  "derived_metrics": { "price_velocity": -2500.0, "dom_percentile_zip": 0.35,
                       "price_per_sqft_median_zip": 412.5,
                       "topsis_percentile_alltime": 0.91, "reappearance_count": 0 },
  "claude_narrative": "Strong Tier-1 Vallejo duplex..."
}
```

**Timing budget:** see §N.3 for the expanded per-URL timing. Sync end-to-end
for 12 URLs (mixed cache hit/miss) is ~35s; 50 URLs is ~145s. If total exceeds
300s, the sync endpoint returns `202` with a partial response and a `batch_id`
the client can poll.

### B.2 POST /api/batch-submit-async — async mode

**Purpose:** Submit a large batch for overnight narration via the LLM provider's
Message Batches API.

**Request body:** same shape as §B.1. Note: `include_narrative` is forced to `true`
(an async batch without narrative would be identical to sync and is rejected 400).

**Response (202 Accepted):** `{ batch_id, external_batch_id, created_at, mode:
"async", input_count, status: "running", poll_url, estimated_completion_at }`.
The server has already scraped, computed criteria, ranked, and written rankings
rows with `narrative_status: 'pending'` before returning. Only the narrative
step is delegated to Message Batches.

### B.3 GET /api/batch-status/{batch_id}

**Purpose:** Poll for async batch status and retrieve results once complete.

**Response (200 OK):** `{ batch_id, external_batch_id, status: pending|running
|complete|failed|partial, progress: {done, total, failed}, rankings: null (or
§B.1 rankings array when complete/partial) }`. On `partial`, some rows have
`claude_narrative: null, narrative_status: "failed"`; the ranking itself is
always complete (does not depend on the LLM).

### B.4 GET /api/batches

**Purpose:** List historical batches for the "My Batches" UI view.

**Query params:** `limit` (default 20, max 100), `offset` (default 0).

**Response (200 OK):** `{ batches: [{ batch_id, created_at, completed_at,
mode, input_count, status, preset_name, top_rank_address }], total }`.

### B.5 GET /api/properties/{url_hash}/history

**Purpose:** Show scrape history and every rank this property received.

**Response (200 OK):** `{ url_hash, canonical_url, address, scrape_count,
snapshots: [{scraped_at, price, dom, scrape_ok}, ...],
rankings: [{batch_id, rank, topsis_score, verdict, created_at}, ...] }`.

### B.5a POST /api/property/extract — consolidated structured-extraction call

**Purpose:** Run the single consolidated structured-extraction LLM call on one
property. The batch pipeline invokes this internally per URL when cache is
stale; exposed publicly for single-URL debugging or targeted re-runs.

**Request:** `{ "url_hash": "...", "url": "...", "force": false }` — `url_hash`
or `url` required; `force: true` bypasses cache.

**Response (200 OK):**
```json
{
  "url_hash": "a1b2...",
  "cached": false,
  "cache_stale_reason": "price_changed",
  "llm_analysis": { /* §E.2 schema */ },
  "tokens_used": { "input": 2340, "output": 612, "cached_input_read": 1800 },
  "insurance_breakdown": { /* §M.3 schema */ }
}
```

### B.5b GET /api/property/{url_hash} — cached snapshot + enrichment + analysis

**Purpose:** Return everything we know about a previously-analyzed URL from
SQLite without scraping or calling the LLM. Frontend uses this to re-render a
historical property view when Jose clicks "Open full analysis" on a batch row.

**Response (200 OK):** `{ url_hash, canonical_url, address, zip_code,
latest_snapshot: {scraped_at, price, beds, baths, sqft, year_built, units,
dom, description, image_url}, enrichment: {lat, lng, flood_zone,
flood_zone_risk, fire_zone, fire_zone_risk, amenity_counts,
walkability_index, enriched_at}, llm_analysis (§E.2 or null), llm_analyzed_at,
insurance: {annual_usd, breakdown (§M.3)} }`. Returns 404 with code
`PROPERTY_NOT_FOUND` if `url_hash` is unknown. Read-only projection — never
scrapes, never calls the LLM.

### B.6 Reuse: existing endpoints unchanged

- `POST /api/analyze` (single URL) is NOT changed in this feature. A future commit
  may refactor it to share the criteria-compute core with the batch path, but that
  is out of scope here.

### B.7 Error response shape (all endpoints, including M1/M3 fix)

```json
{ "error": { "code": "SCRAPE_FAILED", "message": "AI service error", "request_id": "req_a1b2c3..." } }
```

`code` is a stable enum: `VALIDATION_ERROR`, `SCRAPE_FAILED`,
`AI_SERVICE_ERROR`, `DB_ERROR`, `BATCH_NOT_FOUND`, `PROPERTY_NOT_FOUND`,
`ENRICHMENT_FAILED`, `LLM_EXTRACTION_FAILED`, `RATE_LIMIT_EXCEEDED`,
`INTERNAL_ERROR`. `message` is generic — **never** `str(exc)`. Full
traceback logged server-side keyed by `request_id` only.

HTTP status mapping: 400 → `VALIDATION_ERROR`; 404 → `BATCH_NOT_FOUND` /
`PROPERTY_NOT_FOUND`; 429 → `RATE_LIMIT_EXCEEDED`; 502 → `SCRAPE_FAILED` /
`AI_SERVICE_ERROR` / `ENRICHMENT_FAILED` / `LLM_EXTRACTION_FAILED`; 500 →
`DB_ERROR` / `INTERNAL_ERROR`.

---

## C. Ranking algorithm

### C.1 The 13 criteria

Weights sum to 1.00. `dir` is `cost` (lower is better) or `benefit` (higher is
better). `source` names where the value comes from.

| # | Criterion | Dir | Weight | Source |
|---|---|---|---|---|
| 1 | Net PITI (post 75% rental offset) | cost | 0.18 | `calc.js` `netPiti`, Sprint 1 |
| 2 | Cash-to-close vs $45K target | cost | 0.12 | Sprint 4 math, compare to DEFAULTS `maxCashToClose` |
| 3 | Effective rehab (post C-39 edge) | cost | 0.10 | Sprint 4 `effectiveRehab` |
| 4 | DTI headroom at 50% | benefit | 0.08 | Sprint 1 `maxPitiAtDti(.., 50)` minus computed PITI |
| 5 | Cash-on-cash return yr 1 | benefit | 0.10 | existing `coc` calc |
| 6 | 5-yr NPV at 3% appreciation, 8% discount | benefit | 0.10 | **NEW** — formula in C.2 |
| 7 | BRRRR equity capture % | benefit | 0.08 | **NEW** — formula in C.2 |
| 8 | ZIP tier (tier 1 → 3, tier 2 → 2, tier 3 → 1, else 0) | benefit | 0.08 | Sprint 3 |
| 9 | Cap rate | benefit | 0.05 | existing calc |
| 10 | Contractor edge $ savings | benefit | 0.04 | Sprint 4 (retail rehab − effective rehab) |
| 11 | DOM (days on market) | benefit | 0.03 | **NEW** — scraped value |
| 12 | Roof age | cost | 0.02 | Sprint 4 manual input, default 10 if unknown |
| 13 | $/sqft vs ZIP 90-day median | cost | 0.02 | **NEW** — derived metric, §C.3 |

**Insurance note.** Criterion #1 (Net PITI) is the TOPSIS entry point for insurance.
In V1 the insurance component of PITI was a flat rate; in the expanded scope it is
computed by §M's heuristic, which folds in FEMA flood zone, Cal Fire wildfire zone,
pre-1960 wood frame, and the structured-extraction LLM's `insuranceUplift.suggested`
multiplier. This means a property in a high-fire LRA zone with a pre-1960 build year
pays a higher PITI and ranks lower on criterion #1 without us adding a new column to
the matrix. The breakdown is stored in `properties.cached_insurance_breakdown` and
exposed in the inline detail drawer (§D.3) so Jose can audit each bump.

### C.2 BRRRR and NPV formulas

```
all_in_cost     = purchase + closing_costs + rehab_effective
equity_capture  = (ARV - all_in_cost) / ARV        # target ≥ 0.25
refi_out_ratio  = all_in_cost / ARV                # target ≤ 0.75
cash_left_in    = all_in_cost - (ARV * 0.75)       # target ≤ 0
breakeven_occ   = (PITI + opex) / gross_rent       # target ≤ 0.85

NPV_5yr = Σ_t=1..5  CF_t / (1+r)^t  +  (sale_yr5 - loan_bal_yr5) / (1+r)^5
  r = 0.08, appreciation = 0.03
  CF_t         = 12 * (gross_rent_t * (1 - vacancy - maintenance) - PITI_t - opex_t)
  sale_yr5     = purchase * (1 + appreciation)^5
  loan_bal_yr5 = principal after 60 payments on the FHA-adjusted loan
```

ARV defaults to scraped list price. Override in the single-URL wizard.
Zillow zestimate scraping is out of scope for V1.

### C.3 Five derived metrics from SQLite history

Computed at rank-time from `properties` + `scrape_snapshots`:

1. **Price velocity** — `(current - price_14d_ago) / days_between`. NULL if <2
   snapshots or second is older than 30d.
2. **DOM percentile within ZIP (90d)** — 0.0–1.0; informational only (not in
   the 13 TOPSIS criteria).
3. **$/sqft median for ZIP (90d)** — denominator for criterion #13.
4. **TOPSIS percentile all-time** — this batch's scores vs historical
   `rankings.topsis_score`; tooltip on the rank column.
5. **Re-appearance count** — distinct prior `batch_id`s containing this
   `url_hash`. If >0, render a "seen before" badge.

### C.4 Ranking pipeline — TOPSIS math

Per-URL steps (scrape, enrichment, LLM extraction, criteria compute, hard-fail
predicate) are covered in §N.1. Below is the TOPSIS-specific math that runs
once on the non-hard-fail set after all URLs are processed:

1. Build matrix M of shape (n, 13) with criterion values.
2. Normalize each column: `x_ij = v_ij / sqrt(sum_i v_ij^2)`.
3. Apply weights: `x_ij *= w_j`.
4. Ideal `A+`: column max for benefit criteria, column min for cost. Anti-ideal
   `A-`: the reverse.
5. Distances: `D_i+ = sqrt(sum_j (x_ij - A+_j)^2)`, `D_i-` likewise.
6. TOPSIS score: `S_i = D_i- / (D_i+ + D_i-)` ∈ [0, 1].
7. Pareto: property i is efficient if no j is `≥` on all criteria and `>` on at
   least one.
8. Hard-fail set: `topsis_score = 0`, `pareto_efficient = 0`.
9. Sort by `topsis_score DESC` (stable). Assign rank 1..n. UPSERT `rankings` in
   `BEGIN IMMEDIATE`.
10. If `include_narrative`, call the narrative step (inline sync, or Message
    Batches async — note the narrative role is now fused into the structured
    extraction's `narrativeForRanking` field; an optional separate narrative
    pass stays available for UX polish in Commit 2 / async mode).

### C.5 Edge cases

- **Duplicate URLs:** dedupe before scraping; one `rankings` row per unique
  `url_hash`; response reports N duplicates removed.
- **All hard-fails:** empty TOPSIS matrix; return all rows with score=0,
  verdict=red. No crash.
- **Single property in batch:** TOPSIS trivially → 1, Pareto trivially →
  efficient. Still compute so row shape stays consistent.
- **Zero-variance column:** detect and skip (treat as weight=0) to avoid
  divide-by-zero in normalization.

---

## D. Frontend spec

### D.1 Placement

The batch UI is a collapsible `<details>` block at the top of Step 0 (Neighborhood
Search) in `index.html`. It is closed by default so the existing single-URL flow is
unchanged for users who don't want batch. Opening it exposes the whole panel.

### D.2 DOM structure (illustrative; dev may adjust classnames)

```html
<details id="batch-panel"> <summary> ... Batch Analyze Multiple URLs ... </summary>
  <textarea id="batch-urls" rows="8" placeholder="https://www.redfin.com/..."></textarea>
  <span id="batch-url-count">0 URLs</span>
  <fieldset>  <!-- name="aiMode": sync (default) / async (disabled until count≥20) -->
    <input type="radio" name="aiMode" value="sync" checked> Sync (fast, ≤50 URLs)
    <input type="radio" name="aiMode" value="async" disabled> Async (overnight, ≤500 URLs)
  </fieldset>
  <select id="batch-preset"> <!-- "", vallejo-priority, east-bay-nearby, richmond-motivated --> </select>
  <button id="batch-submit">Analyze batch</button>
  <div id="batch-loading" hidden> <progress></progress> <span>Scraping 0 of 12…</span> </div>
  <div id="batch-results" hidden></div>
</details>
```

### D.3 Component behavior

- **URL count** live-updates on `input`; counts non-empty lines starting with
  `http`.
- **Mode toggle** — `sync` default/always enabled; `async` disabled until
  count ≥ 20. Named `aiMode` (not `batchMode`) to avoid overloading "batch."
  Tooltip at 20: "Async routes narration through the provider's overnight
  batch API for 50% savings. Results in 12–24h."
- **Submit** POSTs to `/api/batch-analyze` or `/api/batch-submit-async`.
  Loading state shows progress + "Scraping X of N…". 5xx/429 renders the
  generic error banner with `request_id`.
- **Ranked table columns:** `#` (rank, star if Pareto), Address (40 chars),
  Price, Verdict (pill), Score (TOPSIS 3 dp), Why (first 2 reasons, 80 char
  cap), Action ("Open full analysis" — single-URL wizard with URL pre-filled).
  Row click (outside button) expands the inline detail drawer with all 13
  criteria + 5 derived metrics + full reasons + insurance breakdown (§M.3) +
  LLM analysis drawer (rehab bands, motivation signals, risk flags, ADU
  potential, vision observations) + `narrativeForRanking` text.
- **"Compare top 3"** button opens a 3-column modal with key criteria. Pure
  presentation, no new API call. Escape / click-outside to close.
- **Async status chip** in the top-right renders `Batch 5f3a... — running
  (45/120)`. Click opens the results panel (possibly partial). Polls
  `/api/batch-status/{batch_id}` every 30s while the tab is open.
  Persisted in `localStorage['batchanalyzer.pendingBatches']` so a reload
  resumes polling.

### D.4 Behavior on partial results

Rows with `reasons: ['Scrape failed — cannot evaluate']` render at bottom in
gray with a retry button that fires `/api/analyze` for that single URL and
re-injects the row on success (client-side, no new batch).

### D.5 NOT in V1 UI

No weight editor, no per-property weight override, no drag-to-reorder, no CSV
export in Commit 1, no saved-searches beyond the single-URL flow's
localStorage.

---

## E. Structured-extraction LLM prompt + cache plan

The LLM's role in the expanded scope is **structured data extraction, not
narration**. It replaces a dozen fields of manual hand-entry (roof age, rehab
estimates per category, motivation signals, risk flags, insurance uplift, ADU
potential) with one consolidated call per property. The output is a strict JSON
object. A short `narrativeForRanking` field is included for TOPSIS tie-breaking
display, but it is not scored.

### E.1 Request structure — one call, two blocks, Vision enabled

Model: `claude-sonnet-4-5` (vision-capable). Request uses
`response_format: { type: "json_object" }` where the provider supports it,
otherwise the system prompt enforces "return only valid JSON, no code fences,
no prose before or after."

**System block** (CACHED, ~1500 tokens, `cache_control: { type: "ephemeral" }`):
role as a real-estate listing extractor; Jose's profile excerpts from
USER_PROFILE.md §3, §5, §6, §11; rehab rubric (banded cost tables per category
for 2-4 unit Bay Area cost basis with C-39 note); risk-flag evidence rubric
("galvanized plumbing: phrases 'original pipes', 'mid-century plumbing',
visible cream/gray pipes in image"); insurance-uplift rubric ("1.0–1.5
multiplier based ONLY on what you observe — flood/fire are handled
downstream"); literal JSON schema (§E.2) with a worked example; fallback rule
("if undetermined, emit the default and lower `confidence`").

**User block** (NOT cached, ~500–800 tokens, varies per property):
`"Property: <address>, <price>, <beds>/<baths>, <sqft>, built <year>, <units>.
DOM: N. Description: <full description>. Return JSON per schema."` plus one
image block with the Redfin primary `image_url`.

**Vision: single primary image only in V1.** The hero shot covers exterior
condition (roof, siding, foundation) and most other observations from interior
shots are redundant with the description. Multi-image on demand is a V2 ask.

### E.2 Output schema — the structured JSON contract

This is the exact shape returned on every extraction call. It is also the shape
cached in `properties.llm_analysis` as a JSON blob.

```jsonc
{
  "roofAgeYears": { "value": 12, "confidence": 0.6, "source": "description" },
  "rehabBand": {
    // one entry per category: roof | plumbing | electrical | cosmetic | hvac | other
    // each is { "low": int, "mid": int, "high": int, "confidence": 0.0–1.0, "reasoning": "..." }
    "roof":       { "low": 0,    "mid": 0,    "high": 18000, "confidence": 0.7, "reasoning": "Comp shingle, visible wear." },
    "plumbing":   { "low": 0,    "mid": 4000, "high": 15000, "confidence": 0.5, "reasoning": "1952 build; galvanized risk." },
    "electrical": { "low": 0,    "mid": 2000, "high": 8000,  "confidence": 0.4, "reasoning": "No panel upgrade mentioned." },
    "cosmetic":   { "low": 3000, "mid": 8000, "high": 15000, "confidence": 0.8, "reasoning": "Dated kitchen/bath visible." },
    "hvac":       { "low": 0,    "mid": 0,    "high": 6000,  "confidence": 0.5, "reasoning": "Wall heater only; AC absent." },
    "other":      { "low": 0,    "mid": 1000, "high": 4000,  "confidence": 0.5, "reasoning": "General contingency." }
  },
  "motivationSignals": {
    // 5 booleans: motivatedSeller, asIs, estateSale, tenantOccupied, preForeclosure
    "asIs": true,  "motivatedSeller": false, "estateSale": false,
    "tenantOccupied": false, "preForeclosure": false
  },
  "riskFlags": {
    // 5 keys, each { "present": bool, "evidence": string|null }
    // keys: foundationConcern, galvanizedPlumbing, knobAndTubeElectrical, flatRoof, unpermittedAdu
    "galvanizedPlumbing": { "present": true, "evidence": "1952 build, no plumbing update mentioned" },
    "foundationConcern": { "present": false, "evidence": null },
    "knobAndTubeElectrical": { "present": false, "evidence": null },
    "flatRoof": { "present": false, "evidence": null },
    "unpermittedAdu": { "present": false, "evidence": null }
  },
  "insuranceUplift":  { "suggested": 1.10, "reason": "Older frame, minor overhanging vegetation." },
  "aduPotential":     { "present": true,   "description": "Detached garage convertible." },
  "vision":           { "exteriorCondition": "fair", "roofCondition": "fair", "yardCondition": "fair",
                        "observations": "Chipped paint, composition roof moderate wear.", "hazards": [] },
  "narrativeForRanking": "Priced fair for a Tier-1 Vallejo duplex; cosmetic rehab dominant; 1952 plumbing verify.",
  "tokensUsed": { "input": 2340, "output": 612, "cachedInputReadTokens": 1500 }
}
```

**Per-field fallback on malformed response.** If the provider returns invalid JSON
or the JSON is missing a required field, the server fills in per-field defaults
(e.g., `roofAgeYears: { value: null, confidence: 0.0 }`, all rehab bands zeroed,
all motivation signals false, all risk flags absent, `insuranceUplift.suggested:
1.0`) and logs `LLM_EXTRACTION_FAILED` with the `request_id`. The downstream
insurance heuristic and TOPSIS pipeline run normally on the defaults. A row with
an extraction failure gets a badge "LLM extraction failed — defaults used" in the
detail drawer so Jose knows to treat the property with suspicion.

### E.3 Token and cost math at 100 URLs

System block is cacheable; user block and Vision image vary per property.
Vision counts ~1600 tokens for a 1024×768 JPEG after the provider's resize.

| Line item | Per property (cached) | 100-property batch |
|---|---:|---:|
| System prompt (cached) | ~1500 @ 10% read | 1500 write + 99×150 read = ~16,350 |
| User prompt (variable) | ~650 | 65,000 |
| Vision image | ~1600 | 160,000 |
| Output tokens (JSON) | ~500 | 50,000 |

Cache-hit input read rate saves ~60% of the system-block cost across the batch.
Image tokens dominate per-property variable cost and are not cacheable.

Cache TTL 5 minutes. Sync mode processes ~1 property every 1–3s so 100 URLs
fit in the TTL. Async bypasses prompt cache but gets the 50% batch discount.

### E.4 Two caches, invalidated independently

1. **Provider prompt cache** (5-min TTL, LLM-provider-managed) — we keep the
   system block byte-identical across a batch; it expires on its own.
2. **Our `properties.llm_analysis` cache** (SQLite, §L policy) — 30-day /
   price-delta / DOM-delta cache that avoids calling the LLM at all when the
   property is unchanged.

On a batch run, the SQLite cache is checked first. If fresh, no LLM call. If
stale, the provider's prompt cache covers the system block across remaining
stale URLs in the same batch.

---

## F. Async Message Batches integration

### F.1 Flow

1. Client POSTs `/api/batch-submit-async`. Server inserts `batches`
   (status=running, mode=async), runs the full per-URL pipeline (§N.1)
   inside `BEGIN IMMEDIATE`, writes `rankings` rows with
   `narrative_status=pending`, builds a Message Batches request (one
   `custom_id = url_hash` per row, same system/user blocks as §E.1),
   submits to the provider, stores `external_batch_id`, writes a
   `claude_runs` row, returns 202 with `batch_id` + `poll_url`.
2. Client polls `GET /api/batch-status/{batch_id}` every 30s.
3. Server poll handler short-circuits on terminal state; otherwise checks the
   provider with `external_batch_id`. On `in_progress` returns 200 with
   progress counts. On `ended` fetches results, UPDATEs each ranking's
   `claude_narrative` and `narrative_status`, updates `claude_runs` token
   counts, sets `batches.status = complete` or `partial`, and returns the
   full rankings array.

### F.2 Polling cadence

Client: 30s interval while tab is open, exponential backoff up to 5min on
repeated `running`. Server: no provider push; status endpoint called only on
client poll (idle between polls).

### F.3 Retrieval on process restart

`batches` with `status='running'` older than 30 min at server startup get
re-checked once against the provider via `reconcile_pending_batches()` on
`app.py` startup.

### F.4 Failure modes

- Provider 503 on submit → 502 `AI_SERVICE_ERROR`, atomic rollback of
  `batches` row.
- Provider batch `ended` with row failures → `batches.status='partial'`,
  failed rows `narrative_status='failed'`; ranking still valid.
- Poll returns `expired` → `batches.status='failed'`; UI shows retry button.
- Server killed mid-poll → reconciled on next boot; rankings rows were
  already persisted before submit.

---

## G. Security fix plan — M1/M3

Two exception-leak points were flagged. Both return `str(exc)` directly to the client,
potentially exposing stack traces, file paths, or scraped PII. Both need the same fix
and should ship in commit 1 alongside the batch endpoints.

### G.1 M1 — `app.py:1688` and G.2 M3 — `app.py:1931`

Replace `except Exception as exc: return jsonify({"error": str(exc)}), 500`
with a `request_id = uuid.uuid4().hex`, `logger.exception(...)` call, and a
uniform error envelope (§B.7) returning 502 for upstream failures. M1 uses
`AI_SERVICE_ERROR`; M3 picks between `SCRAPE_FAILED` and `AI_SERVICE_ERROR`
based on which branch the caller took at that line. Dev picks the enum in-place.

### G.3 Logging and verification

`logger.exception()` writes traceback + `request_id` to `./logs/app.log`
(gitignored), weekly rotation via `TimedRotatingFileHandler`. `request_id`
propagates into the batch path so every error response includes it.
Regression tests assert (a) response body does NOT contain the exception
string, and (b) DOES contain `request_id`. After commit 1,
`grep -n "str(exc)" app.py` returns zero matches in endpoint handlers.

---

## H. Operation lock strategy

### H.1 Critical section

Per-batch write path (inside `BEGIN IMMEDIATE ... COMMIT`): UPSERT
`properties`, INSERT `scrape_snapshots`, INSERT `batches`, INSERT `rankings`,
INSERT `claude_runs`. `IMMEDIATE` (not plain `BEGIN`) acquires the reserved
lock at transaction start so concurrent batch submissions (e.g. double-click)
serialize deterministically instead of failing partway through.

### H.2 WAL journaling

Set at DB init. Read-only endpoints (`GET /api/batches`,
`/api/properties/{url_hash}/history`, `/api/batch-status/{batch_id}`) proceed
concurrently with a running batch write.

### H.3 Retry on busy

`with_immediate_tx(conn, fn, max_attempts=3)` wraps every critical-section
write. Delays: 100ms, 300ms, 900ms. On `sqlite3.OperationalError: database is
locked`, retry up to 3 times; on exhaustion, rollback and surface `DB_ERROR`
500. In a single-user tool this should effectively never fire; it exists so a
double-click or a stale `sqlite3` CLI doesn't corrupt a batch.

### H.4 No application-level locks

No file locks, mutexes, or lock tables. `BEGIN IMMEDIATE` + WAL + retry
wrapper is the entire concurrency story.

---

## I. Phasing

### I.1 Recommendation: two commits, in order

**Commit 1 — MVP sync + security fixes.** Ships tomorrow.

- Files changed:
  - `app.py` — adds 5 new endpoints (`/api/batch-analyze`, `/api/property/extract`,
    `/api/property/{url_hash}`, `/api/batches`, `/api/properties/{url_hash}/history`),
    DB init (`property_enrichment` and `rent_comps_cache` included), external
    fetchers (FEMA, Cal Fire, OSM Overpass, Census geocoder), the consolidated
    structured-extraction LLM client with Vision and JSON validation, the
    insurance heuristic, M1/M3 fixes, and the uniform error helper.
  - `calc.js` — adds NPV_5yr, BRRRR equity capture, TOPSIS, Pareto filter, 5
    derived metrics.
  - `index.html` — collapsible batch panel, results table, compare-top-3
    modal, insurance breakdown drawer, LLM analysis drawer.
  - `scripts/init_db.py` (new) — idempotent DB setup.
  - `tests/test_app_baseline.py` — M1/M3 regressions; batch happy-path with
    fixture responses for scrape + FEMA + Cal Fire + OSM Overpass + Census +
    mock LLM; cache-invalidation unit test covering all four triggers.
  - `.gitignore` — `data/`, `logs/`, `.env`.
  - `handoff/ADR-001-batch-ranking.md`, `handoff/BATCH_DESIGN.md`.

- Suggested commit message: `feat(batch): sync batch analyze with TOPSIS ranking + security fixes`

**Commit 2 — Async toggle.** Ships later in the same sprint.

- Files changed:
  - `app.py` — adds `/api/batch-submit-async`, `/api/batch-status/{batch_id}`,
    `reconcile_pending_batches()` on startup.
  - `index.html` — unlocks the async radio when URL count >= 20, adds the status
    chip + localStorage persistence, adds polling.
  - `tests/test_app_baseline.py` — adds async submit + poll tests with a mocked
    Message Batches client.

- Suggested commit message: `feat(batch): async Message Batches mode for overnight runs`

### I.2 Why not one commit, and why not async first

One combined commit would touch >500 lines and conflate ranking-math bugs with
Message Batches plumbing in the same bisect. Commit 1 is the feature Jose uses
tonight; Commit 2 is an overnight optimization. Async first is infeasible
because async wraps "already-scored properties; now narrate them" — the sync
scoring core must exist first. External-dependency isolation: if Message
Batches breaks, sync still works.

### I.4 Effort estimate

| Commit | Component | Hours |
|---|---|---:|
| 1 | SQLite schema + init + retry wrapper | 1.5 |
| 1 | SQLite cache tables (`property_enrichment`, `rent_comps_cache`) + invalidation wiring | 1.5 |
| 1 | Scrape orchestration (parallel workers, error handling) | 1.5 |
| 1 | Criteria compute (NPV, BRRRR, derived metrics) | 2.0 |
| 1 | TOPSIS + Pareto | 1.5 |
| 1 | FEMA + Cal Fire + OSM Overpass fetchers (parallel, timeout-bounded) + geocoder fallback + walkability-index derivation | 2.0 |
| 1 | Consolidated structured-extraction LLM call + Vision + JSON schema validator + per-field fallback | 2.0 |
| 1 | Insurance heuristic wiring (formula + breakdown persistence + UI drawer table) | 0.5 |
| 1 | Per-URL enrichment flow in batch pipeline (§N orchestration) | 1.0 |
| 1 | `/api/batch-analyze`, `/api/property/extract`, `/api/property/{url_hash}` endpoints + error shape | 1.5 |
| 1 | `/api/batches`, `/api/properties/{..}/history` | 1.0 |
| 1 | Frontend: collapsible panel, results table, compare modal, insurance breakdown drawer | 3.0 |
| 1 | Security fixes M1/M3 + regression tests | 1.0 |
| 1 | Happy-path batch test against fixture HTML + fixture responses for FEMA/Cal Fire/OSM Overpass | 1.0 |
| | **Commit 1 subtotal** | **20h** |
| 2 | `/api/batch-submit-async` + polling handler | 2.0 |
| 2 | Message Batches client wrapper + reconcile on boot | 2.0 |
| 2 | Frontend async toggle, status chip, localStorage persistence | 2.0 |
| 2 | Async tests with mocked provider | 1.5 |
| | **Commit 2 subtotal** | **7.5h** |
| | **Total** | **27.5h** |

**Scope deltas from v1 estimate (12.5h → 20h for Commit 1):**
- Consolidated LLM extraction + Vision (+2.0h)
- SQLite cache tables + invalidation (+1.5h)
- FEMA + Cal Fire + OSM Overpass fetchers + geocoder + walkability-index derivation (+2.0h)
- Insurance heuristic wiring (+0.5h)
- Per-URL enrichment flow orchestration (+1.0h)
- New endpoints `/api/property/extract` + `/api/property/{url_hash}` (+0.5h absorbed into endpoint line)

Commit 2 effort is unchanged: the async path wraps whatever the sync path produces,
and the new enrichment work all lives upstream of the narration-or-extraction call
site.

---

## J. Open items for Senior Developer

1. **Scrape parallelism.** If 4 workers trip Redfin rate limits, drop to 2
   with 500ms jitter. Existing single-URL path is the rate reference.
2. **`computeJoseVerdict` reuse.** Sprint 4 placed it in `index.html`; the
   batch path needs it server-side. Either port to `calc.js` + serve via
   `/api/jose-verdict`, or duplicate in `app.py` as Python. Dev picks.
3. **`data/` and `logs/` directory creation** on app boot, pre-DB-open.
4. **localStorage collision check.** Verify `localStorage['batchanalyzer.*']`
   keys don't collide with the existing `deal-defaults` shape.

---

## K. External data integrations

Three external sources (FEMA, Cal Fire, OSM Overpass) auto-populate fields Jose previously typed by hand.
All three are fetched once per `url_hash` into `property_enrichment` and never
re-fetched automatically (§A.2). If any one source fails, its fields are stored
as null, the error is recorded in `fetch_errors_json`, and the ranking pipeline
continues.

All three fetches run in a per-property threadpool with an **8s total wall-clock
cap** for the three combined. Any fetch still outstanding at 8s is abandoned
with its field set to null.

### K.1 FEMA National Flood Hazard Layer (NFHL)

**Provider:** FEMA public ArcGIS MapServer. No API key.

**Endpoint (layer `/28` = Flood Hazard Zones):**
```
GET https://hazards.fema.gov/gis/nfhl/rest/services/public/NFHL/MapServer/28/query
    ?geometry=<lng>,<lat>&geometryType=esriGeometryPoint&inSR=4326
    &spatialRel=esriSpatialRelIntersects
    &outFields=FLD_ZONE,ZONE_SUBTY&returnGeometry=false&f=json
```

**Parse:** `features[0].attributes.FLD_ZONE` → codes like `A`, `AE`, `AH`, `VE`,
`X`, or empty. Map to `flood_zone_risk`:

- `high`: `A`, `AE`, `AH`, `AO`, `VE`, `V`
- `moderate`: `X` with `ZONE_SUBTY` containing "0.2 PCT"
- `low`: `X`
- `unknown`: empty / error / timeout

5s per-call timeout. On any failure: `flood_zone = null`, `flood_zone_risk =
"unknown"`, append `{ "fema": "<error>" }` to `fetch_errors_json`. §M's
insurance heuristic treats "unknown" as 1.0 flood multiplier.

### K.2 Cal Fire Fire Hazard Severity Zone (FHSZ)

**Provider:** Cal Fire public ArcGIS REST. No API key. California only.

**Endpoint:**
```
GET https://services.gis.ca.gov/arcgis/rest/services/Environment/Fire_Severity_Zones/MapServer/0/query
    ?geometry=<lng>,<lat>&geometryType=esriGeometryPoint&inSR=4326
    &spatialRel=esriSpatialRelIntersects
    &outFields=HAZ_CLASS,SRA&returnGeometry=false&f=json
```

**Parse:** no features → `fire_zone = "none"`, `fire_zone_risk = "low"`.
Otherwise build `fire_zone = f"{SRA}-{HAZ_CLASS.lower().replace(' ','-')}"`
(e.g. `LRA-very-high`) and map risk: `Very High → high`, `High → moderate`,
`Moderate → low`. Same failure pattern as FEMA.

### K.3 OpenStreetMap Overpass (Walkability)

**Provider:** OpenStreetMap Overpass API. No auth, no API key. Public service —
respect the fair-use guidance (~10K queries/day soft limit, 2-second cooldown
between calls).

**Endpoint:**
```
POST https://overpass-api.de/api/interpreter
Content-Type: text/plain
<Overpass QL body>
```

**Example Overpass QL query** (1-mile radius around `lat, lng`; transit at 0.5-mile):
```
[out:json][timeout:25];
(
  node["shop"="supermarket"](around:1609,{lat},{lng});
  node["shop"="convenience"](around:1609,{lat},{lng});
  node["amenity"~"^(school|kindergarten)$"](around:1609,{lat},{lng});
  node["amenity"~"^(restaurant|cafe)$"](around:1609,{lat},{lng});
  node["highway"="bus_stop"](around:800,{lat},{lng});
  node["railway"~"^(station|halt|tram_stop)$"](around:1609,{lat},{lng});
  node["leisure"~"^(park|playground)$"](around:1609,{lat},{lng});
);
out count;
```

**Parse and store** in `property_enrichment`:
```
amenityCounts: {
  groceriesWithin1Mile: 3,
  schoolsWithin1Mile: 4,
  restaurantsWithin1Mile: 12,
  transitStopsWithin0.5Mile: 2,
  trainStationsWithin1Mile: 1,
  parksWithin1Mile: 5
}
walkabilityIndex: 0..100  // derived from counts; formula below
```

**Derivation formula for `walkabilityIndex`** (capped at 100):
```
walkabilityIndex = min(100,
    (groceriesWithin1Mile * 10)
  + (schoolsWithin1Mile * 5)
  + (transitStopsWithin0.5Mile * 8)
  + (restaurantsWithin1Mile * 2)
  + (parksWithin1Mile * 3)
)
```

Train stations are captured for drawer display but excluded from the index
(transit stops already cover the commuter signal). Formula is documented in a
module-level constant so a future weights revision is a single-line change.

On any failure (timeout, 429, 5xx, network): `amenity_counts = null`,
`walkability_index = null`, append `{ "overpass": "<error>" }` to
`fetch_errors_json`. Richer than a single Walk Score number — actual amenity
counts map to real tenant concerns — and no TOS restriction for personal use.

### K.4 Geocoding strategy

FEMA and Cal Fire need `(lat, lng)`. Two paths, tried in order:

1. **Scrape-provided coords.** Redfin embeds `lat`/`lng` in
   `__reactEmbeddedData`. The existing scraper is extended to surface these as
   snapshot fields. Hit rate target ~95%. `geocode_source = "scrape"`.
2. **US Census Geocoder fallback.** Free, no auth, US-only, public domain.
   Nominatim (OSM) was considered but rejected for its 1 req/sec rate limit
   and production-use restrictions. `geocode_source = "census"`.
```
GET https://geocoding.geo.census.gov/geocoder/locations/onelineaddress
    ?address=<urlencoded>&benchmark=Public_AR_Current&format=json
```
Response: `result.addressMatches[0].coordinates.{x,y}` → `(lng, lat)`.

If both paths fail, `lat/lng = null`, FEMA, Cal Fire, and Overpass all skip
and log `{ "fema": "no_coords", "calfire": "no_coords", "overpass":
"no_coords" }`. Overpass requires coordinates — there is no address-only
fallback.

### K.5 Failure isolation invariant

**No single external failure blocks ranking.** TOPSIS runs with whatever
enrichment is available, including all-nulls. The insurance heuristic (§M)
treats nulls as "low risk" for math purposes but renders an "enrichment
incomplete" badge in the UI drawer.

---

## L. Cache invalidation policy

The SQLite-side LLM analysis cache is what keeps Jose's marginal cost near zero
on repeat batch runs. Policy is deliberately simple — three checks, any one
triggers a re-extraction.

**Out-of-scope risk note:** the V1 estimate briefly carried a "user-provided
secret" risk stemming from a third-party API key. With the Walk Score → OSM
Overpass swap, no user secrets remain for this feature — Overpass is
unauthenticated and public. Risk removed from the register.

### L.1 Invalidation pseudocode

```
def cacheIsStale(cachedRow, freshScrape) -> (bool, reason_or_null):
    if cachedRow is None or cachedRow.llm_analysis is None:
        return (True, "new_url")
    if cachedRow.last_price and freshScrape.price:
        if abs(freshScrape.price - cachedRow.last_price) / cachedRow.last_price > 0.03:
            return (True, "price_changed")
    if cachedRow.last_dom is not None and freshScrape.dom is not None:
        if freshScrape.dom - cachedRow.last_dom >= 14:
            return (True, "dom_increased")
    if cachedRow.llm_analyzed_at:
        if (now_utc() - parse_iso(cachedRow.llm_analyzed_at)).days > 30:
            return (True, "cache_age_exceeded")
    return (False, None)
```

### L.2 `cache_stale_reason` enum

Stored on `properties.cache_stale_reason` after every batch pass:

- `new_url` — no cached analysis existed.
- `price_changed` — scraped price moved >3% from cached `last_price`.
- `dom_increased` — scraped DOM is 14+ days higher than cached `last_dom`.
- `cache_age_exceeded` — `llm_analyzed_at` is older than 30 days.
- `forced` — operator passed `force: true` to `POST /api/property/extract`.
- `null` — cache was used; no LLM call made.

The UI renders this in the detail drawer so Jose sees, for each property, exactly
why we re-analyzed (or didn't).

### L.3 Scraping is always fresh

Scraping runs on every batch pass even when the cache will be used. The whole
point of the batch feature is to catch price drops and DOM increases. Only the
expensive steps (the LLM extraction, FEMA / Cal Fire / OSM Overpass fetches) are
cached.

### L.4 Rationale for the three thresholds

- **3% price** — larger than normal scrape noise (<1%); a $475K→$459K (−3.4%)
  drop should re-extract motivation signals, $474K should not.
- **14 days DOM** — matches the §C.3 price-velocity window and the conventional
  Bay Area "stale listing" threshold.
- **30 days cache age** — catches description edits and status changes we didn't
  see via price/DOM.

### L.5 Manual invalidation

`DELETE FROM properties WHERE url_hash = ?` cascades via FK and blows away
cached analysis. `DELETE FROM property_enrichment WHERE url_hash = ?` blows
away FEMA / Cal Fire / OSM Overpass. Documented in `RUN_ME.md`.

---

## M. Insurance heuristic

Insurance enters criterion #1 (Net PITI) through the `insurance_annual` term.
The V1 constant was a flat annual dollar value from DEFAULTS. The expanded scope
computes it from a deterministic heuristic stacked with the LLM's uplift
suggestion.

### M.1 Heuristic formula

```
base              = 1800 + 200 * max(0, (price - 400_000) / 100_000)
age_multiplier    = 1.15  if year_built < 1960  else 1.00
flood_multiplier  = 1.25  if flood_zone in ("A", "AE", "VE")  else 1.00
fire_multiplier   = 1.20  if fire_zone == "LRA-very-high"     else 1.00
llm_multiplier    = clamp(llm_analysis.insuranceUplift.suggested, 1.0, 1.5)

annual_usd = round(base * age_multiplier * flood_multiplier * fire_multiplier * llm_multiplier)
```

### M.2 Stacking order

Multipliers are applied most-objective to most-subjective: age → flood → fire
→ LLM. LLM last so its non-deterministic contribution is visible as the final
step in the breakdown.

### M.3 Breakdown object (persisted in `properties.cached_insurance_breakdown`)

```jsonc
{
  "base": 2100, "price_used_for_base": 550000,
  "age_multiplier": 1.15,  "age_reason":   "year_built=1952 (<1960, wood frame age)",
  "flood_multiplier": 1.00,"flood_reason": "zone=X (not in SFHA)",
  "fire_multiplier": 1.20, "fire_reason":  "zone=LRA-very-high",
  "llm_multiplier": 1.10,  "llm_reason":   "Older frame, minor overhanging vegetation.",
  "annual_usd": 3191, "computed_at": "2026-04-18T14:32:42Z",
  "enrichment_missing": false
}
```

Rendered verbatim as a small table in the detail drawer so Jose can audit each
bump. `enrichment_missing: true` surfaces a warning chip explaining that the
flood or fire multiplier defaulted to 1.0 because the official zone lookup
failed.

### M.4 Not in V1

No real insurance API (broker-auth required), no claim-history data (no free
source), no roof-type factor beyond the LLM observation (flat roof is a
hard-fail earlier in the pipeline). Accepted V2 explorations.

---

## N. End-to-end per-URL enrichment flow

This is the architectural flow the batch pipeline runs for each URL in the input
array. It ties together scrape, cache check, external enrichment, LLM extraction,
insurance, and ranking.

### N.1 Flow per URL

For each URL in the batch, the pipeline runs these steps in order:

1. `url_hash = sha256(normalize(url))`.
2. Read `properties` and `property_enrichment` rows for this `url_hash`.
3. Scrape via existing `/api/scrape` — always fresh. Insert a new
   `scrape_snapshots` row (scrape_ok=1 or 0).
4. Evaluate `cacheIsStale(cached, fresh_scrape)` per §L.1; record reason.
5. **If stale or new URL:**
   a. Resolve `(lat, lng)` — prefer `fresh_scrape.lat/lng`, fall back to the
      cached enrichment row, else call the US Census geocoder (§K.4).
   b. Fetch FEMA, Cal Fire, and OSM Overpass in parallel with an 8s total
      wall-clock cap. UPSERT `property_enrichment`.
   c. Resolve rent comps via `rent_comps_cache` (24h TTL per §A.2) or a fresh
      `/api/rent-estimate` call.
   d. Call the consolidated structured-extraction LLM (§E.1) with the cached
      system block, a per-property user block, and the primary image. Validate
      the response against §E.2; per-field defaults fill any missing fields.
   e. Compute the insurance heuristic (§M). Persist
      `llm_analysis`, `llm_analyzed_at`, `cached_insurance`,
      `cached_insurance_breakdown`, and `cache_stale_reason` on the
      `properties` row. Also update `last_price` and `last_dom`.
6. **If cache fresh:** reuse `properties.llm_analysis` and the cached insurance
   fields verbatim. Zero API spend for enrichment and extraction. Still update
   `last_price`, `last_dom`, and clear `cache_stale_reason`.
7. Compute the 13 criteria and the 5 derived metrics.
8. Run `computeJoseVerdict(criteria)`. Hard-fail rows pin `topsis_score = 0`
   and `pareto_efficient = false`.

Once every URL has been processed, the batch-wide steps run:

9. Pareto filter the non-hard-fail set.
10. TOPSIS-rank the non-hard-fail set.
11. UPSERT one `rankings` row per `url_hash` inside `BEGIN IMMEDIATE`.
12. Return the ranked table.

### N.2 Parallelism

Per-URL work is embarrassingly parallel up to the scrape rate limit. We run 4
worker threads. Inside one URL, the three external fetches (FEMA, Cal Fire, OSM
Overpass) run in their own small threadpool for the 8s budget. The LLM extraction
is serial within a URL (depends on scrape + image_url) but runs across URLs in
parallel — sync mode submits up to 4 concurrent LLM calls to keep the prompt
cache warm.

### N.3 Timing budget

Per-URL cache hit: ~1.6s (dominated by scrape). Per-URL cache miss: ~8–10s
(scrape 1.5s + enrichment parallel ~1.2s p95 + LLM 3–6s + rent comps up to 1s).
At 20 URLs all-miss with 4 workers, sync wall-clock is ~50s (LLM dominant).
At 20 URLs all-hit, ~10s. Real batches after week 1 hit mostly cache; marginal
cost on a repeat run is effectively the scrape.

### N.4 Rent comps caching

`rent_comps_cache` is keyed on `(zip, beds, baths)` because rent comps are
ZIP-level, not address-level. Two duplexes in the same ZIP with the same unit
mix get the same comp set. 24h TTL is conservative — comp data updates weekly
on most sources.

### N.5 Failure mode summary

| Failure point | Downstream effect |
|---|---|
| Scrape fails | TOPSIS=0, hard_fail=1, reason "Scrape failed" |
| Geocode fails | FEMA and Cal Fire skip; flood/fire multipliers → 1.0 |
| FEMA / Cal Fire fails | Zone null, risk "unknown", multiplier → 1.0 |
| OSM Overpass fails | `amenity_counts = null`, `walkability_index = null`; UI shows "—" (not a TOPSIS criterion in V1) |
| LLM extraction malformed | Per-field defaults (§E.2); rehab bands zeroed, risk flags absent, LLM multiplier → 1.0 |
| Rent comps fetch fails | Fall back to DEFAULTS per-unit rent; COC/NPV/breakeven use DEFAULTS |

**No single external failure prevents a property from being ranked.** Worst
case is ranking with conservative defaults and a visible badge explaining
what was missing.

---

**End of BATCH_DESIGN.md.**
