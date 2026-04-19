"""Sprint 9-2 — boundary tests for `batch.llm.is_cache_stale`.

The staleness contract uses `>=` for all three thresholds (price delta,
DOM increase, cache age). These tests are the executable documentation
of that contract: if a future edit flips a comparator back to `>`, one
of these boundary assertions trips immediately.

Thresholds (spec-side, from USER_PROFILE §L.1):
- Price change: >= 3.00% of prior price  → stale ("price_changed")
- DOM increase: >= 14 days                → stale ("dom_increased")
- Cache age:    >= 30.00 days             → stale ("cache_age_exceeded")

All tests use a fixed `now_utc` so cache-age boundaries don't flake on
wall-clock drift.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from batch.llm import is_cache_stale  # noqa: E402


# Fixed "now" — UTC, explicitly aware, to keep cache-age math deterministic.
NOW = datetime(2026, 4, 18, 12, 0, 0, tzinfo=timezone.utc)


def _cached_row(
    *,
    last_price: int | None = 500_000,
    last_dom: int | None = 10,
    analyzed_at_days_ago: float | None = 5.0,
) -> dict:
    """Build a cache row fixture anchored to ``NOW``."""
    row: dict = {
        "llm_analysis": {"dummy": True},  # truthy → cache hit path
        "last_price": last_price,
        "last_dom": last_dom,
    }
    if analyzed_at_days_ago is not None:
        analyzed = NOW - timedelta(days=analyzed_at_days_ago)
        row["llm_analyzed_at"] = analyzed.strftime("%Y-%m-%dT%H:%M:%SZ")
    return row


# ---------------------------------------------------------------------------
# No cache → always stale
# ---------------------------------------------------------------------------


def test_new_url_is_stale():
    assert is_cache_stale(
        cached_row=None, fresh_price=500_000, fresh_dom=10, now_utc=NOW
    ) == (True, "new_url")


def test_missing_llm_analysis_is_stale():
    row = {"last_price": 500_000, "last_dom": 10}
    assert is_cache_stale(
        cached_row=row, fresh_price=500_000, fresh_dom=10, now_utc=NOW
    ) == (True, "new_url")


# ---------------------------------------------------------------------------
# Price-change boundary: `>= 3%` triggers stale
# ---------------------------------------------------------------------------


def test_price_change_below_threshold_is_fresh():
    # 2.99% → not stale (on the fresh side of the cliff)
    row = _cached_row(last_price=500_000)
    fresh = 500_000 + int(round(500_000 * 0.0299))  # +$14,950 = +2.99%
    stale, reason = is_cache_stale(
        cached_row=row, fresh_price=fresh, fresh_dom=10, now_utc=NOW
    )
    assert stale is False and reason is None


def test_price_change_at_threshold_is_stale():
    # Exactly 3.00% → stale. This is the behavior change from Sprint 9-2.
    row = _cached_row(last_price=500_000)
    fresh = 500_000 + int(500_000 * 0.03)  # +$15,000 exact
    stale, reason = is_cache_stale(
        cached_row=row, fresh_price=fresh, fresh_dom=10, now_utc=NOW
    )
    assert stale is True and reason == "price_changed"


def test_price_change_above_threshold_is_stale():
    row = _cached_row(last_price=500_000)
    fresh = 500_000 + int(500_000 * 0.0301)  # +3.01%
    stale, reason = is_cache_stale(
        cached_row=row, fresh_price=fresh, fresh_dom=10, now_utc=NOW
    )
    assert stale is True and reason == "price_changed"


def test_price_drop_symmetric():
    """abs() means a 3% drop is as stale as a 3% rise."""
    row = _cached_row(last_price=500_000)
    fresh = 500_000 - int(500_000 * 0.03)  # -$15,000 exact → -3.0%
    stale, reason = is_cache_stale(
        cached_row=row, fresh_price=fresh, fresh_dom=10, now_utc=NOW
    )
    assert stale is True and reason == "price_changed"


# ---------------------------------------------------------------------------
# DOM-increase boundary: `>= 14 days` triggers stale
# ---------------------------------------------------------------------------


def test_dom_increase_13_days_is_fresh():
    row = _cached_row(last_dom=10)
    stale, reason = is_cache_stale(
        cached_row=row, fresh_price=500_000, fresh_dom=23, now_utc=NOW  # +13
    )
    assert stale is False and reason is None


def test_dom_increase_14_days_is_stale():
    row = _cached_row(last_dom=10)
    stale, reason = is_cache_stale(
        cached_row=row, fresh_price=500_000, fresh_dom=24, now_utc=NOW  # +14
    )
    assert stale is True and reason == "dom_increased"


def test_dom_increase_15_days_is_stale():
    row = _cached_row(last_dom=10)
    stale, reason = is_cache_stale(
        cached_row=row, fresh_price=500_000, fresh_dom=25, now_utc=NOW  # +15
    )
    assert stale is True and reason == "dom_increased"


# ---------------------------------------------------------------------------
# Cache-age boundary: `>= 30 days` triggers stale
# ---------------------------------------------------------------------------


def test_cache_age_29_9_days_is_fresh():
    row = _cached_row(analyzed_at_days_ago=29.9)
    stale, reason = is_cache_stale(
        cached_row=row, fresh_price=500_000, fresh_dom=10, now_utc=NOW
    )
    assert stale is False and reason is None


def test_cache_age_exactly_30_days_is_stale():
    row = _cached_row(analyzed_at_days_ago=30.0)
    stale, reason = is_cache_stale(
        cached_row=row, fresh_price=500_000, fresh_dom=10, now_utc=NOW
    )
    assert stale is True and reason == "cache_age_exceeded"


def test_cache_age_30_1_days_is_stale():
    row = _cached_row(analyzed_at_days_ago=30.1)
    stale, reason = is_cache_stale(
        cached_row=row, fresh_price=500_000, fresh_dom=10, now_utc=NOW
    )
    assert stale is True and reason == "cache_age_exceeded"


# ---------------------------------------------------------------------------
# Happy path: everything fresh
# ---------------------------------------------------------------------------


def test_all_fresh_returns_not_stale():
    row = _cached_row(last_price=500_000, last_dom=10, analyzed_at_days_ago=5.0)
    stale, reason = is_cache_stale(
        cached_row=row, fresh_price=501_000, fresh_dom=11, now_utc=NOW
    )
    assert stale is False and reason is None
