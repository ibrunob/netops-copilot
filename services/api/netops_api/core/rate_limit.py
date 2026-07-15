"""Small, explicit rate-limiting primitive for the API's initial public surface."""

from __future__ import annotations

from dataclasses import dataclass
from threading import Lock
from time import monotonic


@dataclass(slots=True)
class _Window:
    started_at: float
    requests: int


class FixedWindowRateLimiter:
    """Thread-safe fixed-window limiter with bounded in-memory state.

    This is intentionally a baseline, not the production distributed limiter. It
    protects an individual API process while Redis ownership is introduced with the
    authenticated case APIs. Keys expire naturally and the map is pruned on writes.
    """

    def __init__(self, *, max_requests: int, window_seconds: int) -> None:
        self._max_requests = max_requests
        self._window_seconds = window_seconds
        self._windows: dict[str, _Window] = {}
        self._lock = Lock()

    def check(self, key: str) -> tuple[bool, int]:
        """Consume a request and return ``(allowed, retry_after_seconds)``."""
        now = monotonic()
        with self._lock:
            self._prune(now)
            window = self._windows.get(key)
            if window is None or now - window.started_at >= self._window_seconds:
                self._windows[key] = _Window(started_at=now, requests=1)
                return True, 0
            if window.requests >= self._max_requests:
                remaining = self._window_seconds - (now - window.started_at)
                return False, max(1, int(remaining) + 1)
            window.requests += 1
            return True, 0

    def _prune(self, now: float) -> None:
        expired_keys = [
            key
            for key, window in self._windows.items()
            if now - window.started_at >= self._window_seconds
        ]
        for key in expired_keys:
            del self._windows[key]
