"""Sprint 10B-1 boundary tests for build_failures_envelope + _human_readable_reason.

The pipeline used to silently drop scrape-failure rows from the rendered
rankings table, so Jose never saw which URLs needed re-entry. The envelope
builder is the contract that keeps those rows visible — if this changes
shape, every 10B-1 UI assertion falls over.
"""
from __future__ import annotations

from batch.pipeline import build_failures_envelope, _human_readable_reason


def test_successful_rows_are_not_failures():
    rows = [
        {"url": "https://redfin.com/a", "canonical_url": "https://redfin.com/a",
         "scrape_ok": True, "hard_fail": False},
    ]
    assert build_failures_envelope(rows) == []


def test_hard_fail_with_scrape_ok_is_not_a_failure():
    # A row that scraped fine but tripped a hard verdict rule (DTI blown,
    # units unknown) belongs in rankings, NOT the retry list.
    rows = [
        {"url": "https://redfin.com/a", "canonical_url": "https://redfin.com/a",
         "scrape_ok": True, "hard_fail": True, "scrape_error": None},
    ]
    assert build_failures_envelope(rows) == []


def test_scrape_failure_surfaces_with_human_reason():
    rows = [
        {"url": "https://redfin.com/b", "canonical_url": "https://redfin.com/b",
         "scrape_ok": False, "scrape_error": "fetch_failed", "hard_fail": True},
    ]
    out = build_failures_envelope(rows)
    assert len(out) == 1
    assert out[0]["url"] == "https://redfin.com/b"
    assert out[0]["canonicalUrl"] == "https://redfin.com/b"
    assert out[0]["errorCode"] == "fetch_failed"
    assert out[0]["reason"] == "Could not reach the listing"


def test_unknown_error_code_passes_through_raw():
    # A new error code that we haven't taught the frontend yet shouldn't crash
    # the envelope — we just fall back to the raw string.
    rows = [
        {"url": "https://redfin.com/c", "canonical_url": "https://redfin.com/c",
         "scrape_ok": False, "scrape_error": "some_future_error", "hard_fail": True},
    ]
    out = build_failures_envelope(rows)
    assert out[0]["reason"] == "some_future_error"


def test_worker_exception_prefix_normalizes():
    # Worker exceptions have the form "worker_exception:ExceptionTypeName"
    # and we strip the suffix before the lookup so KeyError vs ValueError
    # both produce the same user-facing copy.
    assert _human_readable_reason("worker_exception:KeyError") == "Unexpected error while processing"
    assert _human_readable_reason("worker_exception:SomethingElse") == "Unexpected error while processing"


def test_none_reason_is_safe():
    assert _human_readable_reason(None) == "Unknown error"


def test_rate_limited_reason_copy():
    # Distinct wording so the Retry button UX can hint "try again soon"
    # without being misleading for a hard fetch failure.
    assert _human_readable_reason("rate_limited") == "Rate limited — try again in a minute"
