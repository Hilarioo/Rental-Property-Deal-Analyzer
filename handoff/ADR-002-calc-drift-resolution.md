# ADR-002: Calc Drift Resolution — Shared Constants Spec

- **Status:** Accepted (shipped in commits ff5fbdf (Phase A), 809c9cb (Phase B))
- **Date:** 2026-04-18
- **Owner:** Jose (solo dev)
- **Supersedes:** none
- **Related:** ADR-001 (batch ranking), `handoff/BATCH_DESIGN.md`, `handoff/USER_PROFILE.md`

---

## 1. Context

The FHA analyzer now carries the same financial math in three runtimes:

1. **`calc.js`** (~340 lines) — authoritative ES module for the PITI/NOI/DSCR/CoC + Sprint 1 FHA additions (upfront + annual MIP, `FHA_RENTAL_OFFSET`, `computeFhaLoanAmount`, `computeQualifyingIncome`, `maxPitiAtDti`). This is what the 61 passing tests pin.
2. **`index.html`** — the browser UI has **its own inline copies** of the same math plus Sprint 2/3/4 extensions: `DEFAULTS` (27 fields), `FHA_MIP_UPFRONT_RATE`, `FHA_MIP_ANNUAL_RATE`, a re-derived PITI block (~line 3131), qualifying-income + max-PITI-at-DTI (~line 3420), `REHAB_CATEGORIES`, `JOSE_THRESHOLDS` (8 values), `computeJoseVerdict`, `ZIP_TIERS` (tier1/tier2/tier3/excluded zips/excluded cities), `PRESETS` (3 markets), and `getZipTier`.
3. **`batch/*.py`** (~2,500 lines) — `verdict.py`, `insurance.py`, `pipeline.py`, `async_pipeline.py`, `ranking.py` each port the relevant subset into Python for the Zillow batch scoring job. `scripts/init_db.py` holds the SQLite schema.

### Drift that actually happens

Observed change-classes, ranked by how often they occur in practice:

| Change class | Example | Places to edit today |
|---|---|---|
| Threshold tweak | DTI cliff 55% → 56% | 3 (calc.js, index.html, batch/verdict.py) |
| FHA rate update | HUD adjusts annual MIP | 3 |
| New market preset | Add Richmond | 1 (index.html) |
| New ZIP added to tier | 94804 → tier2 | 1 (index.html) |
| TOPSIS weight rebalance | CoC weight up 5pts | 2 (batch/ranking.py, maybe UI) |
| Formula shape change | YELLOW trigger on roof age >18 vs >20 | 3 |
| DEFAULTS numeric change | Jose W-2 $54k → $58k | 1 (index.html) |

The top three change-classes collectively account for the vast majority of edits and all three require hitting multiple files in lockstep today. That is the drift engine this ADR kills.

**Scope cut in force:** no new tests, no UI polish, 3-hour total budget, no build system, must keep all 61 tests green.

---

## 2. Decision

**Extract shared _constants_ (not formulas) into a single `spec/constants.json` file read by all three runtimes. Formulas remain per-runtime; values do not.**

Rationale:

- **Values drift 5× more often than formulas.** Thresholds, rates, weights, presets, ZIP tiers, DEFAULTS — these are _numbers_, and a shared JSON file collapses 3-file edits into 1-file edits for the top change-classes.
- **Zero build system.** Browser reads via `fetch('/spec/constants.json')` at page load. Node (for tests) reads via `import` with JSON module assertion or a tiny `fs.readFileSync` in `calc.js`. Python reads via `json.load` in a `spec/__init__.py` helper.
- **Preserves the test surface.** `calc.js` still exports the same functions with the same names. Tests that pass `FHA_MIP_ANNUAL_STANDARD` as a numeric literal to assertions keep passing because the module re-exports the JSON-sourced value under the same name.
- **Reversible.** If the JSON approach chafes, nothing stops us from re-inlining constants per runtime. No lock-in.

