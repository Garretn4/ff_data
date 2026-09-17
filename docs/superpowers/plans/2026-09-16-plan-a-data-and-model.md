# ff-analyst Plan A: Data Layer, Value Model, Backtest Gate

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Sleeper import, nflverse ingest, league scoring, identity map, practice export, value model, and walk-forward backtest, ending in a report that decides whether the UI phases are built.

**Architecture:** nflverse data lands in one DuckDB file; Sleeper responses and market snapshots land in SQLite; the model reads both and is driven by a CLI. Every function returns polars frames or dataclasses so tests and the CLI share code. No network in tests: httpx MockTransport for HTTP, injected loader callables for nflverse.

**Tech Stack:** Python 3.12 via uv, polars, DuckDB, pyarrow, httpx, nflreadpy, pytest. Repo at `~/code/ff-analyst`, virtualenv at `.venv`, run everything as `./.venv/bin/python -m pytest` or `./.venv/bin/python -m app`.

**Spec:** `docs/superpowers/specs/2026-09-16-ff-analyst-design.md`

## Global Constraints

- Dependencies are exactly: nicegui[native], duckdb, polars, pyarrow, httpx, nflreadpy, pytest. Do not add any other package. If you believe one is needed, stop and report.
- Type hints on every function. Small functions. No dead code, no commented-out code, no TODO comments.
- No AI attribution anywhere: no generated-with comments, no co-author trailers in commits, no signatures, no watermarks. Commit messages are plain imperative sentences.
- Scoring rules and roster slots always come from the league settings dict. Never hardcode PPR or a slot list outside tests.
- Tests never hit the network. Sleeper uses `httpx.MockTransport`; nflverse loaders are injected callables.
- Files on disk under `cache/` are gitignored. Never commit them.
- Run tests with `./.venv/bin/python -m pytest -q` from the repo root.

---

## File map

| File | Responsibility |
|---|---|
| `app/config.py` | `Config` dataclass and `load_config` from `config.local.toml` and env |
| `app/data/store.py` | SQLite: HTTP cache table, market snapshot table |
| `app/sleeper.py` | `RateLimiter`, `SleeperClient` with TTL cache and stale fallback |
| `app/data/nflverse.py` | DuckDB connect, per-season and static ingest with injected loaders |
| `app/data/scoring.py` | Sleeper scoring keys to nflverse columns; actual and expected points |
| `app/data/identity.py` | ID hub frame, name normalization, name matching, diagnostics |
| `app/market.py` | FantasyCalc values, FFC ADP, consensus snapshot from DuckDB |
| `app/data/export.py` | Practice export: duckdb copy, parquet, csv, README |
| `app/__main__.py` | CLI: refresh, export, backtest |
| `app/model/replacement.py` | Starter counts, flex allocation, replacement level |
| `app/model/agecurve.py` | Seasonal PPG and per-position age factors |
| `app/model/projections.py` | Features, blend, per-week projections |
| `app/model/backtest.py` | Metrics, walk-forward harness, tuning, report |

---

### Task 1: Config and SQLite store

**Files:**
- Create: `app/config.py`
- Create: `app/data/store.py`
- Test: `tests/test_config.py`, `tests/test_store.py`

**Interfaces:**
- Produces: `Config(league_id: str, root: Path, stats_from: int, detail_from: int, current_season: int)` with properties `cache_dir`, `duckdb_path`, `sqlite_path`; `load_config(root: Path, env: Mapping[str, str]) -> Config`; `current_nfl_season(today: date) -> int`.
- Produces: `Store(path: Path)` with `get_cached(key, max_age_s, now) -> str | None`, `put_cached(key, body, now) -> None`, `put_snapshot(source, taken_at, rows) -> int`, `latest_snapshot(source) -> pl.DataFrame`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_config.py
from datetime import date
from pathlib import Path

from app.config import Config, current_nfl_season, load_config


def test_current_nfl_season_rolls_in_september() -> None:
    assert current_nfl_season(date(2026, 9, 16)) == 2026
    assert current_nfl_season(date(2026, 3, 1)) == 2025


def test_load_config_reads_toml_and_env_override(tmp_path: Path) -> None:
    (tmp_path / "config.local.toml").write_text('league_id = "111"\n')
    cfg = load_config(tmp_path, env={})
    assert cfg.league_id == "111"
    assert cfg.cache_dir == tmp_path / "cache"
    assert cfg.duckdb_path == tmp_path / "cache" / "stats.duckdb"
    assert cfg.sqlite_path == tmp_path / "cache" / "app.sqlite"
    over = load_config(tmp_path, env={"FF_LEAGUE_ID": "222"})
    assert over.league_id == "222"


def test_load_config_without_file_has_empty_league(tmp_path: Path) -> None:
    cfg = load_config(tmp_path, env={})
    assert cfg.league_id == ""
    assert cfg.stats_from == 2012
    assert cfg.detail_from == 2019
```

```python
# tests/test_store.py
from pathlib import Path

from app.data.store import Store


def test_cache_respects_max_age(tmp_path: Path) -> None:
    store = Store(tmp_path / "app.sqlite")
    store.put_cached("k", '{"a": 1}', now=100.0)
    assert store.get_cached("k", max_age_s=50.0, now=120.0) == '{"a": 1}'
    assert store.get_cached("k", max_age_s=10.0, now=120.0) is None
    assert store.get_cached("k", max_age_s=None, now=999.0) == '{"a": 1}'
    assert store.get_cached("missing", max_age_s=None, now=0.0) is None


def test_put_cached_overwrites(tmp_path: Path) -> None:
    store = Store(tmp_path / "app.sqlite")
    store.put_cached("k", "one", now=1.0)
    store.put_cached("k", "two", now=2.0)
    assert store.get_cached("k", max_age_s=None, now=3.0) == "two"


def test_snapshot_roundtrip(tmp_path: Path) -> None:
    store = Store(tmp_path / "app.sqlite")
    rows = [
        {"sleeper_id": "1", "name": "A", "position": "RB", "team": "DET",
         "value": 10.0, "overall_rank": 1, "position_rank": 1, "trend30": 0.5},
        {"sleeper_id": "2", "name": "B", "position": "WR", "team": "MIN",
         "value": 9.0, "overall_rank": 2, "position_rank": 1, "trend30": -0.1},
    ]
    assert store.put_snapshot("fantasycalc", "2026-09-16T10:00:00", rows) == 2
    store.put_snapshot("fantasycalc", "2026-09-17T10:00:00", rows[:1])
    latest = store.latest_snapshot("fantasycalc")
    assert latest.height == 1
    assert latest["taken_at"][0] == "2026-09-17T10:00:00"
    assert store.latest_snapshot("nothing").height == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/bin/python -m pytest tests/test_config.py tests/test_store.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.config'`

- [ ] **Step 3: Implement config and store**

```python
# app/config.py
"""Runtime configuration: league id, paths, season range."""
from __future__ import annotations

import tomllib
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Mapping

STATS_FROM = 2012
DETAIL_FROM = 2019


def current_nfl_season(today: date) -> int:
    """NFL season year: September onward belongs to the new season."""
    return today.year if today.month >= 9 else today.year - 1


@dataclass(frozen=True)
class Config:
    league_id: str
    root: Path
    stats_from: int
    detail_from: int
    current_season: int

    @property
    def cache_dir(self) -> Path:
        return self.root / "cache"

    @property
    def duckdb_path(self) -> Path:
        return self.cache_dir / "stats.duckdb"

    @property
    def sqlite_path(self) -> Path:
        return self.cache_dir / "app.sqlite"

    @property
    def stats_seasons(self) -> list[int]:
        return list(range(self.stats_from, self.current_season + 1))

    @property
    def detail_seasons(self) -> list[int]:
        return list(range(self.detail_from, self.current_season + 1))


def _read_toml(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    with path.open("rb") as fh:
        return tomllib.load(fh)


def load_config(root: Path, env: Mapping[str, str], today: date | None = None) -> Config:
    """Read config.local.toml under root; FF_LEAGUE_ID in env overrides the file."""
    data = _read_toml(root / "config.local.toml")
    league_id = str(env.get("FF_LEAGUE_ID") or data.get("league_id", ""))
    season = current_nfl_season(today or date.today())
    return Config(
        league_id=league_id,
        root=root,
        stats_from=int(data.get("stats_from", STATS_FROM)),
        detail_from=int(data.get("detail_from", DETAIL_FROM)),
        current_season=season,
    )
```

```python
# app/data/store.py
"""SQLite app state: raw HTTP cache and market value snapshots."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterable, Mapping

import polars as pl

SNAPSHOT_COLUMNS = (
    "sleeper_id", "name", "position", "team",
    "value", "overall_rank", "position_rank", "trend30",
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS http_cache (
    key TEXT PRIMARY KEY,
    fetched_at REAL NOT NULL,
    body TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS market_snapshots (
    source TEXT NOT NULL,
    taken_at TEXT NOT NULL,
    sleeper_id TEXT,
    name TEXT,
    position TEXT,
    team TEXT,
    value REAL,
    overall_rank INTEGER,
    position_rank INTEGER,
    trend30 REAL
);
CREATE INDEX IF NOT EXISTS ix_snapshots_source_time ON market_snapshots (source, taken_at);
"""


class Store:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path)
        self._conn.executescript(_SCHEMA)

    def get_cached(self, key: str, max_age_s: float | None, now: float) -> str | None:
        row = self._conn.execute(
            "SELECT fetched_at, body FROM http_cache WHERE key = ?", (key,)
        ).fetchone()
        if row is None:
            return None
        fetched_at, body = row
        if max_age_s is not None and now - fetched_at > max_age_s:
            return None
        return str(body)

    def put_cached(self, key: str, body: str, now: float) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO http_cache (key, fetched_at, body) VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET fetched_at = excluded.fetched_at, body = excluded.body",
                (key, now, body),
            )

    def put_snapshot(self, source: str, taken_at: str, rows: Iterable[Mapping[str, object]]) -> int:
        values = [
            (source, taken_at, *[row.get(col) for col in SNAPSHOT_COLUMNS]) for row in rows
        ]
        placeholders = ", ".join("?" for _ in range(2 + len(SNAPSHOT_COLUMNS)))
        with self._conn:
            self._conn.executemany(
                f"INSERT INTO market_snapshots (source, taken_at, {', '.join(SNAPSHOT_COLUMNS)}) "
                f"VALUES ({placeholders})",
                values,
            )
        return len(values)

    def latest_snapshot(self, source: str) -> pl.DataFrame:
        cols = ["source", "taken_at", *SNAPSHOT_COLUMNS]
        rows = self._conn.execute(
            f"SELECT {', '.join(cols)} FROM market_snapshots WHERE source = ? "
            "AND taken_at = (SELECT MAX(taken_at) FROM market_snapshots WHERE source = ?)",
            (source, source),
        ).fetchall()
        schema = {
            "source": pl.Utf8, "taken_at": pl.Utf8, "sleeper_id": pl.Utf8, "name": pl.Utf8,
            "position": pl.Utf8, "team": pl.Utf8, "value": pl.Float64,
            "overall_rank": pl.Int64, "position_rank": pl.Int64, "trend30": pl.Float64,
        }
        return pl.DataFrame(rows, schema=schema, orient="row")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./.venv/bin/python -m pytest tests/test_config.py tests/test_store.py -q`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add app/config.py app/data/store.py tests/test_config.py tests/test_store.py
git commit -m "Add config loading and SQLite store"
```

---

### Task 2: Sleeper client with rate limit and TTL cache

**Files:**
- Create: `app/sleeper.py`
- Test: `tests/test_sleeper.py`

**Interfaces:**
- Consumes: `Store` from Task 1.
- Produces: `RateLimiter(max_calls: int, per_seconds: float, clock, sleep)` with `acquire() -> None`; `SleeperClient(store, http: httpx.Client | None = None, limiter: RateLimiter | None = None, clock: Callable[[], float] = time.time)` with methods `state() -> dict`, `league(league_id) -> dict`, `users(league_id) -> list[dict]`, `rosters(league_id) -> list[dict]`, `matchups(league_id, week) -> list[dict]`, `transactions(league_id, week) -> list[dict]`, `traded_picks(league_id) -> list[dict]`, `players() -> dict[str, dict]`, attribute `errors: list[str]`, and `refresh: bool` constructor flag that bypasses TTL for league endpoints. `PLAYERS_TTL = 86400.0`, `STATE_TTL = 3600.0`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_sleeper.py
import json
from pathlib import Path

import httpx
import pytest

from app.data.store import Store
from app.sleeper import PLAYERS_TTL, RateLimiter, SleeperClient


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/bin/python -m pytest tests/test_sleeper.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.sleeper'`

- [ ] **Step 3: Implement the client**

```python
# app/sleeper.py
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
```

Note on cache semantics: `ttl=None` means "cache forever unless the client was built with refresh=True"; league endpoints use it so the app works offline and Refresh forces a refetch. `players` and `state` always use their TTL so Refresh never refetches the 5 MB players blob more than daily.

- [ ] **Step 4: Run tests to verify they pass**

