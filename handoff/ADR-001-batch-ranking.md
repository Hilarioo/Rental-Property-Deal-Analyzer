# ADR-001: Batch Property Analysis and Multi-Criteria Ranking

**Status:** Accepted
**Date:** 2026-04-18
**Deciders:** Jose H Gonzalez (owner), Software Architect agent (facilitator)
**Supersedes:** none
**Superseded by:** none

---

## 1. Context

Jose currently triages Redfin listings one-at-a-time through the single-URL wizard at
`http://localhost:8000`. The V1 sprints (0–4) landed the math he needs for a single
property: FHA-correct PITI with MIP, 75% rental offset, DTI stretch panel, C-39
contractor edge, and a Green/Yellow/Red verdict with reasons.

That single-URL workflow is adequate when he is looking at one listing. It breaks down
on a real weekend of offer-hunting, where he routinely has 15–40 Redfin tabs open across
Vallejo, East Bay, and Richmond. The per-listing wizard forces him to:

1. Copy URL → paste → wait 20–45s → eyeball verdict → repeat.
2. Hold a mental ranking across listings because the tool only ever shows one at a time.
3. Re-enter context (preset choice, per-unit rents) for each analysis.
4. Lose history — every analysis is ephemeral; reopening the same listing 3 days later
   re-scrapes and re-analyzes from scratch.

The north star from `USER_PROFILE.md §12` is "decision-grade output in ≤60s from URL
paste." That holds for one URL. It does not scale to a triage session of 40 tabs. The
asymmetry Jose actually feels is: *I do not need 60 seconds of depth on every listing;
I need to know which 3 of 40 deserve the 60-second treatment.*

### Why now

Three facts force the decision this sprint:

- Jose is actively shopping (he has a pre-approval timer). Every weekend without batch
  triage is ~4 hours of manual copy-paste.
- The narrative LLM provider now ships a Message Batches API that is 50% cheaper than
  serial synchronous calls and supports up to 10,000 requests per batch. Leaving that
  on the table for a 40-URL run costs real money over a shopping season.
- A prior security review flagged two raw-exception leaks at `app.py:1688` and `:1931`
  that return `str(exc)` to the client. The batch endpoint introduces a new public
  surface; shipping it without fixing the twin leaks would triple the blast radius.

### Constraints that shaped the decision

- **Local-only.** No cloud database, no multi-tenant auth. `USER_PROFILE.md §12.3` and
  `ACCEPTANCE_CRITERIA.md §7` explicitly forbid public deploy.
- **Single user.** No login, no ACLs, no row-level security.
- **Math already shipped.** Sprints 0–4 give us `computeJoseVerdict`, `netPiti`,
  `cashToClose`, `effectiveRehab`, ZIP tier lookup, and the Jose thresholds constants.
  The ranker consumes those outputs; it does not reimplement them.
- **The LLM cannot score.** A narrative LLM is non-deterministic and cannot be
  defended to a lender or to future-Jose. Scoring must be arithmetic. The LLM narrates
  *after* the rank is computed, never before.
- **MVP ships tomorrow.** Async batch is valuable but not blocking. Synchronous mode
  must be complete and trustworthy first; async is a toggle layered on top.

---

## 2. Decision

We ship a batch analysis and ranking feature with four load-bearing choices.

### 2.1 TOPSIS as the ranking algorithm

**Technique for Order of Preference by Similarity to Ideal Solution** (Hwang & Yoon
1981). Given a matrix of *n* properties × *k* criteria with per-criterion weights and
direction (benefit vs cost), TOPSIS produces a scalar score in [0, 1] where 1 is the
closest to the ideal (best on every criterion) and 0 is the closest to the anti-ideal.

We use 13 criteria covering affordability, return, BRRRR equity capture, ZIP tier,
contractor edge, and listing signals. Weights sum to 1.00 and are enumerated in the
criteria table of `BATCH_DESIGN.md §C`. Weights are constants, not learned parameters —
they reflect Jose's explicit priorities and are auditable.

**Pareto non-dominance filter runs before TOPSIS.** A property dominates another if it
is at-least-as-good on every criterion and strictly better on one. Pareto-dominated
properties are still scored and still ranked, but the UI flags the Pareto-efficient
subset so Jose can see the frontier at a glance. This guards against the TOPSIS
weakness of hiding a strictly better deal behind an unlucky weight choice.

