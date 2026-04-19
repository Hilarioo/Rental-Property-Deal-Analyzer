#!/usr/bin/env python3
"""Sprint 13a: per-ZIP preset auto-generator.

Takes a city + ZIP list, pulls live signal (Redfin inventory, rent comps)
from the existing batch-pipeline infrastructure, and prints a JSON block
ready to paste into `spec/constants.json.presets[<city>]`. Optional
`--write` flag appends the block in-place.

Deferred to Sprint 13b:
  - County assessor property-tax-rate scraper (Solano / Contra Costa /
    Alameda / Sacramento). Brittle per-county HTML; each needs its own
    parser. Today the generator leaves `propertyTaxRatePct` null and
    annotates the preset with a TODO for manual entry.
  - GreatSchools API integration. Requires paid API key; needs a
    separate config story before landing.

Usage:
    python scripts/generate_preset.py \\
        --name "Pittsburg / East CoCo" \\
        --zips 94565,94531,94509 \\
        --property-type multi-family \\
        [--max-price 525000] \\
        [--min-beds 2] \\
        [--write]

The generator NEVER calls the LLM. It only touches Redfin (shared with
/api/search) and rent_comps_cache (shared with the batch pipeline). No
Anthropic cost.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

# Imports deferred until after sys.path munging so `batch.*` and `app` resolve.
from spec import constants as _spec  # noqa: E402
from scripts.init_db import DEFAULT_DB_PATH as _BATCH_DB_PATH  # noqa: E402

logger = logging.getLogger("generate_preset")


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Generate a per-city preset block from live Redfin + rent-comps data.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--name", required=True, help='Preset name, e.g. "Pittsburg / East CoCo".')
    p.add_argument(
        "--zips", required=True,
        help="Comma-separated 5-digit ZIPs to include in the preset search block.",
    )
    p.add_argument(
        "--property-type", default="multi-family",
        choices=["multi-family", "house", "condo", ""],
        help="Default property type for the preset search (default: multi-family).",
    )
    p.add_argument(
        "--min-beds", type=int, default=None,
        help="Optional min beds filter applied to inventory sampling.",
    )
    p.add_argument(
        "--min-price", type=int, default=None,
        help="Override auto-computed min price (default: 20th percentile of inventory).",
    )
    p.add_argument(
        "--max-price", type=int, default=None,
        help="Override auto-computed max price (default: profile.jose.priceCeilingDuplex).",
    )
    p.add_argument(
        "--max-listings", type=int, default=25,
        help="Per-ZIP sample size for inventory inference (default 25, max 75).",
    )
    p.add_argument(
        "--rent-beds", type=int, default=2,
        help="Bedroom count used for rent-comp lookup (default 2 matches duplex unit).",
    )
    p.add_argument(
        "--write", action="store_true",
        help="If set, append the generated block to spec/constants.json presets in place.",
    )
    p.add_argument(
        "--verbose", "-v", action="store_true",
        help="Log each ZIP fetch + parse step to stderr.",
    )
    args = p.parse_args()
    args.zips = [z.strip() for z in args.zips.split(",") if z.strip()]
    for z in args.zips:
        if len(z) != 5 or not z.isdigit():
            p.error(f"invalid ZIP: {z!r} (must be 5 digits)")
    args.max_listings = max(1, min(args.max_listings, 75))
    return args


def _percentile(values: list[int], pct: float) -> int | None:
    """Cheap nearest-rank percentile. Returns None on empty input."""
    if not values:
        return None
    s = sorted(values)
    k = max(0, min(len(s) - 1, int(round((pct / 100.0) * (len(s) - 1)))))
    return int(s[k])


async def _sample_zip_inventory(
    zip_code: str, property_type: str | None, min_beds: int | None, max_listings: int,
) -> dict:
    """Pull a sample of current Redfin inventory for one ZIP, return stats."""
    # Local import to keep `app` module load lazy.
    from app import _search_redfin_page  # type: ignore

    filters = {
        "min_price": None,
        "max_price": None,
        "min_beds": min_beds,
        "property_type": property_type or None,
        "max_results": max_listings,
    }
    try:
        result = await _search_redfin_page(zip_code, filters)
    except Exception as exc:
        logger.warning("redfin fetch failed for zip=%s: %s", zip_code, exc)
        return {"zip": zip_code, "error": "fetch_failed", "listings": []}

    listings = (result or {}).get("listings") or []
    prices = [int(l["price"]) for l in listings if isinstance(l.get("price"), (int, float)) and l["price"] > 0]
    return {
        "zip": zip_code,
        "label": (result or {}).get("location_label") or zip_code,
        "total_reported": (result or {}).get("total"),
        "sampled": len(listings),
        "prices": prices,
    }


async def _fetch_rent_median(zip_code: str, beds: int) -> dict:
    """Pull median rent for (zip, beds) via the existing batch rent_comps helper."""
    from batch.rent_comps import get_rent_estimate  # type: ignore

    try:
        result = await get_rent_estimate(
            zip_code=zip_code,
            beds=beds,
            baths=1.0,
            db_path=str(_BATCH_DB_PATH),
        )
    except Exception as exc:
        logger.warning("rent_comps failed for zip=%s beds=%d: %s", zip_code, beds, exc)
        result = {"median_rent": None, "sample_size": 0, "source": "fallback"}
    return {
        "zip": zip_code,
        "beds": beds,
        "median_rent": result.get("median_rent"),
        "sample_size": result.get("sample_size"),
        "source": result.get("source"),
    }


def _infer_price_range(
    inventory_samples: list[dict],
    user_min: int | None,
    user_max: int | None,
) -> tuple[int | None, int | None, dict]:
    """Return (min_price, max_price, diagnostics).

    Auto-min = 20th percentile of sampled prices, rounded down to nearest $10K.
    Auto-max = user_max (or profile.jose.priceCeilingDuplex fallback).
    User-provided values always win.
    """
    all_prices: list[int] = []
    for row in inventory_samples:
        all_prices.extend(row.get("prices") or [])

    auto_min: int | None = None
    auto_max: int | None = None

    if all_prices:
        p20 = _percentile(all_prices, 20)
        if p20 is not None:
            auto_min = (p20 // 10_000) * 10_000

    profile_ceiling = (_spec.jose or {}).get("priceCeilingDuplex")
    if isinstance(profile_ceiling, (int, float)) and profile_ceiling > 0:
        auto_max = int(profile_ceiling)

    min_price = user_min if user_min is not None else auto_min
    max_price = user_max if user_max is not None else auto_max

    return min_price, max_price, {
        "auto_min_from_p20": auto_min,
        "auto_max_from_profile_ceiling": auto_max,
        "user_min_override": user_min,
        "user_max_override": user_max,
        "inventory_sample_count": len(all_prices),
    }


def _build_preset_block(
    *,
    name: str,
    zips: list[str],
    property_type: str,
    min_beds: int | None,
    min_price: int | None,
    max_price: int | None,
    inventory_samples: list[dict],
    rent_medians: list[dict],
    sources_diag: dict,
) -> dict:
    """Assemble the JSON block ready to drop into spec.constants.json presets."""
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    search_block: dict = {
        "zips": zips,
        "propertyType": property_type or None,
    }
    if min_price is not None:
        search_block["minPrice"] = min_price
    if max_price is not None:
        search_block["maxPrice"] = max_price
    if min_beds is not None:
        search_block["minBeds"] = min_beds
    search_block["keywords"] = []
    search_block["minDom"] = None

    defaults_block: dict = {
        "propertyTaxRatePct": None,       # Sprint 13b — county assessor scrape.
        "insuranceAnnual": 1800,          # Sprint 13c — could refine from flood/fire zones.
        "vacancyPct": 5,
    }

    diagnostics = {
        "_source": "auto",
        "_generated_at": now_iso,
        "_generator_version": "sprint-13a",
        "_inventory": [
            {
                "zip": row["zip"],
                "label": row.get("label"),
                "sampled": row.get("sampled"),
                "total_reported": row.get("total_reported"),
                "min_price": min(row["prices"]) if row.get("prices") else None,
                "median_price": int(statistics.median(row["prices"])) if row.get("prices") else None,
                "max_price": max(row["prices"]) if row.get("prices") else None,
            }
            for row in inventory_samples
        ],
        "_rent_medians": rent_medians,
        "_price_range_diag": sources_diag,
        "_todo": [
            "Sprint 13b: populate defaults.propertyTaxRatePct from county assessor.",
            "Sprint 13c: insuranceAnnual refinement from FEMA + Cal Fire zones.",
            "Manual: add preset.search.keywords if this market has specific listing phrases (e.g. 'duplex', 'income property').",
        ],
    }

    block = {
        "defaults": defaults_block,
        "search": search_block,
    }
    # Attach diagnostics as a sibling comment block; the spec loader ignores
    # underscore-prefixed keys, so this round-trips safely.
    block.update(diagnostics)
    return {name: block}


def _append_to_spec(new_preset: dict) -> None:
    """Merge the generated preset into spec/constants.json in place."""
    spec_path = REPO_ROOT / "spec" / "constants.json"
    raw = json.loads(spec_path.read_text(encoding="utf-8"))
    presets = raw.setdefault("presets", {})
    for name, block in new_preset.items():
        if name in presets:
            logger.warning(
                "preset %r already exists in spec/constants.json — overwriting with auto block",
                name,
            )
        presets[name] = block
    spec_path.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {len(new_preset)} preset(s) to {spec_path}", file=sys.stderr)


async def _run(args: argparse.Namespace) -> dict:
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s %(message)s",
        stream=sys.stderr,
    )

    # Fan out the per-ZIP inventory + rent-comp fetches concurrently. Browser
    # pool + rent-comp dedup already cap the real work.
    inv_tasks = [
        _sample_zip_inventory(z, args.property_type, args.min_beds, args.max_listings)
        for z in args.zips
    ]
    rent_tasks = [_fetch_rent_median(z, args.rent_beds) for z in args.zips]
    inventory_samples = await asyncio.gather(*inv_tasks)
    rent_medians = await asyncio.gather(*rent_tasks)

    min_price, max_price, diag = _infer_price_range(
        inventory_samples, args.min_price, args.max_price,
    )

    block = _build_preset_block(
        name=args.name,
        zips=args.zips,
        property_type=args.property_type,
        min_beds=args.min_beds,
        min_price=min_price,
        max_price=max_price,
        inventory_samples=inventory_samples,
        rent_medians=rent_medians,
        sources_diag=diag,
    )
    return block


def main() -> int:
    args = _parse_args()
    block = asyncio.run(_run(args))
    print(json.dumps(block, indent=2))
    if args.write:
        _append_to_spec(block)
    return 0


if __name__ == "__main__":
    sys.exit(main())
