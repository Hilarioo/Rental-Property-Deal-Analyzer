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

from typing import Any

JOSE_THRESHOLDS: dict[str, float] = {
    "netPitiGreen": 2500,
    "netPitiRed": 3200,
    "cashCloseGreen": 45000,
    "cashCloseRed": 60000,
    "rehabGreen": 60000,
    "rehabRed": 80000,
    "priceCeilingDuplex": 525000,
    "priceCeilingTriplex": 650000,
    "maxDtiPct": 55,
    "roofAgeYellow": 15,
}


def _fmt_usd(n: float) -> str:
    return "$" + f"{round(n):,}"


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
        red_reasons.append("SFR without legal ADU — no 75% rental offset possible")
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

    net_piti = c.get("netPiti") or 0
    if net_piti > T["netPitiGreen"]:
        net_over = net_piti - T["netPitiGreen"]
        net_over_pct = net_over / T["netPitiGreen"]
        msg = (
            f"Net PITI {_fmt_usd(net_piti)} exceeds {_fmt_usd(T['netPitiGreen'])} "
            f"by {_fmt_usd(net_over)}"
        )
        if net_piti > T["netPitiRed"] or net_over_pct > 0.10:
            red_reasons.append(msg)
        else:
            yellow_reasons.append(msg)

    cash_to_close = c.get("cashToClose") or 0
    if cash_to_close > T["cashCloseGreen"]:
        cash_over = cash_to_close - T["cashCloseGreen"]
        cash_over_pct = cash_over / T["cashCloseGreen"]
        msg = (
            f"Cash to close {_fmt_usd(cash_to_close)} exceeds "
            f"{_fmt_usd(T['cashCloseGreen'])} by {_fmt_usd(cash_over)}"
        )
        if cash_to_close > T["cashCloseRed"] or cash_over_pct > 0.10:
            red_reasons.append(msg)
        else:
            yellow_reasons.append(msg)

    effective_rehab = c.get("effectiveRehab") or 0
    if effective_rehab > T["rehabGreen"]:
        rehab_over = effective_rehab - T["rehabGreen"]
        rehab_over_pct = rehab_over / T["rehabGreen"]
        msg = (
            f"Rehab {_fmt_usd(effective_rehab)} exceeds {_fmt_usd(T['rehabGreen'])} "
            f"by {_fmt_usd(rehab_over)}"
        )
        if effective_rehab > T["rehabRed"] or rehab_over_pct > 0.10:
            red_reasons.append(msg)
        else:
            yellow_reasons.append(msg)

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
        red_reasons.append("Unit count not detected — re-scrape or enter manually")

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
TIER1_ZIPS = {"94590", "94591"}
TIER2_ZIPS = {"94547", "94572", "94525", "94564"}
TIER3_ZIPS = {"94801", "94804", "94805"}
EXCLUDED_ZIPS = {"94803", "94806"}  # Point Richmond, Hilltop Richmond


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