**Hard-fail gates run before TOPSIS.** The existing `computeJoseVerdict` predicate from
Sprint 4 runs first. Any property that comes back RED for a hard-fail reason
(excluded ZIP, flat roof conversion, PITI > 55% DTI max, etc.) gets a TOPSIS score of
0 and a `pareto_efficient: false` regardless of the other criteria. This matches
`USER_PROFILE.md §7` and §11's intent: hard-fails are disqualifying, not just
down-weighting.

### 2.2 SQLite at `./data/analyzer.db` as the persistence layer

A single on-disk file, accessed via Python's stdlib `sqlite3` module with WAL journal
mode enabled. Schema covers five tables: `properties`, `scrape_snapshots`, `batches`,
`rankings`, `claude_runs`. `urlHash` (SHA-256 of the normalized URL) is the dedup key
on `properties`; the same listing pasted twice in a week produces one `properties` row
and N `scrape_snapshots` rows.

Operation locking uses `BEGIN IMMEDIATE` on the critical section that (a) checks for
an existing `urlHash`, (b) inserts a new `scrape_snapshot`, and (c) writes the
`rankings` row. WAL mode lets read-only endpoints (`GET /api/batches`,
`GET /api/properties/{urlHash}/history`) proceed concurrently without blocking a batch
insert.

### 2.3 Sync-first HTTP, async toggle for scale

`POST /api/batch-analyze` is the default. The client POSTs an array of URLs; the
server scrapes, computes criteria, ranks, and returns a JSON payload in one response.
For a 10–20 URL batch this completes in 60–180s on Jose's laptop — acceptable given
that the alternative is 10–20 sequential wizard runs.

`POST /api/batch-submit-async` is the toggle. For overnight runs of 50–500 URLs, the
client submits the batch, receives a `batchId` immediately, and polls
`GET /api/batch-status/{batchId}` until done. The server delegates the LLM narration
step to the Anthropic Message Batches API (50% cheaper, 24h SLA). Scoring still
happens server-side arithmetically; only the narrative generation is batched.

The UI defaults to sync mode. When the URL count exceeds 20, the UI unlocks the async
toggle with a tooltip explaining the tradeoff (cheaper and higher-scale vs. delayed
results).

### 2.4 The narrative LLM narrates; it does not score

Every criterion in the 13-criterion matrix is computed by existing deterministic math
in `calc.js` or new pure functions (BRRRR formulas, derived metrics from SQLite
history). The ranking is 100% arithmetic.

The LLM is called *after* the rank is produced, with prompt caching: a cached system
block carries Jose's profile, the scoring rubric, and the output contract; a
per-property block carries the criteria values and the verdict. The LLM returns a
2–3 sentence narrative explaining the rank in plain English. The narrative is shown
alongside the score but never used as an input to the score.

---

## 3. Consequences

### 3.1 What becomes easier

- **Triage 40 listings in one pass.** Jose pastes a bullet-list of URLs, hits Analyze,
  and sees a ranked table in 2–3 minutes for sync mode or overnight for async.
- **Historical comparison.** Every analyzed property is kept in SQLite. Re-analyzing
  the same address 3 days later surfaces price velocity, DOM change, and whether the
  listing has re-appeared under a new MLS ID.
- **Defensible to the lender.** The TOPSIS score, the criteria matrix, and the weight
  vector are all inspectable. Jose can hand the lender a printed ranking and explain
  every number without saying "the AI told me."
- **Lower API cost.** Prompt caching on the system block trims roughly 60% off the
  input-token cost for a 20-URL batch. Async Message Batches mode trims another 50%
  for overnight runs.
- **Security footprint smaller.** Fixing M1/M3 in the same sprint closes the two
  known exception-leak paths; the new batch endpoint is the third surface and opens
  cleanly.

### 3.2 What becomes harder

- **Tuning weights is a new skill.** The 13 weights sum to 1.00 and reflect Jose's
  current priorities. If Jose changes strategy (say, pure cash-flow investor instead
  of house-hack), the weights need to change. We document the weights as named
  constants with rationale comments, but we do not expose a weight-editor UI in V1.
  That is a V2 ask.
