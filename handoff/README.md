# Handoff — Rental Property Deal Analyzer for Jose

Single source of truth for customizing this tool into Jose's FHA owner-occupied house-hack decision engine.

**Status (2026-04-17):** Sprints 0–2 complete. Sprints 3–5 remaining under a cut scope (see below).

> **Scope cut 2026-04-17:** tests, accessibility, and UI polish are frozen at current state for the remaining sprints. See "V1 philosophy" below.

---

## V1 philosophy: ship the decision engine, not the codebase

Jose is not shipping this to anyone. He needs the tool to tell him GREEN / YELLOW / RED on a real Vallejo duplex he's considering this quarter — and that's the entire goal. The already-landed test suite (58→61 tests across Sprints 0–2) stays as a free regression net, but no new tests are required in Sprints 3–5. No new accessibility work beyond the `aria-live` verdict that already landed. No CSS or layout polish. If the UI is ugly but the number is right, it ships. Every remaining hour goes to the math and flow that determine whether Jose makes an offer.

---

## Read in this order

1. **[`USER_PROFILE.md`](./USER_PROFILE.md)** — Jose's authoritative numbers, markets, thresholds. Every other doc references this. Start here.
2. **[`HANDOFF.md`](./HANDOFF.md)** — original mission brief. The full context for why this project exists.
3. **[`TECHNICAL_ASSESSMENT.md`](./TECHNICAL_ASSESSMENT.md)** — what the tool gets right, what's missing, FIX verdict, file:line gap map.
4. **[`SPRINT_PLAN.md`](./SPRINT_PLAN.md)** — 6 sprints totalling ~27.5h to close the gaps.
5. **[`USER_FLOW.md`](./USER_FLOW.md)** — end-to-end workflow tree: happy paths, branch conditions, failure modes, handoff contracts.
6. **[`ACCEPTANCE_CRITERIA.md`](./ACCEPTANCE_CRITERIA.md)** — testable Given/When/Then criteria + Definition of Done per sprint.

---

## At a glance

| Decision | Status |
|---|---|
| Tool fit | **FIX** (customize, don't rebuild) |
| AI provider | Anthropic Claude API (Jose added key to `.env`) |
| Deployment | Local only — `http://localhost:8000` |
| Effort landed | Sprints 0–2 complete (~15h) |
| Effort remaining | **~9.5h** across Sprints 3–5 (was 12.5h; scope cut removed test/a11y/UI tasks) |
| Critical gate | Sprint 0 (tests) landed — no new test gates going forward |
| North-star | Paste Redfin URL → ≤ 60s → FHA-aware G/Y/R verdict + reasons Jose trusts enough to offer on |

---

## The six gaps to close (short version)

1. **FHA MIP** not in PITI calc → add upfront 1.75% + annual 0.55%/12
2. **75% rental offset** not supported → add qualifying-income + DTI module
3. **Rehab** is a single scalar → add category breakdown + C-39 self-perform toggle
4. **Scoring** hardcodes 20% down → fix + add Jose-tuned G/Y/R overlay
5. **Defaults** scattered → central `DEFAULTS` config with Jose's 17 field values
6. **Presets** single-slot → array storage + 3 market presets

Full file:line map: see [`TECHNICAL_ASSESSMENT.md`](./TECHNICAL_ASSESSMENT.md#3-math-gaps--exact-fileline).

---

## What's explicitly out of scope

- CalHFA MyHome automation (optional input field only)
- BiggerPockets / county permit API scrapers
- SMS / Twilio / Telegram alert pipelines
- Public cloud deploy (datacenter IPs blocked by Redfin/Zillow anyway)
- Vue / Firebase rewrite
- Full BRRRR refi modeling
- Tenant management

See `HANDOFF.md` §5 and `ACCEPTANCE_CRITERIA.md` §6 for full list with rationale.

---

## Running the tool right now

```bash
cd ~/Documents/Projects/Rental-Property-Deal-Analyzer
source venv/bin/activate
python app.py
# open http://localhost:8000
```

A post-Sprint-5 `RUN_ME.md` will replace these instructions with a friendlier Jose-facing quick-start.

---

## Document ownership

- **Jose** owns `USER_PROFILE.md`. No change without his approval.
- **Sprint Prioritizer agent** owns `SPRINT_PLAN.md` (regenerated when scope shifts).
- **Workflow Architect agent** owns `USER_FLOW.md` (regenerated when flow changes).
- **Product Manager agent** owns `ACCEPTANCE_CRITERIA.md` (regenerated when features added).
- **Engineering** owns `TECHNICAL_ASSESSMENT.md` — update when a sprint closes a gap.
- **HANDOFF.md** is immutable historical record. Do not edit.
