"""Sprint 15.5 regression tests for _extract_redfin.

Guards the three extractor fixes:
  #1 — ld+json @type is captured as propertyType (was silently dropped)
  #2 — beds/baths regex accepts both plain and object-wrapped forms
  #3 — sqft regex accepts plain integers as well as object wrappers
  #4 — propertyType regex fallback when ld+json is missing

Each test builds a minimal synthetic HTML document shaped like the
Redfin variants we've observed in the wild, runs it through
``app._extract_redfin``, and asserts the specific field we care about.
No network, no fixtures-on-disk — so tests stay deterministic and
fast.
"""
from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


@pytest.fixture(scope="module")
def extract_redfin():
    """Import ``app._extract_redfin`` once per module."""
    import app
    return app._extract_redfin


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "lxml")


# ---------------------------------------------------------------------------
# Fix #1 — ld+json @type → propertyType
# ---------------------------------------------------------------------------


def test_ldjson_toplevel_type_populates_propertyType(extract_redfin):
    """Fix #1: when ld+json top-level @type is set, propertyType must be captured."""
    html = """
    <html><head>
      <script type="application/ld+json">
      {"@type": "Apartment",
       "name": "401 Foo St",
       "address": {"streetAddress": "401 Foo St", "addressLocality": "Vallejo"},
       "offers": {"price": 535000}}
      </script>
    </head><body></body></html>
    """
    result = extract_redfin(_soup(html))
    assert result["propertyType"] == "Apartment", (
        "ld+json top-level @type was lost — previously the code read "
        "item_type but never stored it on result."
    )
    assert result["address"], "address should still come through"
    assert result["price"] == 535000


def test_ldjson_main_entity_type_preferred(extract_redfin):
    """Fix #1: mainEntity @type is more specific than the outer listing — use it."""
    html = """
    <html><head>
      <script type="application/ld+json">
      {"@type": "Residence",
       "mainEntity": {
         "@type": "SingleFamilyResidence",
         "numberOfBedrooms": 3,
         "numberOfBathroomsTotal": 2,
         "address": {"streetAddress": "123 Main", "addressLocality": "Vallejo"}
       }}
      </script>
    </head><body></body></html>
    """
    result = extract_redfin(_soup(html))
    assert result["propertyType"] == "SingleFamilyResidence"


# ---------------------------------------------------------------------------
# Fix #2 — beds / baths: plain integer AND object wrapper
# ---------------------------------------------------------------------------


def test_beds_plain_integer_form(extract_redfin):
    """Fix #2: plain-integer beds (the original form) still works."""
    html = """
    <html><body>
    <script>window.__pageData = {"listingPrice": 500000, "beds": 4, "baths": 2.5};</script>
    </body></html>
    """
    r = extract_redfin(_soup(html))
    assert r["beds"] == 4
    assert r["baths"] == 2.5


def test_beds_object_wrapped_form(extract_redfin):
    """Fix #2: Redfin object-wrapped form now also extracts (was silently null)."""
    html = """
    <html><body>
    <script>window.__pageData = {"listingPrice": 500000, "beds": {"value": 4}, "baths": {"value": 2.5}};</script>
    </body></html>
    """
    r = extract_redfin(_soup(html))
    assert r["beds"] == 4, "object-wrapped beds should now extract"
    assert r["baths"] == 2.5, "object-wrapped baths should now extract"


def test_baths_amount_wrapper(extract_redfin):
    """Fix #2: some Redfin variants use "amount" instead of "value"."""
    html = """
    <html><body>
    <script>window.__pageData = {"listingPrice": 500000, "beds": {"amount": 3}, "baths": {"amount": 1.5}};</script>
    </body></html>
    """
    r = extract_redfin(_soup(html))
    assert r["beds"] == 3
    assert r["baths"] == 1.5


# ---------------------------------------------------------------------------
# Fix #3 — sqft: wrapped + plain integer fallback
# ---------------------------------------------------------------------------


def test_sqft_wrapped_value(extract_redfin):
    """Fix #3: the historical wrapped form still works (regression guard)."""
    html = """
    <html><body>
    <script>window.__pageData = {"listingPrice": 500000, "sqFt": {"value": 1800}};</script>
    </body></html>
    """
    r = extract_redfin(_soup(html))
    assert r["sqft"] == 1800


