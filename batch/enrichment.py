"""External data fetchers for property enrichment (BATCH_DESIGN.md §K).

Three endpoints + one geocoder fallback, all free and no-auth:
- FEMA NFHL ArcGIS MapServer/28 — flood zone
- Cal Fire FHSZ ArcGIS MapServer/0 — wildfire zone
- OSM Overpass — amenity counts / walkability
- US Census Geocoder — address → (lat, lng) fallback

Failure isolation: no single fetcher blocks ranking. On timeout / HTTP error /
network glitch each function returns `{ok: False, ...}` with the error string
captured. The pipeline records this in `property_enrichment.fetch_errors_json`
and the UI shows an "enrichment incomplete" chip.
"""
from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

# Cap external JSON payloads to avoid memory-exhaustion / JSON-bomb attacks
# from upstream services we don't control (FEMA, Cal Fire, Overpass).
MAX_EXTERNAL_JSON_BYTES = 10 * 1024 * 1024  # 10 MB


async def _safe_json(resp: httpx.Response, source: str) -> Any:
    """Read capped response body and parse JSON. Returns None on overflow/parse fail."""
    try:
        content = await resp.aread()
    except httpx.HTTPError:
        return None
    if len(content) > MAX_EXTERNAL_JSON_BYTES:
        logger.warning(
            "external API response too large (%s): %d bytes", source, len(content)
        )
        return None
    try:
        return json.loads(content)
    except (json.JSONDecodeError, ValueError):
        return None

logger = logging.getLogger(__name__)

FEMA_URL = (
    "https://hazards.fema.gov/gis/nfhl/rest/services/public/NFHL/"
    "MapServer/28/query"
)
CALFIRE_URL = (
    "https://services.gis.ca.gov/arcgis/rest/services/Environment/"
    "Fire_Severity_Zones/MapServer/0/query"
)
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
CENSUS_GEOCODER_URL = (
    "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress"
)

PER_CALL_TIMEOUT_S = 5.0
OVERPASS_TIMEOUT_S = 8.0
TOTAL_ENRICHMENT_BUDGET_S = 8.0

HIGH_FLOOD = {"A", "AE", "AH", "AO", "VE", "V"}
_LAST_OVERPASS_CALL_AT: float = 0.0  # process-wide cooldown (2s per §K.3)
_OVERPASS_COOLDOWN_S = 2.0
_OVERPASS_LOCK = asyncio.Lock()

# Sprint 8-2 — coordinate-bucket cache.
#
# Bucket lat/lng to 3 decimals (~100m cell): two listings in the same ~100m
# square share one Overpass fetch. Cache TTL is generous (30d) because
# walkability shifts on the scale of new transit lines, not weekly.
# In-flight dedupe via a futures map keeps concurrent callers for the same
# bucket from firing duplicate requests — matches the pattern in
# `batch/rent_comps.py`.
OVERPASS_CACHE_TTL_DAYS = 30
_OVERPASS_BUCKET_DECIMALS = 3
_OVERPASS_INFLIGHT: dict[tuple[float, float], asyncio.Future] = {}
_OVERPASS_INFLIGHT_LOCK = asyncio.Lock()


def _default_overpass_db_path() -> str:
    """Resolve the default SQLite DB path without importing app.py.

    Mirrors scripts/init_db.DEFAULT_DB_PATH so this module can be used
    standalone (tests) without paying an import cost on app.py.
    """
    return str(Path(__file__).resolve().parent.parent / "data" / "analyzer.db")


def _bucket_coords(lat: float, lng: float) -> tuple[float, float]:
    """Round a lat/lng to the cache bucket grid (~100m at 3 decimals)."""
    return (
        round(float(lat), _OVERPASS_BUCKET_DECIMALS),
        round(float(lng), _OVERPASS_BUCKET_DECIMALS),
    )