Run: `./.venv/bin/python -m pytest tests/test_sleeper.py -q`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add app/sleeper.py tests/test_sleeper.py
git commit -m "Add Sleeper client with rate limiting and cached responses"
```

---

### Task 3: nflverse ingest into DuckDB

**Files:**
- Create: `app/data/nflverse.py`
- Test: `tests/test_nflverse.py`

**Interfaces:**
- Produces: `connect(path: Path) -> duckdb.DuckDBPyConnection`; `SeasonLoader = Callable[[list[int]], pl.DataFrame]`; `StaticLoader = Callable[[], pl.DataFrame]`; `SEASON_LOADERS: dict[str, SeasonLoader]` with keys `player_stats, snap_counts, ff_opportunity, schedules, rosters, injuries`; `STATIC_LOADERS: dict[str, StaticLoader]` with keys `ff_playerids, ff_rankings`; `replace_season_rows(con, table, df, seasons) -> int`; `replace_table(con, table, df) -> int`; `ingest_seasons(con, seasons, loaders=SEASON_LOADERS) -> dict[str, int]`; `ingest_static(con, loaders=STATIC_LOADERS) -> dict[str, int]`; `loaded_seasons(con, table) -> list[int]`; `table_exists(con, table) -> bool`.
- Table names in DuckDB equal the loader keys. `ff_rankings.scrape_date` is stored as DATE. `schedules.gameday` is stored as DATE.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_nflverse.py
from pathlib import Path

import polars as pl

from app.data.nflverse import (
    connect, ingest_seasons, ingest_static, loaded_seasons, replace_season_rows, table_exists,
)


def stats_frame(season: int, n: int) -> pl.DataFrame:
    return pl.DataFrame({
        "player_id": [f"00-{i:07d}" for i in range(n)],
        "season": [season] * n,
        "week": list(range(1, n + 1)),
        "position": ["RB"] * n,
        "receiving_yards": [float(10 * i) for i in range(n)],
    })


def test_replace_season_rows_is_idempotent(tmp_path: Path) -> None:
    con = connect(tmp_path / "s.duckdb")
    assert replace_season_rows(con, "player_stats", stats_frame(2024, 3), [2024]) == 3
    assert replace_season_rows(con, "player_stats", stats_frame(2025, 2), [2025]) == 2
    assert replace_season_rows(con, "player_stats", stats_frame(2024, 4), [2024]) == 4
    assert loaded_seasons(con, "player_stats") == [2024, 2025]
    assert con.execute("SELECT count(*) FROM player_stats").fetchone()[0] == 6


def test_ingest_seasons_uses_injected_loaders_and_casts_dates(tmp_path: Path) -> None:
    con = connect(tmp_path / "s.duckdb")
    loaders = {
        "player_stats": lambda seasons: pl.concat([stats_frame(s, 2) for s in seasons]),
        "schedules": lambda seasons: pl.DataFrame({
            "season": seasons, "week": [1] * len(seasons), "game_type": ["REG"] * len(seasons),
            "home_team": ["DET"] * len(seasons), "away_team": ["MIN"] * len(seasons),
            "gameday": [f"{s}-09-07" for s in seasons],
        }),
    }
    counts = ingest_seasons(con, [2024, 2025], loaders=loaders)
    assert counts == {"player_stats": 4, "schedules": 2}
    dtype = con.execute("SELECT typeof(gameday) FROM schedules LIMIT 1").fetchone()[0]
    assert dtype == "DATE"


def test_ingest_static_replaces_whole_table(tmp_path: Path) -> None:
    con = connect(tmp_path / "s.duckdb")
    loaders = {
        "ff_rankings": lambda: pl.DataFrame({
            "id": ["1", "2"], "page_type": ["redraft-overall"] * 2, "ecr": [1.0, 2.0],
            "pos": ["RB", "WR"], "scrape_date": ["2024-09-01", "2024-09-01"],
        }),
        "ff_playerids": lambda: pl.DataFrame({"gsis_id": ["00-1"], "sleeper_id": [4046]}),
    }
    ingest_static(con, loaders=loaders)
    ingest_static(con, loaders=loaders)
    assert con.execute("SELECT count(*) FROM ff_rankings").fetchone()[0] == 2
    assert con.execute("SELECT typeof(scrape_date) FROM ff_rankings LIMIT 1").fetchone()[0] == "DATE"
    assert table_exists(con, "ff_playerids")
    assert not table_exists(con, "nope")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/bin/python -m pytest tests/test_nflverse.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.data.nflverse'`

- [ ] **Step 3: Implement the ingest module**

```python
# app/data/nflverse.py
"""Load nflverse tables into DuckDB. Loaders are injectable so tests stay offline."""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Iterable, Mapping

import duckdb
import nflreadpy as nfl
import polars as pl

SeasonLoader = Callable[[list[int]], pl.DataFrame]
StaticLoader = Callable[[], pl.DataFrame]

SEASON_LOADERS: dict[str, SeasonLoader] = {
    "player_stats": lambda seasons: nfl.load_player_stats(seasons=seasons),
    "snap_counts": lambda seasons: nfl.load_snap_counts(seasons=seasons),
    "ff_opportunity": lambda seasons: nfl.load_ff_opportunity(seasons=seasons),
    "schedules": lambda seasons: nfl.load_schedules(seasons=seasons),
    "rosters": lambda seasons: nfl.load_rosters(seasons=seasons),
    "injuries": lambda seasons: nfl.load_injuries(seasons=seasons),
}

STATIC_LOADERS: dict[str, StaticLoader] = {
    "ff_playerids": lambda: nfl.load_ff_playerids(),
    "ff_rankings": lambda: nfl.load_ff_rankings(type="all"),
}

DATE_COLUMNS: dict[str, tuple[str, ...]] = {
    "schedules": ("gameday",),
    "ff_rankings": ("scrape_date",),
}


def connect(path: Path) -> duckdb.DuckDBPyConnection:
    path.parent.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(str(path))


def table_exists(con: duckdb.DuckDBPyConnection, table: str) -> bool:
    row = con.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_name = ?", [table]
    ).fetchone()
    return bool(row and row[0])


def loaded_seasons(con: duckdb.DuckDBPyConnection, table: str) -> list[int]:
    if not table_exists(con, table):
        return []
    rows = con.execute(f"SELECT DISTINCT season FROM {table} ORDER BY season").fetchall()
    return [int(r[0]) for r in rows]


def _cast_dates(table: str, df: pl.DataFrame) -> pl.DataFrame:
    cols = [c for c in DATE_COLUMNS.get(table, ()) if c in df.columns]
    return df.with_columns([pl.col(c).cast(pl.Utf8).str.to_date(strict=False) for c in cols])


def replace_table(con: duckdb.DuckDBPyConnection, table: str, df: pl.DataFrame) -> int:
    frame = _cast_dates(table, df)
    con.register("incoming", frame)
    con.execute(f"CREATE OR REPLACE TABLE {table} AS SELECT * FROM incoming")
    con.unregister("incoming")
    return frame.height


def replace_season_rows(
    con: duckdb.DuckDBPyConnection, table: str, df: pl.DataFrame, seasons: Iterable[int]
) -> int:
    frame = _cast_dates(table, df)
    if not table_exists(con, table):
        return replace_table(con, table, frame)
    season_list = list(seasons)
    con.execute(
        f"DELETE FROM {table} WHERE season IN ({', '.join('?' for _ in season_list)})", season_list
    )
    con.register("incoming", frame)
    con.execute(f"INSERT INTO {table} BY NAME SELECT * FROM incoming")
    con.unregister("incoming")
    return frame.height


def ingest_seasons(
    con: duckdb.DuckDBPyConnection,
    seasons: list[int],
    loaders: Mapping[str, SeasonLoader] = SEASON_LOADERS,
) -> dict[str, int]:
    return {table: replace_season_rows(con, table, load(seasons), seasons) for table, load in loaders.items()}


def ingest_static(
    con: duckdb.DuckDBPyConnection, loaders: Mapping[str, StaticLoader] = STATIC_LOADERS
) -> dict[str, int]:
    return {table: replace_table(con, table, load()) for table, load in loaders.items()}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./.venv/bin/python -m pytest tests/test_nflverse.py -q`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add app/data/nflverse.py tests/test_nflverse.py
git commit -m "Add nflverse ingest into DuckDB with injectable loaders"
```

---

### Task 4: League scoring from Sleeper settings

**Files:**
- Create: `app/data/scoring.py`
- Test: `tests/test_scoring.py`

**Interfaces:**
- Produces: `SCORING_MAP: dict[str, pl.Expr]` (Sleeper key to nflverse expression); `EXPECTED_MAP: dict[str, pl.Expr]` (Sleeper key to ffopportunity expected-stat expression); `score_weeks(stats: pl.DataFrame, scoring: Mapping[str, float]) -> pl.DataFrame` adds a Float64 column `points`; `expected_points(opportunity: pl.DataFrame, scoring) -> pl.DataFrame` adds `exp_points`; `unmapped_keys(scoring) -> list[str]`; `SKILL_POSITIONS = ("QB", "RB", "WR", "TE")`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_scoring.py
import polars as pl

from app.data.scoring import expected_points, score_weeks, unmapped_keys

PPR = {"pass_yd": 0.04, "pass_td": 4.0, "pass_int": -1.0, "rush_yd": 0.1, "rush_td": 6.0,
       "rec": 1.0, "rec_yd": 0.1, "rec_td": 6.0, "fum_lost": -2.0, "bonus_rec_te": 0.5}
HALF = {**PPR, "rec": 0.5}


def week_frame() -> pl.DataFrame:
    return pl.DataFrame({
        "player_id": ["qb", "rb", "te"],
        "position": ["QB", "RB", "TE"],
        "passing_yards": [300.0, 0.0, 0.0],
        "passing_tds": [2.0, 0.0, 0.0],
        "passing_interceptions": [1.0, 0.0, 0.0],
        "rushing_yards": [10.0, 80.0, 0.0],
        "rushing_tds": [0.0, 1.0, 0.0],
        "receptions": [0.0, 4.0, 6.0],
        "receiving_yards": [0.0, 30.0, 60.0],
        "receiving_tds": [0.0, 0.0, 1.0],
        "sack_fumbles_lost": [1.0, 0.0, 0.0],
        "rushing_fumbles_lost": [0.0, 0.0, 0.0],
        "receiving_fumbles_lost": [0.0, 0.0, 0.0],
    })


def test_score_weeks_matches_hand_computation() -> None:
    out = score_weeks(week_frame(), PPR)
    pts = dict(zip(out["player_id"], out["points"]))
    assert pts["qb"] == 300 * 0.04 + 2 * 4 - 1 + 10 * 0.1 - 2
    assert pts["rb"] == 80 * 0.1 + 6 + 4 + 30 * 0.1
    assert pts["te"] == 6 + 60 * 0.1 + 6 + 6 * 0.5


def test_half_ppr_changes_only_receptions() -> None:
    full = score_weeks(week_frame(), PPR)["points"].to_list()
    half = score_weeks(week_frame(), HALF)["points"].to_list()
    assert [round(f - h, 6) for f, h in zip(full, half)] == [0.0, 2.0, 3.0]


def test_missing_stat_columns_count_as_zero() -> None:
    frame = pl.DataFrame({"player_id": ["k"], "position": ["K"], "fg_made_50_59": [1.0]})
    out = score_weeks(frame, {"fgm_50p": 5.0, "xpm": 1.0})
    assert out["points"][0] == 5.0


def test_unmapped_keys_are_reported() -> None:
    assert unmapped_keys({"rec": 1.0, "pass_td_40p": 2.0, "def_st_td": 6.0}) == ["def_st_td", "pass_td_40p"]


def test_expected_points_uses_expected_columns() -> None:
    fo = pl.DataFrame({
        "player_id": ["rb"], "position": ["RB"],
        "rush_yards_gained_exp": [50.0], "rush_touchdown_exp": [0.5],
        "receptions_exp": [3.0], "rec_yards_gained_exp": [20.0], "rec_touchdown_exp": [0.1],
        "pass_yards_gained_exp": [0.0], "pass_touchdown_exp": [0.0], "pass_interception_exp": [0.0],
    })
    out = expected_points(fo, PPR)
    assert round(out["exp_points"][0], 4) == round(5.0 + 3.0 + 3.0 + 2.0 + 0.6, 4)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/bin/python -m pytest tests/test_scoring.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.data.scoring'`

- [ ] **Step 3: Implement scoring**

```python
# app/data/scoring.py
"""Map Sleeper scoring keys onto nflverse columns and compute points per player-week."""
from __future__ import annotations

from typing import Mapping

import polars as pl

SKILL_POSITIONS = ("QB", "RB", "WR", "TE")


def _c(name: str) -> pl.Expr:
    return pl.col(name).cast(pl.Float64).fill_null(0.0)


def _bonus(column: str, threshold: float) -> pl.Expr:
    return (_c(column) >= threshold).cast(pl.Float64)


SCORING_MAP: dict[str, pl.Expr] = {
    "pass_yd": _c("passing_yards"),
    "pass_td": _c("passing_tds"),
    "pass_int": _c("passing_interceptions"),
    "pass_2pt": _c("passing_2pt_conversions"),
    "pass_sack": _c("sacks_suffered"),
    "rush_yd": _c("rushing_yards"),
    "rush_td": _c("rushing_tds"),
    "rush_2pt": _c("rushing_2pt_conversions"),
    "rush_40p": _c("rushing_40"),
    "rec": _c("receptions"),
    "rec_yd": _c("receiving_yards"),
    "rec_td": _c("receiving_tds"),
    "rec_2pt": _c("receiving_2pt_conversions"),
    "rec_40p": _c("receiving_40"),
    "fum": _c("sack_fumbles") + _c("rushing_fumbles") + _c("receiving_fumbles"),
    "fum_lost": _c("sack_fumbles_lost") + _c("rushing_fumbles_lost") + _c("receiving_fumbles_lost"),
    "fum_rec_td": _c("fumble_recovery_tds"),
    "st_td": _c("special_teams_tds"),
    "bonus_pass_yd_300": _bonus("passing_yards", 300),
    "bonus_pass_yd_400": _bonus("passing_yards", 400),
    "bonus_rush_yd_100": _bonus("rushing_yards", 100),
    "bonus_rush_yd_200": _bonus("rushing_yards", 200),
    "bonus_rec_yd_100": _bonus("receiving_yards", 100),
    "bonus_rec_yd_200": _bonus("receiving_yards", 200),
    "bonus_rec_te": pl.when(pl.col("position") == "TE").then(_c("receptions")).otherwise(0.0),
    "bonus_rec_rb": pl.when(pl.col("position") == "RB").then(_c("receptions")).otherwise(0.0),
    "bonus_rec_wr": pl.when(pl.col("position") == "WR").then(_c("receptions")).otherwise(0.0),
    "fgm_0_19": _c("fg_made_0_19"),
    "fgm_20_29": _c("fg_made_20_29"),
    "fgm_30_39": _c("fg_made_30_39"),
    "fgm_40_49": _c("fg_made_40_49"),
    "fgm_50p": _c("fg_made_50_59") + _c("fg_made_60_"),
    "fgmiss": _c("fg_missed"),
    "xpm": _c("pat_made"),
    "xpmiss": _c("pat_missed"),
}

EXPECTED_MAP: dict[str, pl.Expr] = {
    "pass_yd": _c("pass_yards_gained_exp"),
    "pass_td": _c("pass_touchdown_exp"),
    "pass_int": _c("pass_interception_exp"),
    "pass_2pt": _c("pass_two_point_conv_exp"),
    "rush_yd": _c("rush_yards_gained_exp"),
    "rush_td": _c("rush_touchdown_exp"),
    "rush_2pt": _c("rush_two_point_conv_exp"),
    "rec": _c("receptions_exp"),
    "rec_yd": _c("rec_yards_gained_exp"),
    "rec_td": _c("rec_touchdown_exp"),
    "rec_2pt": _c("rec_two_point_conv_exp"),
    "bonus_rec_te": pl.when(pl.col("position") == "TE").then(_c("receptions_exp")).otherwise(0.0),
    "bonus_rec_rb": pl.when(pl.col("position") == "RB").then(_c("receptions_exp")).otherwise(0.0),
    "bonus_rec_wr": pl.when(pl.col("position") == "WR").then(_c("receptions_exp")).otherwise(0.0),
}


def unmapped_keys(scoring: Mapping[str, float]) -> list[str]:
    return sorted(k for k in scoring if k not in SCORING_MAP)


def _needed_columns(exprs: list[pl.Expr]) -> set[str]:
    names: set[str] = set()
    for expr in exprs:
        names.update(expr.meta.root_names())
    return names


def _with_missing_as_zero(frame: pl.DataFrame, exprs: list[pl.Expr]) -> pl.DataFrame:
    missing = [c for c in _needed_columns(exprs) if c not in frame.columns and c != "position"]
    if "position" not in frame.columns:
        frame = frame.with_columns(pl.lit(None, dtype=pl.Utf8).alias("position"))
    return frame.with_columns([pl.lit(0.0).alias(c) for c in missing])


def _weighted_sum(
    frame: pl.DataFrame, scoring: Mapping[str, float], table: Mapping[str, pl.Expr], out: str
) -> pl.DataFrame:
    exprs = [table[k] * float(w) for k, w in scoring.items() if k in table]
    frame = _with_missing_as_zero(frame, list(table.values()))
    total = pl.sum_horizontal(exprs) if exprs else pl.lit(0.0)
    return frame.with_columns(total.cast(pl.Float64).alias(out))


def score_weeks(stats: pl.DataFrame, scoring: Mapping[str, float]) -> pl.DataFrame:
    """Add `points` under the league's scoring to a frame of nflverse weekly stats."""
    return _weighted_sum(stats, scoring, SCORING_MAP, "points")


def expected_points(opportunity: pl.DataFrame, scoring: Mapping[str, float]) -> pl.DataFrame:
    """Add `exp_points`: usage-based expected points under the league's scoring."""
    return _weighted_sum(opportunity, scoring, EXPECTED_MAP, "exp_points")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./.venv/bin/python -m pytest tests/test_scoring.py -q`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add app/data/scoring.py tests/test_scoring.py
