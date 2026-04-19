# Handoff — Rental Property Deal Analyzer for Jose

Single source of truth for Jose's FHA owner-occupied house-hack decision engine.

**Status (2026-04-19):** V1 + all post-V1 sprints through Sprint 12 SHIPPED. 111 pytest + 43 JS + 27/28 parity tests green (the 1 fail is a pre-existing stale `DTI 49.9%` fixture — JS+Py still agree). Current posture: **GREEN local single-user**; remote exposure remains out of scope per Sprint 10A invariants.

> V1 mission + the "paste ZIPs and walk away, come back to ranked list" Sprint 11/11.5/12 delivery are complete. Profile now drives: auto-populated search forms, per-listing tax rates via `matchPresetByZip`, layered Yellow thresholds, 35-mi commute radius from Pittsburg home base, and auto-PM injection at 4+ units. Sprint 13 (automated per-ZIP data puller — replaces the declined per-city agent questionnaire) is next.

---

## Read in this order

1. **[`USER_PROFILE.md`](./USER_PROFILE.md)** — Jose's numbers (authoritative). Start here.
   - **Sprint 10A:** `USER_PROFILE.md` is **local-only / gitignored** (contains W-2 income, credit score, cash balance). New contributors: copy [`USER_PROFILE.example.md`](./USER_PROFILE.example.md) to `USER_PROFILE.md` and fill in your values.
2. **[`HANDOFF.md`](./HANDOFF.md)** — original mission brief (immutable historical record).
3. **[`TECHNICAL_ASSESSMENT.md`](./TECHNICAL_ASSESSMENT.md)** — shipped-state record + post-V1 epilogue. Every gap identified is now closed.
4. **[`SPRINT_PLAN.md`](./SPRINT_PLAN.md)** — retrospective, all 6 sprints DONE with commit hashes.
5. **[`ADR-001-batch-ranking.md`](./ADR-001-batch-ranking.md)** — architectural decision, Accepted.
6. **[`ADR-002-calc-drift-resolution.md`](./ADR-002-calc-drift-resolution.md)** — architectural decision, Accepted.
7. **[`ACCEPTANCE_CRITERIA.md`](./ACCEPTANCE_CRITERIA.md)** — Given/When/Then per feature.
8. **[`USER_FLOW.md`](./USER_FLOW.md)** — workflow tree. **Note: stale — covers single-URL wizard only; batch/async/SQLite flows (~50% of current product) not yet documented. Tracked in [`BACKLOG.md`](./BACKLOG.md) Sprint 7C.**
9. **[`LIVE_RUNTHROUGH.md`](./LIVE_RUNTHROUGH.md)** — 3 real Vallejo properties end-to-end.
10. **[`BACKLOG.md`](./BACKLOG.md)** — prioritized follow-up work (Sprint 7A/7B/7C + Sprint 8/9/10).
11. **[`TROUBLESHOOTING.md`](./TROUBLESHOOTING.md)** — known failure modes and fixes.
12. **[`CHANGELOG.md`](./CHANGELOG.md)** — dated log of what shipped.

---

## At a glance