### What this kills

- Threshold tweaks → 1-file edit.
- FHA rate updates → 1-file edit.
- Preset additions, ZIP tier moves, excluded-city list → 1-file edit.
- TOPSIS weight rebalance → 1-file edit.
- DEFAULTS numeric changes → 1-file edit.

### What this does NOT kill

- **Formula-shape changes** (new term in PITI, restructured verdict predicate, new rental-offset conditional) still require parallel edits in `calc.js` and the relevant `batch/*.py` files. That is acceptable: formula shape changes are ~10% of actual edits and require real thought anyway.
- **UI field additions** (new input that feeds a formula) still require wiring in `index.html`.
- **Schema-shape changes to `constants.json`** (e.g., nesting `fha` under `loanPrograms`) require synchronized reader edits. Mitigated by a `_meta.version` field and a fail-loud loader.

---

## 3. Phased implementation plan

### Phase A — Extract shared constants (ship first, commit, validate) — **~2h**

**Goal:** every numeric constant in the drift table lives in `spec/constants.json` and is read by all three runtimes. No formula moves.

**Files touched:**

1. **New: `spec/constants.json`** — schema per §7 below.
2. **New: `spec/__init__.py`** — ~20-line Python loader. Resolves repo root, `json.load`s the file, exposes module-level constants (`FHA`, `JOSE`, `TOPSIS_WEIGHTS`, `INSURANCE`, `ZIP_TIERS`, `REHAB_CATEGORIES`, `PRESETS`). Hard-fails on missing file or malformed JSON.
3. **Edit: `calc.js`** — replace hardcoded constant literals (`FHA_MIP_UPFRONT_RATE = 0.0175`, etc.) with values read from the JSON. Use a `loadSpec()` helper that works in both Node (via `readFileSync`) and browser (via top-level `await fetch`). Re-export every constant under its existing name so tests and callers see no API change.
4. **Edit: `index.html`** — delete the inline `DEFAULTS`, `JOSE_THRESHOLDS`, `FHA_MIP_*`, `PRESETS`, `ZIP_TIERS`, `REHAB_CATEGORIES` duplicate declarations. Replace with a single fetch at page bootstrap that populates a `window.SPEC` object; rewrite references (`JOSE_THRESHOLDS.dtiCliff` → `SPEC.jose.dtiCliff`, etc.). Inline formulas stay intact for Phase A.
5. **Edit: `batch/verdict.py`, `batch/insurance.py`, `batch/pipeline.py`, `batch/async_pipeline.py`, `batch/ranking.py`** — replace module-level constant dicts with `from spec import JOSE, FHA, TOPSIS_WEIGHTS, INSURANCE`. Formula bodies untouched.

**Validation gate:**

- `pytest` → 61/61 green.
- `node --test tests/` → green.
- Manual: load `index.html` in browser, confirm network tab shows `spec/constants.json` fetched once, verdict engine renders on a known-good deal with identical numbers to pre-change.

**Commit boundary:** single commit, message `refactor: extract shared constants to spec/constants.json`.

### Phase B — Collapse `index.html` formulas into `<script type="module">` import of `calc.js` — **~45m, optional**

**Goal:** browser runtime stops duplicating formulas; imports `computeFhaPITI`, `computeQualifyingIncome`, `maxPitiAtDti`, `computeJoseVerdict` directly from `calc.js`.

**Prereq:** `computeJoseVerdict` must first be ported from `index.html` into `calc.js` (it currently lives only inline). That port is ~80 lines and is the bulk of Phase B's effort.

**Files touched:**

1. `calc.js` — add `computeJoseVerdict(ctx)` (new export) by lifting the inline function verbatim and swapping hardcoded threshold references to `SPEC.jose.*`.
2. `index.html` — change its `<script>` to `<script type="module">`, add `import { computeFhaPITI, computeJoseVerdict, ... } from './calc.js'`, delete the now-duplicated inline bodies.