- **SQLite migrations are a new concern.** We have no migration framework. Schema
  changes post-V1 will need a hand-rolled `ALTER TABLE` script. For a single-user
  local tool this is acceptable; we note the debt.
- **Two request paths instead of one.** Sync and async share the scoring core but
  diverge in the LLM call. We pay a ~15% maintenance tax on the narrator layer to
  keep both modes working. We judge the async payoff worth it because overnight runs
  are the feature that actually scales Jose's workflow.
- **Cache invalidation.** Historical rows are kept forever. A listing that changes
  price and gets re-scraped produces two snapshots; the `price velocity` derived
  metric reads both. If Jose ever edits the database by hand, derived metrics can go
  stale. Mitigation: all derived metrics are computed at rank-time from raw
  `scrape_snapshots`, never materialized.
- **SQLite `BEGIN IMMEDIATE` can raise `OperationalError: database is locked`** under
  write contention. For a single-user tool this should be vanishingly rare, but we
  add a retry-with-backoff wrapper (up to 3 attempts, 100ms / 300ms / 900ms) around
  every write to be defensive.

### 3.3 What we give up by picking TOPSIS

- **Interpretability of pairwise comparisons.** AHP (Analytic Hierarchy Process) would
  produce pairwise consistency checks; TOPSIS does not. We accept this because Jose
  has one coherent priority ordering, not a consensus problem across stakeholders.
- **Outranking semantics.** ELECTRE would give us "A outranks B" statements rather
  than a scalar score. Useful for a group decision; overkill for one buyer.
- **Weight-robustness visualization.** TOPSIS is mildly sensitive to the weight
  vector. We mitigate with the Pareto filter (which is weight-invariant) as a
  cross-check.

### 3.4 What we give up by picking SQLite over Firestore

- **Multi-device sync.** Nothing a fresh clone of the laptop can't recover from. Jose
  is single-device. Accepted.
- **Real-time listeners.** Irrelevant for a batch-triage tool. Accepted.
- **Managed backups.** Mitigated by adding `data/analyzer.db` to a local rsync
  target; documented in `RUN_ME.md` (separate deliverable, not this ADR).
- **Zero-ops scaling.** Irrelevant at 1 user. Accepted.

### 3.5 Consequences of using the LLM as a structured extractor (not a narrator)

An expansion after the initial ADR draft: Jose requires zero hand-entry on the
Loan / Income / Expenses fields in batch mode. The only feasible way to
auto-populate roof age, rehab estimates per category, motivation signals, risk
flags, insurance uplift, and ADU potential from a Redfin listing is to ask a
capable multimodal LLM to extract them in one pass. We use Claude Sonnet 4.5
with Vision for this purpose.

**Key properties of this decision:**

- **The LLM extracts; it does not rank.** Ranking remains algorithmic TOPSIS on
  a 13-criterion matrix. The LLM's outputs (rehab bands, risk flags, etc.) are
  *inputs* to the ranking — they populate criterion values that the TOPSIS math
  then consumes deterministically. The same property, scored twice against the
  same weights and the same LLM output, produces the same rank.
- **One consolidated call per property, not many focused calls.** We send one
  request with a cached system block (~1500 tokens) defining the extraction
  rubric and a per-property user block (~650 tokens) plus the primary listing
  image. The model returns one JSON blob covering every extracted field. See
  §4.5 for the alternatives considered.
- **Per-URL SQLite cache with explicit invalidation.** `properties.llm_analysis`
  holds the last extraction output keyed by `url_hash`. Invalidation triggers
  are exactly: new URL, scraped price moved >3%, scraped DOM increased 14+
  days, or cache age >30 days. Otherwise we reuse the cached JSON and spend
  zero API dollars. Over a multi-week shopping season, the average marginal
  cost per URL approaches zero.
- **Malformed JSON has a defined fallback.** Per-field defaults fill any missing
  or unparseable fields (all rehab bands zero, all risk flags absent, insurance
  uplift 1.0). A badge in the UI tells Jose an extraction failed so he can
  treat that property with extra suspicion. This isolates a single bad LLM
  response to a single property; the batch still completes.