def test_sqft_plain_integer(extract_redfin):
    """Fix #3: plain integer form now extracts too (was silently null)."""
    html = """
    <html><body>
    <script>window.__pageData = {"listingPrice": 500000, "sqFt": 1800};</script>
    </body></html>
    """
    r = extract_redfin(_soup(html))
    assert r["sqft"] == 1800


def test_sqftinfo_amount_wrapper(extract_redfin):
    """Fix #3: the sqftInfo/amount variant also works."""
    html = """
    <html><body>
    <script>window.__pageData = {"listingPrice": 500000, "sqftInfo": {"amount": 2400}};</script>
    </body></html>
    """
    r = extract_redfin(_soup(html))
    assert r["sqft"] == 2400


# ---------------------------------------------------------------------------
# Fix #4 — propertyType regex fallback from JS blob
# ---------------------------------------------------------------------------


def test_propertytype_from_js_blob_when_ldjson_missing(extract_redfin):
    """Fix #4: regex fallback picks up propertyType from the JS bundle."""
    html = """
    <html><body>
    <script>
      window.__pageData = {"listingPrice": 500000, "propertyType": "Duplex"};
    </script>
    </body></html>
    """
    r = extract_redfin(_soup(html))
    assert r["propertyType"] == "Duplex"


def test_hometype_regex_fallback(extract_redfin):
    """Fix #4: homeType fallback catches the alternate field name."""
    html = """
    <html><body>
    <script>window.__pageData = {"listingPrice": 500000, "homeType": "MULTI_FAMILY"};</script>
    </body></html>
    """
    r = extract_redfin(_soup(html))
    assert r["propertyType"] == "MULTI_FAMILY"


# ---------------------------------------------------------------------------
# Interaction test — Duplex listing surfaces as multi-family to PR #29 filter
# ---------------------------------------------------------------------------


def test_duplex_listing_survives_multifamily_filter(extract_redfin):
    """End-to-end: a duplex listing correctly produces propertyType that
    passes the PR #29 strict multi-family filter.
    """
    html = """
    <html><head>
      <script type="application/ld+json">
      {"@type": "Residence",
       "mainEntity": {
         "@type": "Apartment",
         "numberOfBedrooms": 4,
         "numberOfBathroomsTotal": 2,
         "floorSize": {"value": 1800},
         "address": {"streetAddress": "705 State St", "addressLocality": "Vallejo", "postalCode": "94590"}
       }}
      </script>
    </head><body>
    <script>window.__pageData = {"listingPrice": 535000, "propertyType": "Duplex", "beds": {"value": 4}, "baths": {"value": 2}};</script>
    </body></html>
    """
    r = extract_redfin(_soup(html))
    # mainEntity wins over the top-level ld+json value per fix #1
    # (mainEntity @type was "Apartment" and gets stored). Then the JS
    # blob with "Duplex" can't overwrite because fix #4 only fires when
    # propertyType is still null. That's intentional — ld+json is more
    # reliable than regex-scanning a script body.
    assert r["propertyType"] in ("Apartment", "Duplex")
    assert r["beds"] == 4
    assert r["baths"] == 2
    assert r["sqft"] == 1800
    assert r["price"] == 535000


# ---------------------------------------------------------------------------
# Sprint 16.6 Bundle 1A — ld+json numberOfUnits parser
# ---------------------------------------------------------------------------


def test_ldjson_numberOfUnits_toplevel(extract_redfin):
    """Bundle 1A: top-level numberOfUnits in ld+json should populate the field."""
    html = """
    <html><head>
      <script type="application/ld+json">
      {"@type": "Apartment",
       "name": "Triplex",
       "numberOfUnits": 3,
       "offers": {"price": 500000}}
      </script>
    </head><body></body></html>
    """
    r = extract_redfin(_soup(html))
    assert r["numberOfUnits"] == 3


def test_ldjson_numberOfUnits_mainEntity(extract_redfin):
    """Bundle 1A: mainEntity.numberOfUnits is the more common Redfin shape."""
    html = """
    <html><head>
      <script type="application/ld+json">
      {"@type": "Product",
       "mainEntity": {
         "@type": "Apartment",
         "numberOfUnits": 4,
         "numberOfBedrooms": 8,
         "numberOfBathroomsTotal": 4
       },
       "offers": {"price": 650000}}
      </script>
    </head><body></body></html>
    """
    r = extract_redfin(_soup(html))
    assert r["numberOfUnits"] == 4
    assert r["beds"] == 8
    assert r["baths"] == 4