git commit -m "Add league scoring from Sleeper settings over nflverse stats"
```

---

### Task 5: Identity map and name matching

**Files:**
- Create: `app/data/identity.py`
- Test: `tests/test_identity.py`

**Interfaces:**
- Produces: `id_hub(playerids: pl.DataFrame) -> pl.DataFrame` with columns `gsis_id: Utf8, sleeper_id: Utf8, fantasypros_id: Utf8, pfr_id: Utf8, name: Utf8, norm_name: Utf8, position: Utf8, team: Utf8, birthdate: Date`; `normalize_name(name: str) -> str`; `match_by_name(rows: pl.DataFrame, hub: pl.DataFrame, name_col: str, position_col: str) -> tuple[pl.DataFrame, pl.DataFrame]` returning (matched rows with `gsis_id` appended, unmatched rows); `write_diagnostics(con, source: str, unmatched: pl.DataFrame, name_col, position_col, reason) -> int` into DuckDB table `identity_diagnostics(source, key, name, position, reason)`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_identity.py
from pathlib import Path

import polars as pl

from app.data.identity import id_hub, match_by_name, normalize_name, write_diagnostics
from app.data.nflverse import connect


def playerids() -> pl.DataFrame:
    return pl.DataFrame({
        "gsis_id": ["00-1", "00-2", None],
        "sleeper_id": [4046, None, 99],
        "fantasypros_id": [1001, 1002, None],
        "pfr_id": ["MahoPa00", "JeffJu00", None],
        "name": ["Patrick Mahomes", "Justin Jefferson", "Nobody Here"],
        "position": ["QB", "WR", "RB"],
        "team": ["KC", "MIN", "FA"],
        "birthdate": ["1995-09-17", "1999-06-16", None],
    })


def test_normalize_name_strips_suffixes_punctuation_and_case() -> None:
    assert normalize_name("D.J. Moore Jr.") == "dj moore"
    assert normalize_name("Kenneth Walker III") == "kenneth walker"
    assert normalize_name("Ja'Marr Chase") == "jamarr chase"


def test_id_hub_stringifies_ids_and_drops_rows_without_gsis() -> None:
    hub = id_hub(playerids())
    assert hub.height == 2
    assert hub["sleeper_id"].to_list() == ["4046", None]
    assert hub["fantasypros_id"].to_list() == ["1001", "1002"]
    assert hub["norm_name"].to_list() == ["patrick mahomes", "justin jefferson"]
    assert str(hub["birthdate"].dtype) == "Date"


def test_match_by_name_splits_matched_and_unmatched() -> None:
    hub = id_hub(playerids())
    rows = pl.DataFrame({"name": ["Justin Jefferson", "Patrick Mahomes II", "Ghost Player"],
                         "position": ["WR", "QB", "TE"], "adp": [1.0, 20.0, 99.0]})
    matched, unmatched = match_by_name(rows, hub, "name", "position")
    assert matched["gsis_id"].to_list() == ["00-2", "00-1"]
    assert unmatched["name"].to_list() == ["Ghost Player"]


def test_write_diagnostics_appends_rows(tmp_path: Path) -> None:
    con = connect(tmp_path / "s.duckdb")
    unmatched = pl.DataFrame({"name": ["Ghost Player"], "position": ["TE"]})
    assert write_diagnostics(con, "ffc_adp", unmatched, "name", "position", "no name match") == 1
    write_diagnostics(con, "ffc_adp", unmatched, "name", "position", "no name match")
    rows = con.execute("SELECT source, name, reason FROM identity_diagnostics").fetchall()
    assert rows == [("ffc_adp", "Ghost Player", "no name match")] * 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/bin/python -m pytest tests/test_identity.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.data.identity'`

- [ ] **Step 3: Implement identity**

```python
# app/data/identity.py
"""One hub frame keyed by gsis_id that carries every other provider's id."""
from __future__ import annotations

import re

import duckdb
import polars as pl

_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}
_NON_ALNUM = re.compile(r"[^a-z0-9 ]")


def normalize_name(name: str) -> str:
    cleaned = _NON_ALNUM.sub("", name.lower().replace("-", " "))
    parts = [p for p in cleaned.split() if p not in _SUFFIXES]
    return " ".join(parts)


def _as_str(col: str) -> pl.Expr:
    return pl.col(col).cast(pl.Int64, strict=False).cast(pl.Utf8)


def id_hub(playerids: pl.DataFrame) -> pl.DataFrame:
    return (
        playerids.filter(pl.col("gsis_id").is_not_null())
        .select(
            pl.col("gsis_id").cast(pl.Utf8),
            _as_str("sleeper_id").alias("sleeper_id"),
            _as_str("fantasypros_id").alias("fantasypros_id"),
            pl.col("pfr_id").cast(pl.Utf8),
            pl.col("name").cast(pl.Utf8),
            pl.col("name").cast(pl.Utf8).map_elements(normalize_name, return_dtype=pl.Utf8).alias("norm_name"),
            pl.col("position").cast(pl.Utf8),
            pl.col("team").cast(pl.Utf8),
            pl.col("birthdate").cast(pl.Utf8).str.to_date(strict=False).alias("birthdate"),
        )
    )


def match_by_name(
    rows: pl.DataFrame, hub: pl.DataFrame, name_col: str, position_col: str
) -> tuple[pl.DataFrame, pl.DataFrame]:
    keyed = rows.with_columns(
        pl.col(name_col).cast(pl.Utf8).map_elements(normalize_name, return_dtype=pl.Utf8).alias("norm_name")
    )
    lookup = hub.select("norm_name", "position", "gsis_id").unique(subset=["norm_name", "position"], keep="first")
    joined = keyed.join(lookup, left_on=["norm_name", position_col], right_on=["norm_name", "position"], how="left")
    matched = joined.filter(pl.col("gsis_id").is_not_null()).drop("norm_name")
    unmatched = joined.filter(pl.col("gsis_id").is_null()).drop("norm_name", "gsis_id")
    return matched, unmatched


def write_diagnostics(
    con: duckdb.DuckDBPyConnection,
    source: str,
    unmatched: pl.DataFrame,
    name_col: str,
    position_col: str,
    reason: str,
) -> int:
    con.execute(
        "CREATE TABLE IF NOT EXISTS identity_diagnostics "
        "(source VARCHAR, key VARCHAR, name VARCHAR, position VARCHAR, reason VARCHAR)"
    )
    frame = unmatched.select(
        pl.lit(source).alias("source"),
        pl.col(name_col).cast(pl.Utf8).alias("key"),
        pl.col(name_col).cast(pl.Utf8).alias("name"),
        pl.col(position_col).cast(pl.Utf8).alias("position"),
        pl.lit(reason).alias("reason"),
    )
    con.register("diag", frame)
    con.execute("INSERT INTO identity_diagnostics SELECT * FROM diag")
    con.unregister("diag")
    return frame.height
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./.venv/bin/python -m pytest tests/test_identity.py -q`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add app/data/identity.py tests/test_identity.py
git commit -m "Add player identity hub and name matching with diagnostics"
```

---

### Task 6: Market sources

**Files:**
- Create: `app/market.py`
- Test: `tests/test_market.py`

**Interfaces:**
- Consumes: `Store` (Task 1), `id_hub` and `match_by_name` (Task 5), DuckDB `ff_rankings` and `schedules` tables (Task 3).
- Produces: `fantasycalc_params(league: Mapping) -> dict[str, str]`; `fetch_fantasycalc(http: httpx.Client, params) -> pl.DataFrame` with columns `sleeper_id, name, position, team, value, overall_rank, position_rank, trend30`; `fetch_ffc_adp(http, year: int, teams: int) -> pl.DataFrame` with `name, position, team, adp, teams_reported: Int64`; `first_gameday(con, season, week) -> date`; `consensus_snapshot(con, season, week, page_type="redraft-overall") -> pl.DataFrame` with `fantasypros_id: Utf8, pos, ecr, sd, scrape_date` from the latest scrape strictly before that week's first game; `snapshot_fantasycalc(store, http, league, taken_at) -> int`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_market.py
from datetime import date
from pathlib import Path

import httpx
import polars as pl

from app.data.nflverse import connect, replace_table
from app.data.store import Store
from app.market import (
    consensus_snapshot, fantasycalc_params, fetch_fantasycalc, fetch_ffc_adp, first_gameday,
    snapshot_fantasycalc,
)

LEAGUE = {"total_rosters": 10, "scoring_settings": {"rec": 1.0},
          "roster_positions": ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "K", "DEF", "BN"]}

FC_BODY = [
    {"player": {"name": "Jahmyr Gibbs", "sleeperId": "9221", "position": "RB", "maybeTeam": "DET"},
     "value": 10725, "overallRank": 1, "positionRank": 1, "trend30Day": 319},
    {"player": {"name": "No Sleeper", "sleeperId": None, "position": "WR", "maybeTeam": "MIN"},
     "value": 9000, "overallRank": 2, "positionRank": 1, "trend30Day": 0},
]


def test_fantasycalc_params_from_league_settings() -> None:
    assert fantasycalc_params(LEAGUE) == {"isDynasty": "false", "numQbs": "1", "numTeams": "10", "ppr": "1"}
    sf = {**LEAGUE, "roster_positions": ["QB", "SUPER_FLEX", "BN"], "scoring_settings": {"rec": 0.5}}
    assert fantasycalc_params(sf) == {"isDynasty": "false", "numQbs": "2", "numTeams": "10", "ppr": "0.5"}


def test_fetch_fantasycalc_parses_and_keeps_null_sleeper_ids() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["numTeams"] == "10"
        return httpx.Response(200, json=FC_BODY)

    http = httpx.Client(transport=httpx.MockTransport(handler))
    df = fetch_fantasycalc(http, fantasycalc_params(LEAGUE))
    assert df.columns == ["sleeper_id", "name", "position", "team", "value", "overall_rank", "position_rank", "trend30"]
    assert df["sleeper_id"].to_list() == ["9221", None]
    assert df["value"].to_list() == [10725.0, 9000.0]


def test_fetch_ffc_adp_reports_actual_team_count() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"meta": {"teams": 12}, "players": [
            {"name": "Justin Jefferson", "position": "WR", "team": "MIN", "adp": 1.4},
            {"name": "San Francisco Defense", "position": "DEF", "team": "SF", "adp": 90.0},
        ]})

    http = httpx.Client(transport=httpx.MockTransport(handler))
    df = fetch_ffc_adp(http, 2023, 10)
    assert df["teams_reported"].to_list() == [12, 12]
    assert df["adp"].to_list() == [1.4, 90.0]


def test_consensus_snapshot_takes_latest_scrape_before_first_game(tmp_path: Path) -> None:
    con = connect(tmp_path / "s.duckdb")
    replace_table(con, "schedules", pl.DataFrame({
        "season": [2024, 2024, 2024], "week": [1, 1, 2], "game_type": ["REG"] * 3,
        "gameday": ["2024-09-05", "2024-09-08", "2024-09-12"],
    }))
    replace_table(con, "ff_rankings", pl.DataFrame({
        "id": ["7", "7", "7", "8"], "page_type": ["redraft-overall"] * 4,
        "pos": ["RB"] * 4, "ecr": [3.0, 2.0, 1.0, 5.0], "sd": [1.0] * 4,
        "scrape_date": ["2024-08-30", "2024-09-04", "2024-09-11", "2024-09-04"],
    }))
    assert first_gameday(con, 2024, 2) == date(2024, 9, 12)
    snap = consensus_snapshot(con, 2024, 2)
    assert snap.sort("fantasypros_id")["ecr"].to_list() == [1.0]
    week1 = consensus_snapshot(con, 2024, 1)
    assert sorted(week1["fantasypros_id"].to_list()) == ["7", "8"]
    assert week1.filter(pl.col("fantasypros_id") == "7")["ecr"][0] == 2.0


def test_snapshot_fantasycalc_writes_store(tmp_path: Path) -> None:
    http = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=FC_BODY)))
    store = Store(tmp_path / "app.sqlite")
    assert snapshot_fantasycalc(store, http, LEAGUE, "2026-09-16T00:00:00") == 2
    assert store.latest_snapshot("fantasycalc").height == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/bin/python -m pytest tests/test_market.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.market'`

- [ ] **Step 3: Implement market sources**

