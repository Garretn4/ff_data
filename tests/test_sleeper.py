import json
from pathlib import Path

import httpx
import pytest

from app.data.store import Store
from app.sleeper import PLAYERS_TTL, STATE_TTL, RateLimiter, SleeperClient


class FakeClock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def make_client(tmp_path: Path, handler, clock: FakeClock, refresh: bool = False) -> SleeperClient:
    http = httpx.Client(transport=httpx.MockTransport(handler))
    return SleeperClient(Store(tmp_path / "app.sqlite"), http=http, clock=clock, refresh=refresh)


def test_league_is_fetched_then_served_from_cache(tmp_path: Path) -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(200, json={"league_id": "1", "total_rosters": 10})

    clock = FakeClock()
    client = make_client(tmp_path, handler, clock)
    assert client.league("1")["total_rosters"] == 10
    assert client.league("1")["total_rosters"] == 10
    assert calls == ["/v1/league/1"]


def test_refresh_bypasses_cache_but_players_keep_daily_ttl(tmp_path: Path) -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/v1/players/nfl":
            return httpx.Response(200, json={"4046": {"full_name": "Patrick Mahomes"}})
        return httpx.Response(200, json=[{"roster_id": 1}])

    clock = FakeClock()
    client = make_client(tmp_path, handler, clock, refresh=True)
    client.rosters("1")
    client.rosters("1")
    client.players()
    client.players()
    assert calls.count("/v1/league/1/rosters") == 2
    assert calls.count("/v1/players/nfl") == 1
    clock.t += PLAYERS_TTL + 1
    client.players()
    assert calls.count("/v1/players/nfl") == 2


def test_network_failure_falls_back_to_stale_cache_and_records_error(tmp_path: Path) -> None:
    state = {"fail": False}

    def handler(request: httpx.Request) -> httpx.Response:
        if state["fail"]:
            raise httpx.ConnectError("down")
        return httpx.Response(200, json={"week": 2})

    clock = FakeClock()
    client = make_client(tmp_path, handler, clock, refresh=True)
    assert client.state()["week"] == 2
    state["fail"] = True
    clock.t += STATE_TTL + 1
    assert client.state()["week"] == 2
    assert client.errors and "state/nfl" in client.errors[0]


def test_network_failure_without_cache_raises(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    client = make_client(tmp_path, handler, FakeClock())
    with pytest.raises(httpx.HTTPError):
        client.users("1")


def test_rate_limiter_sleeps_when_bucket_empty() -> None:
    slept: list[float] = []
    clock = FakeClock()
    limiter = RateLimiter(max_calls=2, per_seconds=60.0, clock=clock, sleep=lambda s: slept.append(s))
    limiter.acquire()
    limiter.acquire()
    limiter.acquire()
    assert slept and slept[0] > 0


def test_matchups_and_transactions_paths(tmp_path: Path) -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(200, json=[])

    client = make_client(tmp_path, handler, FakeClock())
    client.matchups("9", 3)
    client.transactions("9", 3)
    client.traded_picks("9")
    assert calls == ["/v1/league/9/matchups/3", "/v1/league/9/transactions/3", "/v1/league/9/traded_picks"]