- **Vision is limited to the primary listing image in V1.** Attaching all
  listing photos (typically 20–40) would increase per-property token cost by
  an order of magnitude for marginal extraction gain. Multi-image on demand
  is a V2 refinement.

**What becomes easier:**

- Zero hand-entry in batch mode for Loan / Income / Expenses fields Jose
  previously typed per property. This is the speed-to-offer payoff.
- Rehab estimates are now defensible: the LLM cites the rubric band and the
  evidence used, and Jose can audit or override per-property.
- Risk flags (galvanized, knob-and-tube, foundation concern, flat roof,
  unpermitted ADU) gate the Sprint 4 hard-fail predicates without manual
  data entry.

**What becomes harder:**

- Schema evolution. The LLM's output JSON schema is now a contract surface. If
  we add a rehab category, we change the system prompt, the per-field fallback
  defaults, and any downstream consumers in one coordinated edit. We accept
  this as the cost of the speed-to-offer win.
- Testing. Fixture-based tests now need a mock LLM response for every scenario
  we exercise. Worth it; it was already needed for the narrator path in V1.
- Observability. Three external APIs (FEMA, Cal Fire, OSM Overpass) plus the
  LLM extraction plus the prior scrape means one batch row has five remote
  dependencies. We mitigate with the 8s-hard-cap enrichment budget and the
  "no single external failure blocks ranking" invariant in `BATCH_DESIGN.md §N.5`.

Swapped Walk Score (TOS-restricted to consumer-facing apps) for OSM Overpass — free for personal/private use, yields richer structured amenity data at the cost of running our own simple aggregation formula.

**Failure-mode summary:** every external step (scrape, geocode, FEMA, Cal Fire,
OSM Overpass, LLM) has an explicit null-or-default fallback. The worst case for
any one property is that it is ranked using conservative defaults with a
visible "enrichment incomplete" or "LLM extraction failed" badge. No external
failure prevents the batch from completing.

### 3.6 What we give up by scope-cutting async to a toggle

- **Best cost-per-URL from day one.** Running everything through Message Batches
  would be cheapest, but forces a polling UX on the 10-URL case where sync is snappy.
  Not worth the UX tax for MVP.
- **Uniform code path.** Two code paths (sync scoring + inline LLM, async scoring +
  batched LLM) is more code than one. We accept the complexity as the cost of
  shipping a usable MVP tomorrow.

---

## 4. Alternatives considered

### 4.1 Ranking algorithm

| Option | Why considered | Why rejected |
|---|---|---|
| **Simple weighted sum** | Trivial to implement; one multiply-accumulate per property. | Degenerates when criteria have vastly different scales. A $50K rehab spread gets swamped by a 0.02 cap-rate spread unless you normalize. Once you normalize, you are 80% of the way to TOPSIS anyway — pay the last 20% and get Pareto filtering for free. |
| **AHP (Saaty 1980)** | Strong interpretability; pairwise comparisons force the user to articulate tradeoffs. | Requires a 13×13 pairwise comparison matrix with consistency ratio < 0.10. That is a 78-question elicitation Jose has zero interest in doing. Weights are already clear from his profile. |
| **ELECTRE (Roy 1968)** | Outranking relations handle incommensurable criteria well. | Thresholds (indifference, preference, veto) are another layer of elicitation. The output is a partial order, not a score — harder to render as a ranked list in a 60s triage UI. |
| **PROMETHEE** | Preference functions per criterion give fine-grained control. | Six preference-function choices per criterion = 78 decisions to configure. Same elicitation burden problem as AHP. |
| **TOPSIS (selected)** | Weights are simple, output is a scalar, Pareto pre-filter complements it cleanly. | None — this is the selection. |

References: Behzadian et al. (2012) "A state-of the-art survey of TOPSIS applications"
reports TOPSIS as the most-used MCDM method in supply chain and logistics decisions
since 2000, precisely because weights are easier to elicit than pairwise matrices.

### 4.2 Persistence layer

