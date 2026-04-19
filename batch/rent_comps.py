"""Rent-comp cache lookup + Redfin fallback fetch for the batch pipelines.

BATCH_DESIGN §A.1 (rent_comps_cache) / §F.1 (rent_per_unit).

Public entrypoint: ``get_rent_estimate(zip_code, beds, baths, db_path, ...)``.

Strategy:
    1. Look up a row in ``rent_comps_cache`` keyed by (zip, beds, baths). If
       fresh (within ``cache_ttl_hours``), return its median immediately.
    2. On miss/stale, call ``app._search_redfin_rentals`` (Playwright). The
       existing ``_search_semaphore`` inside that function already caps
       concurrent browsers. We additionally deduplicate concurrent callers
       for the same (zip, beds, baths) triple via an in-flight future map,
       so a batch with 10 URLs in the same ZIP only hits Redfin once.
    3. Compute median from >= 2 valid rents. Persist the payload to
       ``rent_comps_cache`` (UPSERT). Return.
    4. On any failure (timeout, error, insufficient comps) return
       ``{"median_rent": None, "source": "fallback"}`` — the caller layers
       the ``TIER_DEFAULT_RENT_2BR`` fallback on top so the batch never
       dies because of a comp miss.
"""
from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from .db import get_connection, utc_now_iso

logger = logging.getLogger(__name__)

# Hard timeout on a single Redfin fetch. The underlying Playwright call can
# legitimately take ~2-5s; 15s gives headroom on a cold browser boot without
# letting a stuck page stall the whole batch.
_REDFIN_TIMEOUT_S = 15.0

# Module-level in-flight map: dedupe concurrent callers for the same triple.
_INFLIGHT: dict[tuple[str, int, float], asyncio.Future] = {}
_INFLIGHT_LOCK = asyncio.Lock()


# --------------------------------------------------------------------------
# Public helpers
# --------------------------------------------------------------------------


def compute_median_rent(rentals: list[dict]) -> int | None:
    """Median of the non-null, positive ``rent`` values in a rentals list.

    Returns ``None`` when fewer than 2 valid comps are available — a single
    outlier listing is not enough signal to override the tier default.
    """
    rents: list[float] = []
    for r in rentals or []:
        v = r.get("rent") if isinstance(r, dict) else None
        if isinstance(v, (int, float)) and v > 0:
            rents.append(float(v))
    if len(rents) < 2:
        return None
    rents.sort()
    mid = len(rents) // 2
    if len(rents) % 2:
        return int(rents[mid])
    return int((rents[mid - 1] + rents[mid]) / 2)


def _is_cache_fresh(fetched_at_iso: str, ttl_hours: int) -> bool:
    """True iff the cached row is within ``ttl_hours`` of now (UTC)."""
    if not fetched_at_iso:
        return False
    try:
        # Accept both '...Z' and offset-aware ISO strings. ``fromisoformat``
        # in py3.11+ handles 'Z'; for safety strip it first.
        s = fetched_at_iso[:-1] + "+00:00" if fetched_at_iso.endswith("Z") else fetched_at_iso
        fetched = datetime.fromisoformat(s)
        if fetched.tzinfo is None:
            fetched = fetched.replace(tzinfo=timezone.utc)
    except ValueError:
        return False
    age = datetime.now(timezone.utc) - fetched
    return age <= timedelta(hours=ttl_hours)


# --------------------------------------------------------------------------
# DB read / write
# --------------------------------------------------------------------------


def _read_cached(db_path: str, zip_code: str, beds: int, baths: float) -> dict | None:
    conn = get_connection(db_path)
    try:
        cur = conn.execute(
            """SELECT payload_json, fetched_at
               FROM rent_comps_cache
               WHERE zip_code = ? AND beds = ? AND baths = ?""",
            (zip_code, beds, baths),
        )
        row = cur.fetchone()
        if not row:
            return None
        return {"payload_json": row["payload_json"], "fetched_at": row["fetched_at"]}
    finally:
        conn.close()


def _persist_cached(
    db_path: str,
    zip_code: str,
    beds: int,
    baths: float,
    payload: dict,
    fetched_at: str,
) -> None:
    conn = get_connection(db_path)
    try:
        conn.execute(
            """INSERT INTO rent_comps_cache (zip_code, beds, baths, payload_json, fetched_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(zip_code, beds, baths) DO UPDATE SET
                   payload_json = excluded.payload_json,
                   fetched_at   = excluded.fetched_at""",
            (zip_code, beds, baths, json.dumps(payload), fetched_at),
        )
    except sqlite3.Error as exc:  # pragma: no cover - defensive
        logger.warning("rent_comps_cache persist failed for %s/%d/%s: %s",
                       zip_code, beds, baths, exc)
    finally:
        conn.close()


# --------------------------------------------------------------------------
# Redfin fetch (with timeout + error swallow)
# --------------------------------------------------------------------------


