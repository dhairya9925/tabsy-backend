"""In-memory sliding window rate limiter for mobile and web clients."""
import asyncio
import time
from collections import defaultdict


class SlidingWindowRateLimiter:
    """
    Sliding window rate limiter that tracks timestamps per client key.
    Enforces requests per 60-second window.
    """

    def __init__(self, limit: int = 120, window_seconds: int = 60):
        self.limit = limit
        self.window_seconds = window_seconds
        self._history: dict[str, list[float]] = defaultdict(list)
        self._lock = asyncio.Lock()

    async def check(self, key: str) -> tuple[bool, int, int, int]:
        """
        Checks whether the given client key is allowed to make a request.
        Returns:
            (allowed, limit, remaining, retry_after)
        """
        now = time.time()
        window_start = now - self.window_seconds

        async with self._lock:
            timestamps = self._history[key]
            # Prune timestamps older than window
            valid_timestamps = [t for t in timestamps if t > window_start]
            self._history[key] = valid_timestamps

            if len(valid_timestamps) >= self.limit:
                # Oldest timestamp determines when the next slot frees up
                oldest = valid_timestamps[0]
                retry_after = max(1, int(oldest + self.window_seconds - now))
                return False, self.limit, 0, retry_after

            valid_timestamps.append(now)
            remaining = self.limit - len(valid_timestamps)
            return True, self.limit, remaining, 0

    async def reset(self):
        """Clears all tracked client history (used in tests)."""
        async with self._lock:
            self._history.clear()


# Default global instance
rate_limiter = SlidingWindowRateLimiter()