def test_ldjson_numberOfUnits_string_int(extract_redfin):
    """Bundle 1A: string-form integer ('3') should coerce."""
    html = """
    <html><head>
      <script type="application/ld+json">
      {"@type": "RealEstateListing",
       "numberOfUnits": "2",
       "offers": {"price": 450000}}
      </script>
    </head><body></body></html>
    """
    r = extract_redfin(_soup(html))
    assert r["numberOfUnits"] == 2


def test_regex_fallback_numberOfUnits_plain(extract_redfin):
    """Bundle 1A: JS-blob regex picks up plain-int numberOfUnits when
    ld+json didn't populate it."""
    html = """
    <html><body>
    <script>window.__pageData = {"listingPrice": 500000, "numberOfUnits": 3};</script>
    </body></html>
    """
    r = extract_redfin(_soup(html))
    assert r["numberOfUnits"] == 3


def test_regex_fallback_numberOfUnits_wrapped(extract_redfin):
    """Bundle 1A: object-wrapped form {value: N}."""
    html = """
    <html><body>
    <script>window.__pageData = {"listingPrice": 500000, "numberOfUnits": {"value": 4}};</script>
    </body></html>
    """
    r = extract_redfin(_soup(html))
    assert r["numberOfUnits"] == 4


def test_regex_fallback_numberOfUnits_out_of_range_rejected(extract_redfin):
    """Bundle 1A: cap rejects 999 (probably a spurious counter, not units)."""
    html = """
    <html><body>
    <script>window.__pageData = {"listingPrice": 500000, "numberOfUnits": 999};</script>
    </body></html>
    """
    r = extract_redfin(_soup(html))
    assert r["numberOfUnits"] is None, "values > 20 should be rejected as spurious"


def test_no_numberOfUnits_stays_null(extract_redfin):
    """Bundle 1A: baseline — listings without numberOfUnits return None."""
    html = """
    <html><head>
      <script type="application/ld+json">
      {"@type": "Apartment",
       "offers": {"price": 500000},
       "numberOfBedrooms": 3}
      </script>
    </head><body></body></html>
    """
    r = extract_redfin(_soup(html))
    assert r["numberOfUnits"] is None


# ---------------------------------------------------------------------------
# Sprint 16.6 Bundle 1B — keyword tightening regression
# ---------------------------------------------------------------------------

# These tests import the unit-detection logic by exercising _scrape_url
# pieces directly — the keyword block is inline in that function so we
# replicate its essential behavior here via a mini-helper. This locks
# in the review-driven keyword tightening (review P1 on PR #41):
#   - "2 dwellings" was too loose (matched "within 2 dwellings of park")
#   - "rent the other" was too loose (matched "... than rent the other
#     comparable unit across town")
# Both were replaced with narrower phrases.


@pytest.mark.parametrize("description, expected_units", [
    # True positives — should detect
    ("Beautiful duplex in quiet neighborhood", 2),
    ("2-unit property, perfect for house-hacking", 2),
    ("Two family home with separate entrances", 2),
    ("Rare triplex opportunity in Oakland", 3),
    ("3-plex with ground-floor commercial potential", 3),
    ("Fourplex investment — tenants in place", 4),
    ("4-unit property, strong rental history", 4),
    ("Quadplex — fully occupied", 4),
    ("Live in one, rent out the other to cover the mortgage", 2),
    ("Main house and cottage on a large lot", 2),
    ("2 on a lot — endless possibilities", 2),
    # False positives that the old regex caught — should NOT detect
    ("Four bedrooms across the main floor, unit-ready basement", None),
    ("Within 2 dwellings of the community center", None),
    ("You'd pay more to rent the other comparable places in this ZIP", None),
    ("Single-family home with four car garage", None),
])
def test_unit_keyword_detection(description, expected_units):
    """Bundle 1B: the keyword list should catch real multi-family phrasings
    and reject the false-positive patterns the review flagged.

    Imports the canonical tuples from batch.pipeline so a keyword added
    to production automatically extends the test's coverage — prevents
    the silent-drift risk the review flagged on iteration 1 (where the
    test re-declared tuples inline and could have quietly stopped
    validating a real keyword if production diverged)."""
    from batch.pipeline import (
        UNIT_KEYWORDS_FOURPLEX,
        UNIT_KEYWORDS_TRIPLEX,
        UNIT_KEYWORDS_DUPLEX,
    )
    haystack = description.lower()
    if any(k in haystack for k in UNIT_KEYWORDS_FOURPLEX):
        got = 4
    elif any(k in haystack for k in UNIT_KEYWORDS_TRIPLEX):
        got = 3
    elif any(k in haystack for k in UNIT_KEYWORDS_DUPLEX):
        got = 2
    else:
        got = None
    assert got == expected_units, (
        f"{description!r} → got {got}, expected {expected_units}"
    )


