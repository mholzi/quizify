"""Direct tests for the shared SlidingWindowLimiter (#260).

The limiter folds together the two near-identical sliding-window limiters that
used to live inline in websocket.py (per-connection flood guard) and
pack_submission.py (per-IP submit guard). These tests drive it with an
injected clock so the window, eviction, and rejection behaviour are exercised
deterministically.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from custom_components.quizify.server.rate_limit import (  # noqa: E402
    SlidingWindowLimiter,
)


class _Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def test_allows_up_to_the_limit_then_rejects() -> None:
    clock = _Clock()
    lim = SlidingWindowLimiter(max_requests=3, window=1.0, clock=clock)
    assert lim.check("ip") is True
    assert lim.check("ip") is True
    assert lim.check("ip") is True
    # 4th in the same window is rejected.
    assert lim.check("ip") is False


def test_rejected_request_is_not_recorded() -> None:
    """A rejected hit must not extend the window — the bucket stays at the cap."""
    clock = _Clock()
    lim = SlidingWindowLimiter(max_requests=2, window=1.0, clock=clock)
    lim.check("ip")
    lim.check("ip")
    assert lim.check("ip") is False
    assert lim.check("ip") is False
    assert len(lim._buckets["ip"]) == 2


def test_window_prunes_old_hits() -> None:
    clock = _Clock()
    lim = SlidingWindowLimiter(max_requests=2, window=1.0, clock=clock)
    assert lim.check("ip") is True
    assert lim.check("ip") is True
    assert lim.check("ip") is False
    # Advance past the window — old hits drop, capacity is free again.
    clock.t += 1.5
    assert lim.check("ip") is True


def test_unseen_key_has_no_bucket() -> None:
    """A key never checked leaves no entry — the dict is bounded by active keys."""
    lim = SlidingWindowLimiter(max_requests=2, window=1.0, clock=_Clock())
    assert "never" not in lim._buckets


def test_aged_out_bucket_does_not_accumulate() -> None:
    """A key whose hits all aged out is pruned (not accumulated) — after the
    window passes, a fresh allowed hit leaves exactly one timestamp, proving
    the stale ones were dropped rather than piling up forever (#258)."""
    clock = _Clock()
    lim = SlidingWindowLimiter(max_requests=2, window=1.0, clock=clock)
    lim.check("ip")
    lim.check("ip")
    assert len(lim._buckets["ip"]) == 2
    clock.t += 2.0  # everything ages out
    assert lim.check("ip") is True  # prunes stale, records the new hit
    assert lim._buckets["ip"] == [clock.t]


def test_distinct_keys_are_independent() -> None:
    clock = _Clock()
    lim = SlidingWindowLimiter(max_requests=1, window=1.0, clock=clock)
    assert lim.check("a") is True
    assert lim.check("b") is True  # different key, own window
    assert lim.check("a") is False


def test_forget_drops_key_state() -> None:
    clock = _Clock()
    lim = SlidingWindowLimiter(max_requests=1, window=1.0, clock=clock)
    lim.check("a")
    assert lim.check("a") is False
    lim.forget("a")
    # State gone → full capacity again.
    assert lim.check("a") is True
