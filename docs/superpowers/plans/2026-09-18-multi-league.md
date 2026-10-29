# Multiple Leagues Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Discover every Sleeper league for each configured username, list them in a header switcher and on Settings, and keep per-league state in memory so switching is instant and nothing leaks between leagues.

**Architecture:** `Config.usernames` replaces `username`. A new pure module `app/model/leagues.py` discovers leagues through the Sleeper client (or the cache) into `LeagueSummary` rows. `AppState` becomes a dictionary of `LeagueState` objects keyed by league id with an active id and forwarding properties, so no screen changes. Market snapshots and freshness become per league. The header gains a select; Settings gains a usernames field and a league list.

**Tech Stack:** Python 3.12, polars, NiceGUI 3.17, httpx, sqlite3, pytest. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-09-18-multi-league-design.md`

## Global Constraints

- No new dependencies. No AI attribution anywhere (no co-author trailers, no generated-with comments).
- Type hints everywhere, small functions, no dead code. Scoring and slots come from league settings.
- Every UI boundary catches exceptions, stores `state.error`, and notifies.
- Every screen keeps reading `state.ctx`, `state.projections`, `state.values`, `state.matchups`, `state.team_weeks`, `state.last_refresh`, `state.source`, `state.my_roster_id()` unchanged.
- The suite stays green after every task: `uv run pytest -q` (162 pass at the start).
- Browser drive: `FF_NATIVE=0 uv run python -m app run` on port 8765; one instance at a time.
- Commit messages: plain imperative sentence, no trailer.

## Facts the plan relies on (verified 2026-09-18)

- Sleeper: `GET /v1/user/{username}` → `{"user_id": "...", "display_name": "..."}` or JSON `null`; `GET /v1/user/{user_id}/leagues/nfl/{season}` → list of league objects with `league_id, name, season, status, total_rosters, settings{type, draft_rounds}`.
- `SleeperClient._get(path, ttl)` caches under `sleeper:{path}`; `json.loads("null")` is `None`, so an unknown user comes back as `None` from the cache too.
- `Store` schema: `http_cache(key, fetched_at REAL, body)`, `market_snapshots(source, taken_at, ...)`; `Store.latest_snapshot(source)`.
- `app/model/league._cached_json(store, key)` reads a cached JSON body or `None`.
- `AppState` today (post fix wave): fields `cfg, store, con, ctx, projections, values, matchups, team_weeks, source, last_refresh, error, refreshing`; `load()` builds into locals and assigns atomically; `_rebuild_values`, `_recompute_model`, `apply_settings`, `settings_scopes`, `my_roster_id`, `is_stale`, `has_cache`, `_league_refreshed_at`.
- `tests/test_state.py` helpers: `_ctx(rosters)`, `_cfg(username)`; tests construct `AppState(cfg=..., ctx=...)` and preset `state.team_weeks`.
- `app/ui/settings.py` binds `form["username"]` to a `ui.input("Username")`; `FIELD_HELP["username"]` exists; `form` is built from `SAVED_KEYS`.
- `refresh()` in `app/refresh.py` builds `RefreshSummary(league_name, week, tables, snapshot_rows, errors, unmapped_scoring, unmatched_players)`.

## File map

| File | Responsibility |
|---|---|
| `app/config.py` (modify) | `usernames` tuple, legacy lift, TOML array write, validation, form parse |
| `app/sleeper.py` (modify) | `user`, `user_leagues` |
| `app/data/store.py` (modify) | `fetched_at(key)` |
| `app/model/leagues.py` (create) | `LeagueSummary`, `LeagueSource`, `CachedSource`, `discover`, `load_summaries` |
| `app/market.py`, `app/model/values.py` (modify) | Per-league snapshot source |
| `app/refresh.py` (modify) | Discover leagues during refresh; report unknown users |
| `app/ui/state.py` (rewrite) | `LeagueState`, per-league `AppState`, `switch` |
| `app/ui/layout.py`, `app/ui/settings.py` (modify) | Header switcher; usernames field; league list |
| Tests: `tests/test_config.py`, `tests/test_sleeper.py`, `tests/test_store.py` (create if absent), `tests/test_leagues.py` (create), `tests/test_market.py`, `tests/test_refresh.py`, `tests/test_state.py` |

---

### Task 1: Usernames in config

**Files:**
- Modify: `app/config.py`, `app/ui/state.py` (two references), `app/ui/settings.py` (one field)
- Test: `tests/test_config.py`, `tests/test_state.py` (helper)

**Interfaces:**
- Produces: `Config.usernames: tuple[str, ...] = ()`; `SAVED_KEYS` has `"usernames"` in place of `"username"`; `split_names(value: object) -> tuple[str, ...]`; `save_config` writes `usernames = ["a", "b"]`; `load_config` lifts a legacy scalar `username`; `validate_settings` requires at least one name under key `"usernames"`; `config_from_form` parses a comma-separated string or a sequence.

- [ ] **Step 1: Write the failing tests**

In `tests/test_config.py`, change `_cfg` to use `usernames=("ann",)` instead of `username="ann"`, change `_form` to use `usernames="ann"`, and add:

```python
from app.config import split_names


def test_split_names_from_string_or_sequence() -> None:
    assert split_names(" ann , bob,,") == ("ann", "bob")
    assert split_names(("ann", " bob ")) == ("ann", "bob")
    assert split_names(None) == () and split_names("") == ()


