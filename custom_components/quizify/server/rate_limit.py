"""Shared sliding-window rate limiter (#260).

Two near-identical sliding-window limiters used to live inline in
``websocket.py`` (per-connection message flood guard, #169) and
``pack_submission.py`` (per-IP submit guard, #258). They are folded here into
one small, tested helper so the prune-and-evict logic lives in a single place.

The limiter is keyed on an arbitrary hashable (a WebSocket ``id()`` or a client
IP string). On every :meth:`check` the key's bucket is pruned of timestamps
older than ``window`` seconds; a bucket that ends up empty is dropped from the
backing dict so it can't accumulate one dead entry per key seen, forever. A
request that would exceed ``max_requests`` is rejected *without* recording its
timestamp, so a client hammering the limit can't keep its own window pinned
open.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Hashable


class SlidingWindowLimiter:
    """A sliding-window rate limiter shared across the server handlers.

    Parameters
    ----------
    max_requests:
        Maximum number of allowed requests within ``window`` seconds.
    window:
        Length of the sliding window, in seconds.
    clock:
        Monotonic time source returning seconds as a float. Defaults to
        :func:`time.monotonic`; injectable so tests can drive time directly.
    """

    def __init__(
        self,
        max_requests: int,
        window: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._max = max_requests
        self._window = window
        self._clock = clock
        self._buckets: dict[Hashable, list[float]] = {}

    def check(self, key: Hashable) -> bool:
        """Record a hit for ``key`` and report whether it is within the limit.

        Returns ``True`` if the request is allowed (and records its timestamp),
        ``False`` if it would exceed the limit (and records nothing). Empty
        buckets are evicted so the backing dict stays bounded by the number of
        *active* keys.
        """
        now = self._clock()
        cutoff = now - self._window
        bucket = [t for t in self._buckets.get(key, []) if t > cutoff]
        if not bucket:
            # Drop the empty bucket so the dict doesn't accumulate one entry
            # per key that was ever seen, forever (#258).
            self._buckets.pop(key, None)
        if len(bucket) >= self._max:
            # Over the limit: keep the (non-empty) pruned bucket so the window
            # stays accurate, but don't record this rejected request.
            self._buckets[key] = bucket
            return False
        bucket.append(now)
        self._buckets[key] = bucket
        return True

    def forget(self, key: Hashable) -> None:
        """Drop a key's window state (e.g. on disconnect)."""
        self._buckets.pop(key, None)