# ---------------------------------------------------------------------------
# Sprint 16.6 Bundle 1C — _coerce_analysis unitsInferred clamping
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def coerce_analysis():
    """Import batch.llm._coerce_analysis once per module."""
    from batch import llm
    return llm._coerce_analysis


def test_coerce_unitsInferred_missing_entirely(coerce_analysis):
    """Bundle 1C: missing unitsInferred should default to {value=None, conf=0}."""
    out = coerce_analysis({"rehabBand": {}})
    assert out["unitsInferred"]["value"] is None
    assert out["unitsInferred"]["confidence"] == 0.0


def test_coerce_unitsInferred_hallucinated_value(coerce_analysis):
    """Bundle 1C: value=50 gets clamped to 20 (max), not accepted as-is."""
    out = coerce_analysis({"unitsInferred": {"value": 50, "confidence": 0.9}})
    assert out["unitsInferred"]["value"] == 20


def test_coerce_unitsInferred_string_value(coerce_analysis):
    """Bundle 1C: string '3' coerces to int 3."""
    out = coerce_analysis({"unitsInferred": {"value": "3", "confidence": 0.8}})
    assert out["unitsInferred"]["value"] == 3


def test_coerce_unitsInferred_negative_value(coerce_analysis):
    """Bundle 1C: negative values are clamped up to 1 (lower bound)."""
    out = coerce_analysis({"unitsInferred": {"value": -1, "confidence": 0.5}})
    assert out["unitsInferred"]["value"] == 1


def test_coerce_unitsInferred_confidence_clamp(coerce_analysis):
    """Bundle 1C: confidence=99 clamps to 1.0, -5 clamps to 0.0."""
    out = coerce_analysis({"unitsInferred": {"value": 2, "confidence": 99}})
    assert out["unitsInferred"]["confidence"] == 1.0
    out2 = coerce_analysis({"unitsInferred": {"value": 2, "confidence": -5}})
    assert out2["unitsInferred"]["confidence"] == 0.0


def test_coerce_unitsInferred_non_dict_payload(coerce_analysis):
    """Bundle 1C: if LLM returns unitsInferred as a list/string, we default
    to a safe empty shape rather than AttributeError."""
    for bad in ([], "4 units", 3, True):
        out = coerce_analysis({"unitsInferred": bad})
        assert out["unitsInferred"]["value"] is None
        assert out["unitsInferred"]["confidence"] == 0.0


def test_coerce_unitsInferred_none_value_preserved(coerce_analysis):
    """Bundle 1C: explicit value=None stays None, doesn't get clamped."""
    out = coerce_analysis({"unitsInferred": {"value": None, "confidence": 0.3}})
    assert out["unitsInferred"]["value"] is None
    assert out["unitsInferred"]["confidence"] == 0.3


# ---------------------------------------------------------------------------
# Sprint 17 Bundle 1 — vision output field removed from schema
# ---------------------------------------------------------------------------
# Contract: the LLM schema no longer requests vision.* fields. The LLM
# still reads property photos (Anthropic Vision) to inform roofAgeYears,
# rehabBand, and riskFlags, but we stop paying for the ~250 output
# tokens of prose describing what it saw. Zero downstream consumers
# existed for those fields (audit: no refs in verdict.py / ranking.py /
# pipeline.py / index.html).