async def _fetch_from_redfin(zip_code: str, beds: int) -> dict | None:
    """Call the existing app-level Redfin scraper. Returns the raw payload
    dict on success, ``None`` on timeout or any error. Never raises."""
    try:
        # Late import to keep this module decoupled from FastAPI startup.
        import app as main_app  # type: ignore
    except Exception as exc:  # pragma: no cover - import guard
        logger.warning("rent_comps: could not import app for Redfin fetch: %s", exc)
        return None

    try:
        result = await asyncio.wait_for(
            main_app._search_redfin_rentals(zip_code, beds),
            timeout=_REDFIN_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        logger.warning("rent_comps: Redfin fetch timed out for zip=%s beds=%d", zip_code, beds)
        return None
    except Exception as exc:
        logger.warning("rent_comps: Redfin fetch errored for zip=%s beds=%d: %s",
                       zip_code, beds, exc)
        return None

    if not isinstance(result, dict) or result.get("error"):
        return None
    return result


# --------------------------------------------------------------------------
# Main entry
# --------------------------------------------------------------------------


async def get_rent_estimate(
    zip_code: str,
    beds: int,
    baths: float,
    *,
    db_path: str,
    cache_ttl_hours: int = 24,
) -> dict[str, Any]:
    """Return median per-unit rent for (zip, beds, baths).

    Contract:
        {
            "median_rent": int | None,   # dollars/mo; None => caller falls back
            "sample_size": int,          # number of rentals in the payload
            "source": "cache" | "live" | "fallback",
            "fetched_at": str,           # ISO-8601 UTC; now() on fallback
        }

    The function never raises — any Redfin error returns
    ``source="fallback"`` with ``median_rent=None``.
    """
    zip_code = (zip_code or "").strip()
    # Normalize keys so (3, 1.0) and (3, 1) land on the same cache row.
    beds_key = int(beds or 0)
    baths_key = float(baths or 0.0)

    # Guard: a valid cache key needs a 5-digit ZIP. Without one, Redfin can't
    # resolve a location and we can't key the cache row. Short-circuit to
    # fallback so the batch still runs with the tier default.
    if not zip_code or beds_key < 1:
        return {
            "median_rent": None,
            "sample_size": 0,
            "source": "fallback",
            "fetched_at": utc_now_iso(),
        }

    # 1) Cache check.
    cached = _read_cached(db_path, zip_code, beds_key, baths_key)
    if cached and _is_cache_fresh(cached["fetched_at"], cache_ttl_hours):
        try:
            payload = json.loads(cached["payload_json"])
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict):
            rentals = payload.get("rentals") or []
            median = compute_median_rent(rentals)
            if median is not None:
                return {
                    "median_rent": median,
                    "sample_size": len(rentals),
                    "source": "cache",
                    "fetched_at": cached["fetched_at"],
                }
        # Stale/corrupt payload — fall through and re-fetch.

    # 2) In-flight dedupe. Share one fetch per (zip, beds, baths).
    key = (zip_code, beds_key, baths_key)
    async with _INFLIGHT_LOCK:
        fut = _INFLIGHT.get(key)
        is_owner = fut is None
        if is_owner:
            fut = asyncio.get_running_loop().create_future()
            _INFLIGHT[key] = fut

    if not is_owner:
        try:
            return await fut  # type: ignore[return-value]
        except Exception:
            # If the owner crashed, synthesize a fallback so the caller
            # still gets a tier-default rent.
            return {
                "median_rent": None,
                "sample_size": 0,
                "source": "fallback",
                "fetched_at": utc_now_iso(),
            }

    # 3) Owner path — do the actual fetch.
    try:
        payload = await _fetch_from_redfin(zip_code, beds_key)
        now_iso = utc_now_iso()

        if not payload:
            result = {
                "median_rent": None,
                "sample_size": 0,
                "source": "fallback",
                "fetched_at": now_iso,
            }
            fut.set_result(result)
            return result

        rentals = payload.get("rentals") or []
        median = compute_median_rent(rentals)
        if median is None:
            # Insufficient comps — still cache the (empty) payload so we
            # don't hammer Redfin on every retry within the TTL window.
            _persist_cached(db_path, zip_code, beds_key, baths_key, payload, now_iso)
            result = {
                "median_rent": None,
                "sample_size": len(rentals),
                "source": "fallback",
                "fetched_at": now_iso,
            }
            fut.set_result(result)
            return result

        _persist_cached(db_path, zip_code, beds_key, baths_key, payload, now_iso)
        result = {
            "median_rent": median,
            "sample_size": len(rentals),
            "source": "live",
            "fetched_at": now_iso,
        }
        fut.set_result(result)
        return result
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("rent_comps: owner path crashed for %s/%d/%s: %s",
                       zip_code, beds_key, baths_key, exc)
        result = {
            "median_rent": None,
            "sample_size": 0,
            "source": "fallback",
            "fetched_at": utc_now_iso(),
        }
        if not fut.done():
            fut.set_result(result)
        return result
    finally:
        async with _INFLIGHT_LOCK:
            _INFLIGHT.pop(key, None)


# --------------------------------------------------------------------------
# Per-unit derivation helper (used by both pipelines)
# --------------------------------------------------------------------------


def derive_per_unit_profile(
    total_beds: int | None,
    total_baths: float | None,
    units: int | None,
) -> tuple[int, float]:
    """Split listing-level beds/baths into a per-unit profile.

    Examples:
        (6 beds, 3 baths, 3 units) -> (2 beds, 1.0 baths) per unit
        (4 beds, 2 baths, 2 units) -> (2 beds, 1.0 baths) per unit
        (3 beds, 2 baths, None)    -> (3 beds, 2.0 baths)  (SFR-ish)
    """
    tb = int(total_beds or 0)
    # Baths come in 0.5 increments from the scrape.
    tbath = float(total_baths or 0.0)
    u = int(units) if units and units > 0 else 0

    if u > 0:
        # Half-up rounding for beds (int(x + 0.5)) — Python 3's built-in
        # round() uses banker's rounding, so round(2.5) == 2 would land
        # a duplex 5BR/2u on 2BR per-unit when 3BR is the honest match.
        per_beds = max(1, int((tb / u) + 0.5))
        per_baths_raw = tbath / u
        per_baths = max(1.0, int((per_baths_raw * 2) + 0.5) / 2)
    else:
        per_beds = max(1, tb)
        per_baths = max(1.0, tbath)
    return per_beds, per_baths