| Option | Why considered | Why rejected |
|---|---|---|
| **Firestore (initial prior-agent recommendation)** | Real-time sync, zero ops, native TTL for ephemeral runs. | Requires cloud auth, a Google account on Jose's project, and a network dependency for every write. Violates `USER_PROFILE.md §12.3` (local-only). Also introduces per-operation cost that scales with a batch-triage workflow in exactly the wrong direction. |
| **Flat JSON files** | Simplest possible. `git diff`-able. | Concurrent write safety is nonexistent; an interrupted write corrupts the file. No secondary indexes → ranking across 500 historical properties takes seconds. |
| **Postgres via Docker** | SQL parity with a real backend; would ease a future cloud migration. | Introduces a Docker dependency and a second process to manage. For a single-user tool this is over-engineered. If we ever go multi-user, we migrate; the SQLite schema is 95% portable to Postgres. |
| **SQLite (selected)** | Stdlib on Python 3, stable file format, WAL mode handles our concurrency, `sqlite3` CLI is the best ad-hoc query tool there is. | None. |

### 4.3 Async mode

| Option | Why considered | Why rejected |
|---|---|---|
| **Always sync** | One code path. Never tell the user to wait. | Leaves the 50% cost saving on the table for overnight runs. At 100+ URLs, sync wall-clock exceeds a reasonable attention span. |
| **Always async** | Uniform code path, maximum cost savings. | Forces a polling UX on the 3-URL case. Destroys the "paste and see" feel for small batches. |
| **Sync + async toggle (selected)** | Small batches stay snappy; big batches get the cost win. Toggle is explicit, not auto. | Two code paths. Accepted. |

### 4.5 One consolidated LLM extraction call vs several focused calls

Because the LLM now extracts structured data rather than narrating, we had to
decide whether to issue one big call per property (asking for all fields at
once in a single JSON blob) or several smaller calls (one for rehab, one for
motivation signals, one for risk flags, etc.).

| Option | Pros | Cons |
|---|---|---|
| **Several focused calls per property** | Tighter prompts; one bad response only loses one category; parallelizable within a property. | 4–6x the roundtrip latency per property; 4–6x the rate-limit pressure; each call has its own system prompt to cache, multiplying cache writes; vision image would have to be attached to every call that needs it (or we split image-requiring fields from text-only fields, complicating the code path). |
| **One consolidated call per property** (selected) | One system-prompt cache hit serves all fields; one roundtrip; one Vision image attachment; one rate-limit slot per property; simpler code path for the validator and fallback logic. | One malformed response wipes out all extracted fields for that property. Mitigated by robust per-field fallback defaults (§3.5) — the property still ranks, just with conservative values. |