def test_default_analysis_has_no_vision_field():
    """Bundle 1: default_llm_analysis must not carry the vision object —
    removed to cut output token cost."""
    from batch import llm
    d = llm.default_llm_analysis(failed=False)
    assert "vision" not in d, (
        "vision field should be removed from the schema; remove from "
        "default_llm_analysis too so the coerce merge doesn't re-inject it"
    )


def test_default_analysis_preserves_other_fields():
    """Bundle 1: removing vision must not break any sibling field."""
    from batch import llm
    d = llm.default_llm_analysis(failed=False)
    # Core fields consumed downstream by verdict/ranking:
    for key in (
        "roofAgeYears", "rehabBand", "motivationSignals", "riskFlags",
        "insuranceUplift", "aduPotential", "unitsInferred",
        "narrativeForRanking",
    ):
        assert key in d, f"missing {key} after vision removal"
    # rehabBand still has all 6 categories:
    for cat in ("roof", "plumbing", "electrical", "cosmetic", "hvac", "other"):
        assert cat in d["rehabBand"], f"rehabBand missing {cat}"
    # riskFlags still has all 5 flags:
    for flag in (
        "foundationConcern", "galvanizedPlumbing", "knobAndTubeElectrical",
        "flatRoof", "unpermittedAdu",
    ):
        assert flag in d["riskFlags"], f"riskFlags missing {flag}"


def test_coerce_analysis_ignores_vision_if_llm_emits_it(coerce_analysis):
    """Bundle 1: if a stale LLM response still contains a vision object
    (maybe from an older cache), coerce should NOT crash — just leave
    it alone or ignore it. The removed-from-schema prompt tells the
    LLM not to emit it going forward, but defensive resilience matters
    for in-flight/cached responses during the transition."""
    out = coerce_analysis({
        "vision": {"exteriorCondition": "stale data from old cache"},
        "rehabBand": {"roof": {"low": 1000, "mid": 2000, "high": 3000}},
    })
    # Should not throw, should not add vision to the canonical schema:
    assert "rehabBand" in out
    # If vision is passed through as-is (belt-and-suspenders), that's
    # fine — downstream code was already ignoring it. If filtered out,
    # also fine. What we MUST NOT do is crash.
    # (No assertion on vision presence/absence — either is acceptable.)


# ---------------------------------------------------------------------------
# Sprint 17 Bundle 1 — _pre_llm_hard_fail gate coverage
# ---------------------------------------------------------------------------
# Each gate must fire on its own deterministic structural failure AND
# return None on the happy path. Tests exercise the four gate branches
# plus a baseline pass-through. Fixtures use minimal scrape/enrichment
# dicts so the tests don't depend on scrape internals.


@pytest.fixture(scope="module")
def pre_llm_hard_fail():
    """Import batch.pipeline._pre_llm_hard_fail once per module.

    Guard against the arm64 venv issue by importing only the helper,
    not app.py — pipeline.py pulls llm.py + verdict.py which are all
    pure-python and safe to import in the test venv.
    """
    from batch import pipeline
    return pipeline._pre_llm_hard_fail


def test_pre_llm_happy_path_returns_none(pre_llm_hard_fail):
    """Baseline: a normal duplex under ceiling, within commute,
    non-excluded ZIP — should NOT be skipped. LLM gets the call."""
    result = pre_llm_hard_fail(
        scrape={
            "address": "123 Main St, Vallejo, CA 94590",
            "price": 500000, "units": 2, "units_source": "keyword_duplex",
            "beds": 4, "baths": 2,
        },
        enrichment_row={"lat": 38.1041, "lng": -122.2567},
        zip_code="94590",
    )
    assert result is None, f"happy path should pass LLM; got {result}"


def test_pre_llm_excluded_zip(pre_llm_hard_fail):
    """Gate 1a: excluded ZIP fires `excluded_zip` gate."""
    # 94803 is in spec.zipTiers.excludedZips per the default profile.
    result = pre_llm_hard_fail(
        scrape={"address": "1 Fake St, Richmond, CA 94803", "price": 500000, "units": 2, "units_source": "ldjson_numberOfUnits"},
        enrichment_row=None,
        zip_code="94803",
    )
    assert result is not None
    assert result["gate"] == "excluded_zip"
    assert "94803" in result["reason"]