```python
# app/market.py
"""Market values: FantasyCalc (current), FFC ADP (preseason history), consensus archive (DuckDB)."""
from __future__ import annotations

from datetime import date
from typing import Any, Mapping

import duckdb
import httpx
import polars as pl

from app.data.store import Store

FANTASYCALC_URL = "https://api.fantasycalc.com/values/current"
FFC_URL = "https://fantasyfootballcalculator.com/api/v1/adp/ppr"


def _qb_slots(roster_positions: list[str]) -> int:
    return sum(1 for slot in roster_positions if slot in ("QB", "SUPER_FLEX"))


def fantasycalc_params(league: Mapping[str, Any]) -> dict[str, str]:
    rec = float(league.get("scoring_settings", {}).get("rec", 0.0))
    ppr = str(int(rec)) if rec == int(rec) else str(rec)
    return {
        "isDynasty": "false",
        "numQbs": str(min(2, max(1, _qb_slots(list(league.get("roster_positions", [])))))),
        "numTeams": str(int(league["total_rosters"])),
        "ppr": ppr,
    }


def fetch_fantasycalc(http: httpx.Client, params: Mapping[str, str]) -> pl.DataFrame:
    response = http.get(FANTASYCALC_URL, params=dict(params))
    response.raise_for_status()
    rows = [
        {
            "sleeper_id": item["player"].get("sleeperId"),
            "name": item["player"]["name"],
            "position": item["player"]["position"],
            "team": item["player"].get("maybeTeam"),
            "value": float(item["value"]),
            "overall_rank": int(item["overallRank"]),
            "position_rank": int(item["positionRank"]),
            "trend30": float(item.get("trend30Day") or 0.0),
        }
        for item in response.json()
    ]
    schema = {
        "sleeper_id": pl.Utf8, "name": pl.Utf8, "position": pl.Utf8, "team": pl.Utf8,
        "value": pl.Float64, "overall_rank": pl.Int64, "position_rank": pl.Int64, "trend30": pl.Float64,
    }
    return pl.DataFrame(rows, schema=schema)


def fetch_ffc_adp(http: httpx.Client, year: int, teams: int) -> pl.DataFrame:
    response = http.get(FFC_URL, params={"teams": teams, "year": year})
    response.raise_for_status()
    body = response.json()
    reported = int(body["meta"]["teams"])
    rows = [
        {"name": p["name"], "position": p["position"], "team": p.get("team"),
         "adp": float(p["adp"]), "teams_reported": reported}
        for p in body["players"]
    ]
    schema = {"name": pl.Utf8, "position": pl.Utf8, "team": pl.Utf8, "adp": pl.Float64, "teams_reported": pl.Int64}
    return pl.DataFrame(rows, schema=schema)


def first_gameday(con: duckdb.DuckDBPyConnection, season: int, week: int) -> date:
    row = con.execute(
        "SELECT min(gameday) FROM schedules WHERE season = ? AND week = ? AND game_type = 'REG'",
        [season, week],
    ).fetchone()
    if row is None or row[0] is None:
        raise ValueError(f"no schedule rows for season {season} week {week}")
    return row[0]


def consensus_snapshot(
    con: duckdb.DuckDBPyConnection, season: int, week: int, page_type: str = "redraft-overall"
) -> pl.DataFrame:
    cutoff = first_gameday(con, season, week)
    return con.execute(
        """
        WITH latest AS (
            SELECT max(scrape_date) AS d FROM ff_rankings
            WHERE page_type = ? AND scrape_date < ?
        )
        SELECT CAST(id AS VARCHAR) AS fantasypros_id, pos, ecr, sd, scrape_date
        FROM ff_rankings, latest
        WHERE page_type = ? AND scrape_date = latest.d
        """,
        [page_type, cutoff, page_type],
    ).pl()


def snapshot_fantasycalc(store: Store, http: httpx.Client, league: Mapping[str, Any], taken_at: str) -> int:
    frame = fetch_fantasycalc(http, fantasycalc_params(league))
    return store.put_snapshot("fantasycalc", taken_at, frame.to_dicts())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./.venv/bin/python -m pytest tests/test_market.py -q`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add app/market.py tests/test_market.py
git commit -m "Add FantasyCalc, FFC ADP, and consensus snapshot sources"
```

---

### Task 7: Practice export

**Files:**
- Create: `app/data/export.py`
- Test: `tests/test_export.py`

**Interfaces:**
- Consumes: DuckDB connection from Task 3.
- Produces: `export_practice(con, out_dir: Path) -> dict[str, int]` writing `<out_dir>/ff_practice.duckdb`, `<out_dir>/parquet/<table>.parquet`, `<out_dir>/csv/<table>.csv`, `<out_dir>/README.md`; returns row counts per table.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_export.py
from pathlib import Path

import duckdb
import polars as pl

from app.data.export import export_practice
from app.data.nflverse import connect, replace_table


def test_export_writes_duckdb_parquet_csv_and_readme(tmp_path: Path) -> None:
    con = connect(tmp_path / "s.duckdb")
    replace_table(con, "player_stats", pl.DataFrame({"season": [2024, 2024], "week": [1, 2], "points": [1.5, 2.5]}))
    replace_table(con, "schedules", pl.DataFrame({"season": [2024], "week": [1], "gameday": ["2024-09-05"]}))
    out = tmp_path / "practice"
    counts = export_practice(con, out)
    assert counts == {"player_stats": 2, "schedules": 1}
    assert pl.read_parquet(out / "parquet" / "player_stats.parquet").height == 2
    assert pl.read_csv(out / "csv" / "schedules.csv").height == 1
    practice = duckdb.connect(str(out / "ff_practice.duckdb"), read_only=True)
    assert practice.execute("SELECT sum(points) FROM player_stats").fetchone()[0] == 4.0
    readme = (out / "README.md").read_text()
    assert "duckdb.connect" in readme and "dbConnect" in readme
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/bin/python -m pytest tests/test_export.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.data.export'`

- [ ] **Step 3: Implement export**

```python
# app/data/export.py
"""Write a practice copy of the analytics store for Python and R sessions."""
from __future__ import annotations

from pathlib import Path

import duckdb

_README = """# ff-analyst practice data

Tables: {tables}

## Python

```python
import duckdb
con = duckdb.connect("ff_practice.duckdb", read_only=True)
con.sql("SELECT * FROM player_stats LIMIT 5").show()

import polars as pl
pl.read_parquet("parquet/player_stats.parquet")

import pandas as pd
pd.read_csv("csv/player_stats.csv")
```

## R

```r
library(DBI); library(duckdb)
con <- dbConnect(duckdb::duckdb(), "ff_practice.duckdb", read_only = TRUE)
dbGetQuery(con, "SELECT * FROM player_stats LIMIT 5")

library(arrow)
read_parquet("parquet/player_stats.parquet")

library(readr)
read_csv("csv/player_stats.csv")
```
"""


def _tables(con: duckdb.DuckDBPyConnection) -> list[str]:
    rows = con.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main' ORDER BY table_name"
    ).fetchall()
    return [str(r[0]) for r in rows]


def _sql_path(path: Path) -> str:
    return str(path).replace("'", "''")


def export_practice(con: duckdb.DuckDBPyConnection, out_dir: Path) -> dict[str, int]:
    (out_dir / "parquet").mkdir(parents=True, exist_ok=True)
    (out_dir / "csv").mkdir(parents=True, exist_ok=True)
    practice_path = out_dir / "ff_practice.duckdb"
    if practice_path.exists():
        practice_path.unlink()
    con.execute(f"ATTACH '{_sql_path(practice_path)}' AS practice")
    counts: dict[str, int] = {}
    for table in _tables(con):
        con.execute(f"CREATE TABLE practice.{table} AS SELECT * FROM main.{table}")
        con.execute(f"COPY main.{table} TO '{_sql_path(out_dir / 'parquet' / f'{table}.parquet')}' (FORMAT PARQUET)")
        con.execute(f"COPY main.{table} TO '{_sql_path(out_dir / 'csv' / f'{table}.csv')}' (FORMAT CSV, HEADER)")
        counts[table] = int(con.execute(f"SELECT count(*) FROM main.{table}").fetchone()[0])
    con.execute("DETACH practice")
    (out_dir / "README.md").write_text(_README.format(tables=", ".join(counts)))
    return counts
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./.venv/bin/python -m pytest tests/test_export.py -q`
Expected: 1 passed

- [ ] **Step 5: Commit**

```bash
git add app/data/export.py tests/test_export.py
git commit -m "Add practice data export for Python and R"
```

---

### Task 8: Refresh CLI

**Files:**
- Create: `app/__main__.py`
- Create: `app/refresh.py`
- Test: `tests/test_refresh.py`

**Interfaces:**
- Consumes: everything from Tasks 1 through 7.
- Produces: `refresh(cfg: Config, client: SleeperClient, con, store, http: httpx.Client, season_loaders=SEASON_LOADERS, static_loaders=STATIC_LOADERS, now: datetime) -> RefreshSummary` where `RefreshSummary(league_name: str, week: int, tables: dict[str, int], snapshot_rows: int, errors: list[str], unmapped_scoring: list[str])`; DuckDB tables `league_meta` (one row: league_id, name, season, week, refreshed_at) and `sleeper_players` (sleeper_id, full_name, position, team, age, injury_status, gsis_id) written on each refresh.
- CLI: `python -m app refresh`, `python -m app export [--out DIR]`, `python -m app backtest` (added in Task 13).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_refresh.py
from datetime import datetime
from pathlib import Path

import httpx
import polars as pl

from app.config import Config
from app.data.nflverse import connect
from app.data.store import Store
from app.refresh import refresh
from app.sleeper import SleeperClient

LEAGUE = {"league_id": "1", "name": "Test League", "season": "2025", "total_rosters": 10,
          "scoring_settings": {"rec": 1.0, "rec_yd": 0.1, "weird_key": 3.0},
          "roster_positions": ["QB", "RB", "WR", "TE", "FLEX", "BN"], "previous_league_id": None}


def sleeper_handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path == "/v1/state/nfl":
        return httpx.Response(200, json={"week": 2, "season": "2025"})
    if path == "/v1/league/1":
        return httpx.Response(200, json=LEAGUE)
    if path == "/v1/players/nfl":
        return httpx.Response(200, json={
            "4046": {"full_name": "Patrick Mahomes", "position": "QB", "team": "KC", "age": 30, "injury_status": None},
            "DET": {"full_name": "Detroit Lions", "position": "DEF", "team": "DET"},
        })
    return httpx.Response(200, json=[])


def market_handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json=[
        {"player": {"name": "Patrick Mahomes", "sleeperId": "4046", "position": "QB", "maybeTeam": "KC"},
         "value": 5000, "overallRank": 30, "positionRank": 3, "trend30Day": 10},
    ])


def test_refresh_populates_tables_and_summary(tmp_path: Path) -> None:
    cfg = Config(league_id="1", root=tmp_path, stats_from=2025, detail_from=2025, current_season=2025)
    store = Store(cfg.sqlite_path)
    con = connect(cfg.duckdb_path)
    client = SleeperClient(store, http=httpx.Client(transport=httpx.MockTransport(sleeper_handler)), refresh=True)
    market_http = httpx.Client(transport=httpx.MockTransport(market_handler))
    season_loaders = {"player_stats": lambda seasons: pl.DataFrame({
        "player_id": ["00-1"], "season": seasons[:1], "week": [1], "position": ["QB"], "passing_yards": [300.0]})}
    static_loaders = {"ff_playerids": lambda: pl.DataFrame({
        "gsis_id": ["00-1"], "sleeper_id": [4046], "fantasypros_id": [1], "pfr_id": ["x"],
        "name": ["Patrick Mahomes"], "position": ["QB"], "team": ["KC"], "birthdate": ["1995-09-17"]})}
    summary = refresh(cfg, client, con, store, market_http, season_loaders, static_loaders,
                      now=datetime(2025, 9, 16, 12, 0, 0))
    assert summary.league_name == "Test League" and summary.week == 2
    assert summary.tables["player_stats"] == 1 and summary.tables["ff_playerids"] == 1
    assert summary.snapshot_rows == 1
    assert summary.unmapped_scoring == ["weird_key"]
    meta = con.execute("SELECT league_id, name, week FROM league_meta").fetchone()
    assert meta == ("1", "Test League", 2)
    players = con.execute("SELECT sleeper_id, gsis_id FROM sleeper_players ORDER BY sleeper_id").fetchall()
    assert players == [("4046", "00-1"), ("DET", None)]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/bin/python -m pytest tests/test_refresh.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.refresh'`

- [ ] **Step 3: Implement refresh and CLI**

```python
# app/refresh.py
"""One refresh: Sleeper league data, nflverse tables, market snapshot, identity diagnostics."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping

import duckdb
import httpx
import polars as pl

from app.config import Config
from app.data.identity import id_hub
from app.data.nflverse import (
    SEASON_LOADERS, STATIC_LOADERS, SeasonLoader, StaticLoader, ingest_seasons, ingest_static,
    loaded_seasons, replace_table,
)
from app.data.scoring import unmapped_keys
from app.data.store import Store
from app.market import snapshot_fantasycalc
from app.sleeper import SleeperClient


@dataclass
class RefreshSummary:
    league_name: str
    week: int
    tables: dict[str, int] = field(default_factory=dict)
    snapshot_rows: int = 0
    errors: list[str] = field(default_factory=list)
    unmapped_scoring: list[str] = field(default_factory=list)


def _pull_league(client: SleeperClient, league_id: str, week: int) -> dict[str, Any]:
    league = client.league(league_id)
    client.users(league_id)
    client.rosters(league_id)
    client.traded_picks(league_id)
    for w in range(1, week + 1):
        client.matchups(league_id, w)
        client.transactions(league_id, w)
    return league


def _seasons_to_load(con: duckdb.DuckDBPyConnection, table: str, wanted: list[int], current: int) -> list[int]:
    have = set(loaded_seasons(con, table))
    return sorted({s for s in wanted if s not in have} | {current})


def _ingest(
    cfg: Config, con: duckdb.DuckDBPyConnection,
    season_loaders: Mapping[str, SeasonLoader], static_loaders: Mapping[str, StaticLoader],
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for table, load in season_loaders.items():
        wanted = cfg.stats_seasons if table == "player_stats" else cfg.detail_seasons
        seasons = _seasons_to_load(con, table, wanted, cfg.current_season)
        counts.update(ingest_seasons(con, seasons, {table: load}))
    counts.update(ingest_static(con, static_loaders))
    return counts


def _write_players(con: duckdb.DuckDBPyConnection, players: Mapping[str, Mapping[str, Any]]) -> int:
    rows = [
        {"sleeper_id": pid, "full_name": p.get("full_name"), "position": p.get("position"),
         "team": p.get("team"), "age": float(p["age"]) if p.get("age") is not None else None,
         "injury_status": p.get("injury_status")}
        for pid, p in players.items()
    ]
    schema = {"sleeper_id": pl.Utf8, "full_name": pl.Utf8, "position": pl.Utf8, "team": pl.Utf8,
              "age": pl.Float64, "injury_status": pl.Utf8}
    frame = pl.DataFrame(rows, schema=schema)
    hub = id_hub(con.execute("SELECT * FROM ff_playerids").pl()).select("sleeper_id", "gsis_id").drop_nulls()
    joined = frame.join(hub.unique(subset=["sleeper_id"]), on="sleeper_id", how="left")
    return replace_table(con, "sleeper_players", joined)


def _write_meta(con: duckdb.DuckDBPyConnection, league: Mapping[str, Any], week: int, now: datetime) -> None:
    frame = pl.DataFrame({
        "league_id": [str(league["league_id"])], "name": [str(league["name"])],
        "season": [int(league["season"])], "week": [week], "refreshed_at": [now.isoformat()],
    })
    replace_table(con, "league_meta", frame)


def refresh(
    cfg: Config,
    client: SleeperClient,
    con: duckdb.DuckDBPyConnection,
    store: Store,
    market_http: httpx.Client,
    season_loaders: Mapping[str, SeasonLoader] = SEASON_LOADERS,
    static_loaders: Mapping[str, StaticLoader] = STATIC_LOADERS,
    now: datetime | None = None,
) -> RefreshSummary:
    stamp = now or datetime.now()
    week = int(client.state()["week"])
    league = _pull_league(client, cfg.league_id, week)
    summary = RefreshSummary(league_name=str(league["name"]), week=week)
    summary.tables = _ingest(cfg, con, season_loaders, static_loaders)
    summary.tables["sleeper_players"] = _write_players(con, client.players())
    _write_meta(con, league, week, stamp)
    try:
        summary.snapshot_rows = snapshot_fantasycalc(store, market_http, league, stamp.isoformat())
    except httpx.HTTPError as exc:
        summary.errors.append(f"fantasycalc: {exc}")
    summary.unmapped_scoring = unmapped_keys(league["scoring_settings"])
    summary.errors.extend(client.errors)
    return summary
```