**Risk:** module-scoping inside `index.html` can break existing global `onclick` handlers. Mitigation: any handler that needs to be global gets explicitly attached to `window` at the top of the module script.

**Skip this phase if Phase A took >2h.** Phase A alone is the bulk of the value.

### Phase C — Cross-runtime parity check — **~15m, only if cheap**

**Goal:** one documented command that proves `calc.js` (via Node) and `batch/verdict.py` produce identical output for a canonical fixture. No new test files — just a documented `make parity` target (or `scripts/parity_check.sh`) that:

1. Runs `node -e "import('./calc.js').then(m => console.log(JSON.stringify(m.computeFhaPITI({...fixture}))))"`.
2. Runs `python -c "from batch.pipeline import compute_piti; import json; print(json.dumps(compute_piti({...fixture})))"`.
3. Diffs the two JSON lines.

Document the command in `handoff/HANDOFF.md` as "run this after touching `constants.json` or any formula". Scope cut forbids _new tests_, not _documented manual commands_.

---

## 4. What NOT to do

- **Do not introduce Pyodide / Python-in-browser.** 6MB download, 3–5s cold boot for a tool Jose runs on his own laptop. Rejected.
- **Do not transpile Python from JS (or vice versa).** Any codegen path needs a build system; Jose's workflow is "open `index.html`, run `python -m batch.pipeline`". Adding webpack/rollup/pyright violates the "no build system" constraint.
- **Do not have the batch pipeline shell out to a Node process for calc.** Cross-process latency + serialization complexity + a second runtime dependency for what is currently pure arithmetic. Rejected.
- **Do not generate Python from a TypeScript schema.** We have neither TypeScript nor a schema compiler. Future over-engineering.
- **Do not move formulas into the JSON as strings and `eval` them.** Security + debuggability disaster. Formulas stay as code.
- **Do not split `constants.json` into per-domain files** (`fha.json`, `jose.json`, ...) in Phase A. One file = one fetch, one parse, one source of truth. Split later if it grows past ~500 lines.

---

## 5. Consequences

### Easier after this lands

- Threshold tweaks, rate updates, preset additions, new ZIP tier assignments — all 1-file edits.
- Audit trail: `git log spec/constants.json` shows every policy change in one view.
- Onboarding: a future reviewer reads one JSON file to understand what thresholds govern the verdict engine.

### Still hard

- New formula _shapes_ (not just values) require mirrored edits in `calc.js` and `batch/*.py`. This is inherent to having two runtimes; no JSON file fixes it.
- Schema evolution of `constants.json` itself: renaming `jose.netPitiGreen` → `jose.cashflow.green` is a synchronized edit across three readers. Mitigated by `_meta.version` and a fail-loud loader that logs which key is missing.
- The browser still needs to know how to wire SPEC values into DOM inputs; Phase A does not eliminate that wiring, just the duplicate _declarations_.

### Forcing function

The existence of `spec/constants.json` changes the default answer to "where does this threshold live?" from "grep all three places" to "open the JSON". That discipline compounds: future Sprint 5+ additions will reach for the JSON first.

---

## 6. Alternatives considered

**Pyodide (Python in browser).** Rejected. 6MB wasm payload, 3–5s cold boot, complicates the "just open index.html" UX. Solves a problem (running identical Python) we do not have; `calc.js` is already the JS port and its tests pass.

**WASM compile of `calc.js`.** Rejected. Requires a build toolchain Jose does not maintain; introduces a binary artifact in git; the JS runtime is already fine for browser use.

**Keep status quo.** Rejected. Drift risk flagged 4/5 in prior reviews; the top-3 change-classes (threshold, rate, preset) are the ones most likely to slip out of sync silently because they rarely surface test failures (tests assert _behavior_ at a fixed threshold, not that three files agree).

**Per-runtime re-declaration but with a manual sync checklist in HANDOFF.md.** Rejected. Checklists decay. The JSON file is the checklist, enforced by the loader.

