import asyncio
import time


class RateLimiter:
    """Spaces calls evenly so at most `per_minute` start in a minute (STC_LLM_REQUESTS_PER_MIN).

    Shared by every call of one adapter; safe to use from many concurrent tasks."""

    def __init__(self, per_minute: int) -> None:
        if per_minute < 1:
            raise ValueError("per_minute must be >= 1")
        self._interval = 60.0 / per_minute
        self._next = 0.0
        self._lock = asyncio.Lock()

    async def wait(self) -> None:
        async with self._lock:
            now = time.monotonic()
            delay = self._next - now
            self._next = max(now, self._next) + self._interval
        if delay > 0:
            await asyncio.sleep(delay)
