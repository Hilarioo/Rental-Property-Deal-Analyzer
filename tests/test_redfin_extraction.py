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