def test_load_config_reads_usernames_array_and_lifts_legacy_username(tmp_path: Path) -> None:
    (tmp_path / "config.local.toml").write_text('usernames = ["ann", "bob"]\n')
    assert load_config(tmp_path, env={}).usernames == ("ann", "bob")
    (tmp_path / "config.local.toml").write_text('username = "solo"\n')
    assert load_config(tmp_path, env={}).usernames == ("solo",)
    (tmp_path / "config.local.toml").write_text("league_id = \"1\"\n")
    assert load_config(tmp_path, env={}).usernames == ()


def test_save_config_writes_usernames_as_an_array(tmp_path: Path) -> None:
    save_config(tmp_path, _cfg(tmp_path, usernames=("ann", "bob")))
    text = (tmp_path / "config.local.toml").read_text()
    assert 'usernames = ["ann", "bob"]' in text and "username =" not in text.replace("usernames =", "")
    assert load_config(tmp_path, env={}).usernames == ("ann", "bob")


def test_validate_settings_requires_a_username() -> None:
    assert "usernames" in validate_settings(_form(usernames=" , "), current_season=2026)
    assert validate_settings(_form(usernames="ann, bob"), current_season=2026) == {}


def test_config_from_form_parses_usernames(tmp_path: Path) -> None:
    assert config_from_form(_form(usernames="ann, bob"), _cfg(tmp_path)).usernames == ("ann", "bob")
```

Update the existing round-trip test's expectation loop: `SAVED_KEYS` now includes `usernames`, so the equality loop covers it. Update `test_load_config_reads_username_from_toml` and `test_load_config_username_defaults_to_empty` to assert on `usernames == ("alice",)` and `usernames == ()`.

In `tests/test_state.py`, change `_cfg(username)` to build `Config(..., usernames=(username,) if username else ())`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_config.py -q` → ImportError on `split_names`.

- [ ] **Step 3: Implement**

`app/config.py`:

```python
SAVED_KEYS: tuple[str, ...] = (
    "league_id", "usernames", "source_default", "stats_from", "detail_from", "blend_k", "blend_a", "blend_b",
)
```

`Config`: replace `username: str = ""` with `usernames: tuple[str, ...] = ()`.

```python
def split_names(value: object) -> tuple[str, ...]:
    """Usernames from a comma-separated string or any sequence; blanks dropped, order kept."""
    if value is None:
        return ()
    parts = value.split(",") if isinstance(value, str) else list(value)  # type: ignore[call-overload]
    return tuple(p for p in (str(x).strip() for x in parts) if p)


def _toml_scalar(value: object) -> str:
    if isinstance(value, (tuple, list)):
        return "[" + ", ".join(json.dumps(str(v)) for v in value) + "]"
    return json.dumps(value) if isinstance(value, str) else repr(value)
```

`load_config`: replace `username=str(data.get("username", ""))` with `usernames=split_names(data.get("usernames", data.get("username")))`.

`validate_settings`: add after the league-id check:

```python
    if not split_names(form.get("usernames")):
        errors["usernames"] = "at least one Sleeper username"
```

`config_from_form`: replace `username=str(form["username"]).strip()` with `usernames=split_names(form.get("usernames"))`.

`app/ui/state.py`: in `my_roster_id`, replace the `if self.cfg.username:` block with

```python
        for name in self.cfg.usernames:
            for roster in self.ctx.rosters:
                if roster.display_name.lower() == name.lower():
                    return roster.roster_id
```

and in `settings_scopes` compare `(old.league_id, old.usernames) != (new.league_id, new.usernames)`.

`app/ui/settings.py`: rename the key in `FIELD_HELP` to `"usernames"` with text `"Sleeper display names, comma-separated; the first one with a roster is you"`; after building `form`, set `form["usernames"] = ", ".join(state.cfg.usernames)`; change the input to `ui.input("Usernames", ...).bind_value(form, "usernames")` and its help label key.

- [ ] **Step 4: Run tests and commit**

Run: `uv run pytest -q` → all pass.

```bash
git add app/config.py app/ui/state.py app/ui/settings.py tests/test_config.py tests/test_state.py
git commit -m "Replace the single username with a list of Sleeper usernames"
```

---

### Task 2: Sleeper user endpoints and cache timestamps

**Files:**
- Modify: `app/sleeper.py`, `app/data/store.py`
- Test: `tests/test_sleeper.py`, `tests/test_store.py` (create if it does not exist)

**Interfaces:**
- `USER_TTL = 86400.0`
- `SleeperClient.user(username) -> dict[str, Any] | None` (`/user/{username}`, `USER_TTL`)
- `SleeperClient.user_leagues(user_id, season) -> list[dict[str, Any]]` (`/user/{user_id}/leagues/nfl/{season}`, ttl `None`)
- `Store.fetched_at(key) -> float | None`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_sleeper.py`:

```python
def test_user_and_user_leagues_paths_and_ttl(tmp_path: Path) -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/v1/user/nobody":
            return httpx.Response(200, text="null")
        if request.url.path.startswith("/v1/user/"):
            if "/leagues/" in request.url.path:
                return httpx.Response(200, json=[{"league_id": "9", "name": "L"}])
            return httpx.Response(200, json={"user_id": "u1", "display_name": "Ann"})
        return httpx.Response(200, json={})

    clock = FakeClock()
    client = make_client(tmp_path, handler, clock)
    assert client.user("ann") == {"user_id": "u1", "display_name": "Ann"}
    assert client.user("ann")["user_id"] == "u1"
    clock.t += USER_TTL + 1
    client.user("ann")
    assert calls.count("/v1/user/ann") == 2
    assert client.user("nobody") is None
    assert client.user_leagues("u1", 2026) == [{"league_id": "9", "name": "L"}]
    assert calls[-1] == "/v1/user/u1/leagues/nfl/2026"