**Shared JSON spec + ESM imports (recommended).** Accepted. Lowest-ceremony path that kills the majority of drift with no build system and no new test infrastructure.

**YAML instead of JSON.** Rejected. JSON parses natively in the browser and in Node without a dep; Python has it in stdlib. YAML buys comment support but costs a `pyyaml` dep. Opinionated call: JSON wins.

---

## 7. Schema sketch — `spec/constants.json`

```json
{
  "_meta": {
    "version": "1.0.0",
    "lastUpdated": "2026-04-18",
    "description": "Single source of truth for FHA analyzer constants. Read by calc.js (browser + Node tests) and batch/*.py. Formulas live in code; numbers live here."
  },

  "fha": {
    "upfrontMipRate": 0.0175,
    "annualMipStandard": 0.0055,
    "annualMipHigh": 0.0075,
    "baselineLoanLimit": 726200,
    "highBalanceThreshold": 726200,
    "rentalOffset": 0.75,
    "minDownPaymentPct": 0.035
  },

  "jose": {
    "grossIncomeMonthly": 4500,
    "creditScore": 780,
    "cashAvailable": 85000,
    "dtiFrontEndCap": 0.47,
    "dtiBackEndCap": 0.57,
    "netPitiGreen": 2500,
    "netPitiYellow": 2900,
    "netPitiRed": 3200,
    "cashflowGreenMin": 200,
    "roofAgeYellowMax": 20,
    "roofAgeRedMin": 25,
    "maxRehabBudget": 40000
  },

  "topsisWeights": {
    "cashOnCash": 0.25,
    "dscr": 0.20,
    "netCashflow": 0.20,
    "capRate": 0.15,
    "rehabHeadroom": 0.10,
    "tierScore": 0.10
  },

  "insuranceHeuristic": {
    "baseFee": 1800,
    "per100kOver400k": 200,
    "pre1960Multiplier": 1.15,
    "multiUnitMultiplier": 1.10,
    "tier3Multiplier": 1.08
  },

  "presets": [
    {
      "key": "vallejo_priority",
      "name": "Vallejo Priority",
      "zips": ["94590", "94591", "94592", "94589"],
      "defaultRent2BR": 2200,
      "tierOverride": "tier1"
    },
    {
      "key": "east_bay_value",
      "name": "East Bay Value",
      "zips": ["94804", "94805"],
      "defaultRent2BR": 2100,
      "tierOverride": "tier2"
    },
    {
      "key": "richmond_opportunity",
      "name": "Richmond Opportunity",
      "zips": ["94801", "94803"],
      "defaultRent2BR": 2000,
      "tierOverride": "tier2"
    }
  ],

  "zipTiers": {
    "tier1": ["94590", "94591", "94592", "94589"],
    "tier2": ["94804", "94805", "94801"],
    "tier3": ["94803", "94806"],
    "excludedZips": ["94607", "94608"],
    "excludedCities": ["Oakland", "Berkeley", "San Francisco"],
    "tierDefaultRent2BR": {
      "tier1": 2200,
      "tier2": 2050,
      "tier3": 1900
    }
  },

  "rehabCategories": [
    { "key": "roof",     "label": "Roof",          "selfPerformMultiplier": 0.60, "defaultSelfPerform": true  },
    { "key": "electric", "label": "Electrical",    "selfPerformMultiplier": 1.00, "defaultSelfPerform": false },
    { "key": "plumbing", "label": "Plumbing",      "selfPerformMultiplier": 0.85, "defaultSelfPerform": false },
    { "key": "kitchen",  "label": "Kitchen",       "selfPerformMultiplier": 0.70, "defaultSelfPerform": true  },
    { "key": "bath",     "label": "Bathroom",      "selfPerformMultiplier": 0.70, "defaultSelfPerform": true  },
    { "key": "flooring", "label": "Flooring",      "selfPerformMultiplier": 0.65, "defaultSelfPerform": true  },
    { "key": "paint",    "label": "Paint",         "selfPerformMultiplier": 0.50, "defaultSelfPerform": true  },
    { "key": "hvac",     "label": "HVAC",          "selfPerformMultiplier": 1.00, "defaultSelfPerform": false }
  ],

  "defaults": {
    "propertyTaxRate": 0.0125,
    "insuranceAnnual": 1800,
    "vacancyRate": 0.05,
    "maintenanceRate": 0.05,
    "capexRate": 0.05,
    "managementRate": 0.00,
    "closingCostsPct": 0.035,
    "interestRate": 0.0675,
    "termYears": 30
  }
}
```

