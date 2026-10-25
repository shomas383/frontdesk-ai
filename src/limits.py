"""In-memory per-IP rate limiter for the API: a sliding 60-second window plus a per-day count.

State lives in this process only, so the API must run as a single uvicorn worker: with N workers each
keeps its own counts and a client effectively gets N times the limit. Move this to a shared store
(e.g. Postgres or Redis) before scaling out. Clients are keyed by an HMAC of the IP with a random
per-process salt, so raw IPs are never held in memory or written anywhere.
"""

from __future__ import annotations

import hashlib
import hmac
import math
import secrets
import threading
import time
from collections import deque
from collections.abc import Callable
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
WINDOW_S = 60.0
SWEEP_EVERY = 1000  # checks between sweeps of idle clients


def now_jst() -> datetime:
    return datetime.now(JST)


def seconds_until_midnight(now: datetime) -> int:
    midnight = datetime.combine(now.date() + timedelta(days=1), datetime.min.time(), tzinfo=now.tzinfo)
    return max(1, math.ceil((midnight - now).total_seconds()))


class RateLimiter:
    def __init__(
        self,
        per_minute: int,
        per_day: int,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], datetime] = now_jst,
    ) -> None:
        self.per_minute = per_minute
        self.per_day = per_day
        self._clock = clock
        self._wall_clock = wall_clock
        self._salt = secrets.token_bytes(16)
        self._lock = threading.Lock()
        self._windows: dict[str, deque[float]] = {}
        self._day: date | None = None
        self._day_counts: dict[str, int] = {}
        self._checks = 0

    def _key(self, client: str) -> str:
        return hmac.new(self._salt, client.encode("utf-8"), hashlib.sha256).hexdigest()

    def check(self, client: str) -> tuple[bool, int]:
        """Count one request from `client` if allowed. Returns (allowed, retry_after_seconds)."""
        key = self._key(client)
        now, wall = self._clock(), self._wall_clock()
        with self._lock:
            if wall.date() != self._day:
                self._day, self._day_counts = wall.date(), {}
            self._checks += 1
            if self._checks % SWEEP_EVERY == 0:
                self._sweep(now)

            window = self._windows.setdefault(key, deque())
            while window and now - window[0] >= WINDOW_S:
                window.popleft()
            if self._day_counts.get(key, 0) >= self.per_day:
                return False, seconds_until_midnight(wall)
            if len(window) >= self.per_minute:
                return False, max(1, math.ceil(WINDOW_S - (now - window[0])))
            window.append(now)
            self._day_counts[key] = self._day_counts.get(key, 0) + 1
            return True, 0

    def _sweep(self, now: float) -> None:
        idle = [k for k, w in self._windows.items() if not w or now - w[-1] >= WINDOW_S]
        for k in idle:
            del self._windows[k]
