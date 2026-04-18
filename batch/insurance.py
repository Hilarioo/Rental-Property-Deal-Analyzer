"""Insurance heuristic per BATCH_DESIGN.md §M.

Multipliers stack in this order: base → age → flood → fire → LLM.
"""
from __future__ import annotations

from typing import Any

from .db import utc_now_iso

HIGH_FLOOD_ZONES = {"A", "AE", "AH", "AO", "VE", "V"}
VERY_HIGH_FIRE = "LRA-very-high"


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
    price_val = int(price or 400_000)
    base = 1800 + 200 * max(0, (price_val - 400_000) / 100_000)

    age_mult = 1.15 if (year_built and year_built < 1960) else 1.00
    age_reason = (
        f"year_built={year_built} (<1960, wood frame age)" if age_mult > 1 else
        f"year_built={year_built or 'unknown'} (≥1960)"
    )

    flood_code = (flood_zone or "").upper()
    flood_mult = 1.25 if flood_code in HIGH_FLOOD_ZONES else 1.00
    flood_reason = (
        f"zone={flood_code} (SFHA — +25%)" if flood_mult > 1 else
        f"zone={flood_code or 'unknown'} (not in SFHA)"
    )

    fire_mult = 1.20 if fire_zone == VERY_HIGH_FIRE else 1.00
    fire_reason = (
        f"zone={fire_zone} (+20%)" if fire_mult > 1 else
        f"zone={fire_zone or 'unknown'}"
    )

    try:
        llm_raw = float(llm_uplift) if llm_uplift is not None else 1.0
    except (TypeError, ValueError):
        llm_raw = 1.0
    llm_mult = max(1.0, min(1.5, llm_raw))
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
