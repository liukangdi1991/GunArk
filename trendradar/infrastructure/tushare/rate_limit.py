"""Global Tushare request rate limiter (token bucket).

Ceiling: 270 requests/minute. The real per-minute limit for this token tier
was measured at 300/min (not the 500/min in upstream docs); 270 keeps a 10%
margin. Only the submit pace is throttled; in-flight concurrency is bounded
by the thread pool size, so network speed cannot exceed the ceiling.
"""

from __future__ import annotations

import threading
import time


class TokenBucket:
    def __init__(self, rate_per_min: float = 270, burst: int = 270) -> None:
        self._tokens = float(burst)
        self._capacity = float(burst)
        self._rate = rate_per_min / 60.0  # tokens per second
        self._updated = time.monotonic()
        self._lock = threading.Lock()

    def _refill(self) -> None:
        now = time.monotonic()
        self._tokens = min(self._capacity, self._tokens + (now - self._updated) * self._rate)
        self._updated = now

    def acquire(self, timeout: float = 60.0, cancel_check=None) -> bool:
        """Block until a token is available; return False on cancel/timeout."""
        deadline = time.monotonic() + timeout
        while True:
            with self._lock:
                self._refill()
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return True
            if cancel_check and cancel_check():
                return False
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.05)
