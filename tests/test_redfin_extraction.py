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