def _overpass_cache_read(db_path: str, lat_b: float, lng_b: float) -> dict | None:
    """Return ``{payload, fetched_at}`` if a fresh row exists, else None."""
    try:
        conn = sqlite3.connect(db_path, timeout=10.0)
    except sqlite3.Error:
        return None
    try:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            """SELECT payload_json, fetched_at
               FROM overpass_cache
               WHERE lat_bucket = ? AND lng_bucket = ?""",
            (lat_b, lng_b),
        )
        row = cur.fetchone()
    except sqlite3.Error:
        return None
    finally:
        conn.close()
    if not row:
        return None
    fetched_at = row["fetched_at"]
    try:
        s = fetched_at[:-1] + "+00:00" if fetched_at.endswith("Z") else fetched_at
        fetched = datetime.fromisoformat(s)
        if fetched.tzinfo is None:
            fetched = fetched.replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    if datetime.now(timezone.utc) - fetched > timedelta(days=OVERPASS_CACHE_TTL_DAYS):
        return None
    try:
        payload = json.loads(row["payload_json"])
    except (TypeError, json.JSONDecodeError):
        return None
    return {"payload": payload, "fetched_at": fetched_at}


def _overpass_cache_write(
    db_path: str, lat_b: float, lng_b: float, payload: dict
) -> None:
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        conn = sqlite3.connect(db_path, timeout=10.0)
    except sqlite3.Error:
        return
    try:
        conn.execute(
            """INSERT INTO overpass_cache
               (lat_bucket, lng_bucket, payload_json, fetched_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(lat_bucket, lng_bucket) DO UPDATE SET
                   payload_json = excluded.payload_json,
                   fetched_at   = excluded.fetched_at""",
            (lat_b, lng_b, json.dumps(payload), now_iso),
        )
        conn.commit()
    except sqlite3.Error as exc:  # pragma: no cover - defensive
        logger.warning("overpass_cache persist failed for %s,%s: %s",
                       lat_b, lng_b, exc)
    finally:
        conn.close()


# --------------------------------------------------------------------------
# Geocoding
# --------------------------------------------------------------------------


async def geocode_census(
    client: httpx.AsyncClient, address: str
) -> dict[str, Any]:
    """US Census One-Line Geocoder (§K.4). Returns {ok, lat, lng, source, error}."""
    if not address:
        return {"ok": False, "error": "empty_address"}
    params = {
        "address": address,
        "benchmark": "Public_AR_Current",
        "format": "json",
    }
    try:
        resp = await client.get(
            CENSUS_GEOCODER_URL, params=params, timeout=PER_CALL_TIMEOUT_S
        )
        resp.raise_for_status()
        payload = await _safe_json(resp, "census")
        if payload is None:
            return {"ok": False, "error": "parse:oversized_or_invalid"}
        matches = (
            payload.get("result", {}).get("addressMatches") or []
        )
        if not matches:
            return {"ok": False, "error": "no_match"}
        coords = matches[0].get("coordinates", {})
        lng = coords.get("x")
        lat = coords.get("y")
        if lat is None or lng is None:
            return {"ok": False, "error": "missing_coords"}
        return {
            "ok": True,
            "lat": float(lat),
            "lng": float(lng),
            "source": "census",
        }
    except httpx.HTTPError as exc:
        return {"ok": False, "error": f"http:{type(exc).__name__}"}
    except (ValueError, KeyError) as exc:
        return {"ok": False, "error": f"parse:{type(exc).__name__}"}


# --------------------------------------------------------------------------
# FEMA flood zone
# --------------------------------------------------------------------------


def _map_flood_risk(fld_zone: str, zone_subty: str | None) -> str:
    code = (fld_zone or "").upper().strip()
    if not code:
        return "unknown"
    if code in HIGH_FLOOD:
        return "high"
    if code == "X":
        subty = (zone_subty or "").upper()
        if "0.2 PCT" in subty:
            return "moderate"
        return "low"
    return "unknown"


async def fetch_fema(
    client: httpx.AsyncClient, lat: float, lng: float
) -> dict[str, Any]:
    params = {
        "geometry": f"{lng},{lat}",
        "geometryType": "esriGeometryPoint",
        "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": "FLD_ZONE,ZONE_SUBTY",
        "returnGeometry": "false",
        "f": "json",
    }
    try:
        resp = await client.get(FEMA_URL, params=params, timeout=PER_CALL_TIMEOUT_S)
        resp.raise_for_status()
        payload = await _safe_json(resp, "fema")
        if payload is None:
            return {"ok": False, "error": "parse:oversized_or_invalid"}
        features = payload.get("features") or []
        if not features:
            return {
                "ok": True,
                "flood_zone": "X",
                "flood_zone_risk": "low",
            }
        attrs = features[0].get("attributes", {}) or {}
        code = (attrs.get("FLD_ZONE") or "").strip()
        subty = attrs.get("ZONE_SUBTY")
        return {
            "ok": True,
            "flood_zone": code or None,
            "flood_zone_risk": _map_flood_risk(code, subty),
        }
    except httpx.HTTPError as exc:
        return {"ok": False, "error": f"http:{type(exc).__name__}"}
    except (ValueError, KeyError) as exc:
        return {"ok": False, "error": f"parse:{type(exc).__name__}"}


