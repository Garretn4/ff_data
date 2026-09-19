"""Read-only Sleeper API client with a token bucket and a SQLite TTL cache."""
from __future__ import annotations

import json
import time
from collections import deque
from typing import Any, Callable

import httpx

from app.data.store import Store

BASE_URL = "https://api.sleeper.app/v1"
PLAYERS_TTL = 86400.0
STATE_TTL = 3600.0
MAX_CALLS_PER_MINUTE = 600


class RateLimiter:
    """Allow at most max_calls per rolling window of per_seconds."""

    def __init__(
        self,
        max_calls: int = MAX_CALLS_PER_MINUTE,
        per_seconds: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._max = max_calls
        self._window = per_seconds
        self._clock = clock
        self._sleep = sleep
        self._calls: deque[float] = deque()

    def acquire(self) -> None:
        now = self._clock()
        while self._calls and now - self._calls[0] >= self._window:
            self._calls.popleft()
        if len(self._calls) >= self._max:
            self._sleep(self._window - (now - self._calls[0]))
        self._calls.append(self._clock())


class SleeperClient:
    def __init__(
        self,
        store: Store,
        http: httpx.Client | None = None,
        limiter: RateLimiter | None = None,
        clock: Callable[[], float] = time.time,
        refresh: bool = False,
    ) -> None:
        self._store = store
        self._http = http or httpx.Client(timeout=30.0)
        self._limiter = limiter or RateLimiter()
        self._clock = clock
        self._refresh = refresh
        self.errors: list[str] = []

    def _get(self, path: str, ttl: float | None) -> Any:
        """Return parsed JSON for path, honoring ttl (None = never expires unless refresh)."""
        key = f"sleeper:{path}"
        now = self._clock()
        use_cache = not (self._refresh and ttl is None)
        cached = self._store.get_cached(key, ttl, now) if use_cache else None
        if cached is not None:
            return json.loads(cached)
        try:
            self._limiter.acquire()
            response = self._http.get(f"{BASE_URL}{path}")
            response.raise_for_status()
        except httpx.HTTPError as exc:
            stale = self._store.get_cached(key, None, now)
            if stale is None:
                raise
            self.errors.append(f"{path}: {exc.__class__.__name__}: {exc}")
            return json.loads(stale)
        self._store.put_cached(key, response.text, now)
        return response.json()

    def state(self) -> dict[str, Any]:
        return self._get("/state/nfl", STATE_TTL)

    def league(self, league_id: str) -> dict[str, Any]:
        return self._get(f"/league/{league_id}", None)

    def users(self, league_id: str) -> list[dict[str, Any]]:
        return self._get(f"/league/{league_id}/users", None)

    def rosters(self, league_id: str) -> list[dict[str, Any]]:
        return self._get(f"/league/{league_id}/rosters", None)

    def matchups(self, league_id: str, week: int) -> list[dict[str, Any]]:
        return self._get(f"/league/{league_id}/matchups/{week}", None)

    def transactions(self, league_id: str, week: int) -> list[dict[str, Any]]:
        return self._get(f"/league/{league_id}/transactions/{week}", None)

    def traded_picks(self, league_id: str) -> list[dict[str, Any]]:
        return self._get(f"/league/{league_id}/traded_picks", None)

    def players(self) -> dict[str, dict[str, Any]]:
        return self._get("/players/nfl", PLAYERS_TTL)

    def user(self, username: str) -> dict[str, Any] | None:
        """Sleeper user by username; None when Sleeper does not know the name."""
        return self._get(f"/user/{username}", None)

    def user_leagues(self, user_id: str, season: int) -> list[dict[str, Any]]:
        return self._get(f"/user/{user_id}/leagues/nfl/{season}", None) or []
