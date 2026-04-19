"""Sprint 9-1 — Python-side verdict fixture tests.

Loads the shared fixtures from ``tests/fixtures/verdict_parity.json`` and
asserts the Python implementation (`batch.verdict.compute_jose_verdict`)
produces:

- a verdict in {'green','yellow','red'}
- at most 3 reason strings (verdict contract)
- the ``expected_verdict`` listed on the fixture, when provided

The JS half of parity is covered by ``scripts/verdict_parity_check.mjs``
(which runs as part of ``make test``). This file does NOT shell out to
Node — that would defeat the point of a fast pytest.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from batch.verdict import compute_jose_verdict  # noqa: E402

FIXTURE_PATH = REPO_ROOT / "tests" / "fixtures" / "verdict_parity.json"


def _load_fixtures() -> list[dict]:
    with FIXTURE_PATH.open("r", encoding="utf-8") as f:
        fixtures = json.load(f)
    assert isinstance(fixtures, list) and fixtures, "fixtures file empty"
    return fixtures


FIXTURES = _load_fixtures()


def test_fixture_file_loads_and_has_expected_shape():
    """Guardrail: fixture file exists and every entry has name + ctx."""
    assert len(FIXTURES) >= 25, f"expected >=25 fixtures, got {len(FIXTURES)}"
    for fx in FIXTURES:
        assert "name" in fx and isinstance(fx["name"], str)
        assert "ctx" in fx and isinstance(fx["ctx"], dict)


@pytest.mark.parametrize(
    "fx", FIXTURES, ids=[fx["name"] for fx in FIXTURES]
)
def test_fixture_verdict_contract(fx):
    """Each fixture must produce a well-formed verdict response."""
    res = compute_jose_verdict(fx["ctx"])
    assert isinstance(res, dict)
    assert res["verdict"] in {"green", "yellow", "red"}
    assert isinstance(res["reasons"], list)
    assert len(res["reasons"]) <= 3, (
        f"verdict contract: >=3 reasons cap violated "
        f"({len(res['reasons'])} on {fx['name']})"
    )
    for r in res["reasons"]:
        assert isinstance(r, str) and r, "reasons must be non-empty strings"


@pytest.mark.parametrize(
    "fx",
    [fx for fx in FIXTURES if "expected_verdict" in fx],
    ids=[fx["name"] for fx in FIXTURES if "expected_verdict" in fx],
)
def test_fixture_expected_verdict(fx):
    """Where a fixture pins an `expected_verdict`, the Python verdict matches."""
    res = compute_jose_verdict(fx["ctx"])
    assert res["verdict"] == fx["expected_verdict"], (
        f"{fx['name']}: expected {fx['expected_verdict']}, "
        f"got {res['verdict']} (reasons={res['reasons']})"
    )