```python
# app/__main__.py
"""Command line entry: refresh, export, backtest."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import httpx

from app.config import Config, load_config
from app.data.export import export_practice
from app.data.nflverse import connect
from app.data.store import Store
from app.refresh import refresh
from app.sleeper import SleeperClient

ROOT = Path(__file__).resolve().parent.parent


def _require_league(cfg: Config) -> None:
    if not cfg.league_id:
        sys.exit("No league id. Put league_id = \"...\" in config.local.toml or set FF_LEAGUE_ID.")


def cmd_refresh(cfg: Config) -> int:
    _require_league(cfg)
    store = Store(cfg.sqlite_path)
    con = connect(cfg.duckdb_path)
    client = SleeperClient(store, refresh=True)
    summary = refresh(cfg, client, con, store, httpx.Client(timeout=30.0))
    print(f"{summary.league_name}: week {summary.week}")
    for table, rows in sorted(summary.tables.items()):
        print(f"  {table:<20} {rows:>8} rows")
    print(f"  market snapshot      {summary.snapshot_rows:>8} rows")
    if summary.unmapped_scoring:
        print(f"  unmapped scoring keys: {', '.join(summary.unmapped_scoring)}")
    for err in summary.errors:
        print(f"  error: {err}")
    return 1 if summary.errors else 0


def cmd_export(cfg: Config, out: Path) -> int:
    con = connect(cfg.duckdb_path)
    counts = export_practice(con, out)
    for table, rows in counts.items():
        print(f"  {table:<20} {rows:>8} rows")
    print(f"written to {out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ff-analyst")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("refresh")
    export = sub.add_parser("export")
    export.add_argument("--out", type=Path, default=Path.home() / "ff-analyst-data")
    args = parser.parse_args(argv)
    cfg = load_config(ROOT, os.environ)
    if args.command == "refresh":
        return cmd_refresh(cfg)
    return cmd_export(cfg, args.out)


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./.venv/bin/python -m pytest tests/test_refresh.py -q`
Expected: 1 passed

- [ ] **Step 5: Run the full suite and commit**

Run: `./.venv/bin/python -m pytest -q`
Expected: all passed

```bash
git add app/refresh.py app/__main__.py tests/test_refresh.py
git commit -m "Add refresh pipeline and command line entry"
```

---

### Task 9: Replacement level from roster slots

**Files:**
- Create: `app/model/replacement.py`
- Test: `tests/test_replacement.py`

**Interfaces:**
- Produces: `FLEX_ELIGIBILITY: dict[str, tuple[str, ...]]`; `starter_slots(roster_positions) -> list[str]` (drops BN, IR, TAXI); `dedicated_counts(roster_positions) -> dict[str, int]`; `starters_per_position(roster_positions, n_teams, ppg: pl.DataFrame) -> dict[str, int]` where `ppg` has `player_id, position, ppg`; `replacement_ranks(roster_positions, n_teams, ppg) -> dict[str, int]`; `replacement_ppg(ppg, ranks) -> dict[str, float]`; `replacement_from_rosters(ppg, rostered_ids: set[str]) -> dict[str, float]`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_replacement.py
import polars as pl

from app.model.replacement import (
    dedicated_counts, replacement_from_rosters, replacement_ppg, replacement_ranks, starter_slots,
    starters_per_position,
)

SLOTS = ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "K", "DEF", "BN", "BN", "BN", "BN", "BN", "BN", "IR"]


def pool() -> pl.DataFrame:
    rows = []
    for pos, count, top in (("QB", 30, 22.0), ("RB", 60, 20.0), ("WR", 80, 19.0), ("TE", 30, 12.0), ("K", 30, 8.0), ("DEF", 32, 8.0)):
        for i in range(count):
            rows.append({"player_id": f"{pos}{i}", "position": pos, "ppg": top - i * 0.25})
    return pl.DataFrame(rows)


def test_starter_slots_and_dedicated_counts() -> None:
    assert starter_slots(SLOTS) == ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "K", "DEF"]
    assert dedicated_counts(SLOTS) == {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "K": 1, "DEF": 1}


def test_flex_goes_to_best_remaining_eligible_players() -> None:
    starters = starters_per_position(SLOTS, 10, pool())
    assert starters["QB"] == 10 and starters["K"] == 10 and starters["DEF"] == 10
    assert starters["RB"] + starters["WR"] + starters["TE"] == 60
    assert starters["RB"] >= 20 and starters["WR"] >= 20 and starters["TE"] >= 10


def test_replacement_ranks_add_bench_share_to_skill_positions_only() -> None:
    ranks = replacement_ranks(SLOTS, 10, pool())
    starters = starters_per_position(SLOTS, 10, pool())
    assert ranks["K"] == starters["K"] + 1 and ranks["DEF"] == starters["DEF"] + 1
    skill_bench = sum(ranks[p] - starters[p] - 1 for p in ("QB", "RB", "WR", "TE"))
    assert skill_bench == 60


def test_replacement_ppg_reads_rank_and_clamps() -> None:
    frame = pl.DataFrame({"player_id": ["a", "b", "c"], "position": ["TE"] * 3, "ppg": [10.0, 8.0, 6.0]})
    assert replacement_ppg(frame, {"TE": 2}) == {"TE": 8.0}
    assert replacement_ppg(frame, {"TE": 9}) == {"TE": 6.0}


def test_replacement_from_rosters_is_best_unrostered() -> None:
    frame = pl.DataFrame({"player_id": ["a", "b", "c"], "position": ["RB"] * 3, "ppg": [10.0, 8.0, 6.0]})
    assert replacement_from_rosters(frame, {"a", "b"}) == {"RB": 6.0}
    assert replacement_from_rosters(frame, {"a", "b", "c"}) == {"RB": 0.0}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/bin/python -m pytest tests/test_replacement.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.model.replacement'`

- [ ] **Step 3: Implement replacement level**

```python
# app/model/replacement.py
"""Starter counts and replacement level derived from the league's roster slots."""
from __future__ import annotations

from collections import Counter
from typing import Sequence

import polars as pl

from app.data.scoring import SKILL_POSITIONS

NON_STARTER_SLOTS = ("BN", "IR", "TAXI")
FLEX_ELIGIBILITY: dict[str, tuple[str, ...]] = {
    "FLEX": ("RB", "WR", "TE"),
    "SUPER_FLEX": ("QB", "RB", "WR", "TE"),
    "REC_FLEX": ("WR", "TE"),
    "WRRB_FLEX": ("RB", "WR"),
}


def starter_slots(roster_positions: Sequence[str]) -> list[str]:
    return [slot for slot in roster_positions if slot not in NON_STARTER_SLOTS]


def dedicated_counts(roster_positions: Sequence[str]) -> dict[str, int]:
    return dict(Counter(slot for slot in starter_slots(roster_positions) if slot not in FLEX_ELIGIBILITY))


def _flex_slots(roster_positions: Sequence[str]) -> list[str]:
    return [slot for slot in starter_slots(roster_positions) if slot in FLEX_ELIGIBILITY]


def starters_per_position(roster_positions: Sequence[str], n_teams: int, ppg: pl.DataFrame) -> dict[str, int]:
    """Dedicated slots times teams, plus flex slots given to the best remaining eligible players."""
    starters = {pos: count * n_teams for pos, count in dedicated_counts(roster_positions).items()}
    ranked = ppg.sort("ppg", descending=True).with_columns(
        pl.col("ppg").rank("ordinal", descending=True).over("position").alias("pos_rank")
    )
    taken = ranked.filter(pl.col("pos_rank") <= pl.col("position").replace_strict(starters, default=0))
    remaining = ranked.join(taken.select("player_id"), on="player_id", how="anti")
    for slot in _flex_slots(roster_positions):
        eligible = remaining.filter(pl.col("position").is_in(FLEX_ELIGIBILITY[slot])).head(n_teams)
        for pos, count in Counter(eligible["position"].to_list()).items():
            starters[pos] = starters.get(pos, 0) + count
        remaining = remaining.join(eligible.select("player_id"), on="player_id", how="anti")
    return starters


def replacement_ranks(roster_positions: Sequence[str], n_teams: int, ppg: pl.DataFrame) -> dict[str, int]:
    """Rank of the first replacement-level player: starters plus a bench share, then one more."""
    starters = starters_per_position(roster_positions, n_teams, ppg)
    bench_total = sum(1 for slot in roster_positions if slot == "BN") * n_teams
    skill_starters = sum(starters.get(p, 0) for p in SKILL_POSITIONS)
    ranks: dict[str, int] = {}
    allocated = 0
    for pos, count in starters.items():
        share = round(bench_total * count / skill_starters) if pos in SKILL_POSITIONS and skill_starters else 0
        allocated += share
        ranks[pos] = count + share + 1
    if skill_starters:
        ranks["RB"] = ranks.get("RB", 1) + (bench_total - allocated)
    return ranks


def replacement_ppg(ppg: pl.DataFrame, ranks: dict[str, int]) -> dict[str, float]:
    out: dict[str, float] = {}
    for pos, rank in ranks.items():
        values = ppg.filter(pl.col("position") == pos).sort("ppg", descending=True)["ppg"].to_list()
        if values:
            out[pos] = float(values[min(rank, len(values)) - 1])
    return out


def replacement_from_rosters(ppg: pl.DataFrame, rostered_ids: set[str]) -> dict[str, float]:
    free = ppg.filter(~pl.col("player_id").is_in(list(rostered_ids)))
    best = free.group_by("position").agg(pl.col("ppg").max())
    out = {str(pos): float(val) for pos, val in zip(best["position"], best["ppg"])}
    for pos in ppg["position"].unique().to_list():
        out.setdefault(str(pos), 0.0)
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./.venv/bin/python -m pytest tests/test_replacement.py -q`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add app/model/replacement.py tests/test_replacement.py
git commit -m "Add starter counts and replacement level from roster slots"
```

---

### Task 10: Seasonal PPG and age curve

**Files:**
- Create: `app/model/agecurve.py`
- Test: `tests/test_agecurve.py`

**Interfaces:**
- Consumes: scored weekly frame from `score_weeks` (Task 4) with columns `player_id, season, week, season_type, position, points`.
- Produces: `season_ppg(scored: pl.DataFrame, min_games: int = 1) -> pl.DataFrame` with `player_id, season, position, games, ppg` over REG weeks only; `fit_age_curve(seasonal: pl.DataFrame, ages: pl.DataFrame, min_pairs: int = 15) -> pl.DataFrame` with `position, age, factor` where `ages` has `player_id, season, age`; `age_factor(curve, position, age) -> float` defaulting to 1.0; `player_ages(hub: pl.DataFrame, seasons: list[int]) -> pl.DataFrame` with `player_id (gsis), season, age` computed as season minus birth year.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_agecurve.py
from datetime import date

import polars as pl

from app.model.agecurve import age_factor, fit_age_curve, player_ages, season_ppg


def test_season_ppg_uses_regular_season_only() -> None:
    scored = pl.DataFrame({
        "player_id": ["a"] * 3, "season": [2024] * 3, "week": [1, 2, 19],
        "season_type": ["REG", "REG", "POST"], "position": ["RB"] * 3, "points": [10.0, 20.0, 50.0],
    })
    out = season_ppg(scored)
    assert out.row(0, named=True) == {"player_id": "a", "season": 2024, "position": "RB", "games": 2, "ppg": 15.0}


def test_fit_age_curve_recovers_decline_and_clamps() -> None:
    rows = []
    for i in range(40):
        rows.append({"player_id": f"p{i}", "season": 2022, "position": "RB", "games": 16, "ppg": 10.0})
        rows.append({"player_id": f"p{i}", "season": 2023, "position": "RB", "games": 16, "ppg": 8.0})
        rows.append({"player_id": f"y{i}", "season": 2022, "position": "RB", "games": 16, "ppg": 10.0})
        rows.append({"player_id": f"y{i}", "season": 2023, "position": "RB", "games": 16, "ppg": 14.0})
    seasonal = pl.DataFrame(rows)
    ages = pl.DataFrame({
        "player_id": [f"p{i}" for i in range(40)] * 2 + [f"y{i}" for i in range(40)] * 2,
        "season": [2022] * 40 + [2023] * 40 + [2022] * 40 + [2023] * 40,
        "age": [30] * 40 + [31] * 40 + [23] * 40 + [24] * 40,
    })
    curve = fit_age_curve(seasonal, ages)
    assert age_factor(curve, "RB", 31) == 0.8
    assert age_factor(curve, "RB", 24) == 1.1
    assert age_factor(curve, "RB", 40) == 1.0
    assert age_factor(curve, "WR", 24) == 1.0


def test_player_ages_from_birthdate() -> None:
    hub = pl.DataFrame({"gsis_id": ["00-1", "00-2"], "birthdate": [date(1995, 9, 17), None]})
    out = player_ages(hub, [2024, 2025])
    assert out.filter(pl.col("player_id") == "00-1").sort("season")["age"].to_list() == [29, 30]
    assert out.filter(pl.col("player_id") == "00-2").height == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/bin/python -m pytest tests/test_agecurve.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.model.agecurve'`

- [ ] **Step 3: Implement the age curve**