| Decision | Status |
|---|---|
| Tool fit | **FIX — complete** (V1 + Sprint 11/11.5/12 shipped) |
| V1 scope | **SHIPPED** (Sprints 0–5) |
| Post-V1 | **SHIPPED** (7A/7B/7C/8/9/10A/10B/10-6/11/11.5/12) + hotfixes #6/#8/#9 |
| AI provider | Anthropic Claude 4.X — Opus 4.7 / Sonnet 4.6 / Haiku 4.5 (hotfix #6) |
| Deployment | Local only — `http://localhost:8000` |
| Tests | **111 pytest + 43 JS + 27/28 parity** |
| Current posture | **GREEN** local single-user |
| Next | Sprint 13 — automated per-ZIP data puller |
| North-star | Paste ZIPs → walk away → ranked list of FHA duplex/triplex candidates with verdict + reasons ✅ |

---

## What shipped in V1 (all DONE)

1. **FHA MIP** in PITI — upfront 1.75% financed + annual 0.55%/12 (Sprint 1 / dd1737f)
2. **75% rental offset** + qualifying-income + DTI stretch panel (Sprint 1 / dd1737f)
3. **Per-unit rent inputs** + central DEFAULTS config (Sprint 2 / b115e33)
4. **Market presets** + ZIP-tier banner (Sprint 3 / 9e892ad)
5. **Rehab category table** + C-39 self-perform multipliers (Sprint 4 / 6559559)
6. **Jose-tuned Green/Yellow/Red verdict** with up to 3 plain-English reasons (Sprint 4 / 6559559)
7. **Live run-through** on 3 real Vallejo properties + `RUN_ME.md` (Sprint 5 / 8523b4a)

## What shipped post-V1 (not originally in scope)

- **SQLite persistence** — 8 tables, WAL mode, `BEGIN IMMEDIATE` critical sections
- **Batch analysis** — TOPSIS ranking on 13 criteria + Pareto filter + hard-fail gates
- **Sync + async endpoints** — `/api/batch-analyze` + `/api/batch-submit-async` (Anthropic Message Batches, 50% cheaper)
- **Structured LLM extraction** — consolidated one-call-per-property with Vision; per-URL SQLite cache
- **External enrichment** — FEMA, Cal Fire, OSM Overpass, Census geocoder (8s hard-cap budget)
- **Real rent comps** — Redfin medians replace tier default when ≥2 comps found
- **Shared constants spec** — `spec/constants.json` read by all three runtimes (ADR-002 Phase A)
- **calc.js ESM import** — math deduplication between browser and Node (ADR-002 Phase B)
- **Critical security fixes** — H-1 (exception leak), H-2 (SSRF suffix match), H-3 (LLM field clamp), M-4 (sync batch URL validation)

## What shipped 2026-04-19 (Sprint 11 / 11.5 / 12 + hotfixes)

- **Sprint 11** — profile-driven auto-populate on page load, "Analyze all" batch-from-search button, `POST /api/scan-zips` orchestrator (paste ZIPs → fan out → top-N per ZIP → exclusion filtering → batch submission). 20-ZIP / 15-top-N caps. Rate-limited, loopback PII preserved.
- **Sprint 11.5** — Redfin search filter bugfix (Python post-filter re-enforces min/max/beds/property-type, "likely lot" heuristic kills vacant-lot results from bypassed filters, multi-ZIP in Location field redirects to Scan ZIPs, quick-score short-circuits on likely-lot rows).
- **Sprint 12** — layered Yellow classifier (explicit thresholds OR 10% rule, whichever is more forgiving), geospatial gating (`maxMilesHard` hard cap + `conditionalCities` threshold, Haversine from Pittsburg 38.028/-121.8847), auto-PM injection at units >= 4, `matchPresetByZip` per-listing tax/insurance/vacancy overrides.
- **Hotfix #6** — Anthropic model IDs bumped to Claude 4.X family (retired `claude-sonnet-4-20250514` → `claude-sonnet-4-6` / `claude-opus-4-7` / `claude-haiku-4-5-20251001`). Unblocked the AI analysis final page that was 404ing.
- **Hotfix #7** — promoted Sprint 12 onto main (stacked-PR base mishandling).
- **Hotfix #8** — Scan ZIPs UX: clamp Top-N on blur, auto-expand Batch panel on submit, show chosen mode in summary.
- **Hotfix #9** — `_coerce_narrative` helper stops `sqlite3.ProgrammingError` when `narrativeForRanking` holds a dict.
- **Docs #10** — handoff/ truth-up for Sprint 11 / 11.5 / 12 + hotfixes.
- **Hotfix #11** — Separate `batch_scrape:{ip}` rate-limit bucket (180/min, vs. `/api/scrape` 5/min) so Scan ZIPs doesn't self-DoS.
- **Feat #12** — Scan-vs-Paste source pill, per-row `×` delete + "Clear failed" bulk, sync cap 30 → 100.
- **Fix #13** — Unit inference from APT/UNIT/# address suffix + condo/townhouse type. "401 Stinson St APT 3" now returns "Single condo unit — no 75% FHA offset" instead of the generic "Unit count not detected".
- **Fix #14** — Force sync actually forces sync (previously silently flipped to async above cap). Top-N per ZIP cap 15 → 50.
- **Feat #15** — Min/Max Price inputs on Scan ZIPs panel. Max defaults to `profile.jose.priceCeilingDuplex` so "(none)" preset scans don't surface $49K lots or $645K over-ceiling listings.

**Deferred from Sprint 12:** 12-3 rentalStrategy per-unit LTR/MTR UI, 12-6 203(k) contractor-stretch scenario. **Sprint 14 (queued):** Neighborhood Search form accordion, unified results region below all controls, Max Results 25 → 500, async-completion notification. All tracked in `BACKLOG.md`.

---

## Running the tool right now

```bash
cd ~/Documents/Projects/Rental-Property-Deal-Analyzer
source venv/bin/activate
python app.py
# open http://localhost:8000
```

For batch triage of multiple URLs, see the batch panel in the UI (20+ URL runs unlock the async toggle).

If something fails at startup, check [`TROUBLESHOOTING.md`](./TROUBLESHOOTING.md) first.

---

## Document ownership

- **Jose** owns `USER_PROFILE.md`. No change without his approval.
- `SPRINT_PLAN.md`, `TECHNICAL_ASSESSMENT.md`, `ACCEPTANCE_CRITERIA.md` are shipped-state records. Update when a new sprint closes.
- `HANDOFF.md` is immutable historical record. Do not edit.
- `ADR-*` files flip status on acceptance; bodies stay as written.
- `CHANGELOG.md`, `TROUBLESHOOTING.md`, `BACKLOG.md` are living docs — append as work lands.
