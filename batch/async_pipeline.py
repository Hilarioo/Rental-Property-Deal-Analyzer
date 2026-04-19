"""Async overnight batch path via Anthropic Message Batches (BATCH_DESIGN §B.2/B.3/F).

Shape:

    submit_async_batch(urls, db_path, client_ip)
        - scrape + enrich every URL
        - for URLs whose cache is still valid, reuse the cached llm_analysis
        - for the rest, bundle them into a single Anthropic
          POST /v1/messages/batches submission (one request per URL)
        - persist `batches` row with mode='async', status='pending',
          external_batch_id = msgbatch_...
        - if ALL URLs were cache hits, skip the provider call entirely,
          rank inline, mark status='complete', return sync-shape rankings

    poll_async_batch(batch_id, db_path)
        - terminal-state short-circuit from our DB
        - GET provider status; when 'ended', fetch results, coerce each
          per-URL JSON, update properties + claude_runs, run TOPSIS across
          the full input set, persist rankings, mark 'complete' (or
          'complete' with a status_note if some rows failed)

    reconcile_pending_batches_on_startup(db_path)
        - fire-and-forget task started from app.py; re-polls any
          mode='async' status='pending' batches so we don't orphan an
          overnight submission if the server was restarted

Security posture matches the sync path: per-URL scrape still charges the
/api/scrape rate-limit bucket via `process_url`, image fetches honor the
real-estate-CDN allowlist in batch/llm.py, and no provider exception
string crosses the API surface (logs get logger.exception, clients get
a generic envelope via the caller in app.py).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
from typing import Any

import httpx

from . import llm as llm_mod
from . import ranking as ranking_mod
from .db import (
    get_connection,
    new_uuid_hex,
    url_hash as compute_url_hash,
    utc_now_iso,
    with_immediate_tx,
)
from .insurance import compute_insurance
from .pipeline import (
    TIER_DEFAULT_RENT_2BR,
    _coerce_narrative,
    _extract_zip,
    _insert_snapshot,
    _read_enrichment,
    _read_property,
    _scrape_url,
    _update_analysis_cache,
    _upsert_enrichment,
    _upsert_property_row,
    build_failures_envelope,
    compute_property_metrics,
)
from .verdict import classify_zip_tier

logger = logging.getLogger(__name__)

ANTHROPIC_BATCHES_URL = "https://api.anthropic.com/v1/messages/batches"
_PROVIDER_TIMEOUT_S = 60.0
# Hard caps on the results JSONL body — defense-in-depth against a
# compromised/misbehaving upstream feeding us an unbounded response.
_MAX_RESULTS_BYTES = 128 * 1024 * 1024   # 128 MB total body
_MAX_RESULTS_LINE_BYTES = 2 * 1024 * 1024  # 2 MB per JSONL line


# --------------------------------------------------------------------------
# Prompt helpers — mirror batch/llm.py exactly so sync and async get the
# same structured extraction (cache-keyed by the identical system block).
# --------------------------------------------------------------------------


def _build_user_text(
    *,
    address: str | None,
    price: int | None,
    beds: int | None,
    baths: float | None,
    sqft: int | None,
    year_built: int | None,
    units: int | None,
    dom: int | None,
    description: str | None,
) -> str:
    safe_description = (description or "")[:4000]
    return (
        f"Property: {address or 'unknown'}, "
        f"${(price or 0):,}, "
        f"{beds or '?'}BR/{baths or '?'}BA, "
        f"{sqft or '?'} sqft, built {year_built or '?'}, "
        f"{units or '?'} units. "
        f"DOM: {dom if dom is not None else '?'}. "
        f"Description: {safe_description}"
        f"\n\nReturn JSON per schema."
    )


def _build_params(scrape: dict[str, Any]) -> dict[str, Any]:
    """Build the Messages-API params block used inside a Batches request."""
    user_text = _build_user_text(
        address=scrape.get("address"),
        price=scrape.get("price"),
        beds=scrape.get("beds"),
        baths=scrape.get("baths"),
        sqft=scrape.get("sqft"),
        year_built=scrape.get("year_built"),
        units=scrape.get("units"),
        dom=scrape.get("dom"),
        description=scrape.get("description"),
    )
    content_blocks: list[dict[str, Any]] = [{"type": "text", "text": user_text}]
    image_url = scrape.get("image_url")
    if image_url and llm_mod._image_url_allowed(image_url):
        # Provider fetches the image on our behalf. SSRF allowlist matches
        # the sync path so we never hand off a non-CDN URL.
        content_blocks.append({
            "type": "image",
            "source": {"type": "url", "url": image_url},
        })
    return {
        "model": llm_mod.LLM_MODEL,
        "max_tokens": 4096,
        "system": [
            {
                "type": "text",
                "text": llm_mod._SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        "messages": [{"role": "user", "content": content_blocks}],
    }


# --------------------------------------------------------------------------
# Stage 1 — per-URL enrichment without LLM
# --------------------------------------------------------------------------


async def _prepare_url(
    *,
    url: str,
    http_client: httpx.AsyncClient,
    db_path: str,
    client_ip: str | None,
) -> dict[str, Any]:
    """Scrape, enrich, decide cache status. Does NOT call the LLM.

    Returns a dict with the same keys the sync `process_url` produces for
    non-LLM fields, plus:
      - `cache_hit`: True when we can reuse cached llm_analysis
      - `llm_analysis`: cached dict when cache_hit, else None
      - `ready_for_metrics`: True when we can compute metrics without
        waiting on the provider (i.e. cache_hit or scrape failed)
    """
    from . import enrichment as enrichment_mod

    now_iso = utc_now_iso()
    uh = compute_url_hash(url)
    canonical = url

    conn = get_connection(db_path)
    try:
        cached = _read_property(conn, uh)
        cached_enrichment = _read_enrichment(conn, uh)
    finally:
        conn.close()

    if client_ip:
        try:
            import app as main_app
            if not main_app._check_rate_limit(f"scrape:{client_ip}", 5):
                return _skip_row(
                    url=url, uh=uh, canonical=canonical, cached=cached,
                    reason="rate_limited",
                    verdict_reason="Rate limited — skipped",
                )
        except Exception:  # pragma: no cover
            pass

    # Sprint 8-4: reuse a warm snapshot if we scraped this URL recently.
    from datetime import datetime, timezone
    from .pipeline import _reuse_warm_snapshot

    scrape = _reuse_warm_snapshot(
        db_path=db_path,
        url_hash=uh,
        now_utc=datetime.now(timezone.utc),
    )
    if scrape is None:
        scrape = await _scrape_url(url)
    if not scrape.get("ok"):
        return _skip_row(
            url=url, uh=uh, canonical=canonical, cached=cached,
            reason=scrape.get("error", "unknown"),
            verdict_reason=f"Scrape failed — cannot evaluate ({scrape.get('error', 'unknown')})",
            scrape=scrape,
        )

    zip_code = _extract_zip(scrape.get("address"))
    stale, stale_reason = llm_mod.is_cache_stale(
        cached_row=cached,
        fresh_price=scrape.get("price"),
        fresh_dom=scrape.get("dom"),
    )

    # Enrichment — reuse cached when available.
    if cached_enrichment and not cached_enrichment.get("fetch_errors_json"):
        enrichment_row: dict[str, Any] | None = {
            "lat": cached_enrichment.get("lat"),
            "lng": cached_enrichment.get("lng"),
            "geocode_source": cached_enrichment.get("geocode_source"),
            "flood_zone": cached_enrichment.get("flood_zone"),
            "flood_zone_risk": cached_enrichment.get("flood_zone_risk"),
            "fire_zone": cached_enrichment.get("fire_zone"),
            "fire_zone_risk": cached_enrichment.get("fire_zone_risk"),
            "amenity_counts": json.loads(cached_enrichment["amenity_counts"]) if cached_enrichment.get("amenity_counts") else None,
            "walkability_index": cached_enrichment.get("walkability_index"),
            "fetch_errors": {},
            "enrichment_missing": False,
        }
    else:
        enrichment_row = await enrichment_mod.enrich_property(
            client=http_client,
            lat=scrape.get("lat"),
            lng=scrape.get("lng"),
            address=scrape.get("address"),
            db_path=db_path,
        )

    cache_hit = (not stale) and bool(cached) and bool(cached.get("llm_analysis"))
    cached_analysis: dict[str, Any] | None = None
    if cache_hit:
        try:
            cached_analysis = json.loads(cached["llm_analysis"])
        except (TypeError, json.JSONDecodeError):
            cached_analysis = None
            cache_hit = False

    return {
        "url": url,
        "url_hash": uh,
        "canonical_url": canonical,
        "scrape_ok": True,
        "scrape_error": None,
        "scrape": scrape,
        "zip_code": zip_code,
        "address": scrape.get("address"),
        "price": scrape.get("price"),
        "enrichment": enrichment_row,
        "cache_hit": cache_hit,
        "cache_stale_reason": None if cache_hit else (stale_reason or "new_url"),
        "llm_analysis": cached_analysis,
        "cached_analyzed_at": cached.get("llm_analyzed_at") if cached else None,
        "ready_for_metrics": cache_hit,
        "prepared_at": now_iso,
    }


def _skip_row(
    *,
    url: str,
    uh: str,
    canonical: str,
    cached: dict[str, Any] | None,
    reason: str,
    verdict_reason: str,
    scrape: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "url": url,
        "url_hash": uh,
        "canonical_url": canonical,
        "scrape_ok": False,
        "scrape_error": reason,
        "scrape": scrape or {"ok": False, "error": reason},
        "zip_code": None,
        "address": cached.get("address") if cached else None,
        "price": None,
        "enrichment": None,
        "cache_hit": False,
        "cache_stale_reason": None,
        "llm_analysis": None,
        "cached_analyzed_at": None,
        "ready_for_metrics": False,
        "hard_fail": True,
        "verdict_forced": "red",
        "verdict_reason_forced": verdict_reason,
    }


# --------------------------------------------------------------------------
# Stage 2 — compute metrics for one prepared row given its llm_analysis
# --------------------------------------------------------------------------


async def _finalize_row(prepared: dict[str, Any], *, db_path: str) -> dict[str, Any]:
    """Turn a prepared row + (cached|fresh) llm_analysis into the rank-ready
    shape produced by pipeline.process_url."""
    if not prepared.get("scrape_ok"):
        return {
            "url": prepared["url"],
            "url_hash": prepared["url_hash"],
            "canonical_url": prepared["canonical_url"],
            "scrape_ok": False,
            "scrape_error": prepared.get("scrape_error"),
            "address": prepared.get("address"),
            "hard_fail": True,
            "criteria": {name: 0.0 for name in ranking_mod.CRITERION_NAMES},
            "metrics": {},
            "derived_metrics": {},
            "verdict": prepared.get("verdict_forced", "red"),
            "verdict_reasons": [prepared.get("verdict_reason_forced") or "Scrape failed"],
            "llm_analysis": None,
            "insurance_breakdown": {},
            "cache_stale_reason": None,
            "scrape": prepared.get("scrape") or {"ok": False},
            "enrichment": None,
            "llm_tokens": {"input": 0, "cached_input_read": 0, "output": 0},
            "llm_ok": None,
        }

    scrape = prepared.get("scrape") or {}
    enrichment_row = prepared.get("enrichment")
    llm_analysis = prepared.get("llm_analysis") or llm_mod.default_llm_analysis(failed=True)
    llm_tokens = prepared.get("llm_tokens") or {"input": 0, "cached_input_read": 0, "output": 0}
    llm_ok = prepared.get("llm_ok")
    if llm_ok is None:
        llm_ok = bool(prepared.get("cache_hit"))

    insurance = compute_insurance(
        price=scrape.get("price"),
        year_built=scrape.get("year_built"),
        flood_zone=(enrichment_row or {}).get("flood_zone"),
        fire_zone=(enrichment_row or {}).get("fire_zone"),
        llm_uplift=((llm_analysis.get("insuranceUplift") or {}).get("suggested")),
        enrichment_missing=bool((enrichment_row or {}).get("enrichment_missing")),
    )

    zip_code = prepared.get("zip_code")
    # Rent comps — real medians via rent_comps_cache (§A.1/§F.1), with
    # TIER_DEFAULT_RENT_2BR as the fallback on cache+Redfin miss.
    from .rent_comps import derive_per_unit_profile, get_rent_estimate

    zip_tier = classify_zip_tier(zip_code)
    scraped_units = scrape.get("units")
    per_unit_beds, per_unit_baths = derive_per_unit_profile(
        total_beds=scrape.get("beds"),
        total_baths=scrape.get("baths"),
        units=scraped_units,
    )
    rent_result = await get_rent_estimate(
        zip_code=zip_code or "",
        beds=per_unit_beds,
        baths=per_unit_baths,
        db_path=db_path,
    )
    if rent_result.get("median_rent") and rent_result["median_rent"] > 0:
        rent_per_unit = rent_result["median_rent"]
        rent_source = rent_result["source"]
    else:
        rent_per_unit = TIER_DEFAULT_RENT_2BR.get(zip_tier, 2000)
        rent_source = "tier_default"
    rent_comps_sample_size = int(rent_result.get("sample_size") or 0)

    units_unknown = scraped_units is None
    computed = compute_property_metrics(
        price=scrape.get("price"),
        units=scraped_units,
        year_built=scrape.get("year_built"),
        beds=scrape.get("beds"),
        baths=scrape.get("baths"),
        dom=scrape.get("dom"),
        zip_code=zip_code,
        address=scrape.get("address"),
        llm_analysis=llm_analysis,
        enrichment_row=enrichment_row,
        insurance_breakdown=insurance,
        rent_per_unit=rent_per_unit,
        hard_fail_units_unknown=units_unknown,
    )
    computed["metrics"]["rent_per_unit"] = int(round(rent_per_unit))
    computed["metrics"]["rent_source"] = rent_source
    computed["metrics"]["rent_comps_sample_size"] = rent_comps_sample_size

    criteria = ranking_mod.criteria_from_metrics(computed["metrics"])
    derived_metrics = {
        "price_velocity": None,
        "dom_percentile_zip": None,
        "price_per_sqft_median_zip": None,
        "topsis_percentile_alltime": None,
        "reappearance_count": 0,
    }

    return {
        "url": prepared["url"],
        "url_hash": prepared["url_hash"],
        "canonical_url": prepared["canonical_url"],
        "scrape_ok": True,
        "scrape_error": None,
        "address": scrape.get("address"),
        "zip_code": zip_code,
        "price": scrape.get("price"),
        "hard_fail": computed["hard_fail"],
        "criteria": criteria,
        "metrics": computed["metrics"],
        "derived_metrics": derived_metrics,
        "verdict": computed["verdict"],
        "verdict_reasons": computed["verdict_reasons"],
        "llm_analysis": llm_analysis,
        "insurance_breakdown": insurance,
        "cache_stale_reason": prepared.get("cache_stale_reason"),
        "scrape": scrape,
        "enrichment": enrichment_row,
        "llm_tokens": llm_tokens,
        "llm_ok": bool(llm_ok),
        "analyzed_at": prepared.get("analyzed_at") or prepared.get("cached_analyzed_at") or utc_now_iso(),
    }


# --------------------------------------------------------------------------
# Persistence — shared with pipeline module helpers
# --------------------------------------------------------------------------


def _persist_batch_final(
    *,
    db_path: str,
    batch_id: str,
    ranked_rows: list[dict[str, Any]],
    status: str,
    status_note: str | None,
    completed_at: str,
    external_batch_id: str | None,
    include_narrative: bool,
) -> None:
    """Write batches UPDATE + rankings rows + property caches. Called from
    the all-cache-hit submit branch AND the poll reconcile path."""
    conn = get_connection(db_path)
    try:
        def _write(c: sqlite3.Connection) -> None:
            c.execute(
                """UPDATE batches
                   SET completed_at = ?, status = ?, error_reason = ?
                   WHERE batch_id = ?""",
                (completed_at, status, status_note, batch_id),
            )
            # Replace any stale rankings rows (poll path may be re-entered).
            c.execute("DELETE FROM rankings WHERE batch_id = ?", (batch_id,))

            # Sprint 8-3: batch homogeneous rows for executemany.
            snapshot_rows: list[tuple] = []
            ranking_rows: list[tuple] = []
            claude_run_rows: list[tuple] = []

            for row in ranked_rows:
                _upsert_property_row(
                    c,
                    url_hash=row["url_hash"],
                    canonical_url=row["canonical_url"],
                    address=row.get("address"),
                    zip_code=row.get("zip_code"),
                    last_price=row.get("price"),
                    last_dom=row.get("metrics", {}).get("dom"),
                    now_iso=completed_at,
                )
                scrape = row.get("scrape") or {"ok": False}
                snapshot_rows.append((
                    row["url_hash"], completed_at,
                    scrape.get("price"), scrape.get("beds"), scrape.get("baths"),
                    scrape.get("sqft"), scrape.get("year_built"), scrape.get("units"),
                    scrape.get("dom"), scrape.get("description"), scrape.get("image_url"),
                    json.dumps(scrape),
                    1 if scrape.get("ok") else 0,
                    scrape.get("error") if not scrape.get("ok") else None,
                ))
                if row.get("enrichment"):
                    _upsert_enrichment(
                        c, url_hash=row["url_hash"],
                        enrichment=row["enrichment"], now_iso=completed_at,
                    )
                if row.get("scrape_ok"):
                    _update_analysis_cache(
                        c,
                        url_hash=row["url_hash"],
                        llm_analysis=row["llm_analysis"],
                        llm_tokens=row["llm_tokens"],
                        insurance_breakdown=row["insurance_breakdown"],
                        cache_stale_reason=row.get("cache_stale_reason"),
                        analyzed_at=row.get("analyzed_at"),
                    )
                    if row.get("cache_stale_reason") is not None and row.get("llm_ok") is not None:
                        tokens = row.get("llm_tokens") or {}
                        claude_run_rows.append((
                            new_uuid_hex(), batch_id, row["url_hash"],
                            external_batch_id,
                            1 if tokens.get("cached_input_read") else 0,
                            tokens.get("input"),
                            tokens.get("cached_input_read"),
                            tokens.get("output"),
                            completed_at, completed_at,
                            "ok" if row.get("llm_ok") else "failed",
                            None if row.get("llm_ok") else "extraction_failed",
                        ))
                narrative_raw = (row.get("llm_analysis") or {}).get("narrativeForRanking") if include_narrative else None
                ranking_rows.append((
                    batch_id, row["url_hash"], row["rank"],
                    float(row.get("topsis_score") or 0.0),
                    1 if row.get("pareto_efficient") else 0,
                    row.get("verdict", "red"),
                    1 if row.get("hard_fail") else 0,
                    json.dumps(row.get("verdict_reasons") or []),
                    json.dumps(row.get("criteria") or {}),
                    json.dumps(row.get("derived_metrics") or {}),
                    _coerce_narrative(narrative_raw),
                    "ok" if include_narrative and row.get("scrape_ok") else "skipped",
                ))

            if snapshot_rows:
                c.executemany(
                    """INSERT INTO scrape_snapshots
                       (url_hash, scraped_at, price, beds, baths, sqft, year_built,
                        units, dom, description, image_url, raw_json, scrape_ok,
                        error_reason)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    snapshot_rows,
                )
            if claude_run_rows:
                c.executemany(
                    """INSERT INTO claude_runs
                       (run_id, batch_id, url_hash, mode, external_batch_id,
                        prompt_cache_hit, input_tokens, cached_input_tokens,
                        output_tokens, cost_usd, created_at, completed_at,
                        status, error_reason)
                       VALUES (?, ?, ?, 'async', ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?)""",
                    claude_run_rows,
                )
            if ranking_rows:
                c.executemany(
                    """INSERT INTO rankings
                       (batch_id, url_hash, rank, topsis_score, pareto_efficient,
                        verdict, hard_fail, reasons_json, criteria_json,
                        derived_metrics_json, claude_narrative, narrative_status)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    ranking_rows,
                )
        with_immediate_tx(conn, _write)
    finally:
        conn.close()


def _build_response_rankings(ranked_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in ranked_rows:
        out.append({
            "rank": row["rank"],
            "url_hash": row["url_hash"],
            "canonical_url": row["canonical_url"],
            "address": row.get("address"),
            "zip_code": row.get("zip_code"),
            "price": row.get("price"),
            "topsis_score": row.get("topsis_score", 0.0),
            "pareto_efficient": bool(row.get("pareto_efficient")),
            "verdict": row.get("verdict", "red"),
            "hard_fail": bool(row.get("hard_fail")),
            "reasons": row.get("verdict_reasons") or [],
            "criteria": row.get("criteria") or {},
            "metrics": row.get("metrics") or {},
            "derived_metrics": row.get("derived_metrics") or {},
            "cache_stale_reason": row.get("cache_stale_reason"),
            # Emitted so the frontend cache-source badge (Sprint 10B-4) can
            # render "updated Xd ago" on cache-hit rows.
            "analyzed_at": row.get("analyzed_at"),
            "insurance_breakdown": row.get("insurance_breakdown") or {},
            "llm_analysis": row.get("llm_analysis"),
            "enrichment": row.get("enrichment"),
            "claude_narrative": _coerce_narrative((row.get("llm_analysis") or {}).get("narrativeForRanking")),
        })
    return out


# --------------------------------------------------------------------------
# Submit
# --------------------------------------------------------------------------


async def submit_async_batch(
    urls: list[str],
    *,
    db_path: str,
    client_ip: str | None = None,
    api_key: str | None = None,
    preset_name: str | None = None,
) -> dict[str, Any]:
    """Entry point for /api/batch-submit-async."""
    seen: set[str] = set()
    deduped: list[str] = []
    for u in urls:
        u = (u or "").strip()
        if not u or u in seen:
            continue
        seen.add(u)
        deduped.append(u)

    batch_id = new_uuid_hex()
    created_at = utc_now_iso()
    api_key = api_key or os.getenv("ANTHROPIC_API_KEY")

    # Stage 1 — scrape + enrich every URL (no LLM yet).
    async with httpx.AsyncClient(follow_redirects=True) as client:
        sem = asyncio.Semaphore(4)

        async def _worker(u: str) -> dict[str, Any]:
            async with sem:
                try:
                    return await _prepare_url(
                        url=u, http_client=client, db_path=db_path,
                        client_ip=client_ip,
                    )
                except Exception:
                    logger.exception("async _prepare_url failed for %s", u)
                    return _skip_row(
                        url=u, uh=compute_url_hash(u), canonical=u, cached=None,
                        reason="worker_exception",
                        verdict_reason="Worker exception during prepare",
                    )

        prepared = await asyncio.gather(*(_worker(u) for u in deduped))

    cache_hits = [p for p in prepared if p.get("cache_hit")]
    needs_llm = [p for p in prepared if p.get("scrape_ok") and not p.get("cache_hit")]
    skipped = [p for p in prepared if not p.get("scrape_ok")]

    # Insert the batches pending row up-front so the poll endpoint can
    # find it even if the provider call itself blows up. input_count is the
    # number of URLs ATTEMPTED (post-dedupe). scraped_count (successful
    # Stage-1 scrapes) is written by _persist_stage1 after scraping completes,
    # so the row reflects what actually made it through Stage 1.
    external_batch_id: str | None = None
    initial_status = "pending"
    conn = get_connection(db_path)
    try:
        def _write(c: sqlite3.Connection) -> None:
            c.execute(
                """INSERT INTO batches
                   (batch_id, created_at, completed_at, mode, input_count,
                    status, external_batch_id, preset_name, error_reason,
                    scraped_count)
                   VALUES (?, ?, NULL, 'async', ?, ?, NULL, ?, NULL, ?)""",
                (
                    batch_id, created_at, len(deduped), initial_status,
                    preset_name,
                    sum(1 for p in prepared if p.get("scrape_ok")),
                ),
            )
        with_immediate_tx(conn, _write)
    finally:
        conn.close()

    # Short-circuit: no URL needs a fresh LLM call -> complete inline.
    if not needs_llm:
        logger.info(
            "async batch %s: all %d URLs were cache hits (or skipped), short-circuiting",
            batch_id, len(prepared),
        )
        # Still write batch_url_hashes so the inputs of THIS batch are
        # isolated from any concurrent batch sharing URLs.
        _persist_stage1(
            db_path=db_path, batch_id=batch_id,
            external_batch_id=None, prepared=prepared,
        )
        finalized = [await _finalize_row(p, db_path=db_path) for p in prepared]
        ranked = ranking_mod.rank_batch(finalized)
        completed_at = utc_now_iso()
        _persist_batch_final(
            db_path=db_path,
            batch_id=batch_id,
            ranked_rows=ranked,
            status="complete",
            status_note="all_cache_hits" if cache_hits and not skipped else None,
            completed_at=completed_at,
            external_batch_id=None,
            include_narrative=True,
        )
        return {
            "batch_id": batch_id,
            "external_batch_id": None,
            "created_at": created_at,
            "completed_at": completed_at,
            "mode": "async",
            "input_count": len(deduped),
            "cache_hit_count": len(cache_hits),
            "status": "complete",
            "rankings": _build_response_rankings(ranked),
            # Sprint 10B-1: surface per-URL failures so the UI can render a
            # retry row for each one instead of silently dropping them.
            "failures": build_failures_envelope(ranked),
            "note": "all_cache_hits" if cache_hits else None,
        }

    # Need the provider. Without an API key we cannot proceed — fail early
    # so the client can switch back to sync or fix the env.
    if not api_key:
        conn = get_connection(db_path)
        try:
            def _mark_failed(c: sqlite3.Connection) -> None:
                c.execute(
                    "UPDATE batches SET status='failed', completed_at=?, error_reason=? WHERE batch_id=?",
                    (utc_now_iso(), "no_api_key", batch_id),
                )
            with_immediate_tx(conn, _mark_failed)
        finally:
            conn.close()
        return {
            "batch_id": batch_id,
            "external_batch_id": None,
            "created_at": created_at,
            "mode": "async",
            "input_count": len(deduped),
            "cache_hit_count": len(cache_hits),
            "status": "failed",
            "error_reason": "no_api_key",
        }

    # Build provider payload. Anthropic caps custom_id at 64 chars, so we
    # truncate the sha256 hex to 58 (still per-batch unique for our scale).
    # At poll time we map the truncated id back to the full url_hash using
    # the rows we stored in Stage-1 (see _parse_results_jsonl).
    provider_requests: list[dict[str, Any]] = []
    for p in needs_llm:
        short = p["url_hash"][:58]
        provider_requests.append({
            "custom_id": f"prop_{short}",  # 5 + 58 = 63 chars
            "params": _build_params(p["scrape"]),
        })

    async with httpx.AsyncClient(follow_redirects=True) as client:
        try:
            resp = await client.post(
                ANTHROPIC_BATCHES_URL,
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    # Required for cache_control:ephemeral on the system
                    # block to take effect at batch-submit time. Without
                    # this header async batches pay full rate instead of
                    # the cached rate; matches batch/llm.py sync path.
                    "anthropic-beta": "prompt-caching-2024-07-31",
                    "Content-Type": "application/json",
                },
                json={"requests": provider_requests},
                timeout=_PROVIDER_TIMEOUT_S,
            )
        except httpx.HTTPError:
            logger.exception("Anthropic batches submit HTTP failure for batch %s", batch_id)
            _mark_batch_failed(db_path, batch_id, "provider_http_error")
            return {
                "batch_id": batch_id, "external_batch_id": None,
                "created_at": created_at, "mode": "async",
                "input_count": len(deduped),
                "cache_hit_count": len(cache_hits),
                "status": "failed", "error_reason": "provider_http_error",
            }

    if resp.status_code >= 400:
        # Log provider body for diagnostics (local file only, gitignored).
        # The API response to the client never includes this text.
        logger.warning(
            "Anthropic batches submit returned HTTP %s for batch %s (body=%r)",
            resp.status_code, batch_id, resp.text[:400],
        )
        _mark_batch_failed(db_path, batch_id, f"provider_http_{resp.status_code}")
        return {
            "batch_id": batch_id, "external_batch_id": None,
            "created_at": created_at, "mode": "async",
            "input_count": len(deduped),
            "cache_hit_count": len(cache_hits),
            "status": "failed", "error_reason": f"provider_http_{resp.status_code}",
        }

    data = resp.json()
    external_batch_id = data.get("id")
    if not external_batch_id:
        _mark_batch_failed(db_path, batch_id, "provider_no_id")
        return {
            "batch_id": batch_id, "external_batch_id": None,
            "created_at": created_at, "mode": "async",
            "input_count": len(deduped),
            "cache_hit_count": len(cache_hits),
            "status": "failed", "error_reason": "provider_no_id",
        }

    # Stash the prepared rows we need at poll time. Use scrape_snapshots
    # + enrichment rows already written during Stage 1. The rankings table
    # doesn't yet have rows for this batch — they land on poll completion.
    # Persist a per-URL snapshot now so the poll reconcile has the scrape
    # data regardless of what's in the DB by then.
    _persist_stage1(db_path=db_path, batch_id=batch_id,
                    external_batch_id=external_batch_id, prepared=prepared)

    logger.info(
        "async batch %s submitted to Anthropic (%s): %d LLM requests, %d cache hits, %d skipped",
        batch_id, external_batch_id, len(needs_llm), len(cache_hits), len(skipped),
    )

    return {
        "batch_id": batch_id,
        "external_batch_id": external_batch_id,
        "created_at": created_at,
        "mode": "async",
        "input_count": len(deduped),
        "cache_hit_count": len(cache_hits),
        "status": "pending",
    }


def _mark_batch_failed(db_path: str, batch_id: str, reason: str) -> None:
    conn = get_connection(db_path)
    try:
        def _w(c: sqlite3.Connection) -> None:
            c.execute(
                "UPDATE batches SET status='failed', completed_at=?, error_reason=? WHERE batch_id=?",
                (utc_now_iso(), reason, batch_id),
            )
        with_immediate_tx(conn, _w)
    finally:
        conn.close()


def _persist_stage1(
    *,
    db_path: str,
    batch_id: str,
    external_batch_id: str | None,
    prepared: list[dict[str, Any]],
) -> None:
    """Write scrape snapshots + property rows + enrichment for the URLs in
    this batch, and stash the external id on the batches row. Also writes
    one row per input URL to `batch_url_hashes` so the poll path can
    reconstruct THIS batch's inputs without colliding with other concurrent
    batches that share URLs."""
    now_iso = utc_now_iso()
    conn = get_connection(db_path)
    try:
        def _w(c: sqlite3.Connection) -> None:
            if external_batch_id is not None:
                c.execute(
                    "UPDATE batches SET external_batch_id = ? WHERE batch_id = ?",
                    (external_batch_id, batch_id),
                )
            for position, p in enumerate(prepared):
                # Ensure every URL has a properties row so batch_url_hashes'
                # FK holds (even for scrape-failed URLs we didn't upsert
                # elsewhere). Minimal row is fine.
                _upsert_property_row(
                    c, url_hash=p["url_hash"],
                    canonical_url=p["canonical_url"],
                    address=p.get("address"),
                    zip_code=p.get("zip_code"),
                    last_price=(p.get("scrape") or {}).get("price"),
                    last_dom=(p.get("scrape") or {}).get("dom"),
                    now_iso=now_iso,
                )
                if p.get("scrape_ok"):
                    _insert_snapshot(
                        c, url_hash=p["url_hash"], now_iso=now_iso,
                        scrape=p.get("scrape") or {"ok": False},
                    )
                    if p.get("enrichment"):
                        _upsert_enrichment(
                            c, url_hash=p["url_hash"],
                            enrichment=p["enrichment"], now_iso=now_iso,
                        )
                # Record membership in THIS batch's input set. INSERT OR
                # IGNORE guards against dedup collisions if the caller ever
                # passes the same url_hash twice.
                c.execute(
                    """INSERT OR IGNORE INTO batch_url_hashes
                       (batch_id, url_hash, position)
                       VALUES (?, ?, ?)""",
                    (batch_id, p["url_hash"], position),
                )
        with_immediate_tx(conn, _w)
    finally:
        conn.close()


# --------------------------------------------------------------------------
# Poll
# --------------------------------------------------------------------------


def _load_batch_row(db_path: str, batch_id: str) -> dict[str, Any] | None:
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM batches WHERE batch_id = ?", (batch_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _load_batch_urls(db_path: str, batch_id: str) -> list[dict[str, Any]]:
    """Reconstruct the URL list for a pending batch from batch_url_hashes.

    We explicitly track batch membership in `batch_url_hashes` (one row per
    submitted URL), then join to the *latest* scrape_snapshot per url_hash
    so we get this batch's Stage-1 data. This replaces the previous
    `scraped_at >= batches.created_at` heuristic which cross-contaminated
    overlapping concurrent batches.
    """
    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            """SELECT b.url_hash,
                      b.position,
                      p.canonical_url,
                      p.address,
                      p.llm_analysis,
                      p.llm_analyzed_at,
                      (SELECT s.raw_json
                         FROM scrape_snapshots s
                        WHERE s.url_hash = b.url_hash
                        ORDER BY s.scraped_at DESC
                        LIMIT 1) AS raw_json
                 FROM batch_url_hashes b
                 JOIN properties p ON p.url_hash = b.url_hash
                WHERE b.batch_id = ?
                ORDER BY b.position ASC""",
            (batch_id,),
        ).fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            try:
                scrape = json.loads(r["raw_json"]) if r["raw_json"] else {"ok": False}
            except (TypeError, json.JSONDecodeError):
                scrape = {"ok": False}
            out.append({
                "url_hash": r["url_hash"],
                "canonical_url": r["canonical_url"],
                "address": r["address"],
                "scrape": scrape,
                "llm_analysis_raw": r["llm_analysis"],
                "llm_analyzed_at": r["llm_analyzed_at"],
            })
        return out
    finally:
        conn.close()


async def poll_async_batch(
    batch_id: str,
    *,
    db_path: str,
    api_key: str | None = None,
) -> dict[str, Any]:
    api_key = api_key or os.getenv("ANTHROPIC_API_KEY")
    batch_row = _load_batch_row(db_path, batch_id)
    if not batch_row:
        return {"batch_id": batch_id, "status": "unknown", "error_reason": "not_found"}

    status = batch_row["status"]
    external_batch_id = batch_row.get("external_batch_id")

    # Terminal states — return what we have.
    if status in ("complete", "failed"):
        return _build_poll_response(db_path, batch_row)

    # Mid-batch states.
    if not external_batch_id:
        # All-cache-hit short-circuit should have set status='complete'
        # already. If we see pending + no external id we treat as stalled
        # and leave pending for the next poll.
        return _build_poll_response(db_path, batch_row)

    async with httpx.AsyncClient(follow_redirects=True) as client:
        try:
            status_resp = await client.get(
                f"{ANTHROPIC_BATCHES_URL}/{external_batch_id}",
                headers={
                    "x-api-key": api_key or "",
                    "anthropic-version": "2023-06-01",
                },
                timeout=_PROVIDER_TIMEOUT_S,
            )
        except httpx.HTTPError:
            logger.exception("Polling provider failed for batch %s", batch_id)
            return _build_poll_response(db_path, batch_row)

        if status_resp.status_code >= 400:
            logger.warning(
                "Provider status returned HTTP %s for batch %s",
                status_resp.status_code, batch_id,
            )
            return _build_poll_response(db_path, batch_row)

        status_data = status_resp.json()
        provider_status = status_data.get("processing_status")

        if provider_status in ("in_progress", "canceling"):
            return _build_poll_response(db_path, batch_row, progress=status_data.get("request_counts"))

        if provider_status in ("expired", "canceled"):
            _mark_batch_failed(db_path, batch_id, f"provider_{provider_status}")
            refreshed = _load_batch_row(db_path, batch_id) or batch_row
            return _build_poll_response(db_path, refreshed)

        if provider_status != "ended":
            # Unknown state — leave pending, poll again later.
            return _build_poll_response(db_path, batch_row)

        # ended — fetch results. Stream + size-cap: a compromised or
        # misbehaving upstream could feed us an arbitrarily large JSONL
        # body. We truncate at _MAX_RESULTS_BYTES and log a warning.
        results_url = status_data.get("results_url") or f"{ANTHROPIC_BATCHES_URL}/{external_batch_id}/results"
        buf = bytearray()
        truncated = False
        try:
            async with client.stream(
                "GET",
                results_url,
                headers={
                    "x-api-key": api_key or "",
                    "anthropic-version": "2023-06-01",
                },
                timeout=_PROVIDER_TIMEOUT_S,
            ) as results_resp:
                if results_resp.status_code >= 400:
                    logger.warning(
                        "Provider results returned HTTP %s for batch %s",
                        results_resp.status_code, batch_id,
                    )
                    return _build_poll_response(db_path, batch_row)
                async for chunk in results_resp.aiter_bytes():
                    buf.extend(chunk)
                    if len(buf) > _MAX_RESULTS_BYTES:
                        logger.warning(
                            "Anthropic results body exceeded %d bytes for batch %s; truncating",
                            _MAX_RESULTS_BYTES, batch_id,
                        )
                        truncated = True
                        break
        except httpx.HTTPError:
            logger.exception("Fetching provider results failed for batch %s", batch_id)
            return _build_poll_response(db_path, batch_row)

        body_text = bytes(buf).decode("utf-8", errors="replace")

        stored_rows = _load_batch_urls(db_path, batch_id)
        prefix_map = {row["url_hash"][:58]: row["url_hash"] for row in stored_rows}
        per_url_analysis, any_failed = _parse_results_jsonl(body_text, prefix_map)
        if truncated:
            any_failed = True

    # Merge results with the prepared URL rows and finalize.
    finalized: list[dict[str, Any]] = []
    for row in stored_rows:
        uh = row["url_hash"]
        entry = per_url_analysis.get(uh)
        if entry is not None:
            analysis = entry["analysis"]
            tokens = entry["tokens"]
            cache_stale_reason = "async_fresh"
            ok = entry["ok"]
            analyzed_at = utc_now_iso()
        elif row.get("llm_analysis_raw"):
            # Fell back on cache (shouldn't normally happen for this batch's
            # URLs, but keeps the ranking complete).
            try:
                analysis = json.loads(row["llm_analysis_raw"])
            except (TypeError, json.JSONDecodeError):
                analysis = llm_mod.default_llm_analysis(failed=True)
            tokens = {"input": 0, "cached_input_read": 0, "output": 0}
            cache_stale_reason = None
            ok = True
            analyzed_at = row.get("llm_analyzed_at") or utc_now_iso()
        else:
            analysis = llm_mod.default_llm_analysis(failed=True)
            tokens = {"input": 0, "cached_input_read": 0, "output": 0}
            cache_stale_reason = "async_missing_result"
            ok = False
            analyzed_at = utc_now_iso()
            any_failed = True

        scrape = row["scrape"]
        zip_code = _extract_zip(scrape.get("address"))
        prepared = {
            "url": row["canonical_url"],
            "url_hash": uh,
            "canonical_url": row["canonical_url"],
            "scrape_ok": bool(scrape.get("ok")),
            "scrape_error": None if scrape.get("ok") else scrape.get("error"),
            "scrape": scrape,
            "zip_code": zip_code,
            "address": row["address"] or scrape.get("address"),
            "price": scrape.get("price"),
            "enrichment": None,
            "cache_hit": False,
            "cache_stale_reason": cache_stale_reason,
            "llm_analysis": analysis,
            "llm_tokens": tokens,
            "llm_ok": ok,
            "analyzed_at": analyzed_at,
        }
        finalized.append(await _finalize_row(prepared, db_path=db_path))

    ranked = ranking_mod.rank_batch(finalized)
    completed_at = utc_now_iso()
    _persist_batch_final(
        db_path=db_path,
        batch_id=batch_id,
        ranked_rows=ranked,
        status="complete",
        status_note="partial_failures" if any_failed else None,
        completed_at=completed_at,
        external_batch_id=external_batch_id,
        include_narrative=True,
    )
    refreshed = _load_batch_row(db_path, batch_id) or batch_row
    return _build_poll_response(db_path, refreshed, ranked_override=ranked)


def _parse_results_jsonl(
    text: str,
    prefix_map: dict[str, str],
) -> tuple[dict[str, dict[str, Any]], bool]:
    """Parse Message Batches results JSONL. Returns (by_url_hash, any_failed).

    `prefix_map` maps url_hash[:58] -> full url_hash so we can undo the
    custom_id truncation (Anthropic caps custom_id at 64 chars).
    """
    out: dict[str, dict[str, Any]] = {}
    any_failed = False
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        # Per-line cap — skip anything that looks pathologically large
        # before handing it to json.loads.
        if len(line) > _MAX_RESULTS_LINE_BYTES:
            logger.warning(
                "Skipping JSONL line exceeding %d bytes", _MAX_RESULTS_LINE_BYTES,
            )
            any_failed = True
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            any_failed = True
            continue
        custom_id = entry.get("custom_id") or ""
        if not custom_id.startswith("prop_"):
            continue
        short = custom_id[len("prop_"):]
        uh = prefix_map.get(short, short)
        result = entry.get("result") or {}
        rtype = result.get("type")
        if rtype != "succeeded":
            any_failed = True
            out[uh] = {
                "ok": False,
                "analysis": llm_mod.default_llm_analysis(failed=True),
                "tokens": {"input": 0, "cached_input_read": 0, "output": 0},
            }
            continue
        message = result.get("message") or {}
        usage = message.get("usage") or {}
        tokens = {
            "input": int(usage.get("input_tokens") or 0),
            "cached_input_read": int(usage.get("cache_read_input_tokens") or 0),
            "output": int(usage.get("output_tokens") or 0),
        }
        text_parts: list[str] = []
        for block in message.get("content") or []:
            if isinstance(block, dict) and block.get("type") == "text":
                text_parts.append(block.get("text", ""))
        parsed = llm_mod._extract_json_block("".join(text_parts))
        if not parsed:
            any_failed = True
            out[uh] = {
                "ok": False,
                "analysis": llm_mod.default_llm_analysis(failed=True),
                "tokens": tokens,
            }
            continue
        analysis = llm_mod._coerce_analysis(parsed)
        analysis["_failed"] = False
        out[uh] = {"ok": True, "analysis": analysis, "tokens": tokens}
    return out, any_failed


def _build_poll_response(
    db_path: str,
    batch_row: dict[str, Any],
    *,
    progress: dict[str, Any] | None = None,
    ranked_override: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    status = batch_row["status"]
    resp: dict[str, Any] = {
        "batch_id": batch_row["batch_id"],
        "external_batch_id": batch_row.get("external_batch_id"),
        "mode": batch_row["mode"],
        "status": status,
        "created_at": batch_row["created_at"],
        "completed_at": batch_row.get("completed_at"),
        "input_count": batch_row.get("input_count"),
    }
    if progress:
        resp["progress"] = progress
    if batch_row.get("error_reason"):
        resp["error_reason"] = batch_row["error_reason"]
    if status == "complete":
        if ranked_override is not None:
            resp["rankings"] = _build_response_rankings(ranked_override)
            # Sprint 10B-1: propagate failures the same way rankings go.
            resp["failures"] = build_failures_envelope(ranked_override)
        else:
            resp["rankings"] = _load_rankings_for_response(db_path, batch_row["batch_id"])
            resp["failures"] = _load_failures_for_response(db_path, batch_row["batch_id"])
    return resp


def _load_failures_for_response(db_path: str, batch_id: str) -> list[dict[str, Any]]:
    """Sprint 10B-1: rebuild the failures envelope for a completed async batch.

    Joins rankings (which is the authoritative per-batch membership list) with
    the latest scrape_snapshot for each url_hash so we can recover the error
    code. Ordering: submission position, so retry UX matches paste order.
    """
    from .pipeline import _human_readable_reason

    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            """SELECT p.canonical_url,
                      COALESCE(s.error_reason, 'unknown') AS error_reason,
                      COALESCE(s.scrape_ok, 1) AS scrape_ok,
                      bh.position AS position
                 FROM rankings r
                 JOIN properties p ON p.url_hash = r.url_hash
            LEFT JOIN batch_url_hashes bh
                   ON bh.batch_id = r.batch_id AND bh.url_hash = r.url_hash
            LEFT JOIN scrape_snapshots s
                   ON s.url_hash = r.url_hash
                  AND s.scraped_at = (
                      SELECT MAX(scraped_at)
                        FROM scrape_snapshots
                       WHERE url_hash = r.url_hash
                  )
                WHERE r.batch_id = ?
                  AND r.hard_fail = 1
                ORDER BY COALESCE(bh.position, r.rank) ASC""",
            (batch_id,),
        ).fetchall()
    finally:
        conn.close()
    out: list[dict[str, Any]] = []
    for r in rows:
        # Only count rows that truly never scraped — if scrape_ok is 1 the
        # row is a pure verdict failure (DTI blew up, etc.), which should
        # stay in rankings and NOT be surfaced as a retry row.
        if r["scrape_ok"]:
            continue
        code = r["error_reason"] or "unknown"
        out.append({
            "url": r["canonical_url"],
            "canonicalUrl": r["canonical_url"],
            "reason": _human_readable_reason(code),
            "errorCode": code,
        })
    return out


def _load_rankings_for_response(db_path: str, batch_id: str) -> list[dict[str, Any]]:
    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            """SELECT r.rank, r.url_hash, r.topsis_score, r.pareto_efficient,
                      r.verdict, r.hard_fail, r.reasons_json, r.criteria_json,
                      r.derived_metrics_json, r.claude_narrative,
                      p.canonical_url, p.address, p.zip_code,
                      p.last_price, p.llm_analysis,
                      p.cached_insurance_breakdown
                 FROM rankings r
                 JOIN properties p ON p.url_hash = r.url_hash
                WHERE r.batch_id = ?
                ORDER BY r.rank ASC""",
            (batch_id,),
        ).fetchall()
    finally:
        conn.close()
    out: list[dict[str, Any]] = []
    for r in rows:
        def _j(s: str | None) -> Any:
            if not s:
                return None
            try:
                return json.loads(s)
            except json.JSONDecodeError:
                return None
        out.append({
            "rank": int(r["rank"]),
            "url_hash": r["url_hash"],
            "canonical_url": r["canonical_url"],
            "address": r["address"],
            "zip_code": r["zip_code"],
            "price": r["last_price"],
            "topsis_score": float(r["topsis_score"] or 0.0),
            "pareto_efficient": bool(r["pareto_efficient"]),
            "verdict": r["verdict"],
            "hard_fail": bool(r["hard_fail"]),
            "reasons": _j(r["reasons_json"]) or [],
            "criteria": _j(r["criteria_json"]) or {},
            "derived_metrics": _j(r["derived_metrics_json"]) or {},
            "insurance_breakdown": _j(r["cached_insurance_breakdown"]) or {},
            "llm_analysis": _j(r["llm_analysis"]),
            "claude_narrative": r["claude_narrative"],
        })
    return out


# --------------------------------------------------------------------------
# Startup reconcile
# --------------------------------------------------------------------------


async def reconcile_pending_batches_on_startup(db_path: str) -> None:
    """Fire-and-forget: poll any async batches we left pending on last boot."""
    try:
        conn = get_connection(db_path)
        try:
            rows = conn.execute(
                "SELECT batch_id FROM batches WHERE mode='async' AND status='pending'"
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        logger.exception("reconcile: failed to read pending batches")
        return

    if not rows:
        return

    logger.info("reconcile: polling %d pending async batch(es) on startup", len(rows))
    for r in rows:
        bid = r["batch_id"]
        try:
            await poll_async_batch(bid, db_path=db_path)
        except Exception:
            logger.exception("reconcile: poll failed for batch %s", bid)