```python
# app/model/agecurve.py
"""Season-level points per game and a per-position age adjustment learned from history."""
from __future__ import annotations

import polars as pl

FACTOR_MIN = 0.7
FACTOR_MAX = 1.1
MIN_GAMES_FOR_PAIR = 8


def season_ppg(scored: pl.DataFrame, min_games: int = 1) -> pl.DataFrame:
    return (
        scored.filter(pl.col("season_type") == "REG")
        .group_by("player_id", "season", "position")
        .agg(pl.len().alias("games"), pl.col("points").mean().alias("ppg"))
        .filter(pl.col("games") >= min_games)
        .sort("player_id", "season")
    )


def player_ages(hub: pl.DataFrame, seasons: list[int]) -> pl.DataFrame:
    base = hub.filter(pl.col("birthdate").is_not_null()).select(
        pl.col("gsis_id").alias("player_id"), pl.col("birthdate").dt.year().alias("birth_year")
    )
    frames = [base.with_columns(pl.lit(s).alias("season")) for s in seasons]
    return pl.concat(frames).select(
        "player_id", "season", (pl.col("season") - pl.col("birth_year")).cast(pl.Int64).alias("age")
    )


def fit_age_curve(seasonal: pl.DataFrame, ages: pl.DataFrame, min_pairs: int = 15) -> pl.DataFrame:
    """Median year-over-year PPG ratio by position and age, clamped to a sane band."""
    eligible = seasonal.filter(pl.col("games") >= MIN_GAMES_FOR_PAIR)
    nxt = eligible.select(
        "player_id", (pl.col("season") - 1).alias("season"), pl.col("ppg").alias("next_ppg")
    )
    pairs = (
        eligible.join(nxt, on=["player_id", "season"], how="inner")
        .with_columns((pl.col("season") + 1).alias("next_season"))
        .join(ages, left_on=["player_id", "next_season"], right_on=["player_id", "season"], how="inner")
        .filter(pl.col("ppg") > 0)
        .with_columns((pl.col("next_ppg") / pl.col("ppg")).alias("ratio"))
    )
    return (
        pairs.group_by("position", "age")
        .agg(pl.col("ratio").median().alias("factor"), pl.len().alias("n"))
        .filter(pl.col("n") >= min_pairs)
        .with_columns(pl.col("factor").clip(FACTOR_MIN, FACTOR_MAX))
        .select("position", "age", "factor")
        .sort("position", "age")
    )


def age_factor(curve: pl.DataFrame, position: str, age: int) -> float:
    hit = curve.filter((pl.col("position") == position) & (pl.col("age") == age))
    return float(hit["factor"][0]) if hit.height else 1.0
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./.venv/bin/python -m pytest tests/test_agecurve.py -q`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add app/model/agecurve.py tests/test_agecurve.py
git commit -m "Add seasonal points per game and age curve"
```

---

### Task 11: Projections: features, blend, per-week points

**Files:**
- Create: `app/model/projections.py`
- Test: `tests/test_projections.py`

**Interfaces:**
- Consumes: `score_weeks`, `expected_points`, `SKILL_POSITIONS` (Task 4); `consensus_snapshot`, `first_gameday` (Task 6); `id_hub` (Task 5); `season_ppg`, `player_ages`, `age_factor`, `fit_age_curve` (Task 10); `replacement_ranks`, `replacement_ppg` (Task 9).
- Produces:
  - `BlendParams(k: float = 4.0, a: float = 0.6, b: float = 0.5)` frozen dataclass.
  - `rank_to_ppg_curve(seasonal: pl.DataFrame, season: int) -> pl.DataFrame` with `position, pos_rank, ppg` from that season's PPG for players with at least 8 games, ranked descending within position.
  - `curve_ppg(curve, position, pos_rank) -> float` reading the curve, clamping to the last rank.
  - `positional_ranks(consensus: pl.DataFrame) -> pl.DataFrame` adds `pos_rank` by ecr within `pos`, and maps `pos` DST to DEF.
  - `Inputs` dataclass bundling frames the feature builder needs: `scored: pl.DataFrame` (weekly scored stats, REG only, columns player_id, season, week, position, team, opponent_team, points), `opportunity: pl.DataFrame` (columns player_id, season, week, exp_points), `hub: pl.DataFrame`, `consensus_preseason: pl.DataFrame` (fantasypros_id, pos, ecr), `prev_curve: pl.DataFrame`, `prev_seasonal: pl.DataFrame` (player_id, position, ppg, games for previous season), `age_curve: pl.DataFrame`, `ages: pl.DataFrame` (player_id, age for this season), `replacement: dict[str, float]`.
  - `build_features(inputs: Inputs, season: int, week: int) -> pl.DataFrame` with columns `player_id, position, ecr_ppg, last_ppg, age_mult, usage_ppg, actual_ppg, n` for every skill-position player in the preseason consensus that has a gsis id. The prior components stay separate here; `blend` combines them.
  - `blend(features: pl.DataFrame, params: BlendParams) -> pl.DataFrame` adds `prior` and `ppg` using `prior = age_mult * (b * ecr_ppg + (1 - b) * last_ppg)` and `ppg = (k * prior + n * (a * usage_ppg + (1 - a) * actual_ppg)) / (k + n)`; when `n == 0` the data term is absent so `ppg == prior`; null `usage_ppg` falls back to `actual_ppg`.
  - `opponent_factors(scored: pl.DataFrame, through_week: int, shrink_games: int = 6) -> pl.DataFrame` with `defense: Utf8, position, factor` in `[0.9, 1.1]`.
  - `weekly_projection(ppg: pl.DataFrame, schedule: pl.DataFrame, week_from: int, week_to: int, teams: pl.DataFrame, factors: pl.DataFrame, injured: pl.DataFrame) -> pl.DataFrame` with `player_id, week, points`; `schedule` has `week, team, opponent` rows for each team-week (bye = absent); `teams` has `player_id, team`; `injured` has `player_id, status` where status IR zeroes every week and Out zeroes `week_from` only.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_projections.py
import polars as pl

from app.model.projections import (
    BlendParams, blend, curve_ppg, opponent_factors, positional_ranks, rank_to_ppg_curve,
    weekly_projection,
)


def test_rank_curve_orders_within_position_and_clamps() -> None:
    seasonal = pl.DataFrame({
        "player_id": ["a", "b", "c", "d"], "season": [2024] * 4, "position": ["RB", "RB", "WR", "RB"],
        "games": [16, 16, 16, 3], "ppg": [10.0, 15.0, 12.0, 30.0],
    })
    curve = rank_to_ppg_curve(seasonal, 2024)
    assert curve.filter(pl.col("position") == "RB")["ppg"].to_list() == [15.0, 10.0]
    assert curve_ppg(curve, "RB", 1) == 15.0
    assert curve_ppg(curve, "RB", 50) == 10.0
    assert curve_ppg(curve, "TE", 1) == 0.0


def test_positional_ranks_and_dst_alias() -> None:
    snap = pl.DataFrame({"fantasypros_id": ["1", "2", "3"], "pos": ["WR", "DST", "WR"], "ecr": [5.0, 90.0, 2.0]})
    out = positional_ranks(snap).sort("fantasypros_id")
    assert out["pos_rank"].to_list() == [2, 1, 1]
    assert out["pos"].to_list() == ["WR", "DEF", "WR"]


def test_blend_returns_prior_at_zero_games_and_data_at_many() -> None:
    feats = pl.DataFrame({
        "player_id": ["new", "vet"], "position": ["RB", "RB"],
        "ecr_ppg": [12.0, 12.0], "last_ppg": [8.0, 8.0], "age_mult": [1.0, 0.9],
        "usage_ppg": [None, 20.0], "actual_ppg": [None, 16.0], "n": [0, 1000],
    })
    out = blend(feats, BlendParams(k=4.0, a=0.5, b=0.5)).sort("player_id")
    new, vet = out.row(0, named=True), out.row(1, named=True)
    assert new["prior"] == 10.0 and new["ppg"] == 10.0
    assert vet["prior"] == 9.0 and abs(vet["ppg"] - 18.0) < 0.05


def test_blend_uses_actual_when_usage_missing() -> None:
    feats = pl.DataFrame({
        "player_id": ["x"], "position": ["WR"], "ecr_ppg": [10.0], "last_ppg": [10.0], "age_mult": [1.0],
        "usage_ppg": [None], "actual_ppg": [20.0], "n": [4],
    })
    out = blend(feats, BlendParams(k=4.0, a=0.5, b=0.5))
    assert out["ppg"][0] == 15.0


def test_opponent_factors_are_shrunk_and_clamped() -> None:
    rows = []
    for week in range(1, 7):
        rows += [
            {"season": 2025, "week": week, "position": "WR", "team": "A", "opponent_team": "SOFT", "points": 30.0},
            {"season": 2025, "week": week, "position": "WR", "team": "B", "opponent_team": "HARD", "points": 5.0},
            {"season": 2025, "week": week, "position": "WR", "team": "C", "opponent_team": "AVG", "points": 17.5},
        ]
    factors = opponent_factors(pl.DataFrame(rows), through_week=6)
    f = dict(zip(factors["defense"], factors["factor"]))
    assert f["SOFT"] == 1.1 and f["HARD"] == 0.9 and abs(f["AVG"] - 1.0) < 1e-9


def test_weekly_projection_handles_bye_injury_and_factor() -> None:
    ppg = pl.DataFrame({"player_id": ["p", "q"], "position": ["WR", "WR"], "ppg": [10.0, 10.0]})
    teams = pl.DataFrame({"player_id": ["p", "q"], "team": ["A", "B"]})
    schedule = pl.DataFrame({"week": [5, 6, 5], "team": ["A", "A", "B"], "opponent": ["X", "Y", "X"]})
    factors = pl.DataFrame({"defense": ["X", "Y"], "position": ["WR", "WR"], "factor": [1.1, 0.9]})
    injured = pl.DataFrame({"player_id": ["q"], "status": ["Out"]})
    out = weekly_projection(ppg, schedule, 5, 6, teams, factors, injured).sort("player_id", "week")
    assert out.filter(pl.col("player_id") == "p")["points"].to_list() == [11.0, 9.0]
    assert out.filter(pl.col("player_id") == "q")["points"].to_list() == [0.0, 0.0]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/bin/python -m pytest tests/test_projections.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.model.projections'`

- [ ] **Step 3: Implement projections**

```python
# app/model/projections.py
"""Shrinkage-blend projections: prior from consensus and history, data from usage and results."""
from __future__ import annotations

from dataclasses import dataclass

import polars as pl

from app.data.scoring import SKILL_POSITIONS
from app.model.agecurve import age_factor

FACTOR_LOW = 0.9
FACTOR_HIGH = 1.1
CURVE_MIN_GAMES = 8


@dataclass(frozen=True)
class BlendParams:
    k: float = 4.0
    a: float = 0.6
    b: float = 0.5


@dataclass(frozen=True)
class Inputs:
    scored: pl.DataFrame
    opportunity: pl.DataFrame
    hub: pl.DataFrame
    consensus_preseason: pl.DataFrame
    prev_curve: pl.DataFrame
    prev_seasonal: pl.DataFrame
    age_curve: pl.DataFrame
    ages: pl.DataFrame
    replacement: dict[str, float]


def rank_to_ppg_curve(seasonal: pl.DataFrame, season: int) -> pl.DataFrame:
    return (
        seasonal.filter((pl.col("season") == season) & (pl.col("games") >= CURVE_MIN_GAMES))
        .sort("ppg", descending=True)
        .with_columns(pl.col("ppg").rank("ordinal", descending=True).over("position").cast(pl.Int64).alias("pos_rank"))
        .select("position", "pos_rank", "ppg")
        .sort("position", "pos_rank")
    )


def curve_ppg(curve: pl.DataFrame, position: str, pos_rank: int) -> float:
    rows = curve.filter(pl.col("position") == position)
    if rows.height == 0:
        return 0.0
    idx = min(int(pos_rank), rows.height) - 1
    return float(rows["ppg"][idx])


def positional_ranks(consensus: pl.DataFrame) -> pl.DataFrame:
    return consensus.with_columns(
        pl.when(pl.col("pos") == "DST").then(pl.lit("DEF")).otherwise(pl.col("pos")).alias("pos")
    ).with_columns(pl.col("ecr").rank("ordinal").over("pos").cast(pl.Int64).alias("pos_rank"))


def _ecr_prior(inputs: Inputs) -> pl.DataFrame:
    ranked = positional_ranks(inputs.consensus_preseason).filter(pl.col("pos").is_in(SKILL_POSITIONS))
    with_ids = ranked.join(inputs.hub.select("fantasypros_id", "gsis_id"), on="fantasypros_id", how="inner")
    ecr_ppg = [curve_ppg(inputs.prev_curve, str(p), int(r)) for p, r in zip(with_ids["pos"], with_ids["pos_rank"])]
    return with_ids.select(
        pl.col("gsis_id").alias("player_id"), pl.col("pos").alias("position")
    ).with_columns(pl.Series("ecr_ppg", ecr_ppg, dtype=pl.Float64)).unique(subset=["player_id"])


def _last_season(inputs: Inputs) -> pl.DataFrame:
    return inputs.prev_seasonal.filter(pl.col("games") >= 4).select("player_id", pl.col("ppg").alias("last_ppg"))


def _this_season(inputs: Inputs, season: int, week: int) -> pl.DataFrame:
    played = inputs.scored.filter((pl.col("season") == season) & (pl.col("week") < week))
    actual = played.group_by("player_id").agg(pl.col("points").mean().alias("actual_ppg"), pl.len().alias("n"))
    usage = (
        inputs.opportunity.filter((pl.col("season") == season) & (pl.col("week") < week))
        .group_by("player_id").agg(pl.col("exp_points").mean().alias("usage_ppg"))
    )
    return actual.join(usage, on="player_id", how="left")


def build_features(inputs: Inputs, season: int, week: int) -> pl.DataFrame:
    base = _ecr_prior(inputs)
    ages = inputs.ages.select("player_id", "age")
    joined = (
        base.join(_last_season(inputs), on="player_id", how="left")
        .join(ages, on="player_id", how="left")
        .join(_this_season(inputs, season, week), on="player_id", how="left")
    )
    repl = pl.col("position").replace_strict(inputs.replacement, default=0.0, return_dtype=pl.Float64)
    mults = [
        age_factor(inputs.age_curve, str(p), int(a)) if a is not None else 1.0
        for p, a in zip(joined["position"], joined["age"])
    ]
    return joined.with_columns(
        pl.col("last_ppg").fill_null(repl),
        pl.Series("age_mult", mults, dtype=pl.Float64),
        pl.col("n").fill_null(0).cast(pl.Int64),
    ).select("player_id", "position", "ecr_ppg", "last_ppg", "age_mult", "usage_ppg", "actual_ppg", "n")


def blend(features: pl.DataFrame, params: BlendParams) -> pl.DataFrame:
    prior = pl.col("age_mult") * (params.b * pl.col("ecr_ppg") + (1 - params.b) * pl.col("last_ppg"))
    usage = pl.coalesce(pl.col("usage_ppg"), pl.col("actual_ppg"))
    data = params.a * usage + (1 - params.a) * pl.col("actual_ppg")
    n = pl.col("n").cast(pl.Float64)
    ppg = pl.when(n > 0).then((params.k * pl.col("prior") + n * data) / (params.k + n)).otherwise(pl.col("prior"))
    return features.with_columns(prior.alias("prior")).with_columns(ppg.alias("ppg"))


def opponent_factors(scored: pl.DataFrame, through_week: int, shrink_games: int = 6) -> pl.DataFrame:
    played = scored.filter(pl.col("week") <= through_week)
    per_game = played.group_by("opponent_team", "position", "week").agg(pl.col("points").sum().alias("allowed"))
    league_avg = per_game.group_by("position").agg(pl.col("allowed").mean().alias("avg"))
    by_def = per_game.group_by("opponent_team", "position").agg(
        pl.col("allowed").mean().alias("mean_allowed"), pl.len().alias("games")
    )
    raw = by_def.join(league_avg, on="position").with_columns(
        (pl.col("mean_allowed") / pl.col("avg")).alias("raw")
    )
    shrunk = (pl.col("games") * pl.col("raw") + shrink_games) / (pl.col("games") + shrink_games)
    return raw.with_columns(shrunk.clip(FACTOR_LOW, FACTOR_HIGH).alias("factor")).select(
        pl.col("opponent_team").alias("defense"), "position", "factor"
    )


def weekly_projection(
    ppg: pl.DataFrame,
    schedule: pl.DataFrame,
    week_from: int,
    week_to: int,
    teams: pl.DataFrame,
    factors: pl.DataFrame,
    injured: pl.DataFrame,
) -> pl.DataFrame:
    weeks = pl.DataFrame({"week": list(range(week_from, week_to + 1))})
    grid = ppg.join(teams, on="player_id", how="left").join(weeks, how="cross")
    with_opp = grid.join(schedule, on=["week", "team"], how="left")
    with_factor = with_opp.join(
        factors, left_on=["opponent", "position"], right_on=["defense", "position"], how="left"
    ).with_columns(pl.col("factor").fill_null(1.0))
    status = injured.select("player_id", "status")
    with_status = with_factor.join(status, on="player_id", how="left")
    zero = (
        pl.col("opponent").is_null()
        | (pl.col("status") == "IR")
        | ((pl.col("status") == "Out") & (pl.col("week") == week_from))
    )
    points = pl.when(zero).then(0.0).otherwise(pl.col("ppg") * pl.col("factor"))
    return with_status.select("player_id", "week", points.alias("points"))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./.venv/bin/python -m pytest tests/test_projections.py -q`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add app/model/projections.py tests/test_projections.py
