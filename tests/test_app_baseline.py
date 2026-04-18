"""BASELINE — pre-Jose-fix behavior.

Locks current behavior of pure helpers in app.py so Sprint 1+ changes can't
silently regress them. If any Sprint intentionally alters these helpers,
update the expected value here and add a comment linking the sprint.
"""
import sys
from pathlib import Path

# Make the repo root importable (we are in repo_root/tests/)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app  # noqa: E402


# --- _detect_source ---

def test_detect_source_redfin():
    assert app._detect_source("www.redfin.com") == "redfin"
    assert app._detect_source("redfin.com") == "redfin"


def test_detect_source_zillow():
    assert app._detect_source("www.zillow.com") == "zillow"
    assert app._detect_source("zillow.com") == "zillow"


def test_detect_source_unknown():
    assert app._detect_source("example.com") == "unknown"
    assert app._detect_source("") == "unknown"
    assert app._detect_source(None) == "unknown"


# --- _safe_get ---

def test_safe_get_nested_dict():
    assert app._safe_get({"a": {"b": {"c": 42}}}, "a", "b", "c") == 42


def test_safe_get_missing_key_returns_default():
    assert app._safe_get({"a": 1}, "b", default="fallback") == "fallback"


def test_safe_get_traverses_list_indices():
    assert app._safe_get({"items": [10, 20, 30]}, "items", 1) == 20


def test_safe_get_bad_path_returns_default():
    assert app._safe_get({"a": "notadict"}, "a", "b", default=None) is None


# --- _format_address ---

def test_format_address_full():
    addr = {
        "streetAddress": "705 State St",
        "city": "Vallejo",
        "state": "CA",
        "zipcode": "94590",
    }
    assert app._format_address(addr) == "705 State St, Vallejo, CA 94590"


def test_format_address_missing_state():
    addr = {"streetAddress": "123 Main St", "city": "Somewhere"}
    assert app._format_address(addr) == "123 Main St, Somewhere"


def test_format_address_none_returns_none():
    assert app._format_address(None) is None
    assert app._format_address({}) is None


# --- _extract_tax_history ---

def test_extract_tax_history_basic():
    raw = [
        {"time": 2024, "taxPaid": 5200},
        {"time": 2023, "taxPaid": 5000},
    ]
    result = app._extract_tax_history(raw)
    assert len(result) == 2
    assert result[0] == {"year": 2024, "amount": 5200}


def test_extract_tax_history_epoch_ms_converts_to_year():
    # Epoch ms for 2024-01-15 UTC
    raw = [{"time": 1705276800000, "taxPaid": 4500}]
    result = app._extract_tax_history(raw)
    assert len(result) == 1
    assert result[0]["year"] == 2024
    assert result[0]["amount"] == 4500


def test_extract_tax_history_handles_empty_and_bad_input():
    assert app._extract_tax_history([]) == []
    assert app._extract_tax_history(None) == []
    assert app._extract_tax_history("not a list") == []


# --- _get_image_url ---

def test_get_image_url_hires_first():
    prop = {
        "hiResImageLink": "https://cdn.example.com/hi.jpg",
        "responsivePhotos": [{"mixedSources": {"jpeg": [{"url": "https://lo.jpg", "width": 320}]}}],
    }
    assert app._get_image_url(prop) == "https://cdn.example.com/hi.jpg"


def test_get_image_url_falls_back_to_largest_jpeg():
    prop = {
        "responsivePhotos": [{
            "mixedSources": {
                "jpeg": [
                    {"url": "https://small.jpg", "width": 320},
                    {"url": "https://big.jpg", "width": 1600},
                    {"url": "https://mid.jpg", "width": 800},
                ]
            }
        }]
    }
    assert app._get_image_url(prop) == "https://big.jpg"


def test_get_image_url_returns_none_when_no_photos():
    assert app._get_image_url({}) is None
    assert app._get_image_url({"responsivePhotos": []}) is None


# --- _build_result ---

def test_build_result_from_zillow_property_shape():
    prop = {
        "address": {
            "streetAddress": "705 State St",
            "city": "Vallejo",
            "state": "CA",
            "zipcode": "94590",
        },
        "price": 535000,
        "bedrooms": 4,
        "bathrooms": 2,
        "livingArea": 1558,
        "yearBuilt": 1961,
        "homeType": "MULTI_FAMILY",
        "taxHistory": [{"time": 2024, "taxPaid": 5200}],
        "monthlyHoaFee": 0,
        "description": "duplex",
    }
    result = app._build_result(prop)
    assert result["address"] == "705 State St, Vallejo, CA 94590"
    assert result["price"] == 535000
    assert result["beds"] == 4
    assert result["baths"] == 2
    assert result["sqft"] == 1558
    assert result["yearBuilt"] == 1961
    assert result["propertyType"] == "MULTI_FAMILY"
    assert result["annualTax"] == 5200
    assert result["hoaFee"] == 0


def test_build_result_lot_size_string_parses_to_int():
    prop = {"lotSize": "6,000 sqft"}
    result = app._build_result(prop)
    assert result["lotSize"] == 6000
