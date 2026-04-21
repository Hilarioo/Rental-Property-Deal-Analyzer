"""End-to-end per-URL orchestration for the batch endpoint (§N).

Workflow per URL:
    1. url_hash = sha256(normalize(url))
    2. Read cached `properties` + `property_enrichment` rows
    3. Scrape fresh (always) → insert scrape_snapshot row
    4. cache staleness check (§L.1)
    5. if stale or new: geocode, enrichment (FEMA/CalFire/Overpass), LLM extract
    6. compute insurance heuristic, criteria matrix, Jose verdict
    7. UPSERT properties with analysis + insurance

Batch-wide:
    8. Pareto + TOPSIS across non-hard-fail rows
    9. Write `batches` + `rankings` rows inside BEGIN IMMEDIATE
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sqlite3
from typing import Any
from urllib.parse import urlparse

import httpx

from . import enrichment as enrichment_mod
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
from .verdict import classify_zip_tier, compute_jose_verdict

# ADR-002 / Sprint 7B-3: DEFAULTS reads from spec/constants.json.
# Callers use legacy key names (interestRatePct, termYears, baselineInsuranceAnnual)
# — we build a view over spec.defaults with those aliases so no call site breaks.
# Any new caller should prefer the spec key names (interestRate, loanTerm, insuranceAnnual).
from spec import constants as _spec

logger = logging.getLogger(__name__)

_SPEC_DEFAULTS = _spec.defaults
DEFAULTS: dict[str, Any] = {
    # Direct spec reads (names match exactly).
    "buyerMonthlyIncomeW2": _SPEC_DEFAULTS["buyerMonthlyIncomeW2"],
    "downPaymentPct": _SPEC_DEFAULTS["downPaymentPct"],
    "fhaUpfrontMipPct": _SPEC_DEFAULTS["fhaUpfrontMipPct"],
    "fhaAnnualMipPct": _SPEC_DEFAULTS["fhaAnnualMipPct"],
    "propertyTaxRatePct": _SPEC_DEFAULTS["propertyTaxRatePct"],
    "vacancyPct": float(_SPEC_DEFAULTS["vacancyPct"]),
    "maintenancePct": float(_SPEC_DEFAULTS["maintenancePct"]),
    "closingCostsPct": float(_SPEC_DEFAULTS["closingCostsPct"]),
    "rentalOffsetPct": float(_SPEC_DEFAULTS["rentalOffsetPct"]),
    "maxCashToClose": _SPEC_DEFAULTS["maxCashToClose"],
    # Aliases — keep legacy names that callers in this module use.
    "interestRatePct": _SPEC_DEFAULTS["interestRate"],
    "termYears": _SPEC_DEFAULTS["loanTerm"],
    "baselineInsuranceAnnual": _SPEC_DEFAULTS["insuranceAnnual"],
}

# Rehab self-perform multipliers — single source of truth in spec.rehabCategories.
# Built once at import; any spec change takes effect on next process boot.
_REHAB_SELF_PERFORM_MULT: dict[str, float] = {
    cat["key"]: float(cat["selfPerformMultiplier"])
    for cat in _spec.rehab_categories
}

# Sprint 12-4: self-management config. When `units >= trigger` AND the batch
# path can't know the user's intent (no UI), assume he pays a PM and inject
# `fallbackPct` into opex. UI path (index.html) respects an explicit 0 as
# "I'm self-managing."
_SELF_MANAGEMENT: dict[str, Any] = (_spec.profile_raw or {}).get("selfManagement") or {}


def _auto_pm_pct(units: int) -> float:
    """Return auto-injected PM % given unit count, or 0.0 if not triggered."""
    trigger = _SELF_MANAGEMENT.get("propertyManagementTriggerUnits")
    fallback = _SELF_MANAGEMENT.get("propertyManagementFallbackPct")
    if not isinstance(trigger, (int, float)) or not isinstance(fallback, (int, float)):
        return 0.0
    if units >= int(trigger):
        return float(fallback)
    return 0.0


# Sprint 12-6: 203(k) contractor-stretch scenario. Jose's profile has a
# `contractorStretch` block (enabled, maxRehab, selfPerformMinPct) declaring
# he's willing to do heavy rehab if financed via FHA 203(k) AND his self-
# perform share is high enough to actually pull off the work. Without this
# code, the config silently did nothing and heavy-rehab deals simply
# RED-failed on the `rehabRed` threshold.
_CONTRACTOR_STRETCH: dict[str, Any] = (
    (_spec.profile_raw or {}).get("contractorStretch") or {}
)


def _stretch_self_perform_share(retail: float, effective: float) -> float:
    """Fraction of retail rehab Jose plans to self-perform, [0, 1]."""
    if retail <= 0:
        return 0.0
    return max(0.0, min(1.0, (retail - effective) / retail))


def _compute_stretch_scenario(
    *,
    price: float,
    effective_rehab: float,
    retail_rehab: float,
    annual_taxes: float,
    annual_ins: float,
    rental_offset: float,
    rehab_red_threshold: float,
) -> dict[str, Any] | None:
    """Sprint 12-6: compute the 203(k) scenario numbers when preconditions
    hold. Returns None when the stretch path isn't viable (config disabled,
    rehab under the red threshold so no stretch needed, or self-perform
    share too low).

    203(k) math vs. cash-funded:
      - Loan principal *includes* the rehab → higher PITI.
      - Cash-to-close drops by the rehab amount (rehab is no longer out-of-pocket).
      - FHA MIP still applies to the full financed balance.
    """
    if not _CONTRACTOR_STRETCH.get("enabled"):
        return None
    max_rehab = _CONTRACTOR_STRETCH.get("maxRehab")
    min_self_perform_pct = _CONTRACTOR_STRETCH.get("selfPerformMinPct")
    if not isinstance(max_rehab, (int, float)) or not isinstance(min_self_perform_pct, (int, float)):
        return None
    # Only relevant when the deal would otherwise RED-fail on rehab. Below
    # rehab_red, the base scenario is fine and 203(k) only adds complexity.
    if effective_rehab <= rehab_red_threshold:
        return None
    # Respect Jose's absolute ceiling.
    if effective_rehab > float(max_rehab):
        return {
            "viable": False,
            "block_reason": f"effectiveRehab ${int(round(effective_rehab)):,} exceeds contractorStretch.maxRehab ${int(max_rehab):,}",
        }
    share = _stretch_self_perform_share(retail_rehab, effective_rehab)
    if share * 100 < float(min_self_perform_pct):
        return {
            "viable": False,
            "block_reason": (
                f"self-perform share {share * 100:.0f}% below contractorStretch."
                f"selfPerformMinPct {int(min_self_perform_pct)}%"
            ),
            "self_perform_share": round(share, 3),
        }

    # 203(k) loan math — rehab financed, upfront MIP on the full base.
    down_pct = DEFAULTS["downPaymentPct"] / 100.0
    base_loan = price * (1 - down_pct) + effective_rehab
    upfront_mip = base_loan * (DEFAULTS["fhaUpfrontMipPct"] / 100.0)
    financed = base_loan + upfront_mip
    pi = _monthly_pi(financed, DEFAULTS["interestRatePct"], DEFAULTS["termYears"])
    mip_monthly = base_loan * (DEFAULTS["fhaAnnualMipPct"] / 100.0) / 12
    piti_stretch = pi + annual_taxes / 12 + annual_ins / 12 + mip_monthly
    net_piti_stretch = max(0.0, piti_stretch - rental_offset)

    # Cash-to-close under 203(k) — rehab is financed, not out of pocket.
    down_payment = price * down_pct
    closing_costs = price * (DEFAULTS["closingCostsPct"] / 100.0)
    cash_to_close_stretch = down_payment + closing_costs

    return {
        "viable": True,
        "loan_type": "FHA-203k",
        "base_loan": round(base_loan, 2),
        "financed_loan": round(financed, 2),
        "piti": round(piti_stretch, 2),
        "net_piti": round(net_piti_stretch, 2),
        "cash_to_close": round(cash_to_close_stretch, 2),
        "effective_rehab": round(effective_rehab, 2),
        "retail_rehab": round(retail_rehab, 2),
        "self_perform_share": round(share, 3),
    }


# Sprint 12-5: per-ZIP preset overrides. Each preset carries its own
# tax/insurance/vacancy numbers (e.g. Vallejo 1.25% vs Richmond 1.35%).
# When a listing's ZIP matches a preset's search.zips, use that preset's
# defaults for PITI math so bundling multiple cities in one batch doesn't
# silently use a single city's rates for all of them.
_PRESETS: dict[str, Any] = _spec.presets or {}


def _preset_defaults_for_zip(zip_code: str | None) -> dict[str, Any]:
    """Return {propertyTaxRatePct, insuranceAnnual, vacancyPct, preset_name}.
    Falls back to module-level DEFAULTS when no preset matches the ZIP.
    """
    out = {
        "propertyTaxRatePct": DEFAULTS["propertyTaxRatePct"],
        "insuranceAnnual": DEFAULTS["baselineInsuranceAnnual"],
        "vacancyPct": DEFAULTS["vacancyPct"],
        "preset_name": None,
    }
    if not zip_code:
        return out
    z = str(zip_code).strip()[:5]
    if not z:
        return out
    for name, preset in _PRESETS.items():
        search = (preset or {}).get("search") or {}
        zips = search.get("zips") or []
        if z not in zips:
            continue
        pdef = (preset or {}).get("defaults") or {}
        if isinstance(pdef.get("propertyTaxRatePct"), (int, float)):
            out["propertyTaxRatePct"] = float(pdef["propertyTaxRatePct"])
        if isinstance(pdef.get("insuranceAnnual"), (int, float)):
            out["insuranceAnnual"] = float(pdef["insuranceAnnual"])
        if isinstance(pdef.get("vacancyPct"), (int, float)):
            out["vacancyPct"] = float(pdef["vacancyPct"])
        out["preset_name"] = name
        return out
    return out

# Rough per-unit market rent by Tier (USER_PROFILE §9). Used only when rent
# comps are unavailable.
TIER_DEFAULT_RENT_2BR = {
    "tier1": 2100,
    "tier2": 2300,
    "tier3": 2150,
    "outside": 2000,
}

# Sprint 8-4: skip the live scrape if the property was fetched within this
# window. Tight threshold (15m) is the "user pasted the same batch twice in
# a row" optimization — long enough to avoid round-trip duplication in a
# single analysis session, short enough that price/DOM changes propagate
# within the hour. The 30-day LLM cache staleness check (§L.1) is unchanged.
_WARM_SCRAPE_SKIP_MINUTES = 15


# --------------------------------------------------------------------------
# Math helpers (mirror calc.js)
# --------------------------------------------------------------------------


def _monthly_pi(loan: float, annual_rate_pct: float, term_years: int) -> float:
    if loan <= 0 or term_years <= 0:
        return 0.0
    n = term_years * 12
    r = annual_rate_pct / 100 / 12
    if r == 0:
        return loan / n
    return loan * (r * (1 + r) ** n) / ((1 + r) ** n - 1)


def _fha_loan(price: float, down_pct: float) -> dict[str, float]:
    base = price * (1 - down_pct / 100)
    upfront_mip = base * 0.0175
    return {
        "base": base,
        "upfront_mip": upfront_mip,
        "financed": base + upfront_mip,
    }


def _effective_rehab(rehab_band: dict[str, Any]) -> tuple[float, float]:
    """Return (effective_rehab, contractor_edge_savings).

    Multipliers from spec.rehabCategories, not inline. Keys that aren't listed
    in the spec fall back to 1.0x (no self-perform edge).
    """
    retail = 0.0
    effective = 0.0
    for cat, band in (rehab_band or {}).items():
        mid = float((band or {}).get("mid") or 0.0)
        retail += mid
        mult = _REHAB_SELF_PERFORM_MULT.get(cat, 1.0)
        effective += mid * mult
    return effective, max(0.0, retail - effective)


def _coerce_narrative(value: Any) -> str | None:
    """Normalize `narrativeForRanking` for DB binding.

    The LLM schema declares this a string but older in-flight batches (and
    the occasional malformed cache row) can hold a dict/list — sqlite3
    rejects those at bind with ``type 'dict' is not supported``, crashing
    the rankings INSERT. str/None pass through; dict/list become a JSON
    dump; anything else becomes ``str(value)``. Shared by pipeline.py and
    async_pipeline.py so both write paths serialize identically.
    """
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, (dict, list)):
        try:
            return json.dumps(value, separators=(",", ":"))
        except Exception:
            return str(value)
    return str(value)


def _extract_zip(address: str | None) -> str | None:
    if not address:
        return None
    m = re.search(r"\b(\d{5})(?:-\d{4})?\b", address)
    return m.group(1) if m else None


_EXCLUDED_CITY_NEEDLES: tuple[str, ...] = tuple(
    c.lower() for c in _spec.zip_tiers.get("excludedCities") or []
)


# Sprint 10A §10-8: broader bot-wall / CAPTCHA detection.
# Previous two-substring check ("captcha", "access denied") false-negatived on
# Cloudflare's "Just a moment...", PerimeterX "px-captcha", hCaptcha, Akamai
# "Reference #..." blocks, and Redfin's own "for real-time pricing" lockout.
# When any sentinel matches we treat the scrape as a hard failure rather than
# parsing the challenge page as if it were the listing.
_BOT_WALL_SENTINELS: tuple[str, ...] = (
    "captcha",
    "access denied",
    "access to this page has been denied",
    "just a moment",
    "enable javascript",
    "verify you are human",
    "px-captcha",
    "hcaptcha",
    "challenge",
    "are you a robot",
    "for real-time pricing",
)


def _looks_like_bot_wall(html_text: str | None) -> bool:
    """Return True if the first ~3KB of ``html_text`` trips any bot-wall
    sentinel. Case-insensitive substring match, cheap enough to run on every
    scrape. Returning True means: treat this fetch as failed, not parse it.
    """
    if not html_text:
        return False
    window = html_text[:3000].lower()
    return any(s in window for s in _BOT_WALL_SENTINELS)


def _looks_excluded(address: str | None) -> bool:
    """Cheap substring check against spec.zipTiers.excludedCities (§7).

    Case-insensitive substring match, loaded once from spec at import time.
    """
    if not address:
        return False
    a = address.lower()
    for needle in _EXCLUDED_CITY_NEEDLES:
        if needle and needle in a:
            return True
    return False


# --------------------------------------------------------------------------
# Scrape wrapper (delegates to existing app._fetch_and_parse_redfin path)
# --------------------------------------------------------------------------


def _reuse_warm_snapshot(
    *,
    db_path: str,
    url_hash: str,
    now_utc,
) -> dict[str, Any] | None:
    """Sprint 8-4: return a reconstructed scrape dict if the last snapshot
    for this url_hash is within ``_WARM_SCRAPE_SKIP_MINUTES`` AND was
    successful. Returns ``None`` otherwise (caller scrapes live).

    The returned dict matches ``_scrape_url`` output so downstream code is
    unchanged. We read the most recent successful snapshot via
    ``idx_snapshots_urlhash_time`` (LIMIT 1).
    """
    from datetime import datetime, timedelta, timezone

    conn = get_connection(db_path)
    try:
        # Read freshness from the SNAPSHOT row we're about to reuse, not from
        # properties.last_scraped_at. Those diverge when a recent scrape
        # failed: last_scraped_at advances to the failed attempt, while the
        # latest successful snapshot may be older — which would make the
        # 15-min gate reuse stale data. Code Review Sprint 8 finding.
        cur = conn.execute(
            """SELECT scrape_snapshots.scraped_at AS snapshot_at,
                      scrape_snapshots.raw_json
               FROM scrape_snapshots
               WHERE scrape_snapshots.url_hash = ?
                 AND scrape_snapshots.scrape_ok = 1
               ORDER BY scrape_snapshots.scraped_at DESC
               LIMIT 1""",
            (url_hash,),
        )
        row = cur.fetchone()
    finally:
        conn.close()

    if not row or not row["snapshot_at"] or not row["raw_json"]:
        return None

    last_iso = row["snapshot_at"]
    try:
        s = last_iso[:-1] + "+00:00" if last_iso.endswith("Z") else last_iso
        last = datetime.fromisoformat(s)
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
    except ValueError:
        return None

    if now_utc - last > timedelta(minutes=_WARM_SCRAPE_SKIP_MINUTES):
        return None

    try:
        reused = json.loads(row["raw_json"])
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(reused, dict) or not reused.get("ok"):
        return None
    return reused


async def _scrape_url(url: str) -> dict[str, Any]:
    """Call the existing app-internal scrape path and normalize the result.

    Re-uses the same httpx + playwright fallback as `/api/scrape`. Returns
    a dict that always contains `ok: bool` and (when ok) the extracted fields.
    """
    import app as main_app
    from bs4 import BeautifulSoup

    HEADERS = main_app.HEADERS

    parsed = urlparse(url)
    source = main_app._detect_source(parsed.hostname)
    if source == "unknown":
        return {"ok": False, "error": "unsupported_url"}
    if source == "zillow" and not re.search(r"/homedetails/|/zpid_|/homes/", parsed.path or ""):
        return {"ok": False, "error": "invalid_zillow_path"}
    if source == "redfin" and not re.search(r"/home/\d+", parsed.path or ""):
        return {"ok": False, "error": "invalid_redfin_path"}

    html_text: str | None = None
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=20) as client:
            resp = await client.get(url, headers=HEADERS)
        # Sprint 10A §10-8: broader sentinel check than the old 2-string test.
        if resp.status_code < 400 and not _looks_like_bot_wall(resp.text):
            html_text = resp.text
    except httpx.HTTPError:
        pass

    if html_text is None:
        try:
            html_text = await main_app._fetch_with_playwright(url)
            if html_text and _looks_like_bot_wall(html_text):
                html_text = None
        except Exception:
            pass

    if not html_text:
        return {"ok": False, "error": "bot_wall_or_fetch_failed"}

    try:
        soup = BeautifulSoup(html_text, "lxml")
    except Exception:
        return {"ok": False, "error": "parse_failed"}

    extract = None
    if source == "redfin":
        extract = main_app._extract_redfin(soup)
    else:
        extract = (
            main_app._extract_from_next_data(soup)
            or main_app._extract_from_ld_json(soup)
            or main_app._extract_from_dom(soup)
        )

    if not extract:
        return {"ok": False, "error": "extract_failed"}

    # Best-effort lat/lng scan.
    lat = None
    lng = None
    for m in re.finditer(r'"latitude"\s*:\s*([\-0-9\.]+)', html_text):
        try:
            lat = float(m.group(1))
            break
        except ValueError:
            continue
    for m in re.finditer(r'"longitude"\s*:\s*([\-0-9\.]+)', html_text):
        try:
            lng = float(m.group(1))
            break
        except ValueError:
            continue

    dom = None
    for m in re.finditer(r'"daysOnMarket"\s*:\s*(\d+)', html_text):
        try:
            dom = int(m.group(1))
            break
        except ValueError:
            continue

    units = None
    units_source: str | None = None
    for m in re.finditer(r'"numberOfUnits"[^0-9]*(\d+)', html_text):
        try:
            units = int(m.group(1))
            units_source = "json_numberOfUnits"
            break
        except ValueError:
            continue

    # Multifamily hints from description + propertyType (2+ units).
    if not units:
        haystack = (extract.get("description") or "").lower() + " " + (extract.get("propertyType") or "").lower()
        if "duplex" in haystack or "2 unit" in haystack or "two unit" in haystack:
            units = 2
            units_source = "keyword_duplex"
        elif "triplex" in haystack or "3 unit" in haystack:
            units = 3
            units_source = "keyword_triplex"
        elif "fourplex" in haystack or "four" in haystack and "unit" in haystack:
            units = 4
            units_source = "keyword_fourplex"

    # Sprint 12 hotfix 2026-04-19: single-unit inference from address suffix
    # and property-type when the scraper couldn't find `numberOfUnits`.
    # Examples of URLs this fixes:
    #   https://www.zillow.com/homedetails/401-Stinson-St-APT-3-Vallejo-...
    #   .../123-Foo-St-UNIT-5-...
    #   .../123-Foo-St-#12-...
    # Before this, the tool hard-failed with "Unit count not detected — re-
    # scrape or enter manually" on valid condo listings. Now we surface the
    # clearer "single unit — no 75% rental offset" RED reason.
    if not units:
        addr = extract.get("address") or ""
        if re.search(r"\b(apt|apartment|unit|suite|ste)\s*[#]?\s*\w+\b", addr, re.IGNORECASE):
            units = 1
            units_source = "address_suffix"
        elif re.search(r"#\s*\w+", addr):
            units = 1
            units_source = "address_hash_suffix"
        else:
            # Also detect from the URL path itself (common Zillow slug shape
            # like ".../APT-3-..." when address parsing was incomplete).
            url_lower = url.lower()
            if re.search(r"/[^/]*\b(apt|unit|suite)\b[-_]\w+", url_lower):
                units = 1
                units_source = "url_slug"

    # Property-type based fallback — single-family / condo / townhouse all
    # imply units = 1 for FHA owner-occupied math.
    if not units:
        pt_lower = (extract.get("propertyType") or "").lower()
        if any(sig in pt_lower for sig in ("condo", "townhouse", "townhome", "single family", "single-family", "sfr", "sfh")):
            units = 1
            units_source = "property_type"

    normalized = {
        "ok": True,
        "source": source,
        "address": extract.get("address"),
        "price": _as_int(extract.get("price")),
        "beds": _as_int(extract.get("beds")),
        "baths": _as_float(extract.get("baths")),
        "sqft": _as_int(extract.get("sqft")),
        "year_built": _as_int(extract.get("yearBuilt")),
        "units": units,
        "units_source": units_source,   # Sprint 12 hotfix: for verdict copy.
        "property_type_raw": extract.get("propertyType"),  # condo / TOWNHOUSE / etc.
        "dom": dom,
        "description": extract.get("description"),
        "image_url": extract.get("imageUrl"),
        "lat": lat,
        "lng": lng,
    }
    return normalized


def _as_int(v: Any) -> int | None:
    try:
        if v is None or v == "":
            return None
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _as_float(v: Any) -> float | None:
    try:
        if v is None or v == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------
# Metrics computation
# --------------------------------------------------------------------------


def compute_property_metrics(
    *,
    price: int | None,
    units: int | None,
    year_built: int | None,
    beds: int | None,
    baths: float | None,
    dom: int | None,
    zip_code: str | None,
    address: str | None,
    llm_analysis: dict[str, Any],
    enrichment_row: dict[str, Any] | None,
    insurance_breakdown: dict[str, Any],
    rent_per_unit: float,
    hard_fail_units_unknown: bool = False,
    property_type_raw: str | None = None,  # Sprint 12 hotfix 2026-04-19.
    units_source: str | None = None,
) -> dict[str, Any]:
    """Compute the full set of metrics the ranker and verdict need."""
    p = float(price or 0)
    # When units is None, assume duplex (2) for numeric math but signal the
    # unknown-units hard-fail via `hard_fail_units_unknown`.
    u = int(units) if units else 2
    zip_tier = classify_zip_tier(zip_code)

    # Sprint 12-5: resolve per-ZIP preset overrides. Global DEFAULTS still
    # used for rates/term/MIP (buyer-level, not market-level).
    preset_override = _preset_defaults_for_zip(zip_code)

    fha = _fha_loan(p, DEFAULTS["downPaymentPct"])
    pi = _monthly_pi(fha["financed"], DEFAULTS["interestRatePct"], DEFAULTS["termYears"])
    annual_taxes = p * (preset_override["propertyTaxRatePct"] / 100)
    annual_ins = float(insurance_breakdown.get("annual_usd") or preset_override["insuranceAnnual"])
    mip_monthly = fha["base"] * (DEFAULTS["fhaAnnualMipPct"] / 100) / 12
    piti = pi + annual_taxes / 12 + annual_ins / 12 + mip_monthly

    # Per-unit rents (owner occupies unit 0).
    rent_per_unit = float(rent_per_unit or 0.0)
    rented_units = max(0, u - 1) if u > 1 else 0
    gross_rent_monthly_all = rent_per_unit * u
    gross_rent_monthly_rented = rent_per_unit * rented_units
    rental_offset = gross_rent_monthly_rented * (DEFAULTS["rentalOffsetPct"] / 100)
    net_piti = max(0.0, piti - rental_offset)

    qualifying_income = DEFAULTS["buyerMonthlyIncomeW2"] + rental_offset
    dti_headroom = max(0.0, qualifying_income * 0.50 - piti)

    down_payment = p * (DEFAULTS["downPaymentPct"] / 100)
    closing_costs = p * (DEFAULTS["closingCostsPct"] / 100)

    effective_rehab, contractor_edge = _effective_rehab(llm_analysis.get("rehabBand") or {})
    retail_rehab = effective_rehab + contractor_edge  # Sprint 12-6: needed for stretch share.
    cash_to_close = down_payment + closing_costs
    all_in_cost = p + closing_costs + effective_rehab

    # Opex monthly (ex-PITI). Sprint 12-4: auto-inject property management %
    # when units >= profile.selfManagement.propertyManagementTriggerUnits.
    # Batch path has no UI so we assume-pay; single-URL UI can override.
    maint = gross_rent_monthly_all * (DEFAULTS["maintenancePct"] / 100)
    vac = gross_rent_monthly_all * (preset_override["vacancyPct"] / 100)
    pm_pct = _auto_pm_pct(u)
    pm = gross_rent_monthly_all * (pm_pct / 100)
    opex_monthly = maint + vac + pm  # taxes/insurance already in PITI

    annual_cf = 12 * (gross_rent_monthly_all - opex_monthly) - 12 * piti
    coc_pct = (annual_cf / cash_to_close * 100) if cash_to_close > 0 else 0.0
    noi_annual = 12 * gross_rent_monthly_all - 12 * opex_monthly
    cap_rate = (noi_annual / p * 100) if p > 0 else 0.0

    arv = p  # V1: no Zestimate scrape.
    brrrr_eq = ranking_mod.brrrr_equity_capture(arv, all_in_cost)
    npv = ranking_mod.npv_5yr(
        purchase=p,
        gross_rent_monthly=gross_rent_monthly_all,
        piti_monthly=piti,
        opex_monthly=opex_monthly,
        loan_amount=fha["financed"],
        rate_pct=DEFAULTS["interestRatePct"],
        term_years=DEFAULTS["termYears"],
    )

    roof_age = None
    roof = llm_analysis.get("roofAgeYears") or {}
    if isinstance(roof, dict) and isinstance(roof.get("value"), (int, float)):
        roof_age = float(roof["value"])
    if roof_age is None:
        roof_age = 10.0  # spec default when unknown (§C.1 row 12)

    # Hard-fail flags — combine LLM risk flags + ZIP exclusions.
    risk_flags = llm_analysis.get("riskFlags") or {}
    has_flat_roof = bool(risk_flags.get("flatRoof", {}).get("present"))
    has_unpermitted_adu = bool(risk_flags.get("unpermittedAdu", {}).get("present"))
    gal = risk_flags.get("galvanizedPlumbing", {}).get("present")
    knob = risk_flags.get("knobAndTubeElectrical", {}).get("present")
    is_pre1978_gal = bool(gal and knob and (year_built or 9999) < 1978)
    # Sprint 14.5: expose the LLM evidence strings so the verdict reason
    # can surface the basis for the inference (user toggle governs whether
    # this fires as RED or YELLOW).
    gal_evidence = (risk_flags.get("galvanizedPlumbing", {}) or {}).get("evidence") or ""
    knob_evidence = (risk_flags.get("knobAndTubeElectrical", {}) or {}).get("evidence") or ""
    is_excluded = zip_tier == "excluded" or _looks_excluded(address)

    # Sprint 12-2: plumb lat/lng/address into the verdict context so the
    # geospatial predicate in compute_jose_verdict can apply maxMilesHard
    # and conditionalCities rules. When enrichment is missing (single-URL
    # path, geocoder blackhole), both stay None and the geospatial check
    # no-ops.
    enrichment_lat = enrichment_row.get("lat") if enrichment_row else None
    enrichment_lng = enrichment_row.get("lng") if enrichment_row else None

    # Sprint 12-6: compute the 203(k) contractor-stretch scenario alongside
    # the cash-funded numbers. Only emits a payload when rehab would red-fail
    # the cash-funded scenario AND profile.contractorStretch gates pass.
    rehab_red_threshold = (_spec.jose or {}).get("rehabRed") or 0
    stretch_scenario = _compute_stretch_scenario(
        price=p,
        effective_rehab=effective_rehab,
        retail_rehab=retail_rehab,
        annual_taxes=annual_taxes,
        annual_ins=annual_ins,
        rental_offset=rental_offset,
        rehab_red_threshold=float(rehab_red_threshold),
    )

    verdict_ctx = {
        "zip": zip_code,
        "zipTier": zip_tier if zip_tier != "excluded" else "outside",
        "isExcludedByZipTier": is_excluded,
        "hasFlatRoof": has_flat_roof,
        "hasUnpermittedAdu": has_unpermitted_adu,
        "isPre1978WithGalvanized": is_pre1978_gal,
        "galvanizedEvidence": gal_evidence,
        "knobAndTubeEvidence": knob_evidence,
        "propertyType": "multi" if u > 1 else "sfh",
        "propertyTypeRaw": property_type_raw,
        "unitsSource": units_source,
        "units": u,
        "price": p,
        "netPiti": net_piti,
        "piti": piti,
        "qualifyingIncome": qualifying_income,
        "cashToClose": cash_to_close,
        "effectiveRehab": effective_rehab,
        "roofAgeYears": roof_age,
        "hardFailUnitsUnknown": bool(hard_fail_units_unknown),
        "lat": enrichment_lat,
        "lng": enrichment_lng,
        "address": address,
        "stretchScenario": stretch_scenario,  # Sprint 12-6: 203(k) parallel path.
    }
    verdict_result = compute_jose_verdict(verdict_ctx)

    # Sprint 12-2: geospatial fail feeds the hard_fail list so batch
    # short-circuits commute-radius + conditional-city violations the same
    # way it short-circuits excluded ZIPs.
    from .verdict import _geospatial_fail as _geo_fail_fn  # noqa: PLC0415
    geospatial_hard_fail = _geo_fail_fn(verdict_ctx) is not None

    # Hard fail = verdict is red due to hard-fail gates (excluded/flat/adu/pre78/dti/geo).
    # Sprint 14.5: pre-78-gal-and-K&T is now behind `jose.enforceOldHouseGates`
    # (default false). When off, the combo shows as YELLOW and does not
    # short-circuit TOPSIS ranking — the row remains eligible for batch
    # rank alongside cleaner listings.
    enforce_old_gates = bool((_spec.jose or {}).get("enforceOldHouseGates"))
    hard_fail_reasons = [
        is_excluded, has_flat_roof, has_unpermitted_adu,
        (is_pre1978_gal and enforce_old_gates),
        u <= 1,  # SFR without legal ADU
        (qualifying_income > 0 and (piti / qualifying_income) > 0.55),
        bool(hard_fail_units_unknown),
        geospatial_hard_fail,
    ]
    hard_fail = any(hard_fail_reasons)

    metrics = {
        "piti": round(piti, 2),
        "net_piti": round(net_piti, 2),
        "cash_to_close": round(cash_to_close, 2),
        "effective_rehab": round(effective_rehab, 2),
        "contractor_edge": round(contractor_edge, 2),
        "dti_headroom": round(dti_headroom, 2),
        "coc_pct": round(coc_pct, 2),
        "cap_rate": round(cap_rate, 2),
        "npv_5yr": round(npv, 2),
        "brrrr_equity_capture": round(brrrr_eq, 4),
        "zip_tier": zip_tier,
        "dom": int(dom or 0),
        "roof_age": roof_age,
        "auto_pm_pct": round(pm_pct, 2),  # Sprint 12-4: 0 when self-managing.
        "matched_preset": preset_override["preset_name"],  # Sprint 12-5.
        "applied_tax_pct": round(preset_override["propertyTaxRatePct"], 4),
        "price_vs_zip_median": 0.0,  # §C.3 derived metric; 0 until we have history
        "qualifying_income": round(qualifying_income, 2),
        "gross_rent_monthly_all": round(gross_rent_monthly_all, 2),
        "stretch_scenario": stretch_scenario,  # Sprint 12-6: 203(k) parallel.
    }

    return {
        "metrics": metrics,
        "verdict": verdict_result["verdict"],
        "verdict_reasons": verdict_result["reasons"],
        "hard_fail": hard_fail,
        "verdict_ctx": verdict_ctx,
    }


# --------------------------------------------------------------------------
# DB helpers
# --------------------------------------------------------------------------


def _read_property(conn: sqlite3.Connection, url_hash: str) -> dict[str, Any] | None:
    cur = conn.execute(
        "SELECT * FROM properties WHERE url_hash = ?", (url_hash,)
    )
    row = cur.fetchone()
    return dict(row) if row else None


def _read_enrichment(conn: sqlite3.Connection, url_hash: str) -> dict[str, Any] | None:
    cur = conn.execute(
        "SELECT * FROM property_enrichment WHERE url_hash = ?", (url_hash,)
    )
    row = cur.fetchone()
    return dict(row) if row else None


def _upsert_property_row(
    conn: sqlite3.Connection,
    *,
    url_hash: str,
    canonical_url: str,
    address: str | None,
    zip_code: str | None,
    last_price: int | None,
    last_dom: int | None,
    now_iso: str,
) -> None:
    existing = _read_property(conn, url_hash)
    if existing:
        conn.execute(
            """UPDATE properties
               SET last_scraped_at = ?, scrape_count = scrape_count + 1,
                   last_price = ?, last_dom = ?, address = COALESCE(?, address),
                   zip_code = COALESCE(?, zip_code)
               WHERE url_hash = ?""",
            (now_iso, last_price, last_dom, address, zip_code, url_hash),
        )
    else:
        conn.execute(
            """INSERT INTO properties
               (url_hash, canonical_url, address, zip_code, first_seen_at,
                last_scraped_at, scrape_count, last_price, last_dom)
               VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)""",
            (url_hash, canonical_url, address, zip_code, now_iso, now_iso,
             last_price, last_dom),
        )


def _insert_snapshot(
    conn: sqlite3.Connection,
    *,
    url_hash: str,
    now_iso: str,
    scrape: dict[str, Any],
) -> None:
    conn.execute(
        """INSERT INTO scrape_snapshots
           (url_hash, scraped_at, price, beds, baths, sqft, year_built, units,
            dom, description, image_url, raw_json, scrape_ok, error_reason)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            url_hash, now_iso,
            scrape.get("price"), scrape.get("beds"), scrape.get("baths"),
            scrape.get("sqft"), scrape.get("year_built"), scrape.get("units"),
            scrape.get("dom"), scrape.get("description"), scrape.get("image_url"),
            json.dumps(scrape),
            1 if scrape.get("ok") else 0,
            scrape.get("error") if not scrape.get("ok") else None,
        ),
    )