# --------------------------------------------------------------------------
# Cal Fire FHSZ
# --------------------------------------------------------------------------


def _map_fire_risk(haz_class: str) -> str:
    hc = (haz_class or "").lower()
    if hc == "very high":
        return "high"
    if hc == "high":
        return "moderate"
    if hc == "moderate":
        return "low"
    return "unknown"


async def fetch_calfire(
    client: httpx.AsyncClient, lat: float, lng: float
) -> dict[str, Any]:
    params = {
        "geometry": f"{lng},{lat}",
        "geometryType": "esriGeometryPoint",
        "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": "HAZ_CLASS,SRA",
        "returnGeometry": "false",
        "f": "json",
    }
    try:
        resp = await client.get(CALFIRE_URL, params=params, timeout=PER_CALL_TIMEOUT_S)
        resp.raise_for_status()
        payload = await _safe_json(resp, "calfire")
        if payload is None:
            return {"ok": False, "error": "parse:oversized_or_invalid"}
        features = payload.get("features") or []
        if not features:
            return {
                "ok": True,
                "fire_zone": "none",
                "fire_zone_risk": "low",
            }
        attrs = features[0].get("attributes", {}) or {}
        sra = (attrs.get("SRA") or "LRA").strip()
        haz = (attrs.get("HAZ_CLASS") or "").strip()
        label = f"{sra}-{haz.lower().replace(' ', '-')}" if haz else "none"
        return {
            "ok": True,
            "fire_zone": label,
            "fire_zone_risk": _map_fire_risk(haz),
        }
    except httpx.HTTPError as exc:
        return {"ok": False, "error": f"http:{type(exc).__name__}"}
    except (ValueError, KeyError) as exc:
        return {"ok": False, "error": f"parse:{type(exc).__name__}"}


# --------------------------------------------------------------------------
# OSM Overpass
# --------------------------------------------------------------------------


OVERPASS_QUERY = """[out:json][timeout:25];
(
  node["shop"="supermarket"](around:1609,{lat},{lng});
  node["shop"="convenience"](around:1609,{lat},{lng});
  node["amenity"~"^(school|kindergarten)$"](around:1609,{lat},{lng});
  node["amenity"~"^(restaurant|cafe)$"](around:1609,{lat},{lng});
  node["highway"="bus_stop"](around:800,{lat},{lng});
  node["railway"~"^(station|halt|tram_stop)$"](around:1609,{lat},{lng});
  node["leisure"~"^(park|playground)$"](around:1609,{lat},{lng});
);
out tags;
"""


def _derive_walkability(counts: dict[str, int]) -> int:
    value = (
        counts.get("groceriesWithin1Mile", 0) * 10
        + counts.get("schoolsWithin1Mile", 0) * 5
        + counts.get("transitStopsWithin0.5Mile", 0) * 8
        + counts.get("restaurantsWithin1Mile", 0) * 2
        + counts.get("parksWithin1Mile", 0) * 3
    )
    return max(0, min(100, int(value)))


def _tally_overpass_elements(elements: list[dict]) -> dict[str, int]:
    """Reduce raw Overpass elements into our amenity-count shape."""
    counts = {
        "groceriesWithin1Mile": 0,
        "schoolsWithin1Mile": 0,
        "restaurantsWithin1Mile": 0,
        "transitStopsWithin0.5Mile": 0,
        "trainStationsWithin1Mile": 0,
        "parksWithin1Mile": 0,
    }
    for element in elements or []:
        tags = element.get("tags") or {}
        shop = tags.get("shop")
        amenity = tags.get("amenity")
        highway = tags.get("highway")
        railway = tags.get("railway")
        leisure = tags.get("leisure")
        if shop in ("supermarket", "convenience"):
            counts["groceriesWithin1Mile"] += 1
        elif amenity in ("school", "kindergarten"):
            counts["schoolsWithin1Mile"] += 1
        elif amenity in ("restaurant", "cafe"):
            counts["restaurantsWithin1Mile"] += 1
        elif highway == "bus_stop":
            counts["transitStopsWithin0.5Mile"] += 1
        elif railway in ("station", "halt", "tram_stop"):
            counts["trainStationsWithin1Mile"] += 1
        elif leisure in ("park", "playground"):
            counts["parksWithin1Mile"] += 1
    return counts