def test_pre_llm_single_unit(pre_llm_hard_fail):
    """Gate 2: confirmed single-unit property (real units_source
    signal) fires `single_unit` gate regardless of toggles. Mirrors
    verdict.py:u<=1 hard-fail which is unconditional."""
    result = pre_llm_hard_fail(
        scrape={
            "address": "123 Condo Way UNIT 5, Vallejo, CA 94590",
            "price": 400000, "units": 1, "units_source": "address_suffix",
        },
        enrichment_row={"lat": 38.1041, "lng": -122.2567},
        zip_code="94590",
    )
    assert result is not None
    assert result["gate"] == "single_unit"


def test_pre_llm_single_unit_no_source_passes(pre_llm_hard_fail):
    """Gate 2 edge: units=1 but units_source is empty → not confident,
    let LLM see it (maybe ADU candidate, maybe scraper misread).

    Fix per PR #47 review: the old check was tautological; now we
    correctly gate on units_source being a real signal."""
    result = pre_llm_hard_fail(
        scrape={
            "address": "123 Main St, Vallejo, CA 94590",
            "price": 400000, "units": 1, "units_source": "",
        },
        enrichment_row={"lat": 38.1041, "lng": -122.2567},
        zip_code="94590",
    )
    assert result is None, (
        "units=1 with empty units_source is ambiguous — LLM should get "
        "the call; pre-LLM skip should not fire"
    )


def test_pre_llm_price_ceiling_over(pre_llm_hard_fail):
    """Gate 3: price > duplex ceiling by >10% fires `price_ceiling`
    gate. Uses _classify_overage's RED boundary — YELLOW band still
    reaches the LLM."""
    # Jose's priceCeilingDuplex = $525K per profile. 10% overage
    # boundary = $577.5K. $700K sits comfortably above the RED gate.
    result = pre_llm_hard_fail(
        scrape={
            "address": "1 Main St, Vallejo, CA 94590",
            "price": 700000, "units": 2, "units_source": "keyword_duplex",
        },
        enrichment_row={"lat": 38.1041, "lng": -122.2567},
        zip_code="94590",
    )
    assert result is not None
    assert result["gate"] == "price_ceiling"
    assert "700" in result["reason"]


def test_pre_llm_price_yellow_band_passes(pre_llm_hard_fail):
    """Gate 3 edge: price in the YELLOW band (≤10% over ceiling) must
    NOT be skipped — yellow/red nuance needs LLM to land correctly."""
    # priceCeilingDuplex = $525K; $550K is ~4.7% over (yellow band).
    result = pre_llm_hard_fail(
        scrape={
            "address": "1 Main St, Vallejo, CA 94590",
            "price": 550000, "units": 2, "units_source": "keyword_duplex",
        },
        enrichment_row={"lat": 38.1041, "lng": -122.2567},
        zip_code="94590",
    )
    assert result is None, (
        "yellow-band price (within 10% over ceiling) should let LLM "
        "run — the verdict nuance between GREEN/YELLOW matters"
    )


def test_pre_llm_unknown_units_no_skip(pre_llm_hard_fail):
    """Gate 3 edge: units=None means the scraper couldn't determine it;
    we can't pick the right ceiling, so skip the price gate entirely
    and let the LLM + duplex-assumption math handle it downstream."""
    # Price $700K would fail duplex ceiling, but units is unknown.
    result = pre_llm_hard_fail(
        scrape={
            "address": "1 Main St, Vallejo, CA 94590",
            "price": 700000, "units": None, "units_source": "",
        },
        enrichment_row={"lat": 38.1041, "lng": -122.2567},
        zip_code="94590",
    )
    assert result is None, (
        "units=None means we can't pick the unit-adjusted ceiling; "
        "let LLM + downstream duplex-assumed math handle it"
    )


# ---------------------------------------------------------------------------
# Sprint 17 Bundle 2 — Retry-After header parsing
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def parse_retry_after():
    from batch import llm
    return llm._parse_retry_after


def test_retry_after_integer_seconds(parse_retry_after):
    """Integer-seconds form per RFC 7231 §7.1.3."""
    assert parse_retry_after("30") == 30.0
    assert parse_retry_after("0") == 0.0
    assert parse_retry_after("120.5") == 120.5