git commit -m "Add shrinkage blend projections and weekly points"
```

---

### Task 12: Backtest metrics, harness, tuning, report

**Files:**
- Create: `app/model/backtest.py`
- Test: `tests/test_backtest.py`

**Interfaces:**
- Consumes: Tasks 4, 5, 6, 9, 10, 11.
- Produces:
  - `spearman(pred: pl.Series, actual: pl.Series) -> float`; `mae(pred, actual) -> float`; `precision_at(frame: pl.DataFrame, pred_col, actual_col, n: int) -> float`.
  - `actual_ros(scored: pl.DataFrame, season: int, week_from: int) -> pl.DataFrame` with `player_id, ros_points` summing REG weeks `>= week_from`.
  - `remaining_games(schedules: pl.DataFrame, season, week_from) -> pl.DataFrame` with `team, games` counting REG games from week_from through the last REG week.
  - `market_prediction(consensus: pl.DataFrame, hub, prev_curve, games_per_player: pl.DataFrame) -> pl.DataFrame` with `player_id, position, pred` = curve ppg for the positional consensus rank times remaining games.
  - `model_prediction(features_blended: pl.DataFrame, games_per_player) -> pl.DataFrame` with `player_id, position, pred` = ppg times remaining games.
  - `evaluate(pred: pl.DataFrame, actual: pl.DataFrame, starters: dict[str, int]) -> pl.DataFrame` rows `position, spearman, mae, precision, n` including a pooled row with position `ALL` (precision pooled = mean of positional precisions).
  - `SeasonContext` dataclass and `load_season_context(con, season, scoring) -> SeasonContext` assembling frames from DuckDB: scored REG weeks for season and previous season, opportunity with exp_points, hub, preseason consensus (week 1), prev curve, prev seasonal, ages, schedules, and an age curve fitted from all seasons before `season`.
  - `week_features(ctx: SeasonContext, week: int, roster_positions, n_teams) -> pl.DataFrame` calling `build_features` with replacement from `replacement_ranks` over last season's PPG.
  - `run_backtest(con, scoring, roster_positions, n_teams, seasons: list[int], weeks: list[int], params: BlendParams) -> pl.DataFrame` long frame with `season, week, source ("model" or "consensus"), position, spearman, mae, precision, n`.
  - `tune(con, scoring, roster_positions, n_teams, seasons, weeks, grid: list[BlendParams]) -> BlendParams` minimizing pooled MAE minus pooled Spearman times 10 on the tuning seasons.
  - `write_report(metrics: pl.DataFrame, params: BlendParams, path: Path, passed: bool) -> None`.
  - `gate_passes(metrics: pl.DataFrame, eval_seasons: list[int]) -> bool`: pooled `ALL` rows for eval seasons, model mean Spearman >= consensus mean Spearman and model mean MAE <= consensus mean MAE.
  - `DEFAULT_GRID: list[BlendParams]` = every combination of k in (2, 4, 6, 8, 12), a in (0.3, 0.5, 0.7, 0.9), b in (0.3, 0.5, 0.7).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_backtest.py
import polars as pl

from app.model.backtest import (
    actual_ros, evaluate, gate_passes, mae, precision_at, remaining_games, spearman,
)


def test_spearman_and_mae_on_known_values() -> None:
    assert spearman(pl.Series([1.0, 2.0, 3.0]), pl.Series([10.0, 20.0, 30.0])) == 1.0
    assert spearman(pl.Series([1.0, 2.0, 3.0]), pl.Series([30.0, 20.0, 10.0])) == -1.0
    assert mae(pl.Series([1.0, 2.0]), pl.Series([2.0, 4.0])) == 1.5


def test_precision_at_top_n() -> None:
    frame = pl.DataFrame({"player_id": ["a", "b", "c", "d"], "pred": [10.0, 9.0, 8.0, 1.0],
                          "actual": [100.0, 1.0, 90.0, 80.0]})
    assert precision_at(frame, "pred", "actual", 2) == 0.5


def test_actual_ros_sums_regular_weeks_from_start() -> None:
    scored = pl.DataFrame({
        "player_id": ["a"] * 4, "season": [2024] * 4, "week": [3, 4, 5, 19],
        "season_type": ["REG", "REG", "REG", "POST"], "points": [1.0, 2.0, 4.0, 100.0],
    })
    assert actual_ros(scored, 2024, 4)["ros_points"][0] == 6.0


def test_remaining_games_counts_reg_games_per_team() -> None:
    sched = pl.DataFrame({
        "season": [2024] * 3, "week": [1, 2, 3], "game_type": ["REG"] * 3,
        "home_team": ["A", "A", "C"], "away_team": ["B", "C", "B"],
    })
    out = remaining_games(sched, 2024, 2)
    assert dict(zip(out["team"], out["games"])) == {"A": 1, "B": 1, "C": 2}


def test_evaluate_reports_positions_and_pooled() -> None:
    pred = pl.DataFrame({"player_id": ["a", "b", "c", "d"], "position": ["RB", "RB", "WR", "WR"], "pred": [10.0, 5.0, 8.0, 2.0]})
    actual = pl.DataFrame({"player_id": ["a", "b", "c", "d"], "ros_points": [12.0, 4.0, 9.0, 1.0]})
    out = evaluate(pred, actual, {"RB": 1, "WR": 1})
    assert set(out["position"].to_list()) == {"RB", "WR", "ALL"}
    pooled = out.filter(pl.col("position") == "ALL").row(0, named=True)
    assert pooled["spearman"] == 1.0 and pooled["precision"] == 1.0 and pooled["n"] == 4


def test_gate_compares_pooled_model_to_consensus() -> None:
    metrics = pl.DataFrame({
        "season": [2024, 2024, 2025, 2025], "week": [3] * 4,
        "source": ["model", "consensus", "model", "consensus"], "position": ["ALL"] * 4,
        "spearman": [0.7, 0.6, 0.7, 0.6], "mae": [30.0, 35.0, 31.0, 35.0], "precision": [0.5] * 4, "n": [100] * 4,
    })
    assert gate_passes(metrics, [2024, 2025])
    worse = metrics.with_columns(pl.when(pl.col("source") == "model").then(50.0).otherwise(pl.col("mae")).alias("mae"))
    assert not gate_passes(worse, [2024, 2025])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/bin/python -m pytest tests/test_backtest.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.model.backtest'`

- [ ] **Step 3: Implement the backtest module**

