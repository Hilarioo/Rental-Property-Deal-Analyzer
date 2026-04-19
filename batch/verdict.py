"""Server-side port of `computeJoseVerdict` from index.html (Sprint 4, line ~2272).

Architectural note: we port the verdict predicate to Python rather than
subprocess-calling calc.js / Node. Rationale:
- The batch pipeline is already Python (FastAPI, sqlite3, httpx). A subprocess
  roundtrip per URL would add ~50ms of overhead and a hard Node dependency to
  the server boot path.
- The predicate is ~100 lines of boolean + arithmetic logic with zero I/O and
  no floating-point edge cases that diverge between V8 and CPython for the
  integer thresholds at play. A unit-test comparing the two on representative
  inputs is cheap to write later if we grow doubt.
- The Sprint 4 thresholds (`JOSE_THRESHOLDS`) are mirrored verbatim below; any
  future tweak must update both sites in the same commit.

If the two implementations ever disagree, the JS frontend is authoritative
(that's what Jose eyeballs in the single-URL wizard), and we fix the Python.
"""
from __future__ import annotations

import math
from typing import Any

# ADR-002: thresholds now live in spec/constants.json.
# Hard-fail at import time if the spec is missing or malformed.
from spec import constants as _spec

JOSE_THRESHOLDS: dict[str, float] = _spec.jose


# Sprint 12-2: geospatial settings. location lives on the PRIVATE profile
# (Jose's home base is PII-adjacent — commute radius reveals where he lives).
# conditionalCities is PUBLIC (market logic, not buyer identity).
_PROFILE_LOCATION: dict[str, Any] = (
    (_spec.profile_raw or {}).get("location") or {}
)
_CONDITIONAL_CITIES: dict[str, Any] = (
    _spec.zip_tiers.get("conditionalCities") or {}
)