async def _fetch_overpass_remote(
    client: httpx.AsyncClient, lat: float, lng: float
) -> dict[str, Any]:
    """Actually hit Overpass. Respects the 2s fair-use cooldown (§K.3)."""
    global _LAST_OVERPASS_CALL_AT
    async with _OVERPASS_LOCK:
        now = time.monotonic()
        wait = _OVERPASS_COOLDOWN_S - (now - _LAST_OVERPASS_CALL_AT)
        if wait > 0:
            await asyncio.sleep(min(wait, _OVERPASS_COOLDOWN_S))
        _LAST_OVERPASS_CALL_AT = time.monotonic()

    body = OVERPASS_QUERY.format(lat=lat, lng=lng)
    try:
        resp = await client.post(
            OVERPASS_URL,
            content=body,
            headers={"Content-Type": "text/plain"},
            timeout=OVERPASS_TIMEOUT_S,
        )
        resp.raise_for_status()
        payload = await _safe_json(resp, "overpass")
        if payload is None:
            return {"ok": False, "error": "parse:oversized_or_invalid"}
    except httpx.HTTPError as exc:
        return {"ok": False, "error": f"http:{type(exc).__name__}"}
    except ValueError as exc:
        return {"ok": False, "error": f"parse:{type(exc).__name__}"}

    return {"ok": True, "raw": payload}


async def fetch_overpass(
    client: httpx.AsyncClient,
    lat: float,
    lng: float,
    *,
    db_path: str | None = None,
) -> dict[str, Any]:
    """Fetch amenity counts + walkability, cache-aware.

    Sprint 8-2: bucket lat/lng to ~100m, read from ``overpass_cache``, and
    on miss coalesce concurrent callers for the same bucket via an
    in-flight futures map so 5 URLs in the same block trigger ONE Overpass
    request. The 2s cooldown still applies to actual network fetches; cache
    hits bypass it entirely.
    """
    lat_b, lng_b = _bucket_coords(lat, lng)
    db_path = db_path or _default_overpass_db_path()

    cached = _overpass_cache_read(db_path, lat_b, lng_b)
    if cached is not None:
        counts = cached["payload"].get("amenity_counts") or _tally_overpass_elements(
            cached["payload"].get("elements") or []
        )
        return {
            "ok": True,
            "amenity_counts": counts,
            "walkability_index": _derive_walkability(counts),
        }

    key = (lat_b, lng_b)
    async with _OVERPASS_INFLIGHT_LOCK:
        fut = _OVERPASS_INFLIGHT.get(key)
        is_owner = fut is None
        if is_owner:
            fut = asyncio.get_running_loop().create_future()
            _OVERPASS_INFLIGHT[key] = fut

    if not is_owner:
        try:
            return await fut  # type: ignore[return-value]
        except Exception:
            return {"ok": False, "error": "inflight_owner_failed"}

    try:
        raw_result = await _fetch_overpass_remote(client, lat, lng)
        if not raw_result.get("ok"):
            fut.set_result(raw_result)
            return raw_result

        payload = raw_result["raw"]
        counts = _tally_overpass_elements(payload.get("elements") or [])
        result = {
            "ok": True,
            "amenity_counts": counts,
            "walkability_index": _derive_walkability(counts),
        }
        # Persist a slim shape (counts only) so future cache hits skip the
        # element-tally pass entirely. Ignore persist errors — the caller
        # still gets the live result.
        _overpass_cache_write(
            db_path, lat_b, lng_b,
            {"amenity_counts": counts},
        )
        fut.set_result(result)
        return result
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("overpass: owner path crashed for %s,%s: %s",
                       lat_b, lng_b, exc)
        result = {"ok": False, "error": f"owner_crash:{type(exc).__name__}"}
        if not fut.done():
            fut.set_result(result)
        return result
    finally:
        async with _OVERPASS_INFLIGHT_LOCK:
            _OVERPASS_INFLIGHT.pop(key, None)


