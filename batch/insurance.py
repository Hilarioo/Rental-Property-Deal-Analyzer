"""Insurance heuristic per BATCH_DESIGN.md §M.

Multipliers stack in this order: base → age → flood → fire → LLM.
"""
from __future__ import annotations

from typing import Any

from spec import constants as _spec

from .db import utc_now_iso

# ADR-002: insurance heuristic constants now live in spec/constants.json.
_INS = _spec.insurance
HIGH_FLOOD_ZONES = set(_INS["highFloodZones"])
VERY_HIGH_FIRE = _INS["veryHighFireZone"]


def compute_insurance(
    *,
    price: int | None,
    year_built: int | None,
    flood_zone: str | None,
    fire_zone: str | None,
    llm_uplift: float | None,
    enrichment_missing: bool = False,
) -> dict[str, Any]:
    """Return a breakdown dict persisted in properties.cached_insurance_breakdown."""
    price_floor = int(_INS["priceFloor"])
    price_val = int(price or price_floor)
    base = _INS["baseFee"] + _INS["per100kOver400k"] * max(0, (price_val - price_floor) / 100_000)

    pre1960_threshold = int(_INS["pre1960Threshold"])
    age_mult = _INS["pre1960Multiplier"] if (year_built and year_built < pre1960_threshold) else 1.00
    age_reason = (
        f"year_built={year_built} (<{pre1960_threshold}, wood frame age)" if age_mult > 1 else
        f"year_built={year_built or 'unknown'} (≥{pre1960_threshold})"
    )

    flood_code = (flood_zone or "").upper()
    flood_mult = _INS["highFloodZoneMultiplier"] if flood_code in HIGH_FLOOD_ZONES else 1.00
    flood_reason = (
        f"zone={flood_code} (SFHA — +{int((flood_mult - 1) * 100)}%)" if flood_mult > 1 else
        f"zone={flood_code or 'unknown'} (not in SFHA)"
    )

    fire_mult = _INS["veryHighFireZoneMultiplier"] if fire_zone == VERY_HIGH_FIRE else 1.00
    fire_reason = (
        f"zone={fire_zone} (+{int((fire_mult - 1) * 100)}%)" if fire_mult > 1 else
        f"zone={fire_zone or 'unknown'}"
    )

    try:
        llm_raw = float(llm_uplift) if llm_uplift is not None else 1.0
    except (TypeError, ValueError):
        llm_raw = 1.0
    llm_mult = max(float(_INS["llmMultiplierMin"]), min(float(_INS["llmMultiplierMax"]), llm_raw))
    llm_reason = f"LLM uplift {llm_mult:.2f}"

    annual = round(base * age_mult * flood_mult * fire_mult * llm_mult)
    return {
        "base": round(base),
        "price_used_for_base": price_val,
        "age_multiplier": age_mult,
        "age_reason": age_reason,
        "flood_multiplier": flood_mult,
        "flood_reason": flood_reason,
        "fire_multiplier": fire_mult,
        "fire_reason": fire_reason,
        "llm_multiplier": llm_mult,
        "llm_reason": llm_reason,
        "annual_usd": int(annual),
        "computed_at": utc_now_iso(),
        "enrichment_missing": bool(enrichment_missing),
    }