```

Import `USER_TTL` from `app.sleeper` at the top.

Create or extend `tests/test_store.py`:

```python
from pathlib import Path

from app.data.store import Store


def test_fetched_at_returns_the_put_time_or_none(tmp_path: Path) -> None:
    store = Store(tmp_path / "app.sqlite")
    assert store.fetched_at("sleeper:/league/1") is None
    store.put_cached("sleeper:/league/1", "{}", now=1234.5)
    assert store.fetched_at("sleeper:/league/1") == 1234.5
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_sleeper.py tests/test_store.py -q` → ImportError / AttributeError.

- [ ] **Step 3: Implement**

`app/sleeper.py`: add `USER_TTL = 86400.0` after `STATE_TTL`, and methods after `players`:

```python
    def user(self, username: str) -> dict[str, Any] | None:
        """Sleeper user by username; None when Sleeper does not know the name."""
        return self._get(f"/user/{username}", USER_TTL)

    def user_leagues(self, user_id: str, season: int) -> list[dict[str, Any]]:
        return self._get(f"/user/{user_id}/leagues/nfl/{season}", None) or []
```

`app/data/store.py`, after `get_cached`:

```python
    def fetched_at(self, key: str) -> float | None:
        row = self._conn.execute("SELECT fetched_at FROM http_cache WHERE key = ?", (key,)).fetchone()
        return None if row is None else float(row[0])
```

- [ ] **Step 4: Run tests and commit**

Run: `uv run pytest -q` → all pass.

```bash
git add app/sleeper.py app/data/store.py tests/test_sleeper.py tests/test_store.py
git commit -m "Add Sleeper user lookups and cache fetch timestamps"
```

---

### Task 3: League discovery

**Files:**
- Create: `app/model/leagues.py`
- Test: `tests/test_leagues.py`

**Interfaces:**
- Consumes: `SleeperClient.user/user_leagues` (Task 2), `app.model.league._cached_json`.
- Produces:
  - `LeagueSummary(league_id, name, season, status, n_teams, league_type, draft_rounds, members)` with properties `dynasty` and `label`
  - `LeagueSource` Protocol with `user(username)` and `user_leagues(user_id, season)`
  - `CachedSource(store)` implementing `LeagueSource` from the HTTP cache only
  - `discover(source, usernames, season) -> tuple[list[LeagueSummary], list[str]]`
  - `load_summaries(store, usernames, season) -> list[LeagueSummary]`

- [ ] **Step 1: Write the failing tests**

```python
import json
from pathlib import Path

from app.data.store import Store
from app.model.leagues import CachedSource, LeagueSummary, discover, load_summaries

USERS = {"ann": {"user_id": "u1", "display_name": "Ann"}, "bob": {"user_id": "u2", "display_name": "Bob"}}
L1 = {"league_id": "1", "name": "Shared", "season": "2026", "status": "in_season", "total_rosters": 10,
      "settings": {"type": 0, "draft_rounds": 3}}
L2 = {"league_id": "2", "name": "Dyn", "season": "2026", "status": "in_season", "total_rosters": 12,
      "settings": {"type": 2, "draft_rounds": 4}}
L3 = {"league_id": "3", "name": "Odd", "season": "2026", "status": "pre_draft", "total_rosters": 18,
      "settings": {"type": 3}}
LEAGUES = {"u1": [L1], "u2": [L2, L1, L3]}


class FakeSource:
    def user(self, username: str):
        return USERS.get(username)

    def user_leagues(self, user_id: str, season: int):
        return LEAGUES.get(user_id, [])


def test_discover_unions_leagues_and_records_members_in_config_order() -> None:
    summaries, errors = discover(FakeSource(), ("ann", "bob"), 2026)
    assert errors == []
    assert [s.league_id for s in summaries] == ["1", "2", "3"]
    assert summaries[0].members == ("ann", "bob") and summaries[1].members == ("bob",)
    assert summaries[0] == LeagueSummary("1", "Shared", 2026, "in_season", 10, 0, 3, ("ann", "bob"))


def test_discover_skips_unknown_users_with_an_error() -> None:
    summaries, errors = discover(FakeSource(), ("ghost", "bob"), 2026)
    assert errors == ["user ghost: not found"]
    assert [s.league_id for s in summaries] == ["2", "1", "3"]


def test_labels_and_dynasty_flag() -> None:
    s1, s2, s3 = discover(FakeSource(), ("ann", "bob"), 2026)[0]
    assert (s1.dynasty, s2.dynasty, s3.dynasty) == (False, True, False)
    assert s1.label == "Shared · redraft 10"
    assert s2.label == "Dyn · dynasty 12"
    assert s3.label == "Odd · pre-draft type 3 18"
    assert s3.draft_rounds == 4


