"""Sprint 9-3 — circuit breaker state-machine tests.

Uses an injectable clock (no real sleeps) so the 5-minute cooldown
elapses in microseconds. Each test builds its own breaker — no shared
registry state leaking across tests.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from batch.circuit_breaker import (  # noqa: E402
    CircuitBreaker,
    _reset_for_tests,
    all_breakers,
    get_breaker,
)


class FakeClock:
    """Monotonic-style clock you can manually advance."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _make(clock: FakeClock, **overrides) -> CircuitBreaker:
    return CircuitBreaker(
        "test",
        failure_threshold=5,
        cooldown_seconds=300.0,
        clock=clock,
        **overrides,
    )


def test_starts_closed_and_allows_calls():
    clock = FakeClock()
    br = _make(clock)
    snap = br.snapshot()
    assert snap.state == "closed"
    assert snap.failures == 0
    assert snap.cooldown_until is None
    assert br.before_call() is True


def test_four_failures_stay_closed():
    """Under the threshold — still closed, still letting calls through."""
    clock = FakeClock()
    br = _make(clock)
    for _ in range(4):
        br.record_failure()
    assert br.snapshot().state == "closed"
    assert br.before_call() is True
    assert br.snapshot().failures == 4


def test_five_consecutive_failures_opens_circuit():
    clock = FakeClock()
    br = _make(clock)
    for _ in range(5):
        br.record_failure()
    snap = br.snapshot()
    assert snap.state == "open"
    assert snap.failures == 5
    # Cooldown is parked 300s in the future.
    assert snap.cooldown_until == 300.0
    # And the gate denies calls.
    assert br.before_call() is False


def test_success_resets_failure_count():
    """A success mid-way back to 0 — six total failures with a success in
    between must NOT trip the breaker."""
    clock = FakeClock()
    br = _make(clock)
    for _ in range(3):
        br.record_failure()
    br.record_success()
    assert br.snapshot().failures == 0
    for _ in range(3):
        br.record_failure()
    assert br.snapshot().state == "closed"


def test_before_call_denies_while_open():
    clock = FakeClock()
    br = _make(clock)
    for _ in range(5):
        br.record_failure()
    # Repeated denies while in the cooldown window.
    for _ in range(10):
        assert br.before_call() is False
    clock.advance(299.9)
    assert br.before_call() is False


def test_cooldown_elapses_transitions_to_half_open():
    clock = FakeClock()
    br = _make(clock)
    for _ in range(5):
        br.record_failure()
    clock.advance(300.0)
    # First call after cooldown is allowed AND flips state to half_open.
    assert br.before_call() is True
    assert br.snapshot().state == "half_open"


def test_half_open_denies_concurrent_probes():
    """Only ONE probe runs in half-open — a second pre-outcome call is denied."""
    clock = FakeClock()
    br = _make(clock)
    for _ in range(5):
        br.record_failure()
    clock.advance(300.0)
    assert br.before_call() is True  # first probe allowed, now half_open
    assert br.before_call() is False  # concurrent probe denied


def test_success_in_half_open_closes_circuit():
    clock = FakeClock()
    br = _make(clock)
    for _ in range(5):
        br.record_failure()
    clock.advance(300.0)
    br.before_call()  # → half_open
    br.record_success()
    snap = br.snapshot()
    assert snap.state == "closed"
    assert snap.failures == 0
    assert br.before_call() is True


def test_failure_in_half_open_reopens_and_resets_cooldown():
    clock = FakeClock()
    br = _make(clock)
    for _ in range(5):
        br.record_failure()
    clock.advance(300.0)
    br.before_call()  # → half_open
    clock.advance(50.0)  # some time passes during the failed probe
    br.record_failure()
    snap = br.snapshot()
    assert snap.state == "open"
    # Cooldown clock restarts from the half-open failure time.
    assert snap.cooldown_until == 350.0 + 300.0
    # Still blocked until the NEW cooldown expires.
    assert br.before_call() is False
    clock.advance(299.0)
    assert br.before_call() is False
    clock.advance(2.0)  # clear 300s mark
    assert br.before_call() is True


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def test_registry_returns_same_instance_per_name():
    _reset_for_tests()
    a = get_breaker("some_source")
    b = get_breaker("some_source")
    assert a is b
    c = get_breaker("other_source")
    assert c is not a
    names = {br.name for br in all_breakers()}
    assert names == {"some_source", "other_source"}
    _reset_for_tests()