def _haversine_miles(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance in statute miles between two (lat, lng) points."""
    R_MILES = 3958.8
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return 2 * R_MILES * math.asin(min(1.0, math.sqrt(a)))


def _geospatial_fail(ctx: dict[str, Any]) -> str | None:
    """Return a RED-reason string if the listing fails geospatial gates, else None.

    Two gates, in order:
      1. Hard commute radius — `profile.location.maxMilesHard`.
         If listing's distance from `homeBase` exceeds it, RED.
      2. Conditional cities — `zipTiers.conditionalCities[<city>]`.
         If the listing's address contains a conditional city name AND
         its distance exceeds that city's threshold, RED. (If inside the
         threshold, the listing is implicitly *allowed* to proceed even
         though the name would otherwise fail `_looks_excluded` checks.)

    If lat/lng or homeBase is missing (e.g. single-property analyzer with
    no geocode), both gates no-op. This preserves JS↔Py parity for the
    pre-12-2 fixtures that don't carry coordinates.
    """
    lat = ctx.get("lat")
    lng = ctx.get("lng")
    if not isinstance(lat, (int, float)) or not isinstance(lng, (int, float)):
        return None
    home = _PROFILE_LOCATION.get("homeBase") or {}
    home_lat = home.get("lat")
    home_lng = home.get("lng")
    if not isinstance(home_lat, (int, float)) or not isinstance(home_lng, (int, float)):
        return None
    if home_lat == 0 and home_lng == 0:
        return None  # redacted example profile — skip.

    miles = _haversine_miles(float(lat), float(lng), float(home_lat), float(home_lng))

    max_hard = _PROFILE_LOCATION.get("maxMilesHard")
    if isinstance(max_hard, (int, float)) and miles > max_hard:
        return (
            f"Outside commute radius — {miles:.0f} mi from home base "
            f"exceeds {int(max_hard)} mi hard cap"
        )

    address_lower = (ctx.get("address") or "").lower()
    for city, rule in _CONDITIONAL_CITIES.items():
        if not isinstance(rule, dict):
            continue
        if city.lower() not in address_lower:
            continue
        if rule.get("rule") != "maxMilesFromHomeBase":
            continue
        threshold = rule.get("threshold")
        if not isinstance(threshold, (int, float)):
            continue
        if miles > threshold:
            return (
                f"{city} conditional-market rule: {miles:.0f} mi from home base "
                f"exceeds {int(threshold)} mi threshold"
            )
    return None


def _fmt_usd(n: float) -> str:
    return "$" + f"{round(n):,}"


def _classify_overage(
    value: float,
    green: float,
    yellow: float | None,
    red: float,
) -> str | None:
    """Sprint 12-1: layered Yellow classification.

    Returns 'green' (≤ green, no issue), 'yellow', or 'red'.
    Yellow fires if EITHER the explicit yellow threshold allows it OR the
    legacy 10% rule allows it (missed green by ≤10%). Red only when BOTH
    fail. Backward-compatible: if `yellow` is None, falls back to pure 10%.
    """
    if value <= green:
        return "green"
    if value > red:
        return "red"
    # Layered: pass if either the explicit yellow band OR the 10% overage rule
    # accepts the value.
    ten_pct_ok = (value - green) / green <= 0.10
    explicit_yellow_ok = yellow is not None and value <= yellow
    if explicit_yellow_ok or ten_pct_ok:
        return "yellow"
    return "red"


def compute_jose_verdict(ctx: dict[str, Any]) -> dict[str, Any]:
    """Return {'verdict': 'green'|'yellow'|'red', 'reasons': [str, ...]}.

    Ordering: hard-fail gates first, then numeric predicates, then soft flags.
    Up to 3 reasons returned.
    """
    c = ctx or {}
    T = JOSE_THRESHOLDS
    red_reasons: list[str] = []
    yellow_reasons: list[str] = []

    # ---- Hard-fail gates ----
    zip_code = c.get("zip") or ""
    if c.get("isExcludedByZipTier"):
        red_reasons.append(f"ZIP {zip_code} on excluded list")
    if c.get("hasFlatRoof"):
        red_reasons.append("Flat roof / commercial conversion — FHA disqualifier")
    if c.get("hasUnpermittedAdu"):
        red_reasons.append("Unpermitted ADU / garage conversion — FHA disqualifier")
    if c.get("isPre1978WithGalvanized"):
        red_reasons.append("Pre-1978 w/ galvanized + knob-and-tube — FHA disqualifier")
    units = c.get("units") or 1
    if c.get("propertyType") == "sfh" and units <= 1:
        # Sprint 12 hotfix 2026-04-19: differentiate condo / townhouse /
        # single-family so the RED reason tells Jose what kind of listing
        # this is, not just "SFR without legal ADU" for every single-unit
        # scrape. `propertyTypeRaw` is what the scraper saw (e.g. CONDO,
        # TOWNHOUSE, SINGLE_FAMILY); `unitsSource` traces the inference
        # path (e.g. address_suffix when the URL said /APT-3/).
        pt_raw = (c.get("propertyTypeRaw") or "").lower()
        usrc = c.get("unitsSource") or ""
        if "condo" in pt_raw:
            red_reasons.append("Single condo unit — no other units to rent, 75% FHA offset unavailable")
        elif "townhouse" in pt_raw or "townhome" in pt_raw:
            red_reasons.append("Single townhouse unit — no other units to rent, 75% FHA offset unavailable")
        elif usrc in ("address_suffix", "address_hash_suffix", "url_slug"):
            red_reasons.append("Address suffix (APT/UNIT/#) indicates one unit of a larger building — no 75% FHA rental offset possible")
        else:
            red_reasons.append("SFR without legal ADU — no 75% rental offset possible")
    # Sprint 12-2: geospatial hard-fail (commute radius + conditional cities).
    # Only fires when lat/lng are present AND a homeBase is configured;
    # otherwise no-ops (preserves parity for pre-12-2 fixtures).
    geo_fail = _geospatial_fail(c)
    if geo_fail:
        red_reasons.append(geo_fail)
    # Units-unknown hard-fail is appended LAST so dominant fails show first.
    units_unknown_fail = bool(c.get("hardFailUnitsUnknown"))
    qualifying_income = c.get("qualifyingIncome") or 0
    piti = c.get("piti") or 0
    if qualifying_income > 0 and piti > 0:
        dti_pct = (piti / qualifying_income) * 100
        if dti_pct > T["maxDtiPct"]:
            red_reasons.append(
                f"PITI {_fmt_usd(piti)} is {round(dti_pct)}% of qualifying income — "
                f"exceeds {T['maxDtiPct']}% DTI gate"
            )

    # ---- Numeric predicates ----
    price_ceiling = T["priceCeilingTriplex"] if units >= 3 else T["priceCeilingDuplex"]
    price = c.get("price") or 0
    if price > price_ceiling:
        over = price - price_ceiling
        over_pct = over / price_ceiling
        which = "triplex+" if units >= 3 else "duplex"
        msg = (
            f"Price {_fmt_usd(price)} exceeds {which} ceiling "
            f"{_fmt_usd(price_ceiling)} by {_fmt_usd(over)}"
        )
        (red_reasons if over_pct > 0.10 else yellow_reasons).append(msg)

    # Sprint 12-1: layered Yellow. Each band reads an optional explicit
    # yellow threshold from spec; falls back to the Sprint 4 10%-overage
    # rule. Layering means the MORE forgiving classification wins — either
    # condition can downgrade Red → Yellow.
    net_piti = c.get("netPiti") or 0
    if net_piti > T["netPitiGreen"]:
        cls = _classify_overage(
            net_piti, T["netPitiGreen"], T.get("netPitiYellow"), T["netPitiRed"]
        )
        msg = (
            f"Net PITI {_fmt_usd(net_piti)} exceeds {_fmt_usd(T['netPitiGreen'])} "
            f"by {_fmt_usd(net_piti - T['netPitiGreen'])}"
        )
        (red_reasons if cls == "red" else yellow_reasons).append(msg)

    cash_to_close = c.get("cashToClose") or 0
    if cash_to_close > T["cashCloseGreen"]:
        cls = _classify_overage(
            cash_to_close,
            T["cashCloseGreen"],
            T.get("cashCloseYellow"),
            T["cashCloseRed"],
        )
        msg = (
            f"Cash to close {_fmt_usd(cash_to_close)} exceeds "
            f"{_fmt_usd(T['cashCloseGreen'])} by {_fmt_usd(cash_to_close - T['cashCloseGreen'])}"
        )
        (red_reasons if cls == "red" else yellow_reasons).append(msg)

    effective_rehab = c.get("effectiveRehab") or 0
    if effective_rehab > T["rehabGreen"]:
        cls = _classify_overage(
            effective_rehab,
            T["rehabGreen"],
            T.get("rehabYellow"),
            T["rehabRed"],
        )
        msg = (
            f"Rehab {_fmt_usd(effective_rehab)} exceeds {_fmt_usd(T['rehabGreen'])} "
            f"by {_fmt_usd(effective_rehab - T['rehabGreen'])}"
        )
        (red_reasons if cls == "red" else yellow_reasons).append(msg)

    zip_tier = c.get("zipTier")
    if zip_tier == "outside":
        red_reasons.append("ZIP outside all target market tiers")
    elif zip_tier == "tier3":
        yellow_reasons.append("Tier 3 ZIP — Richmond motivated sellers, underwrite conservatively")

    roof_age = c.get("roofAgeYears")
    if isinstance(roof_age, (int, float)) and roof_age > T["roofAgeYellow"]:
        yellow_reasons.append(f"Roof {int(roof_age)} yrs old — FHA appraisal risk")

    # Append units-unknown hard-fail LAST so existing dominant fails surface first.
    if units_unknown_fail:
        red_reasons.append("Unit count ambiguous — cannot confirm 2-4 unit eligibility; set units manually in the single-property wizard")

    if red_reasons:
        verdict = "red"
        reasons = red_reasons + yellow_reasons
    elif yellow_reasons:
        verdict = "yellow"
        reasons = yellow_reasons
    else:
        verdict = "green"
        reasons = [
            f"Net PITI {_fmt_usd(net_piti)} under {_fmt_usd(T['netPitiGreen'])} cap",
            f"Cash to close {_fmt_usd(cash_to_close)} under {_fmt_usd(T['cashCloseGreen'])} cap",
        ]
        if zip_tier in ("tier1", "tier2"):
            label = "Tier 1" if zip_tier == "tier1" else "Tier 2"
            tail = f" {zip_code}" if zip_code else ""
            reasons.append(f"{label} priority ZIP{tail}")

    return {"verdict": verdict, "reasons": reasons[:3]}


# --- ZIP tier lookup (USER_PROFILE §6, §7) ---
# ADR-002: ZIP tiers now read from spec/constants.json.
TIER1_ZIPS = set(_spec.zip_tiers["tier1"])
TIER2_ZIPS = set(_spec.zip_tiers["tier2"])
TIER3_ZIPS = set(_spec.zip_tiers["tier3"])
EXCLUDED_ZIPS = set(_spec.zip_tiers["excludedZips"])  # e.g. Point Richmond, Hilltop


def classify_zip_tier(zip_code: str | None) -> str:
    """Return one of 'tier1'|'tier2'|'tier3'|'excluded'|'outside'."""
    z = (zip_code or "").strip()[:5]
    if not z:
        return "outside"
    if z in EXCLUDED_ZIPS:
        return "excluded"
    if z in TIER1_ZIPS:
        return "tier1"
    if z in TIER2_ZIPS:
        return "tier2"
    if z in TIER3_ZIPS:
        return "tier3"
    return "outside"
