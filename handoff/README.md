# Handoff — Rental Property Deal Analyzer for Jose

Single source of truth for Jose's FHA owner-occupied house-hack decision engine.

**Status (2026-04-18):** V1 SHIPPED. All 6 sprints DONE. 61 tests green. 7 batch-feature commits landed post-V1. Current posture: **GREEN local single-user**; **YELLOW if remote-exposed** (see [`BACKLOG.md`](./BACKLOG.md) Sprint 7A for the hardening plan).

> The original V1 mission — "paste a Redfin URL, get a GREEN/YELLOW/RED verdict Jose trusts enough to offer on" — is complete. Post-V1 work (batch triage, SQLite persistence, Anthropic Message Batches, external enrichment, security hotfixes) is layered on top and tracked in `BACKLOG.md`.

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
| Tool fit | **FIX — complete** (V1 customization shipped) |
| V1 scope | **SHIPPED** (all 6 sprints DONE) |
| AI provider | Anthropic Claude API (Sonnet 4.5 with Vision for structured extraction) |
| Deployment | Local only — `http://localhost:8000` |
| Tests | **61 green** (pytest + node --test) |
| Post-V1 commits | 7 batch-feature commits + critical security hotfixes |
| Current posture | **GREEN** local single-user / **YELLOW** if remote-exposed |
| Next hard gate | Sprint 7A security hotfixes (see `BACKLOG.md`) |
| North-star | Paste Redfin URL → ≤60s → FHA-aware G/Y/R verdict Jose trusts enough to offer on ✅ |

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