def _upsert_enrichment(
    conn: sqlite3.Connection,
    *,
    url_hash: str,
    enrichment: dict[str, Any],
    now_iso: str,
) -> None:
    conn.execute(
        """INSERT INTO property_enrichment
           (url_hash, lat, lng, geocode_source, flood_zone, flood_zone_risk,
            fire_zone, fire_zone_risk, amenity_counts, walkability_index,
            enriched_at, fetch_errors_json)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(url_hash) DO UPDATE SET
             lat = excluded.lat, lng = excluded.lng,
             geocode_source = excluded.geocode_source,
             flood_zone = excluded.flood_zone,
             flood_zone_risk = excluded.flood_zone_risk,
             fire_zone = excluded.fire_zone,
             fire_zone_risk = excluded.fire_zone_risk,
             amenity_counts = excluded.amenity_counts,
             walkability_index = excluded.walkability_index,
             enriched_at = excluded.enriched_at,
             fetch_errors_json = excluded.fetch_errors_json
        """,
        (
            url_hash,
            enrichment.get("lat"), enrichment.get("lng"),
            enrichment.get("geocode_source"),
            enrichment.get("flood_zone"), enrichment.get("flood_zone_risk"),
            enrichment.get("fire_zone"), enrichment.get("fire_zone_risk"),
            json.dumps(enrichment.get("amenity_counts")) if enrichment.get("amenity_counts") is not None else None,
            enrichment.get("walkability_index"),
            now_iso,
            json.dumps(enrichment.get("fetch_errors")) if enrichment.get("fetch_errors") else None,
        ),
    )