def test_retry_after_http_date(parse_retry_after):
    """HTTP-date form — 'delta from now' calculation."""
    # One second in the future expressed as HTTP-date — should
    # parse to a small positive delta, not the absolute timestamp.
    import email.utils
    from datetime import datetime, timezone, timedelta
    future = datetime.now(timezone.utc) + timedelta(seconds=10)
    http_date = email.utils.format_datetime(future)
    parsed = parse_retry_after(http_date)
    assert parsed is not None
    assert 5 < parsed < 15, (
        f"expected ~10s delta, got {parsed}s — parser should compute "
        "time-from-now, not return absolute epoch seconds"
    )


def test_retry_after_past_date_returns_zero(parse_retry_after):
    """HTTP-date in the past should clamp to 0 (retry immediately)."""
    import email.utils
    from datetime import datetime, timezone, timedelta
    past = datetime.now(timezone.utc) - timedelta(seconds=30)
    parsed = parse_retry_after(email.utils.format_datetime(past))
    assert parsed == 0.0


def test_retry_after_malformed_returns_none(parse_retry_after):
    """Garbage input falls through to exp backoff (None signal)."""
    for bad in (None, "", "not a date or number", "2026-13-45"):
        assert parse_retry_after(bad) is None, f"{bad!r} should return None"


def test_retry_after_whitespace_tolerant(parse_retry_after):
    """Some servers emit the value with surrounding whitespace."""
    assert parse_retry_after("  30  ") == 30.0


# ---------------------------------------------------------------------------
# Sprint 17 Bundle 2 — global LLM semaphore + extended cache wiring
# ---------------------------------------------------------------------------


def test_llm_concurrency_env_default():
    """BATCH_LLM_CONCURRENCY defaults to 5 when unset, and clamps
    bad input to a safe fallback rather than deadlocking the
    semaphore (review P0 fix on PR #48)."""
    from batch import llm
    # Sanity on the parsed value — always >= 1 regardless of env
    # input, never 0 or negative.
    assert llm._LLM_CONCURRENCY >= 1, "concurrency must be at least 1"


def test_llm_parse_concurrency_safety():
    """_parse_concurrency guards against 0/negative/malformed env."""
    import os
    from batch import llm
    # Monkeypatch the env lookup at call time.
    original = os.environ.get("BATCH_LLM_CONCURRENCY")
    try:
        for raw, expected_min in [
            ("0", 1),        # zero would deadlock — clamp to 1
            ("-5", 1),       # negative — clamp to 1
            ("not_a_number", 5),  # garbage — fallback to default
            ("", 5),         # empty string — fallback to default
            ("3", 3),        # valid — return as-is
            ("12", 12),      # valid high value — no upper clamp
        ]:
            os.environ["BATCH_LLM_CONCURRENCY"] = raw
            assert llm._parse_concurrency() == expected_min, (
                f"_parse_concurrency({raw!r}) should return {expected_min}"
            )
    finally:
        if original is None:
            os.environ.pop("BATCH_LLM_CONCURRENCY", None)
        else:
            os.environ["BATCH_LLM_CONCURRENCY"] = original


def test_llm_sem_lazy_binding():
    """_get_llm_sem defers construction until first call, so the
    semaphore binds to the request-handling loop rather than
    whatever loop may have been current at module import time
    (review P0 fix)."""
    import asyncio
    from batch import llm
    # Reset the cached instance so we can observe first-creation.
    llm._LLM_SEM_CACHED = None
    # Inside a running event loop, _get_llm_sem should create and
    # return the semaphore.
    async def _probe():
        sem = llm._get_llm_sem()
        assert sem is not None
        assert isinstance(sem, asyncio.Semaphore)
        # Second call returns the SAME instance.
        assert llm._get_llm_sem() is sem
    asyncio.run(_probe())


def test_llm_extended_cache_beta_header_constant():
    """Extended 1-hour cache beta feature name is stable."""
    from batch import llm
    assert llm._EXTENDED_CACHE_BETA == "extended-cache-ttl-2025-04-11", (
        "beta header string must match Anthropic's published identifier"
    )


def test_llm_retry_constants_reasonable():
    """Retry policy should be bounded — no infinite loops, no
    backoffs longer than the typical rate-limit window."""
    from batch import llm
    assert 3 <= llm._LLM_MAX_RETRIES <= 10
    assert 1.0 <= llm._LLM_BACKOFF_MIN_S <= 10.0
    assert llm._LLM_BACKOFF_MAX_S <= 120.0
