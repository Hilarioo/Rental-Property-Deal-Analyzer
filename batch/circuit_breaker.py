"""Sprint 9-3 — in-memory circuit breaker for free external APIs.

One breaker per upstream source (FEMA, Cal Fire, Overpass, Freddie Mac, …).
Open the circuit after ``failure_threshold`` consecutive failures; after
``cooldown_seconds`` elapse the next call is allowed in a half-open
state. A success closes the circuit; a failure re-opens it and resets
the cooldown clock.

Why bother: when FEMA is down for an hour, every batch otherwise pays a
5s timeout per URL before falling back. With the breaker, after 5
consecutive fails we short-circuit for the next 5 minutes — batches
stay snappy and we still auto-recover without operator intervention.

Not applied to Anthropic (has its own rate/retry handling) or to Redfin
scraping (load-bearing — if it's down, the whole pipeline should pause).

State machine:

    closed ──5 fails──► open ──cooldown elapsed──► half-open
       ▲                 │                            │
       │                 │                            ├── success ──► closed
       │                 │                            └── failure ──► open (cooldown reset)
       └─────────────────┴── success at any time ──► closed
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable, Literal

BreakerState = Literal["closed", "open", "half_open"]


@dataclass
class BreakerSnapshot:
    """Plain-data view of a breaker for the /api/source-health endpoint."""
    name: str
    state: BreakerState
    failures: int
    cooldown_until: float | None  # monotonic time, or None if not open


class CircuitBreaker:
    """Thread-safe breaker. One instance per upstream source.

    Uses ``time.monotonic`` by default; tests can inject a fake clock via
    the ``clock`` parameter so we don't sleep 300 seconds in CI.
    """

    def __init__(
        self,
        name: str,
        *,
        failure_threshold: int = 5,
        cooldown_seconds: float = 300.0,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.name = name
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self._clock = clock or time.monotonic
        self._lock = threading.Lock()
        self._state: BreakerState = "closed"
        self._consecutive_failures = 0
        self._opened_at: float | None = None

    # -- Decision gate -----------------------------------------------------

    def before_call(self) -> bool:
        """Return True if the caller should proceed with the upstream call.

        When ``False`` the caller must treat this like an upstream failure
        (e.g. `{ok: False, error: "circuit_open"}`) without actually
        making the request.
        """
        with self._lock:
            if self._state == "closed":
                return True
            if self._state == "open":
                # Check cooldown. When it elapses we silently transition
                # to half-open and allow exactly this one probe.
                if (
                    self._opened_at is not None
                    and self._clock() - self._opened_at >= self.cooldown_seconds
                ):
                    self._state = "half_open"
                    return True
                return False
            # half_open: we've already allowed the probe; deny concurrent probes.
            return False

    # -- Outcome reporting -------------------------------------------------

    def record_success(self) -> None:
        with self._lock:
            self._state = "closed"
            self._consecutive_failures = 0
            self._opened_at = None

    def record_failure(self) -> None:
        with self._lock:
            self._consecutive_failures += 1
            # A half-open probe that fails re-opens the circuit and resets
            # the cooldown clock.
            if self._state == "half_open":
                self._state = "open"
                self._opened_at = self._clock()
                return
            if (
                self._state == "closed"
                and self._consecutive_failures >= self.failure_threshold
            ):
                self._state = "open"
                self._opened_at = self._clock()

    # -- Introspection -----------------------------------------------------

    def snapshot(self) -> BreakerSnapshot:
        with self._lock:
            cooldown_until: float | None = None
            if self._state == "open" and self._opened_at is not None:
                cooldown_until = self._opened_at + self.cooldown_seconds
            return BreakerSnapshot(
                name=self.name,
                state=self._state,
                failures=self._consecutive_failures,
                cooldown_until=cooldown_until,
            )


# -- Registry --------------------------------------------------------------

_BREAKERS: dict[str, CircuitBreaker] = {}
_REGISTRY_LOCK = threading.Lock()


def get_breaker(name: str, **kwargs) -> CircuitBreaker:
    """Lazy-init a per-name singleton breaker.

    ``**kwargs`` are forwarded to ``CircuitBreaker.__init__`` on first
    creation only; subsequent calls return the existing instance and
    ignore overrides (that way the config lives with the first caller).
    """
    with _REGISTRY_LOCK:
        br = _BREAKERS.get(name)
        if br is None:
            br = CircuitBreaker(name, **kwargs)
            _BREAKERS[name] = br
        return br


def all_breakers() -> list[CircuitBreaker]:
    """Return a snapshot of every registered breaker."""
    with _REGISTRY_LOCK:
        return list(_BREAKERS.values())


def _reset_for_tests() -> None:
    """Clear the registry. Test-only."""
    with _REGISTRY_LOCK:
        _BREAKERS.clear()