def _update_analysis_cache(
    conn: sqlite3.Connection,
    *,
    url_hash: str,
    llm_analysis: dict[str, Any],
    llm_tokens: dict[str, int],
    insurance_breakdown: dict[str, Any],
    cache_stale_reason: str | None,
    analyzed_at: str | None,
) -> None:
    conn.execute(
        """UPDATE properties
           SET llm_analysis = ?, llm_analyzed_at = ?, llm_model = ?,
               llm_input_tokens = ?, llm_cached_input_tokens = ?,
               llm_output_tokens = ?,
               cached_insurance = ?, cached_insurance_breakdown = ?,
               cache_stale_reason = ?
           WHERE url_hash = ?""",
        (
            json.dumps(llm_analysis) if llm_analysis else None,
            analyzed_at,
            llm_mod.LLM_MODEL,
            llm_tokens.get("input"),
            llm_tokens.get("cached_input_read"),
            llm_tokens.get("output"),
            insurance_breakdown.get("annual_usd"),
            json.dumps(insurance_breakdown),
            cache_stale_reason,
            url_hash,
        ),
    )


def _insert_claude_run(
    conn: sqlite3.Connection,
    *,
    batch_id: str,
    url_hash: str,
    tokens: dict[str, int],
    status: str,
    error_reason: str | None,
    now_iso: str,
) -> None:
    conn.execute(
        """INSERT INTO claude_runs
           (run_id, batch_id, url_hash, mode, prompt_cache_hit,
            input_tokens, cached_input_tokens, output_tokens, cost_usd,
            created_at, completed_at, status, error_reason)
           VALUES (?, ?, ?, 'sync', ?, ?, ?, ?, NULL, ?, ?, ?, ?)""",
        (
            new_uuid_hex(),
            batch_id,
            url_hash,
            1 if tokens.get("cached_input_read") else 0,
            tokens.get("input"), tokens.get("cached_input_read"), tokens.get("output"),
            now_iso, now_iso, status, error_reason,
        ),
    )