# --------------------------------------------------------------------------
# Top-level enrich coordinator
# --------------------------------------------------------------------------


async def enrich_property(
    *,
    client: httpx.AsyncClient,
    lat: float | None,
    lng: float | None,
    address: str | None,
    db_path: str | None = None,
) -> dict[str, Any]:
    """Run geocoding (if needed) + FEMA + Cal Fire + Overpass in parallel.

    Returns a dict shaped like the `property_enrichment` row minus url_hash.
    Fields:
        lat, lng, geocode_source,
        flood_zone, flood_zone_risk,
        fire_zone, fire_zone_risk,
        amenity_counts, walkability_index,
        fetch_errors (dict), enrichment_missing (bool)
    """
    errors: dict[str, str] = {}
    geocode_source = "scrape"

    if lat is None or lng is None:
        geo = await geocode_census(client, address or "")
        if geo.get("ok"):
            lat = geo["lat"]
            lng = geo["lng"]
            geocode_source = geo["source"]
        else:
            errors["geocode"] = geo.get("error", "unknown")
            geocode_source = None

    result: dict[str, Any] = {
        "lat": lat,
        "lng": lng,
        "geocode_source": geocode_source,
        "flood_zone": None,
        "flood_zone_risk": "unknown",
        "fire_zone": None,
        "fire_zone_risk": "unknown",
        "amenity_counts": None,
        "walkability_index": None,
    }

    if lat is None or lng is None:
        for key in ("fema", "calfire", "overpass"):
            errors.setdefault(key, "no_coords")
        result["fetch_errors"] = errors
        result["enrichment_missing"] = True
        return result

    fema_task = asyncio.create_task(fetch_fema(client, lat, lng))
    fire_task = asyncio.create_task(fetch_calfire(client, lat, lng))
    overpass_task = asyncio.create_task(fetch_overpass(client, lat, lng, db_path=db_path))

    try:
        results = await asyncio.wait_for(
            asyncio.gather(fema_task, fire_task, overpass_task, return_exceptions=True),
            timeout=TOTAL_ENRICHMENT_BUDGET_S,
        )
    except asyncio.TimeoutError:
        for t, key in ((fema_task, "fema"), (fire_task, "calfire"), (overpass_task, "overpass")):
            if not t.done():
                t.cancel()
                errors[key] = "timeout"
        results = [
            fema_task.result() if fema_task.done() and not fema_task.cancelled() else {"ok": False, "error": "timeout"},
            fire_task.result() if fire_task.done() and not fire_task.cancelled() else {"ok": False, "error": "timeout"},
            overpass_task.result() if overpass_task.done() and not overpass_task.cancelled() else {"ok": False, "error": "timeout"},
        ]

    fema_res, fire_res, overpass_res = results

    if isinstance(fema_res, dict) and fema_res.get("ok"):
        result["flood_zone"] = fema_res.get("flood_zone")
        result["flood_zone_risk"] = fema_res.get("flood_zone_risk") or "unknown"
    else:
        errors["fema"] = (fema_res or {}).get("error", "unknown") if isinstance(fema_res, dict) else str(fema_res)

    if isinstance(fire_res, dict) and fire_res.get("ok"):
        result["fire_zone"] = fire_res.get("fire_zone")
        result["fire_zone_risk"] = fire_res.get("fire_zone_risk") or "unknown"
    else:
        errors["calfire"] = (fire_res or {}).get("error", "unknown") if isinstance(fire_res, dict) else str(fire_res)

    if isinstance(overpass_res, dict) and overpass_res.get("ok"):
        result["amenity_counts"] = overpass_res.get("amenity_counts")
        result["walkability_index"] = overpass_res.get("walkability_index")
    else:
        errors["overpass"] = (overpass_res or {}).get("error", "unknown") if isinstance(overpass_res, dict) else str(overpass_res)

    result["fetch_errors"] = errors
    result["enrichment_missing"] = bool(
        errors.get("fema") or errors.get("calfire")
    )
    return result