**Decision: single consolidated call.** The batch-throughput argument dominates.
A 40-URL batch with 5 focused calls each would be 200 LLM roundtrips; one
consolidated call is 40. The failure mode (one bad JSON response = one
property's fields defaulted) is already bounded by per-field fallbacks, and
Jose sees a badge in the UI when it happens. We value the 5x throughput and
the simpler code path more than the marginal robustness of splitting the call.

### 4.6 LLM role

| Option | Why considered | Why rejected |
|---|---|---|
| **LLM scores the properties** | Could handle criteria the rubric misses (e.g., "this listing photo looks like deferred maintenance"). | Non-deterministic. Two runs of the same property would give two different ranks. Cannot be defended to a lender. Destroys the acceptance-test harness from Sprint 0. |
| **LLM picks the weights** | Automates the weight-elicitation problem. | Same non-determinism problem; also hides Jose's actual priorities behind an opaque step. |
| **LLM narrates only (initial selection)** | Adds the "why this rank" plain-English layer without letting it touch the score. Also caches cleanly — system prompt is identical across all properties in a batch. | Superseded by the structured-extraction role. See §3.5 and §4.5. |
| **LLM extracts structured data, ranking stays algorithmic (selected, expanded scope)** | Auto-populates fields Jose previously hand-entered. Output is deterministic-with-fallback (per-field defaults on malformed JSON). One consolidated call per property caches the system block once and keeps the code path simple. Includes a short `narrativeForRanking` field for display-only tie-break text. | Introduces a schema contract to evolve; adds per-URL SQLite cache and invalidation policy; widens external-dependency surface by three APIs. Accepted per §3.5. |

---

## 5. Implementation phasing

This ADR commits to two phased commits within one sprint:

**Commit 1 (MVP, synchronous):** SQLite schema + `/api/batch-analyze` sync endpoint +
TOPSIS + Pareto + hard-fail gates + UI collapsible section + security fixes M1/M3.

**Commit 2 (async toggle):** Message Batches integration + `/api/batch-submit-async` +
`/api/batch-status/{batchId}` + async UI toggle + status chip.

Rationale for phasing: Commit 1 is the MVP. It can ship and deliver value tomorrow
even if Commit 2 slips. Commit 2 is strictly additive — no changes to the sync path.
If we ship them together we conflate two unrelated failure modes (ranking math bugs
vs. async plumbing bugs) in the same bisect target.

See `BATCH_DESIGN.md §H` for the detailed phasing justification including file-level
diff scope.

---

## 6. References

### 6.1 Academic

- Hwang, C.L. and Yoon, K. (1981). *Multiple Attribute Decision Making: Methods and
  Applications*. Springer-Verlag. Original TOPSIS paper.
- Behzadian, M., Khanmohammadi Otaghsara, S., Yazdani, M., and Ignatius, J. (2012).
  "A state-of the-art survey of TOPSIS applications." *Expert Systems with
  Applications* 39(17): 13051–13069.
- Saaty, T.L. (1980). *The Analytic Hierarchy Process.* McGraw-Hill. AHP reference
  for the comparison in §4.1.
- Roy, B. (1968). "Classement et choix en présence de points de vue multiples."
  *Revue Française d'Informatique et de Recherche Opérationnelle* 2(8): 57–75.
  ELECTRE origin.

### 6.2 Internal

- `handoff/USER_PROFILE.md` §3 (loan strategy), §5 (C-39 multipliers), §6 (ZIP
  tiers), §7 (excluded markets), §10 (DEFAULTS config), §11 (Green/Yellow/Red
  predicates).
- `handoff/SPRINT_PLAN.md` §2–§6 (Sprint 0–4 shipped math).
- `handoff/ACCEPTANCE_CRITERIA.md` F1–F10 (feature-level ACs for the already-shipped
  single-property path; the batch path consumes these outputs and does not redefine
  them).
- `index.html` `JOSE_THRESHOLDS` and `computeJoseVerdict` at approximately line 2254
  (Sprint 4 deliverable; reused verbatim as the hard-fail pre-filter).
- `calc.js` (Sprint 0 extraction; reused for per-property criterion computation).

### 6.3 Patterns adopted from Scrappy

- `urlHash` as a canonical dedup field (SHA-256 of normalized URL).
- Prompt caching on the narrative LLM system block.
- `BEGIN IMMEDIATE` for the dedup-and-insert critical section.
- Agent review hooks registered in `.claude/settings.json`.
- ADR files under `handoff/` (this file is ADR-001).
- AI-attribution policy: no "AI assistant" references in commit messages or body
  copy for this feature forward.

### 6.4 Third-party APIs

- The narrative LLM provider's Message Batches API (product name: Anthropic Message
  Batches). 50% discount on both input and output tokens; 24-hour processing SLA;
  up to 10,000 requests per batch.
- The narrative LLM provider's prompt caching feature. Cached blocks re-read at
  10% of the input-token price after the first call within a 5-minute TTL.

---

## 7. Open questions deferred to later

These are deliberately out of scope for V1 batch:

1. **Weight editor UI.** V2 ask. The weights are named constants in this ADR and the
   design doc; editing them is a source edit today.
2. **Alternative scoring schemes side-by-side.** Could show TOPSIS rank and a pure
   cash-flow rank next to each other. Deferred; adds a second elicitation question
   ("which rank do I trust?") that is not blocking.
3. **Cloud sync or mobile app.** Explicitly out of scope per `USER_PROFILE.md §12.3`.
4. **Multi-user sharing of batch results.** Out of scope; single-user tool.
5. **Learning weights from Jose's past offer decisions.** Interesting V3 direction
   once Jose has made 5–10 real offers. Not actionable today.

---

## 8. Sign-off

This ADR is accepted when:

- Jose reads §1–§3 and signs off on the decision.
- The Senior Developer agent accepts `BATCH_DESIGN.md` as implementable without
  further architectural input.
- The first commit of the two-commit phasing plan merges to `main` on a feature
  branch with the security fixes M1/M3 included.

**Jose sign-off:** _pending_
**Engineer accept:** _pending_