# --------------------------------------------------------------------------
# Main entry: run_sync_batch
# --------------------------------------------------------------------------


async def process_url(
    *,
    url: str,
    http_client: httpx.AsyncClient,
    api_key: str | None,
    db_path: str,
    client_ip: str | None = None,
) -> dict[str, Any]:
    """Process one URL fully. Returns a dict the caller stores in rankings."""
    now_iso = utc_now_iso()
    uh = compute_url_hash(url)

    # Read cached state (quick read, no lock).
    conn = get_connection(db_path)
    try:
        cached = _read_property(conn, uh)
        cached_enrichment = _read_enrichment(conn, uh)
    finally:
        conn.close()

    # Per-scrape rate-limit charge. SEPARATE bucket from /api/scrape
    # (Sprint 12 hotfix 2026-04-19): the /api/scrape 5/min bucket is sized
    # for humans pasting one URL at a time; a 126-URL scan tripped it on
    # request #6 and skipped everything after. Batch already throttles real
    # concurrency via the `_search_semaphore(3)` browser pool (Sprint 8-1)
    # and the outer batch endpoints' own 3/min rate limits. 180/min here
    # = ~3/sec sustained, matches browser-pool saturation rate, still catches
    # any runaway loop. Skipped entirely when no IP supplied.
    if client_ip:
        try:
            import app as main_app
            if not main_app._check_rate_limit(f"batch_scrape:{client_ip}", 180):
                return {
                    "url": url,
                    "url_hash": uh,
                    "canonical_url": url,
                    "scrape_ok": False,
                    "scrape_error": "rate_limited",
                    "address": cached.get("address") if cached else None,
                    "hard_fail": True,
                    "criteria": {name: 0.0 for name in ranking_mod.CRITERION_NAMES},
                    "metrics": {},
                    "derived_metrics": {},
                    "verdict": "red",
                    "verdict_reasons": ["Rate limited — skipped"],
                    "llm_analysis": None,
                    "insurance_breakdown": {},
                    "cache_stale_reason": None,
                    "scrape": {"ok": False, "error": "rate_limited"},
                    "enrichment": None,
                    "llm_tokens": {"input": 0, "cached_input_read": 0, "output": 0},
                    "llm_ok": None,
                }
        except Exception:  # pragma: no cover - import/runtime guard
            pass

    # Sprint 8-4: warm-cache skip. If the same URL was scraped in the last
    # `_WARM_SCRAPE_SKIP_MINUTES` we reuse the stored snapshot instead of
    # hitting the site again — the real-estate listing isn't going to
    # change in 15 minutes, and re-pastes of the same batch are common.
    from datetime import datetime, timezone

    scrape = _reuse_warm_snapshot(
        db_path=db_path,
        url_hash=uh,
        now_utc=datetime.now(timezone.utc),
    )
    if scrape is None:
        scrape = await _scrape_url(url)
    if not scrape.get("ok"):
        return {
            "url": url,
            "url_hash": uh,
            "canonical_url": url,
            "scrape_ok": False,
            "scrape_error": scrape.get("error", "unknown"),
            "address": cached.get("address") if cached else None,
            "hard_fail": True,
            "criteria": {name: 0.0 for name in ranking_mod.CRITERION_NAMES},
            "metrics": {},
            "derived_metrics": {},
            "verdict": "red",
            "verdict_reasons": [f"Scrape failed — cannot evaluate ({scrape.get('error', 'unknown')})"],
            "llm_analysis": None,
            "insurance_breakdown": {},
            "cache_stale_reason": None,
            "scrape": {"ok": False, "error": scrape.get("error")},
            "enrichment": None,
            "llm_tokens": {"input": 0, "cached_input_read": 0, "output": 0},
            "llm_ok": None,
        }

    zip_code = _extract_zip(scrape.get("address"))

    # Cache staleness check.
    stale, stale_reason = llm_mod.is_cache_stale(
        cached_row=cached,
        fresh_price=scrape.get("price"),
        fresh_dom=scrape.get("dom"),
    )

    # Enrichment — re-use cached row if we have one and it isn't missing.
    enrichment_row: dict[str, Any] | None = None
    if cached_enrichment and not cached_enrichment.get("fetch_errors_json"):
        enrichment_row = {
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

    # LLM extraction — cache hit branch.
    if not stale and cached and cached.get("llm_analysis"):
        try:
            llm_analysis = json.loads(cached["llm_analysis"])
        except json.JSONDecodeError:
            llm_analysis = llm_mod.default_llm_analysis(failed=True)
        llm_tokens = {"input": 0, "cached_input_read": 0, "output": 0}
        llm_ok = True
        final_stale_reason = None
        analyzed_at = cached.get("llm_analyzed_at")
    else:
        llm_result = await llm_mod.extract_property(
            client=http_client,
            api_key=api_key,
            address=scrape.get("address"),
            price=scrape.get("price"),
            beds=scrape.get("beds"),
            baths=scrape.get("baths"),
            sqft=scrape.get("sqft"),
            year_built=scrape.get("year_built"),
            units=scrape.get("units"),
            dom=scrape.get("dom"),
            description=scrape.get("description"),
            image_url=scrape.get("image_url"),
        )
        llm_analysis = llm_result["analysis"]
        llm_tokens = llm_result["tokens"]
        llm_ok = bool(llm_result["ok"])
        final_stale_reason = stale_reason or "new_url"
        analyzed_at = now_iso

    # Insurance heuristic.
    insurance = compute_insurance(
        price=scrape.get("price"),
        year_built=scrape.get("year_built"),
        flood_zone=(enrichment_row or {}).get("flood_zone"),
        fire_zone=(enrichment_row or {}).get("fire_zone"),
        llm_uplift=((llm_analysis.get("insuranceUplift") or {}).get("suggested")),
        enrichment_missing=bool((enrichment_row or {}).get("enrichment_missing")),
    )

    # Rent comps — look up real Redfin medians from rent_comps_cache (§A.1/§F.1).
    # Cache-first; on miss we fetch via the app-level Redfin scraper, persist
    # the payload, and fall back to TIER_DEFAULT_RENT_2BR only when no comps
    # are available (or Redfin times out / errors).
    from .rent_comps import derive_per_unit_profile, get_rent_estimate

    zip_tier = classify_zip_tier(zip_code)
    scraped_units_for_rent = scrape.get("units")
    per_unit_beds, per_unit_baths = derive_per_unit_profile(
        total_beds=scrape.get("beds"),
        total_baths=scrape.get("baths"),
        units=scraped_units_for_rent,
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

    # Metrics + verdict. Units-unknown silently DTI-passed duplex math before;
    # now we surface it as its own hard-fail reason while still running the math.
    scraped_units = scrape.get("units")
    units_unknown = scraped_units is None
    computed = compute_property_metrics(
        price=scrape.get("price"),
        units=scraped_units,  # None → verdict flags hardFailUnitsUnknown
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
        property_type_raw=scrape.get("property_type_raw"),
        units_source=scrape.get("units_source"),
    )

    # Expose rent-comp provenance on the metrics payload so the ranker +
    # Jose can see whether a rank is backed by real comps or a tier guess.
    computed["metrics"]["rent_per_unit"] = int(round(rent_per_unit))
    computed["metrics"]["rent_source"] = rent_source
    computed["metrics"]["rent_comps_sample_size"] = rent_comps_sample_size

    criteria = ranking_mod.criteria_from_metrics(computed["metrics"])
    derived_metrics = {
        "price_velocity": None,
        "dom_percentile_zip": None,
        "price_per_sqft_median_zip": None,
        "topsis_percentile_alltime": None,
        "reappearance_count": (cached.get("scrape_count") if cached else 0),
    }

    return {
        "url": url,
        "url_hash": uh,
        "canonical_url": url,
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
        "cache_stale_reason": final_stale_reason,
        "scrape": scrape,
        "enrichment": enrichment_row,
        "llm_tokens": llm_tokens,
        "llm_ok": llm_ok,
        "analyzed_at": analyzed_at,
    }


# --------------------------------------------------------------------------
# Failures envelope helper (Sprint 10B-1)
# --------------------------------------------------------------------------

# Map scrape_error codes to short, Jose-friendly reasons. Keep the raw code
# as errorCode so the UI can still key off it (cache_source badge logic, etc.)
_ERROR_REASONS: dict[str, str] = {
    "unsupported_url": "Unsupported site (only Redfin / Zillow)",
    "invalid_zillow_path": "Invalid Zillow URL",
    "invalid_redfin_path": "Invalid Redfin URL",
    "fetch_failed": "Could not reach the listing",
    "parse_failed": "Could not parse the page",
    "extract_failed": "Listing payload had no usable data",
    "rate_limited": "Rate limited — try again in a minute",
    "worker_exception": "Unexpected error while processing",
}


def _human_readable_reason(raw: str | None) -> str:
    """Map an internal scrape_error code to a short human-readable phrase."""
    if not raw:
        return "Unknown error"
    # worker_exception:KeyError style — strip the type suffix for the lookup.
    base = raw.split(":", 1)[0]
    return _ERROR_REASONS.get(base, raw)


def build_failures_envelope(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Extract per-URL failures from the pipeline result rows.

    Input is the same `ranked`/`finalized` list we already hand to the ranking
    writer. A row is a failure if scrape_ok is falsy (scrape never succeeded)
    or if hard_fail is True with no real address/price to act on.

    Output shape matches the Sprint 10B-1 contract:
        [{url, canonicalUrl, reason, errorCode}]
    """
    failures: list[dict[str, Any]] = []
    for row in rows:
        # Only surface rows that never produced usable data. Hard-fail rows
        # that HAVE a price (e.g. DTI blew the envelope) are still valuable —
        # the user should see them in the rankings, not the failures list.
        if row.get("scrape_ok"):
            continue
        url = row.get("url") or row.get("canonical_url") or ""
        canonical = row.get("canonical_url") or url
        raw_code = row.get("scrape_error") or "unknown"
        failures.append({
            "url": url,
            "canonicalUrl": canonical,
            "reason": _human_readable_reason(raw_code),
            "errorCode": raw_code,
        })
    return failures


async def run_sync_batch(
    urls: list[str],
    *,
    db_path: str,
    api_key: str | None,
    preset_name: str | None = None,
    include_narrative: bool = True,
    client_ip: str | None = None,
    batch_id: str | None = None,
    created_at: str | None = None,
) -> dict[str, Any]:
    """End-to-end sync batch. Returns the full response envelope (§B.1).

    Sprint 15.5: `batch_id` + `created_at` now optional. When provided,
    reuses an existing pre-written `batches` row (status='pending') so
    the orchestrator can return the batch_id to the client *before* the
    work completes, enabling a polling UX for big batches. When omitted,
    generates them inline (backward-compat for tests + small batches
    that never needed polling).
    """
    # Dedupe while preserving order.
    seen = set()
    deduped: list[str] = []
    for u in urls:
        u = (u or "").strip()
        if not u or u in seen:
            continue
        seen.add(u)
        deduped.append(u)

    batch_id = batch_id or new_uuid_hex()
    created_at = created_at or utc_now_iso()

    async with httpx.AsyncClient(follow_redirects=True) as client:
        # Cap concurrency at 4 workers per §N.2.
        sem = asyncio.Semaphore(4)

        async def _worker(u: str) -> dict[str, Any]:
            async with sem:
                try:
                    return await process_url(
                        url=u, http_client=client, api_key=api_key, db_path=db_path,
                        client_ip=client_ip,
                    )
                except Exception as exc:  # pragma: no cover - safety net
                    logger.exception("Batch worker failed for %s", u)
                    return {
                        "url": u,
                        "url_hash": compute_url_hash(u),
                        "canonical_url": u,
                        "scrape_ok": False,
                        "scrape_error": f"worker_exception:{type(exc).__name__}",
                        "address": None,
                        "hard_fail": True,
                        "criteria": {name: 0.0 for name in ranking_mod.CRITERION_NAMES},
                        "metrics": {},
                        "derived_metrics": {},
                        "verdict": "red",
                        "verdict_reasons": [f"Worker exception: {type(exc).__name__}"],
                        "llm_analysis": None,
                        "insurance_breakdown": {},
                        "cache_stale_reason": None,
                        "scrape": {"ok": False},
                        "enrichment": None,
                        "llm_tokens": {"input": 0, "cached_input_read": 0, "output": 0},
                        "llm_ok": None,
                    }

        results = await asyncio.gather(*(_worker(u) for u in deduped))

    # Rank across non-hard-fail.
    ranked = ranking_mod.rank_batch(results)

    # Persist everything inside BEGIN IMMEDIATE.
    now_iso = utc_now_iso()
    conn = get_connection(db_path)
    try:
        def _write(c: sqlite3.Connection) -> None:
            # Sprint 15.5: UPSERT so a pre-written pending row (from the
            # polling-backed submit path) transitions to complete instead
            # of conflicting on PRIMARY KEY. When no prior row exists,
            # this acts identically to the original INSERT.
            c.execute(
                """INSERT INTO batches
                   (batch_id, created_at, completed_at, mode, input_count,
                    status, preset_name, error_reason)
                   VALUES (?, ?, ?, 'sync', ?, 'complete', ?, NULL)
                   ON CONFLICT(batch_id) DO UPDATE SET
                     completed_at = excluded.completed_at,
                     input_count  = excluded.input_count,
                     status       = 'complete',
                     preset_name  = excluded.preset_name,
                     error_reason = NULL""",
                (batch_id, created_at, now_iso, len(deduped), preset_name),
            )

            # Sprint 8-3: collect homogeneous rows then ``executemany`` at
            # the end to cut down BEGIN IMMEDIATE hold time. Property
            # upserts stay per-row (their insert-vs-update branch is
            # heterogeneous); same for enrichment (ON CONFLICT upsert with
            # optional enrichment key) and analysis-cache updates.
            snapshot_rows: list[tuple] = []
            ranking_rows: list[tuple] = []
            claude_run_rows: list[tuple] = []

            for row in ranked:
                _upsert_property_row(
                    c,
                    url_hash=row["url_hash"],
                    canonical_url=row["canonical_url"],
                    address=row.get("address"),
                    zip_code=row.get("zip_code"),
                    last_price=row.get("price"),
                    last_dom=row.get("metrics", {}).get("dom"),
                    now_iso=now_iso,
                )
                scrape = row.get("scrape") or {"ok": False}
                snapshot_rows.append((
                    row["url_hash"], now_iso,
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
                        enrichment=row["enrichment"], now_iso=now_iso,
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
                        tokens = row["llm_tokens"] or {}
                        claude_run_rows.append((
                            new_uuid_hex(),
                            batch_id,
                            row["url_hash"],
                            1 if tokens.get("cached_input_read") else 0,
                            tokens.get("input"), tokens.get("cached_input_read"), tokens.get("output"),
                            now_iso, now_iso,
                            "ok" if row["llm_ok"] else "failed",
                            None if row["llm_ok"] else "extraction_failed",
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
                       (run_id, batch_id, url_hash, mode, prompt_cache_hit,
                        input_tokens, cached_input_tokens, output_tokens, cost_usd,
                        created_at, completed_at, status, error_reason)
                       VALUES (?, ?, ?, 'sync', ?, ?, ?, ?, NULL, ?, ?, ?, ?)""",
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

    # Build response envelope.
    response_rankings: list[dict[str, Any]] = []
    for row in ranked:
        response_rankings.append({
            "rank": row["rank"],
            "url_hash": row["url_hash"],
            "canonical_url": row["canonical_url"],
            "address": row.get("address"),
            "zip_code": row.get("zip_code"),
            "price": row.get("price"),
            # Sprint 14.5: include scraped shape so the unified results table
            # can render Beds/Baths + Sqft columns alongside verdict/TOPSIS.
            "beds": row.get("beds"),
            "baths": row.get("baths"),
            "sqft": row.get("sqft"),
            "year_built": row.get("year_built"),
            "units": row.get("units"),
            "dom": row.get("dom"),
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
            "claude_narrative": _coerce_narrative((row.get("llm_analysis") or {}).get("narrativeForRanking")) if include_narrative else None,
        })

    return {
        "batch_id": batch_id,
        "created_at": created_at,
        "completed_at": now_iso,
        "mode": "sync",
        "input_count": len(deduped),
        "duplicates_removed": len(urls) - len(deduped),
        "status": "complete",
        "preset_name": preset_name,
        "rankings": response_rankings,
        # Sprint 10B-1: per-URL failure visibility. Rankings used to silently
        # drop the scrape-failure rows off the table visually; now the client
        # can render them in a separate "Failed URLs" section with Retry.
        "failures": build_failures_envelope(ranked),
    }