```python
# app/model/backtest.py
"""Walk-forward backtest of the value model against market consensus."""
from __future__ import annotations

from dataclasses import dataclass
from itertools import product
from pathlib import Path

import duckdb
import polars as pl

from app.data.identity import id_hub
from app.data.scoring import SKILL_POSITIONS, expected_points, score_weeks
from app.market import consensus_snapshot
from app.model.agecurve import fit_age_curve, player_ages, season_ppg
from app.model.projections import (
    BlendParams, Inputs, blend, build_features, curve_ppg, positional_ranks, rank_to_ppg_curve,
)
from app.model.replacement import replacement_ppg, replacement_ranks, starters_per_position

DEFAULT_GRID: list[BlendParams] = [
    BlendParams(k=k, a=a, b=b)
    for k, a, b in product((2.0, 4.0, 6.0, 8.0, 12.0), (0.3, 0.5, 0.7, 0.9), (0.3, 0.5, 0.7))
]
AGE_CURVE_FROM = 2012


def spearman(pred: pl.Series, actual: pl.Series) -> float:
    if len(pred) < 2:
        return 0.0
    rp = pred.rank("average").cast(pl.Float64)
    ra = actual.rank("average").cast(pl.Float64)
    corr = pl.DataFrame({"a": rp, "b": ra}).select(pl.corr("a", "b")).item()
    return float(corr) if corr is not None else 0.0


def mae(pred: pl.Series, actual: pl.Series) -> float:
    return float((pred - actual).abs().mean()) if len(pred) else 0.0


def precision_at(frame: pl.DataFrame, pred_col: str, actual_col: str, n: int) -> float:
    if frame.height == 0 or n <= 0:
        return 0.0
    top_pred = set(frame.sort(pred_col, descending=True).head(n)["player_id"].to_list())
    top_actual = set(frame.sort(actual_col, descending=True).head(n)["player_id"].to_list())
    return len(top_pred & top_actual) / n


def actual_ros(scored: pl.DataFrame, season: int, week_from: int) -> pl.DataFrame:
    return (
        scored.filter((pl.col("season") == season) & (pl.col("season_type") == "REG") & (pl.col("week") >= week_from))
        .group_by("player_id").agg(pl.col("points").sum().alias("ros_points"))
    )


def remaining_games(schedules: pl.DataFrame, season: int, week_from: int) -> pl.DataFrame:
    games = schedules.filter((pl.col("season") == season) & (pl.col("game_type") == "REG") & (pl.col("week") >= week_from))
    teams = pl.concat([games.select(pl.col("home_team").alias("team")), games.select(pl.col("away_team").alias("team"))])
    return teams.group_by("team").agg(pl.len().alias("games"))


def _games_per_player(teams: pl.DataFrame, per_team: pl.DataFrame) -> pl.DataFrame:
    return teams.join(per_team, on="team", how="left").with_columns(pl.col("games").fill_null(0)).select("player_id", "games")


def market_prediction(
    consensus: pl.DataFrame, hub: pl.DataFrame, prev_curve: pl.DataFrame, games_per_player: pl.DataFrame
) -> pl.DataFrame:
    ranked = positional_ranks(consensus).filter(pl.col("pos").is_in(SKILL_POSITIONS))
    with_ids = ranked.join(hub.select("fantasypros_id", "gsis_id"), on="fantasypros_id", how="inner")
    ppg = [curve_ppg(prev_curve, str(p), int(r)) for p, r in zip(with_ids["pos"], with_ids["pos_rank"])]
    frame = with_ids.select(pl.col("gsis_id").alias("player_id"), pl.col("pos").alias("position")).with_columns(
        pl.Series("ppg", ppg, dtype=pl.Float64)
    ).unique(subset=["player_id"])
    return frame.join(games_per_player, on="player_id", how="left").with_columns(
        (pl.col("ppg") * pl.col("games").fill_null(0)).alias("pred")
    ).select("player_id", "position", "pred")


def model_prediction(blended: pl.DataFrame, games_per_player: pl.DataFrame) -> pl.DataFrame:
    return blended.join(games_per_player, on="player_id", how="left").with_columns(
        (pl.col("ppg") * pl.col("games").fill_null(0)).alias("pred")
    ).select("player_id", "position", "pred")


def _metrics_row(frame: pl.DataFrame, position: str, n_top: int) -> dict[str, object]:
    return {
        "position": position,
        "spearman": spearman(frame["pred"], frame["ros_points"]),
        "mae": mae(frame["pred"], frame["ros_points"]),
        "precision": precision_at(frame, "pred", "ros_points", n_top),
        "n": frame.height,
    }


def evaluate(pred: pl.DataFrame, actual: pl.DataFrame, starters: dict[str, int]) -> pl.DataFrame:
    joined = pred.join(actual, on="player_id", how="left").with_columns(pl.col("ros_points").fill_null(0.0))
    rows = [
        _metrics_row(joined.filter(pl.col("position") == pos), pos, starters.get(pos, 0))
        for pos in sorted(joined["position"].unique().to_list())
    ]
    pooled = _metrics_row(joined, "ALL", 0)
    pooled["precision"] = sum(r["precision"] for r in rows) / len(rows) if rows else 0.0
    rows.append(pooled)
    return pl.DataFrame(rows)


@dataclass(frozen=True)
class SeasonContext:
    season: int
    scored: pl.DataFrame
    prev_seasonal: pl.DataFrame
    prev_curve: pl.DataFrame
    opportunity: pl.DataFrame
    hub: pl.DataFrame
    consensus_preseason: pl.DataFrame
    age_curve: pl.DataFrame
    ages: pl.DataFrame
    schedules: pl.DataFrame
    teams_by_week: pl.DataFrame


def _scored_weeks(con: duckdb.DuckDBPyConnection, seasons: list[int], scoring: dict[str, float]) -> pl.DataFrame:
    stats = con.execute(
        "SELECT * FROM player_stats WHERE season IN (SELECT unnest(?)) AND season_type = 'REG'", [seasons]
    ).pl()
    return score_weeks(stats, scoring).select(
        "player_id", "season", "week", "season_type", "position", "team", "opponent_team", "points"
    )


def _team_schedule(schedules: pl.DataFrame, season: int) -> pl.DataFrame:
    reg = schedules.filter((pl.col("season") == season) & (pl.col("game_type") == "REG"))
    home = reg.select("week", pl.col("home_team").alias("team"), pl.col("away_team").alias("opponent"))
    away = reg.select("week", pl.col("away_team").alias("team"), pl.col("home_team").alias("opponent"))
    return pl.concat([home, away])


def load_season_context(con: duckdb.DuckDBPyConnection, season: int, scoring: dict[str, float]) -> SeasonContext:
    hub = id_hub(con.execute("SELECT * FROM ff_playerids").pl())
    history = _scored_weeks(con, list(range(AGE_CURVE_FROM, season)), scoring)
    seasonal = season_ppg(history)
    ages_all = player_ages(hub, list(range(AGE_CURVE_FROM, season + 1)))
    scored = _scored_weeks(con, [season], scoring)
    opportunity = expected_points(
        con.execute("SELECT * FROM ff_opportunity WHERE season = ?", [season]).pl(), scoring
    ).select("player_id", "season", "week", "exp_points")
    schedules = con.execute("SELECT season, week, game_type, home_team, away_team FROM schedules WHERE season = ?", [season]).pl()
    return SeasonContext(
        season=season,
        scored=scored,
        prev_seasonal=seasonal.filter(pl.col("season") == season - 1),
        prev_curve=rank_to_ppg_curve(seasonal, season - 1),
        opportunity=opportunity,
        hub=hub,
        consensus_preseason=consensus_snapshot(con, season, 1),
        age_curve=fit_age_curve(seasonal, ages_all),
        ages=ages_all.filter(pl.col("season") == season).select("player_id", "age"),
        schedules=schedules,
        teams_by_week=_team_schedule(schedules, season),
    )


def _player_teams(ctx: SeasonContext, week: int) -> pl.DataFrame:
    latest = (
        ctx.scored.filter(pl.col("week") < week).sort("week").group_by("player_id").agg(pl.col("team").last())
    )
    fallback = ctx.hub.select(pl.col("gsis_id").alias("player_id"), pl.col("team").alias("hub_team"))
    return fallback.join(latest, on="player_id", how="left").select(
        "player_id", pl.coalesce(pl.col("team"), pl.col("hub_team")).alias("team")
    )


def week_features(ctx: SeasonContext, week: int, roster_positions: list[str], n_teams: int) -> pl.DataFrame:
    prev_ppg = ctx.prev_seasonal.select("player_id", "position", "ppg")
    replacement = replacement_ppg(prev_ppg, replacement_ranks(roster_positions, n_teams, prev_ppg))
    inputs = Inputs(
        scored=ctx.scored, opportunity=ctx.opportunity, hub=ctx.hub,
        consensus_preseason=ctx.consensus_preseason, prev_curve=ctx.prev_curve,
        prev_seasonal=ctx.prev_seasonal, age_curve=ctx.age_curve, ages=ctx.ages, replacement=replacement,
    )
    return build_features(inputs, ctx.season, week)


def _week_eval(
    con: duckdb.DuckDBPyConnection, ctx: SeasonContext, week: int, roster_positions: list[str], n_teams: int,
    params: BlendParams, features: pl.DataFrame,
) -> pl.DataFrame:
    games = _games_per_player(_player_teams(ctx, week), remaining_games(ctx.schedules, ctx.season, week))
    actual = actual_ros(ctx.scored, ctx.season, week)
    starters = starters_per_position(roster_positions, n_teams, ctx.prev_seasonal.select("player_id", "position", "ppg"))
    model = model_prediction(blend(features, params), games)
    market = market_prediction(consensus_snapshot(con, ctx.season, week), ctx.hub, ctx.prev_curve, games)
    universe = market.select("player_id")
    model_eval = evaluate(model.join(universe, on="player_id", how="inner"), actual, starters)
    market_eval = evaluate(market, actual, starters)
    return pl.concat([
        model_eval.with_columns(pl.lit("model").alias("source")),
        market_eval.with_columns(pl.lit("consensus").alias("source")),
    ]).with_columns(pl.lit(ctx.season).alias("season"), pl.lit(week).alias("week"))


def _features_by_week(
    con: duckdb.DuckDBPyConnection, scoring: dict[str, float], roster_positions: list[str], n_teams: int,
    seasons: list[int], weeks: list[int],
) -> dict[tuple[int, int], tuple[SeasonContext, pl.DataFrame]]:
    out: dict[tuple[int, int], tuple[SeasonContext, pl.DataFrame]] = {}
    for season in seasons:
        ctx = load_season_context(con, season, scoring)
        for week in weeks:
            out[(season, week)] = (ctx, week_features(ctx, week, roster_positions, n_teams))
    return out


def run_backtest(
    con: duckdb.DuckDBPyConnection, scoring: dict[str, float], roster_positions: list[str], n_teams: int,
    seasons: list[int], weeks: list[int], params: BlendParams,
) -> pl.DataFrame:
    prepared = _features_by_week(con, scoring, roster_positions, n_teams, seasons, weeks)
    frames = [
        _week_eval(con, ctx, week, roster_positions, n_teams, params, feats)
        for (_, week), (ctx, feats) in prepared.items()
    ]
    return pl.concat(frames).select("season", "week", "source", "position", "spearman", "mae", "precision", "n")


def _score(metrics: pl.DataFrame) -> float:
    pooled = metrics.filter((pl.col("position") == "ALL") & (pl.col("source") == "model"))
    return float(pooled["mae"].mean()) - 10.0 * float(pooled["spearman"].mean())


def tune(
    con: duckdb.DuckDBPyConnection, scoring: dict[str, float], roster_positions: list[str], n_teams: int,
    seasons: list[int], weeks: list[int], grid: list[BlendParams] = DEFAULT_GRID,
) -> BlendParams:
    prepared = _features_by_week(con, scoring, roster_positions, n_teams, seasons, weeks)
    best: tuple[float, BlendParams] | None = None
    for params in grid:
        frames = [
            _week_eval(con, ctx, week, roster_positions, n_teams, params, feats)
            for (_, week), (ctx, feats) in prepared.items()
        ]
        score = _score(pl.concat(frames))
        if best is None or score < best[0]:
            best = (score, params)
    if best is None:
        raise ValueError("empty grid")
    return best[1]


def gate_passes(metrics: pl.DataFrame, eval_seasons: list[int]) -> bool:
    pooled = metrics.filter((pl.col("position") == "ALL") & pl.col("season").is_in(eval_seasons))
    by_source = pooled.group_by("source").agg(pl.col("spearman").mean(), pl.col("mae").mean())
    rows = {str(r["source"]): r for r in by_source.iter_rows(named=True)}
    if "model" not in rows or "consensus" not in rows:
        return False
    return rows["model"]["spearman"] >= rows["consensus"]["spearman"] and rows["model"]["mae"] <= rows["consensus"]["mae"]


def _table(frame: pl.DataFrame) -> str:
    header = "| " + " | ".join(frame.columns) + " |"
    sep = "|" + "---|" * len(frame.columns)
    body = ["| " + " | ".join(f"{v:.3f}" if isinstance(v, float) else str(v) for v in row) + " |" for row in frame.iter_rows()]
    return "\n".join([header, sep, *body])


def write_report(metrics: pl.DataFrame, params: BlendParams, path: Path, passed: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    eval_seasons = sorted(metrics["season"].unique().to_list())
    pooled = (
        metrics.filter(pl.col("position") == "ALL")
        .group_by("season", "source").agg(pl.col("spearman").mean(), pl.col("mae").mean(), pl.col("precision").mean())
        .sort("season", "source")
    )
    by_pos = (
        metrics.filter(pl.col("position") != "ALL")
        .group_by("position", "source").agg(pl.col("spearman").mean(), pl.col("mae").mean(), pl.col("precision").mean())
        .sort("position", "source")
    )
    bucket = pl.when(pl.col("week") <= 5).then(pl.lit("w2-5")).when(pl.col("week") <= 9).then(pl.lit("w6-9")).otherwise(pl.lit("w10-14"))
    by_bucket = (
        metrics.filter(pl.col("position") == "ALL").with_columns(bucket.alias("bucket"))
        .group_by("bucket", "source").agg(pl.col("spearman").mean(), pl.col("mae").mean())
        .sort("bucket", "source")
    )
    verdict = "PASS" if passed else "FAIL"
    text = "\n\n".join([
        "# Backtest report",
        f"Seasons: {', '.join(str(s) for s in eval_seasons)}. Params: k={params.k}, a={params.a}, b={params.b}.",
        f"Gate: **{verdict}** (model must match or beat consensus on pooled Spearman and MAE).",
        "## Pooled by season", _table(pooled),
        "## By position", _table(by_pos),
        "## By week bucket", _table(by_bucket),
    ])
    path.write_text(text + "\n")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./.venv/bin/python -m pytest tests/test_backtest.py -q`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add app/model/backtest.py tests/test_backtest.py
git commit -m "Add walk-forward backtest with tuning and report"
```

---

### Task 13: Backtest CLI, real run, gate decision

**Files:**
- Modify: `app/__main__.py` (add `backtest` subcommand)
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: Task 12 plus `league_meta` and Sleeper league cache from Task 8.
- Produces: `python -m app backtest [--tune-seasons 2021 2022 2023] [--eval-seasons 2024 2025] [--weeks 2-14] [--quick]` that reads scoring and roster slots from the cached Sleeper league JSON in the store, tunes, evaluates, writes `docs/backtest/REPORT.md` and `docs/backtest/metrics.parquet`, prints the pooled table, and exits 0 on PASS, 2 on FAIL. `--quick` uses a three-point grid (k in 4, 8; a in 0.5; b in 0.5) for a fast smoke run.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cli.py
from app.__main__ import build_parser, week_range


def test_backtest_parser_defaults() -> None:
    args = build_parser().parse_args(["backtest"])
    assert args.tune_seasons == [2021, 2022, 2023]
    assert args.eval_seasons == [2024, 2025]
    assert args.weeks == "2-14"
    assert args.quick is False


def test_week_range_parses_inclusive_span() -> None:
    assert week_range("2-4") == [2, 3, 4]
    assert week_range("7") == [7]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/bin/python -m pytest tests/test_cli.py -q`
Expected: FAIL with `ImportError: cannot import name 'build_parser'`

- [ ] **Step 3: Add the backtest command**

Replace `main` in `app/__main__.py` and add the helpers:

```python
import json

from app.model.backtest import BlendParams, DEFAULT_GRID, gate_passes, run_backtest, tune, write_report

QUICK_GRID = [BlendParams(k=4.0, a=0.5, b=0.5), BlendParams(k=8.0, a=0.5, b=0.5)]


def week_range(spec: str) -> list[int]:
    if "-" in spec:
        lo, hi = spec.split("-", 1)
        return list(range(int(lo), int(hi) + 1))
    return [int(spec)]


def _league_settings(cfg: Config) -> tuple[dict[str, float], list[str], int]:
    store = Store(cfg.sqlite_path)
    body = store.get_cached(f"sleeper:/league/{cfg.league_id}", None, 0.0)
    if body is None:
        sys.exit("League not cached. Run `python -m app refresh` first.")
    league = json.loads(body)
    return league["scoring_settings"], list(league["roster_positions"]), int(league["total_rosters"])


def cmd_backtest(cfg: Config, tune_seasons: list[int], eval_seasons: list[int], weeks: list[int], quick: bool) -> int:
    _require_league(cfg)
    scoring, slots, n_teams = _league_settings(cfg)
    con = connect(cfg.duckdb_path)
    grid = QUICK_GRID if quick else DEFAULT_GRID
    params = tune(con, scoring, slots, n_teams, tune_seasons, weeks, grid)
    metrics = run_backtest(con, scoring, slots, n_teams, eval_seasons, weeks, params)
    passed = gate_passes(metrics, eval_seasons)
    out_dir = ROOT / "docs" / "backtest"
    write_report(metrics, params, out_dir / "REPORT.md", passed)
    metrics.write_parquet(out_dir / "metrics.parquet")
    print((out_dir / "REPORT.md").read_text())
    return 0 if passed else 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ff-analyst")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("refresh")
    export = sub.add_parser("export")
    export.add_argument("--out", type=Path, default=Path.home() / "ff-analyst-data")
    bt = sub.add_parser("backtest")
    bt.add_argument("--tune-seasons", type=int, nargs="+", default=[2021, 2022, 2023])
    bt.add_argument("--eval-seasons", type=int, nargs="+", default=[2024, 2025])
    bt.add_argument("--weeks", default="2-14")
    bt.add_argument("--quick", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cfg = load_config(ROOT, os.environ)
    if args.command == "refresh":
        return cmd_refresh(cfg)
    if args.command == "export":
        return cmd_export(cfg, args.out)
    return cmd_backtest(cfg, args.tune_seasons, args.eval_seasons, week_range(args.weeks), args.quick)
```

- [ ] **Step 4: Run the full suite**

Run: `./.venv/bin/python -m pytest -q`
Expected: all passed

- [ ] **Step 5: Commit**

```bash
git add app/__main__.py tests/test_cli.py
git commit -m "Add backtest command with tuning and gate exit code"
```

- [ ] **Step 6: Real run (requires league id in config.local.toml)**

Run: `./.venv/bin/python -m app refresh` then `./.venv/bin/python -m app backtest --quick` and finally `./.venv/bin/python -m app backtest`.
Expected: `docs/backtest/REPORT.md` exists with three tables and a PASS or FAIL line. Commit the report:

```bash
git add docs/backtest/REPORT.md
git commit -m "Add backtest report"
```

Report the verdict and the pooled table verbatim. Do not start Plan B on a FAIL.

---

## Dependency graph for parallel execution

- Wave 1 (independent): Task 1, Task 3, Task 4, Task 5, Task 7.
- Wave 2: Task 2 (needs 1), Task 6 (needs 1, 3, 5), Task 9 (needs 4), Task 10 (needs 4).
- Wave 3: Task 8 (needs 1 through 7), Task 11 (needs 4, 5, 6, 9, 10).
- Wave 4: Task 12 (needs 11), then Task 13 (needs 8, 12).