### Opinionated calls codified above

- **JSON, not YAML.** Browser + stdlib parse natively.
- **One file, not split per domain.** Split later if >500 lines.
- **Presets carry their own `zips` list, separate from `zipTiers`.** Presets are curated bundles; tiers are the underlying scoring surface. They intentionally overlap.
- **ZIP tiers use arrays of strings, not sets.** JSON has no set type; arrays are fine at this scale (<100 ZIPs total).
- **`defaults` block captures the DEFAULTS object from `index.html` line ~2001.** Numeric only; UI-specific defaults (placeholder text, help copy) stay in HTML.
- **`_meta.version` is semver.** Bump major when a reader-breaking schema change ships.

---

## 8. Implementation handoff

**Readers:**

- **Browser (`index.html`):** `const SPEC = await fetch('/spec/constants.json').then(r => r.json()); if (!SPEC?._meta?.version) throw new Error('spec/constants.json missing or malformed'); window.SPEC = SPEC;` at the top of the bootstrap script, before any code that reads a threshold.
- **Node tests (`calc.js`):** detect Node via `typeof window === 'undefined'`; if so, `const SPEC = JSON.parse(readFileSync(resolve(__dirname, 'spec/constants.json'), 'utf8'))`. Same fail-loud check.
- **Python (`spec/__init__.py`):** `_path = Path(__file__).parent / 'constants.json'; _data = json.loads(_path.read_text()); assert _data['_meta']['version'], 'spec/constants.json missing _meta.version'`. Expose module-level bindings: `FHA = _data['fha']`, `JOSE = _data['jose']`, etc.

**Path conventions:** `spec/constants.json` at repo root. Python package `spec/` with `__init__.py` sits next to `batch/`. The JSON file is served as a static asset by `app.py` — confirm the existing static route covers `/spec/*` or add a line.

**Missing-file behavior:** **hard fail on startup** in all three runtimes. Silent fallback to hardcoded defaults is the exact drift mode this ADR kills. Loaders throw; browser shows a visible error banner; Python raises `FileNotFoundError` uncaught so the pipeline aborts.

**Commit boundaries:**

- Phase A → one commit: `refactor: extract shared constants to spec/constants.json`.
- Phase B → separate commit: `refactor: collapse index.html math into calc.js imports`.
- Phase C → separate commit: `docs: add cross-runtime parity check command`.

**Do not combine phases.** Phase A must ship and validate green before Phase B starts, so if Phase B hits a snag (likely around `window.onclick` handlers losing scope in a module script) we keep the Phase A wins.

**Test order after Phase A:**

1. `pytest tests/` — must be 61/61.
2. `node --test tests/` — must be green.
3. Manual browser smoke: load a known deal, confirm verdict + PITI numbers match the pre-change snapshot. Keep a screenshot in `logs/` for reference.

**Rollback:** `git revert` the Phase A commit. No schema migration, no data loss. The JSON file is additive; deleting it + reverting the loader restores the prior world byte-for-byte.

---

## Sign-off

Accepted 2026-04-18 — Phase A (ff5fbdf) and Phase B (809c9cb) both shipped. Drift engine killed; all three runtimes now read `spec/constants.json` for the top-5 change-classes.