def test_cached_source_and_load_summaries(tmp_path: Path) -> None:
    store = Store(tmp_path / "app.sqlite")
    store.put_cached("sleeper:/user/ann", json.dumps(USERS["ann"]), now=1.0)
    store.put_cached("sleeper:/user/u1/leagues/nfl/2026", json.dumps([L1]), now=1.0)
    store.put_cached("sleeper:/user/ghost", "null", now=1.0)
    src = CachedSource(store)
    assert src.user("ann") == USERS["ann"] and src.user("ghost") is None and src.user("bob") is None
    assert src.user_leagues("u1", 2026) == [L1] and src.user_leagues("u2", 2026) == []
    assert [s.league_id for s in load_summaries(store, ("ann", "bob"), 2026)] == ["1"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_leagues.py -q` → ModuleNotFoundError.

- [ ] **Step 3: Implement**

```python
"""Which leagues the configured Sleeper users are in, from the API or the cache."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from app.data.store import Store
from app.model.league import _cached_json

DYNASTY_TYPES = (1, 2)
DEFAULT_DRAFT_ROUNDS = 4
TYPE_NAMES = {0: "redraft", 1: "keeper", 2: "dynasty"}


@dataclass(frozen=True)
class LeagueSummary:
    league_id: str
    name: str
    season: int
    status: str
    n_teams: int
    league_type: int
    draft_rounds: int
    members: tuple[str, ...]

    @property
    def dynasty(self) -> bool:
        return self.league_type in DYNASTY_TYPES

    @property
    def label(self) -> str:
        kind = TYPE_NAMES.get(self.league_type, f"type {self.league_type}")
        phase = "pre-draft " if self.status == "pre_draft" else ""
        return f"{self.name} · {phase}{kind} {self.n_teams}"


class LeagueSource(Protocol):
    def user(self, username: str) -> dict[str, Any] | None: ...
    def user_leagues(self, user_id: str, season: int) -> list[dict[str, Any]]: ...


class CachedSource:
    """LeagueSource over the HTTP cache only: never touches the network."""

    def __init__(self, store: Store) -> None:
        self._store = store

    def user(self, username: str) -> dict[str, Any] | None:
        return _cached_json(self._store, f"sleeper:/user/{username}")

    def user_leagues(self, user_id: str, season: int) -> list[dict[str, Any]]:
        return _cached_json(self._store, f"sleeper:/user/{user_id}/leagues/nfl/{season}") or []


def _summary(raw: dict[str, Any], members: tuple[str, ...]) -> LeagueSummary:
    settings = raw.get("settings") or {}
    return LeagueSummary(
        league_id=str(raw["league_id"]), name=str(raw.get("name") or raw["league_id"]),
        season=int(raw.get("season") or 0), status=str(raw.get("status") or ""),
        n_teams=int(raw.get("total_rosters") or 0), league_type=int(settings.get("type", 0)),
        draft_rounds=int(settings.get("draft_rounds", DEFAULT_DRAFT_ROUNDS)), members=members,
    )


def discover(source: LeagueSource, usernames: tuple[str, ...], season: int) -> tuple[list[LeagueSummary], list[str]]:
    """Union of every user's leagues, first appearance first; unknown users are reported, not fatal."""
    raws: dict[str, dict[str, Any]] = {}
    members: dict[str, list[str]] = {}
    errors: list[str] = []
    for name in usernames:
        user = source.user(name)
        if not user:
            errors.append(f"user {name}: not found")
            continue
        for raw in source.user_leagues(str(user["user_id"]), season):
            league_id = str(raw["league_id"])
            raws.setdefault(league_id, raw)
            members.setdefault(league_id, []).append(name)
    return [_summary(raw, tuple(members[league_id])) for league_id, raw in raws.items()], errors


def load_summaries(store: Store, usernames: tuple[str, ...], season: int) -> list[LeagueSummary]:
    return discover(CachedSource(store), usernames, season)[0]
```

Check the second test by hand: `ghost` errors; `bob` lists L2, L1, L3 in that order → ids 2, 1, 3. ✓ Third test: L3 has no `draft_rounds` → default 4. ✓

- [ ] **Step 4: Run tests and commit**

Run: `uv run pytest -q` → all pass.

```bash
git add app/model/leagues.py tests/test_leagues.py
git commit -m "Discover leagues for every configured Sleeper user"
```

---

### Task 4: Per-league market snapshots and discovery during refresh

**Files:**
- Modify: `app/market.py`, `app/model/values.py`, `app/refresh.py`
- Test: `tests/test_market.py`, `tests/test_refresh.py`

**Interfaces:**
- `snapshot_source(league_id) -> str` = `f"fantasycalc:{league_id}"` in `app/market.py`; `snapshot_fantasycalc` stores under it.
- `player_values` reads `store.latest_snapshot(snapshot_source(ctx.league_id))`.
- `RefreshSummary.leagues: int = 0`; `refresh()` calls `discover(client, cfg.usernames, cfg.current_season)`, sets `summary.leagues`, and extends `summary.errors` with discovery errors.

- [ ] **Step 1: Write the failing tests**

`tests/test_market.py`: in `test_snapshot_fantasycalc_writes_store`, assert the rows land under `snapshot_source(str(LEAGUE["league_id"]))` (read with `store.latest_snapshot(snapshot_source("1"))`, and assert `store.latest_snapshot("fantasycalc").is_empty()`). Import `snapshot_source`.

`tests/test_refresh.py`: extend `make_sleeper_handler` with:

```python
        if path == "/v1/user/ann":
            return httpx.Response(200, json={"user_id": "u1", "display_name": "Ann"})
        if path == "/v1/user/ghost":
            return httpx.Response(200, text="null")
        if path == "/v1/user/u1/leagues/nfl/2025":
            return httpx.Response(200, json=[LEAGUE, {**LEAGUE, "league_id": "2", "name": "Other"}])
```

and add a test that builds `cfg` with `usernames=("ann", "ghost")`, runs `refresh(...)` as the existing tests do, and asserts `summary.leagues == 2` and `"user ghost: not found" in summary.errors`. Also assert the existing `test_refresh_populates_tables_and_summary` still passes with `usernames=()` (no discovery calls, `summary.leagues == 0`).

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_market.py tests/test_refresh.py -q` → ImportError / AttributeError.

- [ ] **Step 3: Implement**

`app/market.py`:

```python
def snapshot_source(league_id: str) -> str:
    return f"fantasycalc:{league_id}"
```

and `snapshot_fantasycalc` stores with `store.put_snapshot(snapshot_source(str(league["league_id"])), taken_at, frame.to_dicts())`.

`app/model/values.py`: import `snapshot_source` from `app.market` (check for an import cycle: `app.market` imports `duckdb, httpx, polars` and nothing from `app.model`; fine) and read `store.latest_snapshot(snapshot_source(ctx.league_id))`.

`app/refresh.py`: import `discover` from `app.model.leagues`; add `leagues: int = 0` to `RefreshSummary`; after `_write_meta(...)`:

```python
    summaries, user_errors = discover(client, cfg.usernames, cfg.current_season)
    summary.leagues = len(summaries)
    summary.errors.extend(user_errors)
```

- [ ] **Step 4: Run tests and commit**

Run: `uv run pytest -q` → all pass.

```bash
git add app/market.py app/model/values.py app/refresh.py tests/test_market.py tests/test_refresh.py
git commit -m "Key market snapshots by league and discover leagues on refresh"
```

---

### Task 5: Per-league app state

**Files:**
- Rewrite: `app/ui/state.py`
- Test: `tests/test_state.py`

**Interfaces:**
- Consumes: `LeagueSummary`, `discover`, `load_summaries`, `CachedSource` (Task 3); `Store.fetched_at` (Task 2); `SleeperClient` (Task 2).
- Produces: `LeagueState` dataclass; `AppState` with `leagues: dict[str, LeagueState]`, `active: str`, `summaries: list[LeagueSummary]`, forwarding properties `ctx, projections, values, matchups, team_weeks, last_refresh`, methods `load, switch, refresh, primary, secondary, is_stale, has_cache, my_roster_id, apply_settings, _rebuild_values, _recompute_model, _build, _rediscover`; `NOT_CACHED_MARKER = "not cached"`; `settings_scopes` unchanged; `startup_plan`, `friendly_error`, `get_state` unchanged.

- [ ] **Step 1: Adapt existing tests and write the failing ones**

In `tests/test_state.py`:

- Add a helper that injects a loaded league:

```python
from app.ui.state import LeagueState, NOT_CACHED_MARKER


def _state_with(ctx: LeagueContext, username: str = "ann", **league: object) -> AppState:
    state = AppState(cfg=replace(_cfg(username), league_id=ctx.league_id))
    fields = dict(ctx=ctx, projections={}, values=pl.DataFrame(), matchups={}, team_weeks={}, last_refresh=None)
    fields.update(league)
    state.leagues[ctx.league_id] = LeagueState(**fields)  # type: ignore[arg-type]
    return state
```

- Rewrite every `AppState(cfg=..., ctx=...)` construction to `_state_with(ctx, ...)`, and every preset of `state.team_weeks = {...}` / `state.projections = {...}` to pass them through `_state_with(..., team_weeks=..., projections=...)`. The `my_roster_id` tests use `_state_with(_ctx(rosters), "Alice")` etc.
- `test_load_leaves_state_untouched_when_projection_fails`: keep the assertions on the forwarding properties; for the pre-seeded case use `_state_with(other_ctx)` and assert `state.leagues[other_ctx.league_id].ctx is other_ctx` afterwards.
- `test_apply_settings_saves_and_returns_the_top_scope`: the `"league"` scope now means usernames changed, so replace the `league_id="42"` step with `replace(state.cfg, usernames=("bob",), blend_a=0.2)` and monkeypatch `state._rediscover` to `lambda: calls.append("discover")`; expected calls end with `"discover"` instead of `"load"`, and the model recompute still does not run for that step (assert `calls == ["values", "model", "model", "discover"]`).

Add new tests:

```python
def test_forwarding_properties_are_empty_before_any_league_loads() -> None:
    state = AppState(cfg=_cfg("ann"))
    assert state.ctx is None and state.projections == {} and state.values.is_empty()
    assert state.matchups == {} and state.team_weeks == {} and state.last_refresh is None


def test_switch_loads_a_league_once_and_persists_the_active_id(tmp_path: Path, monkeypatch) -> None:
    ctx1, ctx2 = _ctx((Roster(1, "u1", "A", "ann", None, (), (), ()),)), replace(_ctx(()), league_id="2")
    state = _state_with(ctx1)
    state.cfg = replace(state.cfg, root=tmp_path)
    built: list[str] = []

    def fake_build(league_id: str) -> LeagueState:
        built.append(league_id)
        return LeagueState(ctx=ctx2, projections={}, values=pl.DataFrame(), matchups={}, team_weeks={}, last_refresh=None)

    monkeypatch.setattr(state, "_build", fake_build)
    state.switch("2")
    assert state.active == "2" and state.ctx is ctx2 and built == ["2"]
    assert load_config(tmp_path, env={}).league_id == "2"
    state.switch("1")
    state.switch("2")
    assert built == ["2"] and state.ctx is ctx2


def test_switch_to_an_unfetched_league_keeps_it_active_and_raises(tmp_path: Path, monkeypatch) -> None:
    state = _state_with(_ctx(()))
    state.cfg = replace(state.cfg, root=tmp_path)

    def fake_build(league_id: str) -> LeagueState:
        raise ValueError(f"league {league_id} {NOT_CACHED_MARKER}; run refresh first")

    monkeypatch.setattr(state, "_build", fake_build)
    with pytest.raises(ValueError):
        state.switch("9")
    assert state.active == "9" and state.cfg.league_id == "9" and state.ctx is None


def test_my_roster_id_follows_username_order() -> None:
    rosters = (Roster(4, "u1", "A", "bob", None, (), (), ()), Roster(7, "u2", "B", "ann", None, (), (), ()))
    state = _state_with(_ctx(rosters))
    state.cfg = replace(state.cfg, usernames=("ann", "bob"))
    assert state.my_roster_id() == 7
    state.cfg = replace(state.cfg, usernames=("zed", "bob"))
    assert state.my_roster_id() == 4


def test_staleness_reads_the_league_fetch_time(tmp_path: Path) -> None:
    from datetime import datetime
    state = AppState(cfg=replace(_cfg("ann"), root=tmp_path, league_id="1"))
    assert state.has_cache() is False and state.is_stale() is True
    store, _ = state._connect()
    store.put_cached("sleeper:/league/1", "{}", now=datetime.now().timestamp())
    assert state.has_cache() is True and state.is_stale() is False
    store.put_cached("sleeper:/league/1", "{}", now=datetime.now().timestamp() - 7200)
    assert state.is_stale() is True


def test_rediscover_falls_back_to_the_first_league_when_active_disappears(tmp_path: Path, monkeypatch) -> None:
    import app.ui.state as state_mod
    from app.model.leagues import LeagueSummary

    state = _state_with(_ctx(()))
    state.cfg = replace(state.cfg, root=tmp_path, usernames=("bob",))
    summaries = [LeagueSummary("5", "Five", 2026, "in_season", 10, 0, 3, ("bob",))]
    monkeypatch.setattr(state_mod, "discover", lambda source, names, season: (summaries, ["user zed: not found"]))
    switched: list[str] = []
    monkeypatch.setattr(state, "switch", lambda league_id: switched.append(league_id))
    state._rediscover()
    assert state.summaries == summaries and switched == ["5"] and state.error == "user zed: not found"
```

`_connect` in the staleness test opens a real `Store` and DuckDB under `tmp_path/cache`; that is fine (`connect` creates the file).

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_state.py -q` → ImportError on `LeagueState`.

- [ ] **Step 3: Rewrite `app/ui/state.py`**

```python
"""App-wide state: connections, one LeagueState per loaded league, the active league, and refresh."""
from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from pathlib import Path

import duckdb
import httpx
import polars as pl

from app.config import Config, load_config, save_config
from app.data.nflverse import connect
from app.data.store import Store
from app.model.league import LeagueContext, load_league_context
from app.model.leagues import LeagueSummary, discover, load_summaries
from app.model.live import SOURCES, Projection, project
from app.model.standings import Matchups, load_matchups, team_week_projections
from app.model.values import player_values
from app.refresh import RefreshSummary, refresh as run_refresh
from app.sleeper import SleeperClient

SCOPE_ORDER: tuple[str, ...] = ("league", "history", "model", "source")
STALE_AFTER = timedelta(hours=1)
ROOT = Path(__file__).resolve().parent.parent.parent
LOCK_ERROR_MARKER = "Could not set lock"
NOT_CACHED_MARKER = "not cached"


@dataclass
class LeagueState:
    """Everything derived for one league. Built whole by AppState._build, never mutated piecemeal by screens."""
    ctx: LeagueContext
    projections: dict[str, Projection]
    values: pl.DataFrame
    matchups: Matchups
    # source -> team_week_projections(ctx, projections[source]); invalidated only by _build and _recompute_model.
    team_weeks: dict[str, pl.DataFrame]
    last_refresh: datetime | None


@dataclass
class AppState:
    cfg: Config = field(default_factory=lambda: load_config(ROOT, os.environ))
    store: Store | None = None
    con: duckdb.DuckDBPyConnection | None = None
    leagues: dict[str, LeagueState] = field(default_factory=dict)
    active: str = ""
    summaries: list[LeagueSummary] = field(default_factory=list)
    source: str = ""
    error: str | None = None
    refreshing: bool = False

    def __post_init__(self) -> None:
        if not self.source:
            self.source = self.cfg.source_default
        if not self.active:
            self.active = self.cfg.league_id

    # ---- forwarding properties: every screen reads these, unchanged from the single-league app ----

    def _current(self) -> LeagueState | None:
        return self.leagues.get(self.active)

    @property
    def ctx(self) -> LeagueContext | None:
        current = self._current()
        return None if current is None else current.ctx

    @property
    def projections(self) -> dict[str, Projection]:
        current = self._current()
        return {} if current is None else current.projections

    @property
    def values(self) -> pl.DataFrame:
        current = self._current()
        return pl.DataFrame() if current is None else current.values

    @property
    def matchups(self) -> Matchups:
        current = self._current()
        return {} if current is None else current.matchups

    @property
    def team_weeks(self) -> dict[str, pl.DataFrame]:
        current = self._current()
        return {} if current is None else current.team_weeks

    @property
    def last_refresh(self) -> datetime | None:
        current = self._current()
        return None if current is None else current.last_refresh

    # ---- connections and building ----

    def _connect(self) -> tuple[Store, duckdb.DuckDBPyConnection]:
        if self.store is None:
            self.store = Store(self.cfg.sqlite_path)
        if self.con is None:
            self.con = connect(self.cfg.duckdb_path)
        return self.store, self.con

    def _secondary_source(self) -> str:
        return SOURCES[0] if self.source == SOURCES[1] else SOURCES[1]

    def _compute_values(self, ctx: LeagueContext, projections: dict[str, Projection]) -> pl.DataFrame:
        store, con = self._connect()
        return player_values(ctx, con, store, projections[self.source], projections[self._secondary_source()])

    def _build(self, league_id: str) -> LeagueState:
        """Build a league's state from the caches. Raises ValueError("... not cached ...") when never fetched."""
        store, con = self._connect()
        ctx = load_league_context(store, league_id)
        projections = {source: project(con, store, ctx, source, params=self.cfg.blend) for source in SOURCES}
        matchups = load_matchups(store, league_id, list(range(1, ctx.playoff_week_start)))
        team_weeks = {source: team_week_projections(ctx, proj) for source, proj in projections.items()}
        values = self._compute_values(ctx, projections)
        return LeagueState(ctx=ctx, projections=projections, values=values, matchups=matchups,
                           team_weeks=team_weeks, last_refresh=self._fetched_at(league_id))

    def _fetched_at(self, league_id: str) -> datetime | None:
        store, _ = self._connect()
        stamp = store.fetched_at(f"sleeper:/league/{league_id}")
        return None if stamp is None else datetime.fromtimestamp(stamp)

    def load(self) -> None:
        """Build the active league from the caches and read the discovered league list."""
        store, _ = self._connect()
        built = self._build(self.active)
        self.leagues[self.active] = built
        self.summaries = load_summaries(store, self.cfg.usernames, self.cfg.current_season)

    def switch(self, league_id: str) -> None:
        """Make league_id active and persist it; build it on first use. Raises when it was never fetched."""
        self.active = league_id
        self.cfg = replace(self.cfg, league_id=league_id)
        save_config(self.cfg.root, self.cfg)
        if league_id not in self.leagues:
            self.leagues[league_id] = self._build(league_id)

    def refresh(self) -> RefreshSummary:
        """Pull the active league, shared nflverse data, and its market snapshot, then rebuild it."""
        store, con = self._connect()
        client = SleeperClient(store, refresh=True)
        summary = run_refresh(self.cfg, client, con, store, httpx.Client(timeout=30.0))
        self.load()
        return summary

    def primary(self) -> Projection:
        return self.projections[self.source]

    def secondary(self) -> Projection:
        return self.projections[self._secondary_source()]

    def is_stale(self) -> bool:
        """True when the active league's Sleeper fetch is missing or older than one hour."""
        last = self._fetched_at(self.active)
        return last is None or datetime.now() - last > STALE_AFTER

    def has_cache(self) -> bool:
        return self._fetched_at(self.active) is not None

    def my_roster_id(self) -> int:
        """First configured username with a roster in the active league, else the first roster; 1 before load."""
        ctx = self.ctx
        if ctx is None or not ctx.rosters:
            return 1
        for name in self.cfg.usernames:
            for roster in ctx.rosters:
                if roster.display_name.lower() == name.lower():
                    return roster.roster_id
        return ctx.rosters[0].roster_id

    def _rebuild_values(self) -> None:
        current = self._current()
        if current is not None:
            current.values = self._compute_values(current.ctx, current.projections)

    def _recompute_model(self) -> None:
        """Re-run only the active league's model projection with the current blend weights."""
        current = self._current()
        if current is None:
            return
        store, con = self._connect()
        model_projection = project(con, store, current.ctx, "model", params=self.cfg.blend)
        model_team_weeks = team_week_projections(current.ctx, model_projection)
        current.projections["model"], current.team_weeks["model"] = model_projection, model_team_weeks
        self._rebuild_values()

    def _rediscover(self) -> None:
        """Re-list leagues for the configured usernames over the network; fall back to the first if the active one is gone."""
        store, _ = self._connect()
        self.summaries, errors = discover(SleeperClient(store, refresh=True), self.cfg.usernames, self.cfg.current_season)
        self.error = "; ".join(errors) if errors else None
        ids = [s.league_id for s in self.summaries]
        if self.summaries and self.active not in ids:
            self.switch(ids[0])

    def apply_settings(self, new: Config) -> str:
        """Save new config, apply it by scope, and return the most significant scope (or "none")."""
        old, self.cfg = self.cfg, new
        save_config(new.root, new)
        scopes = settings_scopes(old, new)
        if "source" in scopes:
            self.source = new.source_default
        if "league" in scopes:
            self._rediscover()
        elif "model" in scopes:
            self._recompute_model()
        elif "source" in scopes:
            self._rebuild_values()
        return next((scope for scope in SCOPE_ORDER if scope in scopes), "none")


def settings_scopes(old: Config, new: Config) -> set[str]:
    """Which parts of the app a config change touches."""
    scopes: set[str] = set()
    if old.usernames != new.usernames:
        scopes.add("league")
    if (old.stats_from, old.detail_from) != (new.stats_from, new.detail_from):
        scopes.add("history")
    if (old.blend_k, old.blend_a, old.blend_b) != (new.blend_k, new.blend_a, new.blend_b):
        scopes.add("model")
    if old.source_default != new.source_default:
        scopes.add("source")
    return scopes


def startup_plan(has_cache: bool, stale: bool) -> tuple[bool, bool]:
    """(load_now, refresh_in_background) for the startup sequence."""
    return has_cache, stale or not has_cache


def friendly_error(msg: str) -> str:
    """Rewrite the DuckDB cross-process lock error into an actionable message."""
    if LOCK_ERROR_MARKER in msg:
        return "another ff-analyst instance is already running; close it and press Retry"
    return msg


STATE = AppState()


def get_state() -> AppState:
    return STATE
```

Notes for the implementer: `settings_scopes` for a `league_id` change no longer exists because `league_id` changes only through `switch`. `table_exists` and `league_meta` are no longer read here; drop the import. The blend-change path (`_recompute_model`) applies to the active league only; other loaded leagues pick up new weights when they are next built. Check that `app/ui/layout.py::_on_source_change` still works: it sets `state.source` and calls `state._rebuild_values()`; both exist.

- [ ] **Step 4: Run tests and commit**

Run: `uv run pytest -q` → all pass.

```bash
git add app/ui/state.py tests/test_state.py
git commit -m "Keep one state per league and switch between them"
```

---

### Task 6: Header switcher, Settings league list, drive

**Files:**
- Modify: `app/ui/layout.py`, `app/ui/settings.py`

**Interfaces:**
- `layout.on_switch(league_id: str) -> None` (async): switches, refreshing in the background when the league was never fetched, then reloads the page.
- Header: a `ui.select` of `state.summaries` before the source toggle; hidden when there are no summaries, replaced by a hint label.
- Settings: usernames field first; no League id field (league changes go through switching); a "Leagues" section listing `state.summaries` with a Switch button per row and the active row marked.

- [ ] **Step 1: Header switcher**

In `app/ui/layout.py` add:

```python
async def on_switch(league_id: str) -> None:
    """Switch the active league; a never-fetched league is refreshed in the background first."""
    state = get_state()
    if league_id == state.active:
        return
    state.error = None
    try:
        await run.io_bound(state.switch, league_id)
    except ValueError as exc:
        if NOT_CACHED_MARKER not in str(exc):
            state.error = str(exc)
            ui.notify(friendly_error(state.error), type="negative")
            return
        ui.notify("Fetching this league for the first time", type="info")
        await on_refresh()
        return
    except Exception as exc:  # UI boundary: caught, recorded, and surfaced, never swallowed
        state.error = str(exc)
        ui.notify(friendly_error(state.error), type="negative")
        return
    ui.navigate.reload()


def _league_switcher(state: AppState) -> None:
    if not state.summaries:
        ui.label("add your Sleeper username in Settings").classes("ff-label")
        return
    options = {s.league_id: s.label for s in state.summaries}
    value = state.active if state.active in options else None
    ui.select(options, value=value, on_change=lambda e: on_switch(e.value)).classes("min-w-64").props("dark dense")
```

Import `NOT_CACHED_MARKER` from `app.ui.state`. In `_header`, call `_league_switcher(state)` as the first element of the right-hand row, before the source toggle. `on_refresh` already reloads the page after a refresh, so the unfetched-league path ends on the new league.

- [ ] **Step 2: Settings changes**

In `app/ui/settings.py`:
- Remove the "League id" input and its help label (`form["league_id"]` stays in the dict so validation and `config_from_form` keep working).
- Put the Usernames input first.
- After the Save button and the refresh row, add a refreshable "Leagues" section:

```python
        @ui.refreshable
        def leagues_section() -> None:
            ui.label("LEAGUES").classes("ff-label")
            if not state.summaries:
                ui.label("No leagues found yet. Save a username, then Refresh.").style(f"color:{MUTED}")
                return
            for summary in state.summaries:
                with ui.row().classes("ff-card q-pa-sm items-center gap-4 w-full no-wrap"):
                    ui.label(summary.label).style(f"color:{TEXT};min-width:18rem")
                    ui.label(", ".join(summary.members)).classes("ff-label")
                    if summary.league_id == state.active:
                        ui.label("active").style(f"color:{ACCENT}")
                    else:
                        ui.button("Switch", on_click=lambda _, lid=summary.league_id: on_switch(lid)).props("dense outline")

        leagues_section()
```

and in `on_save`, after the notification, call `leagues_section.refresh()`. Import `on_switch` from `app.ui.layout` and `ACCENT, TEXT` from theme. Add `"league": "Usernames saved and leagues rediscovered"` as the new message for that scope.

- [ ] **Step 3: Launch and drive**

Run in the background: `FF_NATIVE=0 uv run python -m app run`. Market snapshots are now keyed by league (Task 4), so the pre-existing cache has none under the new key: press the header Refresh once before checking values anywhere. With the Playwright tools:
1. Open `/settings`. Confirm the Usernames field shows `alice` and no League id field. Confirm the Leagues section lists "Kathy VS Jenifer · redraft 10" as active (it may say "No leagues found yet" until the first refresh under the new code; if so press the header Refresh and reload).
2. Set Usernames to `alice, bob` and Save. Confirm the positive notification and that the Leagues section now lists eight leagues (the shared one shows members `alice, bob`).
3. In the header select, pick "Trade addicts · dynasty 12". Confirm the info notification, the refresh, and that the header then shows "Trade addicts" with its week. Open `/league` and `/matchups` and confirm they render for that league; on `/matchups` confirm the my-card is for bob's roster (he is the only configured member there).
4. Pick "Kathy VS Jenifer" again. Confirm it is instant (no refresh) and the my-card on `/matchups` is alice's roster.
5. Open `/players` in each of the two leagues and confirm market values differ (dynasty vs redraft scale), proving per-league snapshots.
6. Server log: no tracebacks. Kill the server; confirm gone; `config.local.toml` now has `usernames = ["alice", "bob"]` and `league_id = "1000000000000000001"`. Do not commit it (ignored).

- [ ] **Step 4: Run tests and commit**

Run: `uv run pytest -q` → all pass.

```bash
git add app/ui/layout.py app/ui/settings.py
git commit -m "Add a league switcher to the header and a league list to settings"
```

---

## Dependency graph for parallel execution

- Wave 1: Task 1, Task 2 (independent).
- Wave 2: Task 3 (needs 2), Task 4 (needs 3).
- Wave 3: Task 5 (needs 1, 2, 3, 4).
- Wave 4: Task 6 (needs 5).
