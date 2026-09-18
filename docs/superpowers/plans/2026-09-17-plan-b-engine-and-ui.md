# ff-analyst Plan B: Live Projections, Trade Engine, Manager Profiles, UI

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the Plan A data and model into the desktop app: live rest-of-season projections for every rostered and free-agent player, a value table, exact optimal-lineup trade grading, a trade finder, manager profiles, and six NiceGUI screens in a native dark window.

**Architecture:** A `LeagueContext` loads everything the screens need from the caches in one call. Two projection sources (consensus-based and model-based) produce the same per-player per-week frame; a setting picks which one drives lineups. Every screen calls one function in `app/model` that returns a polars frame or dataclass, so the model layer is tested without a browser and the UI stays thin.

**Tech Stack:** NiceGUI 3.17 native window (pywebview), ECharts via `ui.echart`, polars, DuckDB, SQLite, httpx, pytest. Repo `~/code/ff-analyst`, branch `plan-b` from `plan-a`, `./.venv`.

**Spec:** `docs/superpowers/specs/2026-09-16-ff-analyst-design.md` (sections Trade engine, Manager profiles, UI, Error handling).

## Global Constraints

- Dependencies are exactly nicegui[native], duckdb, polars, pyarrow, httpx, nflreadpy, pytest. Do not add any other package. If you believe one is needed, stop and report.
- Type hints on every function. Small functions. No dead code, no commented-out code, no TODO comments.
- No AI attribution anywhere: no generated-with comments, no trailers in commits, no signatures, no watermarks. Commit messages are plain imperative sentences.
- Scoring rules and roster slots always come from the league dict loaded from the cache. Never hardcode PPR, slot lists, team count, or week counts outside tests. NFL team colors and CDN URL patterns are static reference data and may live in `app/ui/theme.py`.
- Tests never hit the network. UI modules are not unit tested (no extra test dependency); they are verified by launching the app in Task 14 and driving it.
- Visual direction (Garret, 2026-09-17): black, high contrast, terminal-esque. Pure black background `#000000`, cards `#0A0A0A` with a 1px `#262626` border and 4px corners, text `#F2F2F2`, muted `#8A8F98`, one accent `#F5A524`, monospace type everywhere (`"JetBrains Mono", "SF Mono", Menlo, Consolas, monospace`), uppercase 11px letter-spaced section labels, no gradients, no shadows. Team colors only as a 3px left border or a small badge. Cards, not tables, wherever possible. ECharts for every chart with the same palette. Refresh button plus refresh on launch; no scheduler.
- Rulings inherited from Plan A: the model-based projection is not the default until the gate passes; `projection_source` defaults to `"consensus"` and every screen labels which source it shows. Availability comes from `app/model/availability.py` (Task 14 of Plan A).
- Run tests with `./.venv/bin/python -m pytest -q` from the repo root.

---

## File map

| File | Responsibility |
|---|---|
| `app/model/league.py` | `LeagueContext`: league, users, rosters, state, scoring, slots, remaining weeks, opponents, Sleeper weekly points |
| `app/model/live.py` | Per-player per-week projections for the current season from either source; ROS totals; K and DEF handling |
| `app/model/values.py` | Player value table: market vs intrinsic, floor, ceiling, consistency, trend, ownership, images |
| `app/model/lineup.py` | Exact optimal lineup for one week; team strength over remaining weeks |
| `app/model/trade.py` | Trade delta for both sides |
| `app/model/finder.py` | Enumerate mutually beneficial deals per opponent |
| `app/model/managers.py` | Manager profiles from the league chain's transactions |
| `app/main.py` | Entry point: native window, dark theme, routes |
| `app/ui/theme.py` | Palette, accent, NFL team colors, CDN URL helpers, ECharts base options |
| `app/ui/state.py` | App-wide state: contexts, projections, selected source, refresh in background |
| `app/ui/layout.py` | Header, nav, refresh button, page frame |
| `app/ui/heatmap.py`, `players.py`, `scatter.py`, `trade.py`, `finder.py`, `managers.py` | One screen each |

---

### Task 1: League context

**Files:**
- Create: `app/model/league.py`
- Test: `tests/test_league.py`

**Interfaces:**
- Consumes: `Store` (cache keys `sleeper:/league/{id}`, `sleeper:/league/{id}/users`, `sleeper:/league/{id}/rosters`, `sleeper:/league/{id}/matchups/{w}`, `sleeper:/state/nfl`), DuckDB `schedules`, `starter_slots` from `app/model/replacement.py`.
- Produces:
  - `@dataclass(frozen=True) Roster(roster_id: int, owner_id: str, team_name: str, display_name: str, avatar: str | None, players: tuple[str, ...], starters: tuple[str, ...], reserve: tuple[str, ...])`
  - `@dataclass(frozen=True) LeagueContext(league_id: str, name: str, season: int, week: int, n_teams: int, scoring: dict[str, float], roster_positions: list[str], slots: list[str], playoff_week_start: int, last_week: int, trade_deadline: int, rosters: tuple[Roster, ...])` with property `remaining_weeks -> list[int]` (`week .. last_week` inclusive) and method `roster_of(player_id: str) -> int | None`.
  - `load_league_context(store: Store, league_id: str) -> LeagueContext`; raises `ValueError("league not cached")` when the league body is missing.
  - `team_opponents(con, season: int, weeks: list[int]) -> pl.DataFrame` with `week, team, opponent` (REG games only; byes absent).
  - `sleeper_points(store, league_id, weeks) -> pl.DataFrame` with `week: Int64, sleeper_id: Utf8, points: Float64` parsed from each cached matchup's `players_points`.
  - `LAST_NFL_WEEK = 17`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_league.py
import json
from pathlib import Path

import polars as pl

from app.data.nflverse import connect, replace_table
from app.data.store import Store
from app.model.league import LeagueContext, load_league_context, sleeper_points, team_opponents

LEAGUE = {"league_id": "1", "name": "Test", "season": "2026", "total_rosters": 2,
          "scoring_settings": {"rec": 1.0}, "roster_positions": ["QB", "RB", "FLEX", "BN", "IR"],
          "settings": {"playoff_week_start": 15, "trade_deadline": 12}}
USERS = [{"user_id": "u1", "display_name": "Ann", "avatar": "a1", "metadata": {"team_name": "Ann's Army"}},
         {"user_id": "u2", "display_name": "Bob", "avatar": None, "metadata": {}}]
ROSTERS = [{"roster_id": 1, "owner_id": "u1", "players": ["p1", "p2", "p3"], "starters": ["p1", "p2"], "reserve": ["p3"]},
           {"roster_id": 2, "owner_id": "u2", "players": ["p4"], "starters": ["p4"], "reserve": None}]


def seed(store: Store) -> None:
    store.put_cached("sleeper:/league/1", json.dumps(LEAGUE), now=1.0)
    store.put_cached("sleeper:/league/1/users", json.dumps(USERS), now=1.0)
    store.put_cached("sleeper:/league/1/rosters", json.dumps(ROSTERS), now=1.0)
    store.put_cached("sleeper:/state/nfl", json.dumps({"week": 3, "season": "2026"}), now=1.0)
    store.put_cached("sleeper:/league/1/matchups/1", json.dumps([
        {"roster_id": 1, "players_points": {"p1": 12.5, "p2": 0.0}}, {"roster_id": 2, "players_points": {"p4": 7.0}}]), now=1.0)
    store.put_cached("sleeper:/league/1/matchups/2", json.dumps([{"roster_id": 1, "players_points": {"p1": 20.0}}]), now=1.0)


def test_load_league_context(tmp_path: Path) -> None:
    store = Store(tmp_path / "app.sqlite")
    seed(store)
    ctx = load_league_context(store, "1")
    assert isinstance(ctx, LeagueContext)
    assert (ctx.name, ctx.season, ctx.week, ctx.n_teams) == ("Test", 2026, 3, 2)
    assert ctx.slots == ["QB", "RB", "FLEX"]
    assert ctx.playoff_week_start == 15 and ctx.trade_deadline == 12
    assert ctx.remaining_weeks == list(range(3, 18))
    ann = ctx.rosters[0]
    assert (ann.team_name, ann.display_name, ann.reserve) == ("Ann's Army", "Ann", ("p3",))
    assert ctx.rosters[1].team_name == "Bob"
    assert ctx.roster_of("p4") == 2 and ctx.roster_of("nope") is None


def test_missing_league_raises(tmp_path: Path) -> None:
    store = Store(tmp_path / "app.sqlite")
    try:
        load_league_context(store, "9")
    except ValueError as exc:
        assert "not cached" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_team_opponents_and_sleeper_points(tmp_path: Path) -> None:
    con = connect(tmp_path / "s.duckdb")
    replace_table(con, "schedules", pl.DataFrame({
        "season": [2026, 2026, 2026], "week": [3, 3, 4], "game_type": ["REG", "POST", "REG"],
        "home_team": ["DET", "X", "MIN"], "away_team": ["MIN", "Y", "GB"], "gameday": ["2026-09-20"] * 3}))
    opp = team_opponents(con, 2026, [3, 4]).sort("week", "team")
    assert opp.rows() == [(3, "DET", "MIN"), (3, "MIN", "DET"), (4, "GB", "MIN"), (4, "MIN", "GB")]
    store = Store(tmp_path / "app.sqlite")
    seed(store)
    pts = sleeper_points(store, "1", [1, 2]).sort("week", "sleeper_id")
    assert pts.rows() == [(1, "p1", 12.5), (1, "p2", 0.0), (1, "p4", 7.0), (2, "p1", 20.0)]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/bin/python -m pytest tests/test_league.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.model.league'`

- [ ] **Step 3: Implement**

```python
# app/model/league.py
"""Everything the screens need about the league, loaded from the caches in one call."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import duckdb
import polars as pl

from app.data.store import Store
from app.model.replacement import starter_slots

LAST_NFL_WEEK = 17


@dataclass(frozen=True)
class Roster:
    roster_id: int
    owner_id: str
    team_name: str
    display_name: str
    avatar: str | None
    players: tuple[str, ...]
    starters: tuple[str, ...]
    reserve: tuple[str, ...]


@dataclass(frozen=True)
class LeagueContext:
    league_id: str
    name: str
    season: int
    week: int
    n_teams: int
    scoring: dict[str, float]
    roster_positions: list[str]
    slots: list[str]
    playoff_week_start: int
    last_week: int
    trade_deadline: int
    rosters: tuple[Roster, ...]

    @property
    def remaining_weeks(self) -> list[int]:
        return list(range(self.week, self.last_week + 1))

    def roster_of(self, player_id: str) -> int | None:
        for roster in self.rosters:
            if player_id in roster.players:
                return roster.roster_id
        return None


def _cached_json(store: Store, key: str) -> Any:
    body = store.get_cached(key, None, 0.0)
    return None if body is None else json.loads(body)


def _rosters(raw: list[dict[str, Any]], users: list[dict[str, Any]]) -> tuple[Roster, ...]:
    by_id = {u["user_id"]: u for u in users}
    out: list[Roster] = []
    for r in raw:
        user = by_id.get(r.get("owner_id") or "", {})
        name = (user.get("metadata") or {}).get("team_name") or user.get("display_name") or f"Roster {r['roster_id']}"
        out.append(Roster(
            roster_id=int(r["roster_id"]), owner_id=str(r.get("owner_id") or ""), team_name=str(name),
            display_name=str(user.get("display_name") or ""), avatar=user.get("avatar"),
            players=tuple(r.get("players") or ()), starters=tuple(r.get("starters") or ()),
            reserve=tuple(r.get("reserve") or ()),
        ))
    return tuple(sorted(out, key=lambda x: x.roster_id))


def load_league_context(store: Store, league_id: str) -> LeagueContext:
    league = _cached_json(store, f"sleeper:/league/{league_id}")
    if league is None:
        raise ValueError(f"league {league_id} not cached; run refresh first")
    users = _cached_json(store, f"sleeper:/league/{league_id}/users") or []
    rosters = _cached_json(store, f"sleeper:/league/{league_id}/rosters") or []
    state = _cached_json(store, "sleeper:/state/nfl") or {"week": 1}
    settings = league.get("settings") or {}
    positions = list(league["roster_positions"])
    return LeagueContext(
        league_id=league_id, name=str(league["name"]), season=int(league["season"]), week=int(state["week"]),
        n_teams=int(league["total_rosters"]), scoring={k: float(v) for k, v in league["scoring_settings"].items()},
        roster_positions=positions, slots=starter_slots(positions),
        playoff_week_start=int(settings.get("playoff_week_start", 15)), last_week=LAST_NFL_WEEK,
        trade_deadline=int(settings.get("trade_deadline", 99)), rosters=_rosters(rosters, users),
    )


def team_opponents(con: duckdb.DuckDBPyConnection, season: int, weeks: list[int]) -> pl.DataFrame:
    games = con.execute(
        "SELECT week, home_team, away_team FROM schedules WHERE season = ? AND game_type = 'REG' "
        "AND week IN (SELECT unnest(?))", [season, weeks],
    ).pl().with_columns(pl.col("week").cast(pl.Int64))
    home = games.select("week", pl.col("home_team").alias("team"), pl.col("away_team").alias("opponent"))
    away = games.select("week", pl.col("away_team").alias("team"), pl.col("home_team").alias("opponent"))
    return pl.concat([home, away])


def sleeper_points(store: Store, league_id: str, weeks: list[int]) -> pl.DataFrame:
    rows: list[tuple[int, str, float]] = []
    for week in weeks:
        for matchup in _cached_json(store, f"sleeper:/league/{league_id}/matchups/{week}") or []:
            for pid, pts in (matchup.get("players_points") or {}).items():
                rows.append((week, str(pid), float(pts)))
    return pl.DataFrame(rows, schema={"week": pl.Int64, "sleeper_id": pl.Utf8, "points": pl.Float64}, orient="row")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./.venv/bin/python -m pytest tests/test_league.py -q`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add app/model/league.py tests/test_league.py
git commit -m "Add league context loaded from the Sleeper cache"
```

---

### Task 2: Live projections for the current season

**Files:**
- Create: `app/model/live.py`
- Test: `tests/test_live.py`

**Interfaces:**
- Consumes: `LeagueContext`, `team_opponents`, `sleeper_points` (Task 1); `consensus_snapshot` (app/market.py); `id_hub` (app/data/identity.py); `score_weeks`, `expected_points`, `SKILL_POSITIONS` (app/data/scoring.py); `season_ppg`, `player_ages`, `fit_age_curve` (app/model/agecurve.py); `rank_to_ppg_curve`, `positional_ranks`, `curve_ppg`, `Inputs`, `build_features`, `blend`, `BlendParams`, `opponent_factors` (app/model/projections.py); `replacement_ranks`, `replacement_ppg` (app/model/replacement.py); `availability`, `sleeper_overlay` (app/model/availability.py from Plan A Task 14); DuckDB tables `player_stats, ff_opportunity, injuries, rosters_weekly, sleeper_players, ff_playerids, schedules`.
- Produces:
  - `SOURCES = ("consensus", "model")`.
  - `@dataclass(frozen=True) Projection(source: str, weekly: pl.DataFrame, ppg: pl.DataFrame)` where `weekly` has `sleeper_id, gsis_id, position, week, points` for every remaining week and every player in the universe, and `ppg` has `sleeper_id, gsis_id, position, nfl_team, ppg, avail_ros`.
  - `project(con, store, ctx: LeagueContext, source: str, params: BlendParams = BlendParams()) -> Projection`.
  - `ros_totals(proj: Projection) -> pl.DataFrame` with `sleeper_id, position, ros_points` (sum of weekly points).
  - Universe: every player in `sleeper_players` with a gsis_id and position in QB/RB/WR/TE plus every K and DEF that appears in `sleeper_players` and is rostered or in the consensus snapshot.
  - Consensus source: current-week consensus snapshot (page_type redraft-overall) → positional rank → `curve_ppg` on the previous season's rank-to-PPG curve; DEF (pos DST → DEF) and K use the same curve from their previous-season PPG computed from Sleeper points if present in the previous league season, else from `player_stats` kicking columns for K; DEF with no curve uses the median of this season's Sleeper DEF points.
  - Model source: `build_features` on the current season through `week - 1` then `blend`; K and DEF fall back to the consensus source.
  - Both sources multiply by availability (`availability` for the current week then `sleeper_overlay` with live injury_status), then per week: `ppg * opponent_factor` (factors from this season's scored weeks through `week - 1`, `1.0` before week 3) with byes at 0; weeks after `week` use `avail_ros`, the current week uses `avail_now`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_live.py
import polars as pl

from app.model.live import _apply_weeks, ros_totals


def test_apply_weeks_uses_now_then_ros_and_byes() -> None:
    ppg = pl.DataFrame({"sleeper_id": ["s1"], "gsis_id": ["g1"], "position": ["WR"], "nfl_team": ["DET"],
                        "ppg": [10.0], "avail_now": [0.0], "avail_ros": [0.85]})
    opponents = pl.DataFrame({"week": [3, 5], "team": ["DET", "DET"], "opponent": ["MIN", "GB"]})
    factors = pl.DataFrame({"defense": ["MIN", "GB"], "position": ["WR", "WR"], "factor": [1.1, 0.9]})
    weekly = _apply_weeks(ppg, opponents, factors, week=3, weeks=[3, 4, 5]).sort("week")
    assert weekly["points"].to_list() == [0.0, 0.0, 7.65]


def test_ros_totals_sums_weeks() -> None:
    weekly = pl.DataFrame({"sleeper_id": ["s1", "s1", "s2"], "gsis_id": ["g1", "g1", None], "position": ["WR", "WR", "DEF"],
                           "week": [3, 4, 3], "points": [5.0, 6.0, 8.0]})
    from app.model.live import Projection
    out = ros_totals(Projection("consensus", weekly, pl.DataFrame())).sort("sleeper_id")
    assert out.rows() == [("s1", "WR", 11.0), ("s2", "DEF", 8.0)]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/bin/python -m pytest tests/test_live.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.model.live'`

- [ ] **Step 3: Implement**

```python
# app/model/live.py
"""Rest-of-season projections for the current season from either source."""
from __future__ import annotations

from dataclasses import dataclass

import duckdb
import polars as pl

from app.data.identity import id_hub
from app.data.scoring import SKILL_POSITIONS, expected_points, score_weeks
from app.data.store import Store
from app.market import consensus_snapshot
from app.model.agecurve import fit_age_curve, player_ages, season_ppg
from app.model.availability import availability, sleeper_overlay
from app.model.league import LeagueContext, sleeper_points, team_opponents
from app.model.projections import (
    BlendParams, Inputs, blend, build_features, curve_ppg, opponent_factors, positional_ranks, rank_to_ppg_curve,
)
from app.model.replacement import replacement_ppg, replacement_ranks

SOURCES = ("consensus", "model")
FACTOR_MIN_WEEK = 3


@dataclass(frozen=True)
class Projection:
    source: str
    weekly: pl.DataFrame
    ppg: pl.DataFrame


def _players(con: duckdb.DuckDBPyConnection) -> pl.DataFrame:
    return con.execute(
        "SELECT sleeper_id, full_name, position, team AS nfl_team, injury_status, gsis_id FROM sleeper_players"
    ).pl()


def _hub(con: duckdb.DuckDBPyConnection) -> pl.DataFrame:
    return id_hub(con.execute("SELECT * FROM ff_playerids").pl())


def _scored(con: duckdb.DuckDBPyConnection, seasons: list[int], scoring: dict[str, float]) -> pl.DataFrame:
    stats = con.execute(
        "SELECT * FROM player_stats WHERE season IN (SELECT unnest(?)) AND season_type = 'REG'", [seasons]
    ).pl()
    return score_weeks(stats, scoring).select(
        "player_id", "season", "week", "season_type", "position", "team", "opponent_team", "points"
    )


def _prev_curve(con: duckdb.DuckDBPyConnection, ctx: LeagueContext) -> pl.DataFrame:
    seasonal = season_ppg(_scored(con, [ctx.season - 1], ctx.scoring))
    return rank_to_ppg_curve(seasonal, ctx.season - 1)


def _universe(players: pl.DataFrame, ctx: LeagueContext, consensus_ids: set[str]) -> pl.DataFrame:
    rostered = {p for r in ctx.rosters for p in r.players}
    skill = players.filter(pl.col("position").is_in(SKILL_POSITIONS) & pl.col("gsis_id").is_not_null())
    kd = players.filter(pl.col("position").is_in(["K", "DEF"]) & (
        pl.col("sleeper_id").is_in(list(rostered)) | pl.col("sleeper_id").is_in(list(consensus_ids))
    ))
    return pl.concat([skill, kd]).unique(subset=["sleeper_id"])


def _consensus_ppg(con: duckdb.DuckDBPyConnection, ctx: LeagueContext, hub: pl.DataFrame, curve: pl.DataFrame) -> pl.DataFrame:
    snap = positional_ranks(consensus_snapshot(con, ctx.season, ctx.week))
    with_ids = snap.join(hub.select("fantasypros_id", "gsis_id"), on="fantasypros_id", how="left")
    ppg = [curve_ppg(curve, str(p), int(r)) for p, r in zip(with_ids["pos"], with_ids["pos_rank"])]
    return with_ids.with_columns(pl.Series("ppg", ppg, dtype=pl.Float64)).select(
        "gsis_id", "fantasypros_id", pl.col("pos").alias("position"), "ppg"
    )


def _model_ppg(con: duckdb.DuckDBPyConnection, ctx: LeagueContext, hub: pl.DataFrame, curve: pl.DataFrame,
               params: BlendParams) -> pl.DataFrame:
    history = _scored(con, list(range(ctx.season - 6, ctx.season)), ctx.scoring)
    seasonal = season_ppg(history)
    prev = seasonal.filter(pl.col("season") == ctx.season - 1)
    ages = player_ages(hub, list(range(ctx.season - 6, ctx.season + 1)))
    scored = _scored(con, [ctx.season], ctx.scoring)
    opportunity = expected_points(
        con.execute("SELECT * FROM ff_opportunity WHERE season = ?", [ctx.season]).pl(), ctx.scoring
    ).select("player_id", "season", "week", "exp_points")
    prev_ppg = prev.select("player_id", "position", "ppg")
    replacement = replacement_ppg(prev_ppg, replacement_ranks(ctx.roster_positions, ctx.n_teams, prev_ppg))
    inputs = Inputs(
        scored=scored, opportunity=opportunity, hub=hub, consensus_preseason=consensus_snapshot(con, ctx.season, 1),
        prev_curve=curve, prev_seasonal=prev, age_curve=fit_age_curve(seasonal, ages),
        ages=ages.filter(pl.col("season") == ctx.season).select("player_id", "age"), replacement=replacement,
    )
    return blend(build_features(inputs, ctx.season, ctx.week), params).select(
        pl.col("player_id").alias("gsis_id"), "position", "ppg"
    )


def _kd_from_sleeper(store: Store, ctx: LeagueContext, players: pl.DataFrame) -> pl.DataFrame:
    pts = sleeper_points(store, ctx.league_id, list(range(1, ctx.week)))
    kd = players.filter(pl.col("position").is_in(["K", "DEF"])).select("sleeper_id", "position")
    mean = pts.join(kd, on="sleeper_id", how="inner").group_by("sleeper_id", "position").agg(pl.col("points").mean().alias("ppg"))
    median = mean.group_by("position").agg(pl.col("ppg").median().alias("pos_median"))
    return kd.join(mean, on=["sleeper_id", "position"], how="left").join(median, on="position", how="left").select(
        "sleeper_id", "position", pl.coalesce(pl.col("ppg"), pl.col("pos_median"), pl.lit(0.0)).alias("ppg")
    )


def _availability(con: duckdb.DuckDBPyConnection, ctx: LeagueContext, universe: pl.DataFrame) -> pl.DataFrame:
    rosters_weekly = con.execute(
        "SELECT * FROM rosters_weekly WHERE season = ? AND game_type = 'REG'", [ctx.season]
    ).pl()
    injuries = con.execute("SELECT * FROM injuries WHERE season = ? AND game_type = 'REG'", [ctx.season]).pl()
    ids = universe.filter(pl.col("gsis_id").is_not_null()).select(pl.col("gsis_id").alias("player_id"))
    avail = availability(rosters_weekly, injuries, ctx.season, ctx.week, ids)
    live = con.execute("SELECT sleeper_id, gsis_id, injury_status FROM sleeper_players").pl()
    return sleeper_overlay(avail, live).rename({"player_id": "gsis_id"})


def _apply_weeks(ppg: pl.DataFrame, opponents: pl.DataFrame, factors: pl.DataFrame, week: int, weeks: list[int]) -> pl.DataFrame:
    grid = ppg.join(pl.DataFrame({"week": weeks}), how="cross")
    with_opp = grid.join(opponents, left_on=["week", "nfl_team"], right_on=["week", "team"], how="left")
    with_factor = with_opp.join(
        factors, left_on=["opponent", "position"], right_on=["defense", "position"], how="left"
    ).with_columns(pl.col("factor").fill_null(1.0))
    avail = pl.when(pl.col("week") == week).then(pl.col("avail_now")).otherwise(pl.col("avail_ros"))
    points = pl.when(pl.col("opponent").is_null()).then(0.0).otherwise(pl.col("ppg") * pl.col("factor") * avail)
    return with_factor.select("sleeper_id", "gsis_id", "position", "week", points.alias("points"))


def _factors(con: duckdb.DuckDBPyConnection, ctx: LeagueContext) -> pl.DataFrame:
    if ctx.week < FACTOR_MIN_WEEK:
        return pl.DataFrame({"defense": [], "position": [], "factor": []}, schema={"defense": pl.Utf8, "position": pl.Utf8, "factor": pl.Float64})
    return opponent_factors(_scored(con, [ctx.season], ctx.scoring), through_week=ctx.week - 1)


def project(con: duckdb.DuckDBPyConnection, store: Store, ctx: LeagueContext, source: str,
            params: BlendParams = BlendParams()) -> Projection:
    if source not in SOURCES:
        raise ValueError(f"unknown projection source {source}")
    players = _players(con)
    hub = _hub(con)
    curve = _prev_curve(con, ctx)
    cons = _consensus_ppg(con, ctx, hub, curve)
    cons_sleeper = cons.join(hub.select("gsis_id", "sleeper_id"), on="gsis_id", how="left")
    universe = _universe(players, ctx, set(cons_sleeper["sleeper_id"].drop_nulls().to_list()))
    skill_ppg = cons.select("gsis_id", "ppg") if source == "consensus" else _model_ppg(con, ctx, hub, curve, params).select("gsis_id", "ppg")
    kd = _kd_from_sleeper(store, ctx, players)
    base = universe.join(skill_ppg, on="gsis_id", how="left").join(kd.select("sleeper_id", pl.col("ppg").alias("kd_ppg")), on="sleeper_id", how="left")
    base = base.with_columns(pl.coalesce(pl.col("ppg"), pl.col("kd_ppg"), pl.lit(0.0)).alias("ppg")).drop("kd_ppg")
    avail = _availability(con, ctx, universe)
    ppg = base.join(avail, on="gsis_id", how="left").with_columns(
        pl.col("avail_now").fill_null(1.0), pl.col("avail_ros").fill_null(1.0)
    ).select("sleeper_id", "gsis_id", "position", "nfl_team", "ppg", "avail_now", "avail_ros")
    weekly = _apply_weeks(ppg, team_opponents(con, ctx.season, ctx.remaining_weeks), _factors(con, ctx), ctx.week, ctx.remaining_weeks)
    return Projection(source=source, weekly=weekly, ppg=ppg.drop("avail_now"))


def ros_totals(proj: Projection) -> pl.DataFrame:
    return proj.weekly.group_by("sleeper_id", "position").agg(pl.col("points").sum().alias("ros_points"))
```

Note for the implementer: DEF players in `sleeper_players` have `sleeper_id` equal to the team abbreviation and `gsis_id` null; they still get an availability of 1.0 through the fill and a `nfl_team` from Sleeper's `team` column, so byes apply.

- [ ] **Step 4: Run tests to verify they pass**

Run: `./.venv/bin/python -m pytest tests/test_live.py -q`
Expected: 2 passed

- [ ] **Step 5: Real smoke, then commit**

Run from the repo root with the live cache: `./.venv/bin/python -c "from app.config import load_config; from app.data.store import Store; from app.data.nflverse import connect; from app.model.league import load_league_context; from app.model.live import project, ros_totals; import os; cfg=load_config(__import__('pathlib').Path('.'), os.environ); s=Store(cfg.sqlite_path); c=connect(cfg.duckdb_path); ctx=load_league_context(s, cfg.league_id); p=project(c, s, ctx, 'consensus'); print(p.weekly.height, ros_totals(p).sort('ros_points', descending=True).head(10))"`
Expected: a positive row count and a top-10 that looks like real fantasy studs. Fix genuine errors, then:

```bash
git add app/model/live.py tests/test_live.py
git commit -m "Add live rest-of-season projections from either source"
```

---

### Task 3: Value table

**Files:**
- Create: `app/model/values.py`
- Test: `tests/test_values.py`

**Interfaces:**
- Consumes: `Projection`, `ros_totals` (Task 2); `LeagueContext` (Task 1); `Store.latest_snapshot("fantasycalc")`; `sleeper_points`; `replacement_from_rosters` (app/model/replacement.py).
- Produces: `player_values(ctx, con, store, primary: Projection, secondary: Projection | None) -> pl.DataFrame` with columns `sleeper_id, gsis_id, name, position, nfl_team, roster_id (Int64, null = free agent), team_name, market_value, market_rank, ros_primary, ros_secondary, replacement_ros, intrinsic, floor, ceiling, consistency, trend (List[Float64] of this season's Sleeper weekly points), injury_status`. `intrinsic = ros_primary - replacement_ros` where `replacement_ros` is the best unrostered player's ros at that position. `floor`/`ceiling` are the 20th/80th percentiles of the trend when at least 3 weeks exist, else null; `consistency` is std/mean, null when fewer than 3 weeks or mean 0.
- Helper `replacement_ros(values_like: pl.DataFrame) -> dict[str, float]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_values.py
import polars as pl

from app.model.values import _trend_stats, replacement_ros


def test_trend_stats_percentiles_and_consistency() -> None:
    out = _trend_stats(pl.DataFrame({"sleeper_id": ["a"] * 5 + ["b"], "week": [1, 2, 3, 4, 5, 1],
                                     "points": [10.0, 20.0, 30.0, 40.0, 50.0, 9.0]})).sort("sleeper_id")
    a, b = out.row(0, named=True), out.row(1, named=True)
    assert a["trend"] == [10.0, 20.0, 30.0, 40.0, 50.0]
    assert a["floor"] == 18.0 and a["ceiling"] == 42.0
    assert round(a["consistency"], 3) == round((pl.Series([10.0, 20, 30, 40, 50]).std() / 30.0), 3)
    assert b["floor"] is None and b["consistency"] is None


def test_replacement_ros_is_best_unrostered() -> None:
    frame = pl.DataFrame({"sleeper_id": ["a", "b", "c"], "position": ["RB", "RB", "RB"],
                          "roster_id": [1, None, None], "ros_primary": [100.0, 60.0, 70.0]})
    assert replacement_ros(frame) == {"RB": 70.0}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/bin/python -m pytest tests/test_values.py -q`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement**

```python
# app/model/values.py
"""One row per player: market value, projected points, intrinsic value, and card details."""
from __future__ import annotations

import duckdb
import polars as pl

from app.data.store import Store
from app.model.league import LeagueContext, sleeper_points
from app.model.live import Projection, ros_totals

MIN_TREND_WEEKS = 3


def _trend_stats(points: pl.DataFrame) -> pl.DataFrame:
    grouped = points.sort("week").group_by("sleeper_id").agg(
        pl.col("points").alias("trend"),
        pl.col("points").quantile(0.2, "linear").alias("floor"),
        pl.col("points").quantile(0.8, "linear").alias("ceiling"),
        (pl.col("points").std() / pl.col("points").mean()).alias("consistency"),
        pl.len().alias("weeks"),
    )
    few = pl.col("weeks") < MIN_TREND_WEEKS
    return grouped.with_columns(
        pl.when(few).then(None).otherwise(pl.col("floor")).alias("floor"),
        pl.when(few).then(None).otherwise(pl.col("ceiling")).alias("ceiling"),
        pl.when(few | pl.col("consistency").is_infinite() | pl.col("consistency").is_nan()).then(None)
        .otherwise(pl.col("consistency")).alias("consistency"),
    ).drop("weeks")


def replacement_ros(frame: pl.DataFrame) -> dict[str, float]:
    free = frame.filter(pl.col("roster_id").is_null()).group_by("position").agg(pl.col("ros_primary").max())
    return {str(p): float(v) for p, v in zip(free["position"], free["ros_primary"])}


def _ownership(ctx: LeagueContext) -> pl.DataFrame:
    rows = [(p, r.roster_id, r.team_name) for r in ctx.rosters for p in r.players]
    return pl.DataFrame(rows, schema={"sleeper_id": pl.Utf8, "roster_id": pl.Int64, "team_name": pl.Utf8}, orient="row")


def player_values(ctx: LeagueContext, con: duckdb.DuckDBPyConnection, store: Store,
                  primary: Projection, secondary: Projection | None) -> pl.DataFrame:
    players = con.execute("SELECT sleeper_id, full_name AS name, position, team AS nfl_team, injury_status FROM sleeper_players").pl()
    base = primary.ppg.select("sleeper_id", "gsis_id").join(players, on="sleeper_id", how="left")
    ros_p = ros_totals(primary).select("sleeper_id", pl.col("ros_points").alias("ros_primary"))
    frame = base.join(ros_p, on="sleeper_id", how="left")
    if secondary is not None:
        frame = frame.join(ros_totals(secondary).select("sleeper_id", pl.col("ros_points").alias("ros_secondary")), on="sleeper_id", how="left")
    else:
        frame = frame.with_columns(pl.lit(None, dtype=pl.Float64).alias("ros_secondary"))
    market = store.latest_snapshot("fantasycalc").select(
        "sleeper_id", pl.col("value").alias("market_value"), pl.col("overall_rank").alias("market_rank")
    )
    frame = frame.join(market, on="sleeper_id", how="left").join(_ownership(ctx), on="sleeper_id", how="left")
    repl = replacement_ros(frame)
    frame = frame.with_columns(
        pl.col("position").replace_strict(repl, default=0.0, return_dtype=pl.Float64).alias("replacement_ros")
    ).with_columns((pl.col("ros_primary") - pl.col("replacement_ros")).alias("intrinsic"))
    trend = _trend_stats(sleeper_points(store, ctx.league_id, list(range(1, ctx.week))))
    return frame.join(trend, on="sleeper_id", how="left").select(
        "sleeper_id", "gsis_id", "name", "position", "nfl_team", "roster_id", "team_name", "market_value", "market_rank",
        "ros_primary", "ros_secondary", "replacement_ros", "intrinsic", "floor", "ceiling", "consistency", "trend", "injury_status",
    )
```

- [ ] **Step 4: Run tests, then commit**

Run: `./.venv/bin/python -m pytest tests/test_values.py -q`
Expected: 2 passed

```bash
git add app/model/values.py tests/test_values.py
git commit -m "Add player value table"
```

---

### Task 4: Exact lineup solver and team strength

**Files:**
- Create: `app/model/lineup.py`
- Test: `tests/test_lineup.py`

**Interfaces:**
- Consumes: `FLEX_ELIGIBILITY` (app/model/replacement.py).
- Produces:
  - `eligible(slot: str, position: str) -> bool`.
  - `optimal_lineup(slots: list[str], players: list[tuple[str, str, float]]) -> tuple[float, dict[str, str]]` returning total points and `{slot_index_as_str: player_id}` for filled slots; exact via memoized DP over (slot index, used bitmask); a slot with no eligible player left is skipped at 0.
  - `weekly_strength(slots, weekly: pl.DataFrame, player_ids: set[str]) -> pl.DataFrame` with `week, points` where `weekly` has `sleeper_id, position, week, points`.
  - `team_strength(slots, weekly, player_ids) -> float` (sum over weeks).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_lineup.py
import polars as pl

from app.model.lineup import eligible, optimal_lineup, team_strength


def test_eligibility() -> None:
    assert eligible("FLEX", "RB") and eligible("FLEX", "TE") and not eligible("FLEX", "QB")
    assert eligible("SUPER_FLEX", "QB") and eligible("DEF", "DEF") and not eligible("K", "DEF")


def test_optimal_lineup_uses_flex_for_best_leftover() -> None:
    slots = ["RB", "WR", "FLEX"]
    players = [("r1", "RB", 20.0), ("r2", "RB", 15.0), ("w1", "WR", 10.0), ("w2", "WR", 14.0), ("t1", "TE", 5.0)]
    total, assignment = optimal_lineup(slots, players)
    assert total == 49.0
    assert set(assignment.values()) == {"r1", "r2", "w2"}


def test_optimal_lineup_skips_unfillable_slot() -> None:
    total, assignment = optimal_lineup(["QB", "RB"], [("r1", "RB", 9.0)])
    assert total == 9.0 and assignment == {"1": "r1"}


def test_two_for_one_only_counts_if_it_starts() -> None:
    slots = ["RB", "RB"]
    weekly = pl.DataFrame({"sleeper_id": ["a", "b", "c"] * 2, "position": ["RB"] * 6,
                           "week": [1, 1, 1, 2, 2, 2], "points": [20.0, 15.0, 5.0, 20.0, 15.0, 5.0]})
    assert team_strength(slots, weekly, {"a", "b"}) == 70.0
    assert team_strength(slots, weekly, {"a", "b", "c"}) == 70.0
    assert team_strength(slots, weekly, {"a", "c"}) == 50.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/bin/python -m pytest tests/test_lineup.py -q`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement**

```python
# app/model/lineup.py
"""Exact optimal starting lineup for a week and team strength over remaining weeks."""
from __future__ import annotations

from functools import lru_cache

import polars as pl

from app.model.replacement import FLEX_ELIGIBILITY


def eligible(slot: str, position: str) -> bool:
    if slot in FLEX_ELIGIBILITY:
        return position in FLEX_ELIGIBILITY[slot]
    return slot == position


def optimal_lineup(slots: list[str], players: list[tuple[str, str, float]]) -> tuple[float, dict[str, str]]:
    n = len(players)

    @lru_cache(maxsize=None)
    def best(i: int, used: int) -> tuple[float, tuple[tuple[int, int], ...]]:
        if i == len(slots):
            return 0.0, ()
        skip = best(i + 1, used)
        result = skip
        for j in range(n):
            if used & (1 << j) or not eligible(slots[i], players[j][1]):
                continue
            total, picks = best(i + 1, used | (1 << j))
            candidate = (total + players[j][2], ((i, j),) + picks)
            if candidate[0] > result[0]:
                result = candidate
        return result

    total, picks = best(0, 0)
    return total, {str(i): players[j][0] for i, j in picks}


def weekly_strength(slots: list[str], weekly: pl.DataFrame, player_ids: set[str]) -> pl.DataFrame:
    mine = weekly.filter(pl.col("sleeper_id").is_in(list(player_ids)))
    rows: list[tuple[int, float]] = []
    for (week,), frame in mine.group_by("week"):
        players = list(zip(frame["sleeper_id"].to_list(), frame["position"].to_list(), frame["points"].to_list()))
        rows.append((int(week), optimal_lineup(slots, players)[0]))
    return pl.DataFrame(rows, schema={"week": pl.Int64, "points": pl.Float64}, orient="row").sort("week")


def team_strength(slots: list[str], weekly: pl.DataFrame, player_ids: set[str]) -> float:
    return float(weekly_strength(slots, weekly, player_ids)["points"].sum())
```

- [ ] **Step 4: Run tests, then commit**

Run: `./.venv/bin/python -m pytest tests/test_lineup.py -q`
Expected: 4 passed

```bash
git add app/model/lineup.py tests/test_lineup.py
git commit -m "Add exact optimal lineup solver and team strength"
```

---

### Task 5: Trade delta and trade finder

**Files:**
- Create: `app/model/trade.py`, `app/model/finder.py`
- Test: `tests/test_trade.py`

**Interfaces:**
- Consumes: `LeagueContext`, `Roster` (Task 1); `Projection` (Task 2); `team_strength` (Task 4).
- Produces:
  - `startable(roster: Roster) -> set[str]` = players minus reserve.
  - `@dataclass(frozen=True) TradeResult(delta_a: float, delta_b: float, before_a: float, after_a: float, before_b: float, after_b: float)`.
  - `trade_delta(ctx, proj: Projection, roster_a: Roster, roster_b: Roster, give_a: set[str], give_b: set[str]) -> TradeResult` using `weekly` restricted to weeks `> ctx.week` if the current week has started? No: use all remaining weeks as returned by the projection.
  - `find_trades(ctx, proj, values: pl.DataFrame, my_roster_id: int, opp_roster_id: int, top_n: int = 12, max_side: int = 2, limit: int = 10) -> list[tuple[TradeResult, tuple[str, ...], tuple[str, ...]]]` enumerating 1-for-1, 2-for-1, 1-for-2, 2-for-2 from each side's top `top_n` players by `intrinsic` (fallback `ros_primary`), keeping deals where both deltas exceed `MIN_GAIN = 0.5`, sorted by the smaller delta descending.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_trade.py
import polars as pl

from app.model.finder import find_trades
from app.model.league import LeagueContext, Roster
from app.model.live import Projection
from app.model.trade import trade_delta


def ctx_with(rosters: tuple[Roster, ...]) -> LeagueContext:
    return LeagueContext(league_id="1", name="t", season=2026, week=3, n_teams=2, scoring={"rec": 1.0},
                         roster_positions=["RB", "RB", "BN"], slots=["RB", "RB"], playoff_week_start=15,
                         last_week=4, trade_deadline=12, rosters=rosters)


def weekly(points: dict[str, float]) -> pl.DataFrame:
    rows = [(pid, "RB", w, p) for pid, p in points.items() for w in (3, 4)]
    return pl.DataFrame(rows, schema={"sleeper_id": pl.Utf8, "position": pl.Utf8, "week": pl.Int64, "points": pl.Float64}, orient="row")


def test_two_for_one_grades_by_lineup_not_sum() -> None:
    a = Roster(1, "u1", "A", "A", None, ("a1", "a2", "a3"), (), ())
    b = Roster(2, "u2", "B", "B", None, ("b1", "b2"), (), ())
    ctx = ctx_with((a, b))
    proj = Projection("consensus", weekly({"a1": 20.0, "a2": 10.0, "a3": 9.0, "b1": 25.0, "b2": 5.0}), pl.DataFrame())
    result = trade_delta(ctx, proj, a, b, give_a={"a2", "a3"}, give_b={"b1"})
    assert result.delta_a == (25 + 20 - (20 + 10)) * 2
    assert result.delta_b == (10 + 9 - (25 + 5)) * 2


def test_find_trades_returns_mutual_gains_only() -> None:
    a = Roster(1, "u1", "A", "A", None, ("a1", "a2", "a3"), (), ())
    b = Roster(2, "u2", "B", "B", None, ("b1", "b2", "b3"), (), ())
    ctx = ctx_with((a, b))
    proj = Projection("consensus", weekly({"a1": 20.0, "a2": 18.0, "a3": 2.0, "b1": 30.0, "b2": 1.0, "b3": 1.0}), pl.DataFrame())
    values = pl.DataFrame({"sleeper_id": ["a1", "a2", "a3", "b1", "b2", "b3"], "roster_id": [1, 1, 1, 2, 2, 2],
                           "intrinsic": [20.0, 18.0, 2.0, 30.0, 1.0, 1.0], "ros_primary": [40.0, 36, 4, 60, 2, 2]})
    deals = find_trades(ctx, proj, values, my_roster_id=1, opp_roster_id=2)
    assert deals, "expected at least one mutual-gain deal"
    for result, give, get in deals:
        assert result.delta_a > 0 and result.delta_b > 0
    best = deals[0]
    assert set(best[1]) == {"a1", "a2"} and best[2] == ("b1",)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/bin/python -m pytest tests/test_trade.py -q`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement**

```python
# app/model/trade.py
"""Grade a trade by the change in each side's optimal-lineup points over remaining weeks."""
from __future__ import annotations

from dataclasses import dataclass

from app.model.league import LeagueContext, Roster
from app.model.lineup import team_strength
from app.model.live import Projection


@dataclass(frozen=True)
class TradeResult:
    delta_a: float
    delta_b: float
    before_a: float
    after_a: float
    before_b: float
    after_b: float


def startable(roster: Roster) -> set[str]:
    return set(roster.players) - set(roster.reserve)


def trade_delta(ctx: LeagueContext, proj: Projection, roster_a: Roster, roster_b: Roster,
                give_a: set[str], give_b: set[str]) -> TradeResult:
    a_before, b_before = startable(roster_a), startable(roster_b)
    a_after = (a_before - give_a) | give_b
    b_after = (b_before - give_b) | give_a
    before_a = team_strength(ctx.slots, proj.weekly, a_before)
    after_a = team_strength(ctx.slots, proj.weekly, a_after)
    before_b = team_strength(ctx.slots, proj.weekly, b_before)
    after_b = team_strength(ctx.slots, proj.weekly, b_after)
    return TradeResult(after_a - before_a, after_b - before_b, before_a, after_a, before_b, after_b)
```

```python
# app/model/finder.py
"""Enumerate small trades with an opponent that improve both starting lineups."""
from __future__ import annotations

from itertools import combinations

import polars as pl

from app.model.league import LeagueContext, Roster
from app.model.live import Projection
from app.model.trade import TradeResult, trade_delta

MIN_GAIN = 0.5


def _roster(ctx: LeagueContext, roster_id: int) -> Roster:
    for roster in ctx.rosters:
        if roster.roster_id == roster_id:
            return roster
    raise ValueError(f"roster {roster_id} not in league")


def _top_players(values: pl.DataFrame, roster_id: int, top_n: int) -> list[str]:
    score = pl.coalesce(pl.col("intrinsic"), pl.col("ros_primary"), pl.lit(0.0))
    mine = values.filter(pl.col("roster_id") == roster_id).with_columns(score.alias("score")).sort("score", descending=True)
    return mine["sleeper_id"].head(top_n).to_list()


def _packages(players: list[str], max_side: int) -> list[tuple[str, ...]]:
    return [combo for size in range(1, max_side + 1) for combo in combinations(players, size)]


def find_trades(ctx: LeagueContext, proj: Projection, values: pl.DataFrame, my_roster_id: int, opp_roster_id: int,
                top_n: int = 12, max_side: int = 2, limit: int = 10) -> list[tuple[TradeResult, tuple[str, ...], tuple[str, ...]]]:
    me, opp = _roster(ctx, my_roster_id), _roster(ctx, opp_roster_id)
    mine = _packages(_top_players(values, my_roster_id, top_n), max_side)
    theirs = _packages(_top_players(values, opp_roster_id, top_n), max_side)
    deals: list[tuple[TradeResult, tuple[str, ...], tuple[str, ...]]] = []
    for give in mine:
        for get in theirs:
            result = trade_delta(ctx, proj, me, opp, set(give), set(get))
            if result.delta_a > MIN_GAIN and result.delta_b > MIN_GAIN:
                deals.append((result, give, get))
    deals.sort(key=lambda d: min(d[0].delta_a, d[0].delta_b), reverse=True)
    return deals[:limit]
```

- [ ] **Step 4: Run tests, then commit**

Run: `./.venv/bin/python -m pytest tests/test_trade.py -q`
Expected: 2 passed

```bash
git add app/model/trade.py app/model/finder.py tests/test_trade.py
git commit -m "Add trade grading and trade finder"
```

---

### Task 6: Manager profiles

**Files:**
- Create: `app/model/managers.py`
- Test: `tests/test_managers.py`

**Interfaces:**
- Consumes: `SleeperClient` (for prior seasons through `previous_league_id`), `Store`, `LeagueContext`, `consensus_snapshot`, `positional_ranks`, `curve_ppg`, `rank_to_ppg_curve`, `id_hub`.
- Produces:
  - `league_chain(client, league_id) -> list[dict]` following `previous_league_id` until None (newest first), using cached bodies (client handles caching).
  - `collect_transactions(client, chain, weeks: range) -> pl.DataFrame` with `season: Int64, week: Int64, type: Utf8, transaction_id, roster_ids: List[Int64], adds: Struct or dict serialized as two list columns: add_player: List[Utf8], add_roster: List[Int64], drop_player: List[Utf8], drop_roster: List[Int64], waiver_bid: Int64 (null unless waiver)`. Keep it flat: one row per transaction with list columns.
  - `@dataclass(frozen=True) ManagerProfile(owner_id: str, display_name: str, team_name: str, seasons: int, trades: int, trades_per_season: float, partners: dict[str, int], acquired_by_position: dict[str, int], given_by_position: dict[str, int], overpay_total: float, faab_spent: int, adds_by_position: dict[str, int])`.
  - `profiles(ctx, client, con, store) -> list[ManagerProfile]`, ordered by trades descending. Overpay per trade = rank-value given minus rank-value received at that week, where rank-value = `curve_ppg(prev curve for that season, pos, positional rank)` from the consensus snapshot of that season and week; players without a consensus rank contribute 0.
  - Positions come from `sleeper_players`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_managers.py
import polars as pl

from app.model.managers import _overpay, _trade_flows, collect_transactions


class FakeClient:
    def __init__(self) -> None:
        self.tx = {("1", 1): [
            {"type": "trade", "transaction_id": "t1", "roster_ids": [1, 2], "adds": {"p1": 2, "p2": 1}, "drops": {"p1": 1, "p2": 2}, "settings": None},
            {"type": "waiver", "transaction_id": "w1", "roster_ids": [1], "adds": {"p9": 1}, "drops": None, "settings": {"waiver_bid": 7}},
        ]}

    def transactions(self, league_id: str, week: int) -> list[dict]:
        return self.tx.get((league_id, week), [])


def test_collect_transactions_flattens() -> None:
    chain = [{"league_id": "1", "season": "2026"}]
    df = collect_transactions(FakeClient(), chain, range(1, 3))
    assert df.height == 2
    trade = df.filter(pl.col("type") == "trade").row(0, named=True)
    assert trade["season"] == 2026 and sorted(trade["add_player"]) == ["p1", "p2"]
    waiver = df.filter(pl.col("type") == "waiver").row(0, named=True)
    assert waiver["waiver_bid"] == 7 and waiver["add_player"] == ["p9"]


def test_trade_flows_and_overpay() -> None:
    positions = {"p1": "RB", "p2": "WR"}
    flows = _trade_flows({"p1": 2, "p2": 1}, positions)
    assert flows == {2: {"acquired": ["p1"], "given": ["p2"]}, 1: {"acquired": ["p2"], "given": ["p1"]}}
    values = {"p1": 12.0, "p2": 8.0}
    assert _overpay(flows[2], values) == 8.0 - 12.0
    assert _overpay(flows[1], values) == 12.0 - 8.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/bin/python -m pytest tests/test_managers.py -q`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement**

```python
# app/model/managers.py
"""Manager tendencies from the league chain's trades and waiver moves."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any, Protocol

import duckdb
import polars as pl

from app.data.identity import id_hub
from app.data.scoring import score_weeks
from app.data.store import Store
from app.market import consensus_snapshot
from app.model.agecurve import season_ppg
from app.model.league import LeagueContext
from app.model.projections import curve_ppg, positional_ranks, rank_to_ppg_curve

SEASON_WEEKS = range(1, 19)


class TransactionClient(Protocol):
    def transactions(self, league_id: str, week: int) -> list[dict[str, Any]]: ...


def league_chain(client: Any, league_id: str) -> list[dict[str, Any]]:
    chain: list[dict[str, Any]] = []
    current: str | None = league_id
    while current:
        league = client.league(current)
        chain.append(league)
        current = league.get("previous_league_id")
    return chain


def _flatten(season: int, week: int, tx: dict[str, Any]) -> dict[str, Any]:
    adds = tx.get("adds") or {}
    drops = tx.get("drops") or {}
    settings = tx.get("settings") or {}
    return {
        "season": season, "week": week, "type": str(tx.get("type")), "transaction_id": str(tx.get("transaction_id")),
        "roster_ids": [int(r) for r in (tx.get("roster_ids") or [])],
        "add_player": list(adds.keys()), "add_roster": [int(v) for v in adds.values()],
        "drop_player": list(drops.keys()), "drop_roster": [int(v) for v in drops.values()],
        "waiver_bid": settings.get("waiver_bid"),
    }


def collect_transactions(client: TransactionClient, chain: list[dict[str, Any]], weeks: range) -> pl.DataFrame:
    rows = [
        _flatten(int(league["season"]), week, tx)
        for league in chain for week in weeks for tx in client.transactions(str(league["league_id"]), week)
    ]
    schema = {"season": pl.Int64, "week": pl.Int64, "type": pl.Utf8, "transaction_id": pl.Utf8,
              "roster_ids": pl.List(pl.Int64), "add_player": pl.List(pl.Utf8), "add_roster": pl.List(pl.Int64),
              "drop_player": pl.List(pl.Utf8), "drop_roster": pl.List(pl.Int64), "waiver_bid": pl.Int64}
    return pl.DataFrame(rows, schema=schema)


def _trade_flows(adds: dict[str, int], positions: dict[str, str]) -> dict[int, dict[str, list[str]]]:
    flows: dict[int, dict[str, list[str]]] = {}
    for player, to_roster in adds.items():
        flows.setdefault(to_roster, {"acquired": [], "given": []})["acquired"].append(player)
    for player, to_roster in adds.items():
        for roster in flows:
            if roster != to_roster and player not in flows[roster]["acquired"]:
                flows[roster]["given"].append(player)
    return flows


def _overpay(flow: dict[str, list[str]], values: dict[str, float]) -> float:
    given = sum(values.get(p, 0.0) for p in flow["given"])
    received = sum(values.get(p, 0.0) for p in flow["acquired"])
    return given - received


def _rank_values(con: duckdb.DuckDBPyConnection, season: int, week: int, scoring: dict[str, float],
                 hub: pl.DataFrame, cache: dict[int, pl.DataFrame]) -> dict[str, float]:
    if season not in cache:
        stats = con.execute("SELECT * FROM player_stats WHERE season = ? AND season_type = 'REG'", [season - 1]).pl()
        cache[season] = rank_to_ppg_curve(season_ppg(score_weeks(stats, scoring)), season - 1)
    try:
        snap = positional_ranks(consensus_snapshot(con, season, week))
    except ValueError:
        return {}
    with_ids = snap.join(hub.select("fantasypros_id", "sleeper_id"), on="fantasypros_id", how="inner").drop_nulls("sleeper_id")
    return {str(s): curve_ppg(cache[season], str(p), int(r)) for s, p, r in zip(with_ids["sleeper_id"], with_ids["pos"], with_ids["pos_rank"])}


@dataclass(frozen=True)
class ManagerProfile:
    owner_id: str
    display_name: str
    team_name: str
    seasons: int
    trades: int
    trades_per_season: float
    partners: dict[str, int]
    acquired_by_position: dict[str, int]
    given_by_position: dict[str, int]
    overpay_total: float
    faab_spent: int
    adds_by_position: dict[str, int]


def _owner_maps(chain: list[dict[str, Any]], client: Any) -> dict[tuple[int, int], str]:
    out: dict[tuple[int, int], str] = {}
    for league in chain:
        for roster in client.rosters(str(league["league_id"])):
            out[(int(league["season"]), int(roster["roster_id"]))] = str(roster.get("owner_id") or "")
    return out


def profiles(ctx: LeagueContext, client: Any, con: duckdb.DuckDBPyConnection, store: Store) -> list[ManagerProfile]:
    chain = league_chain(client, ctx.league_id)
    tx = collect_transactions(client, chain, SEASON_WEEKS)
    owners = _owner_maps(chain, client)
    positions = dict(con.execute("SELECT sleeper_id, position FROM sleeper_players").fetchall())
    hub = id_hub(con.execute("SELECT * FROM ff_playerids").pl())
    curve_cache: dict[int, pl.DataFrame] = {}
    stats: dict[str, dict[str, Any]] = {}

    def bucket(owner: str) -> dict[str, Any]:
        return stats.setdefault(owner, {"trades": 0, "partners": Counter(), "acq": Counter(), "given": Counter(),
                                        "overpay": 0.0, "faab": 0, "adds": Counter()})

    for row in tx.iter_rows(named=True):
        season = int(row["season"])
        if row["type"] == "trade":
            adds = dict(zip(row["add_player"], row["add_roster"]))
            flows = _trade_flows(adds, positions)
            values = _rank_values(con, season, int(row["week"]), ctx.scoring, hub, curve_cache)
            owners_in = {r: owners.get((season, r), "") for r in flows}
            for roster, flow in flows.items():
                b = bucket(owners_in[roster])
                b["trades"] += 1
                for other, owner in owners_in.items():
                    if other != roster:
                        b["partners"][owner] += 1
                b["acq"].update(positions.get(p, "?") for p in flow["acquired"])
                b["given"].update(positions.get(p, "?") for p in flow["given"])
                b["overpay"] += _overpay(flow, values)
        else:
            for player, roster in zip(row["add_player"], row["add_roster"]):
                b = bucket(owners.get((season, int(roster)), ""))
                b["adds"][positions.get(player, "?")] += 1
                b["faab"] += int(row["waiver_bid"] or 0)
    names = {r.owner_id: (r.display_name, r.team_name) for r in ctx.rosters}
    n_seasons = len(chain)
    out = [
        ManagerProfile(
            owner_id=owner, display_name=names.get(owner, (owner, owner))[0], team_name=names.get(owner, (owner, owner))[1],
            seasons=n_seasons, trades=b["trades"], trades_per_season=b["trades"] / max(n_seasons, 1),
            partners={names.get(k, (k, k))[0]: v for k, v in b["partners"].items()},
            acquired_by_position=dict(b["acq"]), given_by_position=dict(b["given"]), overpay_total=round(b["overpay"], 2),
            faab_spent=b["faab"], adds_by_position=dict(b["adds"]),
        )
        for owner, b in stats.items() if owner in names
    ]
    return sorted(out, key=lambda p: p.trades, reverse=True)
```

- [ ] **Step 4: Run tests, then commit**

Run: `./.venv/bin/python -m pytest tests/test_managers.py -q`
Expected: 2 passed

```bash
git add app/model/managers.py tests/test_managers.py
git commit -m "Add manager profiles from league transaction history"
```

---

### Task 7: App shell, theme, state, refresh

**Files:**
- Create: `app/main.py`, `app/ui/theme.py`, `app/ui/state.py`, `app/ui/layout.py`
- Modify: `app/__main__.py` (add `run` subcommand calling `app.main.run_app()`)

**Interfaces:**
- `app/ui/theme.py`: `ACCENT = "#F5A524"`, `BG = "#000000"`, `CARD = "#0A0A0A"`, `BORDER = "#262626"`, `TEXT = "#F2F2F2"`, `MUTED = "#8A8F98"`, `FONT` monospace stack, `POSITIVE = "#3FB950"`, `NEGATIVE = "#F85149"`; `TEAM_COLORS: dict[str, str]` for the 32 NFL abbreviations (primary color hex each); `headshot(sleeper_id: str, position: str, nfl_team: str | None) -> str` (players: `https://sleepercdn.com/content/nfl/players/thumb/{id}.jpg`; DEF: `https://sleepercdn.com/images/team_logos/nfl/{team.lower()}.png`); `avatar(avatar_id: str | None) -> str | None` (`https://sleepercdn.com/avatars/thumbs/{id}`); `echart_base() -> dict` (dark background transparent, text color, accent as first color, grid margins); `apply_theme() -> None` calling `ui.colors(primary=ACCENT, ...)` and `ui.dark_mode().enable()` plus a small CSS block for card backgrounds and the app font.
- `app/ui/state.py`: `class AppState` holding `cfg, store, con, ctx, projections: dict[str, Projection], values: pl.DataFrame, source: str, last_refresh: datetime | None, error: str | None`; `load(self) -> None` builds ctx, both projections, values from the caches; `refresh(self) -> RefreshSummary` runs `app.refresh.refresh` with a real SleeperClient(refresh=True) then `load()`; `primary(self) -> Projection`; `secondary(self) -> Projection`; module-level `STATE = AppState()` lazily initialized by `get_state()`.
- `app/ui/layout.py`: `frame(title: str)` context manager yielding a content container; renders the header with league name, `Week N`, projection-source toggle (`ui.toggle(["consensus", "model"])` bound to state), Refresh button (runs `state.refresh` via `run.io_bound`, shows `ui.notify` with the summary or error, then `ui.navigate.reload()`), and a nav row of links to the six screens. Screens register with `@ui.page('/heatmap')` etc. in their own modules and are imported by `app/main.py`.
- `app/main.py`: `run_app() -> None`: `apply_theme()`, import screen modules, redirect `/` to `/heatmap`, then `ui.run(native=True, dark=True, title="ff-analyst", window_size=(1440, 900), reload=False, show=False)`. On startup (`app.on_startup`) call `get_state().refresh()` in a background task if the last Sleeper league fetch is older than one hour, else `load()`.

- [ ] **Step 1: Implement (no unit tests; verified in Task 14)**

Write the four files. Keep every function under about 30 lines. Example skeletons:

```python
# app/ui/theme.py
from __future__ import annotations

from nicegui import ui

ACCENT = "#F5A524"
BG = "#000000"
CARD = "#0A0A0A"
BORDER = "#262626"
TEXT = "#F2F2F2"
MUTED = "#8A8F98"
FONT = '"JetBrains Mono", "SF Mono", Menlo, Consolas, monospace'
POSITIVE = "#3FB950"
NEGATIVE = "#F85149"
TEAM_COLORS: dict[str, str] = {
    "ARI": "#97233F", "ATL": "#A71930", "BAL": "#241773", "BUF": "#00338D", "CAR": "#0085CA", "CHI": "#0B162A",
    "CIN": "#FB4F14", "CLE": "#311D00", "DAL": "#041E42", "DEN": "#FB4F14", "DET": "#0076B6", "GB": "#203731",
    "HOU": "#03202F", "IND": "#002C5F", "JAX": "#006778", "KC": "#E31837", "LV": "#A5ACAF", "LAC": "#0080C6",
    "LA": "#003594", "LAR": "#003594", "MIA": "#008E97", "MIN": "#4F2683", "NE": "#002244", "NO": "#D3BC8D",
    "NYG": "#0B2265", "NYJ": "#125740", "PHI": "#004C54", "PIT": "#FFB612", "SF": "#AA0000", "SEA": "#002244",
    "TB": "#D50A0A", "TEN": "#0C2340", "WAS": "#5A1414",
}


def team_color(team: str | None) -> str:
    return TEAM_COLORS.get(team or "", MUTED)


def headshot(sleeper_id: str, position: str, nfl_team: str | None) -> str:
    if position == "DEF":
        return f"https://sleepercdn.com/images/team_logos/nfl/{(nfl_team or sleeper_id).lower()}.png"
    return f"https://sleepercdn.com/content/nfl/players/thumb/{sleeper_id}.jpg"


def avatar(avatar_id: str | None) -> str | None:
    return f"https://sleepercdn.com/avatars/thumbs/{avatar_id}" if avatar_id else None


def echart_base() -> dict:
    return {"backgroundColor": "transparent", "color": [ACCENT, "#4C9AFF", "#3FB950", "#F85149", "#B68CFF"],
            "textStyle": {"color": TEXT}, "grid": {"left": 48, "right": 24, "top": 32, "bottom": 40}}


def apply_theme() -> None:
    ui.colors(primary=ACCENT, secondary=MUTED, accent=ACCENT, dark=BG, positive=POSITIVE, negative=NEGATIVE)
    ui.dark_mode().enable()
    ui.add_head_html(
        f"<style>body{{background:{BG};color:{TEXT};font-family:{FONT}}} "
        f".ff-card{{background:{CARD};border:1px solid {BORDER};border-radius:4px}} "
        f".ff-label{{font-size:11px;letter-spacing:.08em;text-transform:uppercase;color:{MUTED}}}</style>"
    )
```

State and layout follow the interface text above; the implementer writes them in the same style. `refresh` must catch `Exception` at the UI boundary only, store the message in `state.error`, and surface it in a `ui.notify(type="negative")`; nothing is swallowed silently.

- [ ] **Step 2: Add the run command and launch once**

In `app/__main__.py` add `sub.add_parser("run")` and dispatch to `from app.main import run_app; run_app()`. Then run `./.venv/bin/python -m app run` for ten seconds in the background (`timeout 10 ... || true`) and confirm no traceback on startup. Commit:

```bash
git add app/main.py app/ui/theme.py app/ui/state.py app/ui/layout.py app/__main__.py
git commit -m "Add app shell with theme, state, and refresh"
```

---

### Task 8: Heatmap screen

**Files:**
- Create: `app/model/heatmap.py`, `app/ui/heatmap.py`
- Test: `tests/test_heatmap.py`

**Interfaces:**
- `app/model/heatmap.py`: `position_strength(ctx, proj: Projection) -> pl.DataFrame` with `roster_id, team_name, position, points` = for each team and position, the sum over remaining weeks of that team's optimal-lineup points contributed by players at that position (run `optimal_lineup` per week, attribute each assigned player's points to his position; flex-assigned players count toward their own position). `surplus(strength: pl.DataFrame) -> pl.DataFrame` adds `median` (league median at that position) and `surplus = points - median`.
- `app/ui/heatmap.py`: page `/heatmap` rendering an ECharts heatmap (x = positions in slot order, y = team names, value = surplus, diverging color scale centered at 0 with NEGATIVE to POSITIVE), a tooltip showing points and median, and a source badge.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_heatmap.py
import polars as pl

from app.model.heatmap import position_strength, surplus
from app.model.league import LeagueContext, Roster
from app.model.live import Projection


def test_position_strength_attributes_flex_to_own_position() -> None:
    a = Roster(1, "u1", "A", "A", None, ("r1", "r2", "w1"), (), ())
    b = Roster(2, "u2", "B", "B", None, ("r3", "w2", "w3"), (), ())
    ctx = LeagueContext("1", "t", 2026, 3, 2, {}, ["RB", "WR", "FLEX", "BN"], ["RB", "WR", "FLEX"], 15, 3, 12, (a, b))
    weekly = pl.DataFrame({"sleeper_id": ["r1", "r2", "w1", "r3", "w2", "w3"], "position": ["RB", "RB", "WR", "RB", "WR", "WR"],
                           "week": [3] * 6, "points": [20.0, 15.0, 10.0, 12.0, 18.0, 16.0]})
    strength = position_strength(ctx, Projection("consensus", weekly, pl.DataFrame())).sort("roster_id", "position")
    assert strength.filter(pl.col("roster_id") == 1)["points"].to_list() == [35.0, 10.0]
    assert strength.filter(pl.col("roster_id") == 2)["points"].to_list() == [12.0, 34.0]
    out = surplus(strength)
    assert out.filter((pl.col("roster_id") == 1) & (pl.col("position") == "RB"))["surplus"][0] == 35.0 - 23.5
```

- [ ] **Step 2: Implement model and page, run test, commit**

```python
# app/model/heatmap.py
from __future__ import annotations

import polars as pl

from app.model.league import LeagueContext
from app.model.lineup import optimal_lineup
from app.model.live import Projection
from app.model.trade import startable


def position_strength(ctx: LeagueContext, proj: Projection) -> pl.DataFrame:
    rows: list[tuple[int, str, str, float]] = []
    for roster in ctx.rosters:
        mine = proj.weekly.filter(pl.col("sleeper_id").is_in(list(startable(roster))))
        totals: dict[str, float] = {}
        for (_week,), frame in mine.group_by("week"):
            players = list(zip(frame["sleeper_id"].to_list(), frame["position"].to_list(), frame["points"].to_list()))
            by_id = {pid: (pos, pts) for pid, pos, pts in players}
            for pid in optimal_lineup(ctx.slots, players)[1].values():
                pos, pts = by_id[pid]
                totals[pos] = totals.get(pos, 0.0) + pts
        for pos in sorted({s for s in ctx.slots if s not in ("FLEX", "SUPER_FLEX", "REC_FLEX", "WRRB_FLEX")} | set(totals)):
            rows.append((roster.roster_id, roster.team_name, pos, totals.get(pos, 0.0)))
    return pl.DataFrame(rows, schema={"roster_id": pl.Int64, "team_name": pl.Utf8, "position": pl.Utf8, "points": pl.Float64}, orient="row")


def surplus(strength: pl.DataFrame) -> pl.DataFrame:
    median = strength.group_by("position").agg(pl.col("points").median().alias("median"))
    return strength.join(median, on="position").with_columns((pl.col("points") - pl.col("median")).alias("surplus"))
```

Page: `ui.echart` with `series: [{"type": "heatmap", "data": [[x_index, y_index, round(surplus, 1)], ...], "label": {"show": True}}]`, `visualMap` `{"min": -m, "max": m, "inRange": {"color": [NEGATIVE, CARD, POSITIVE]}}`, `xAxis.data` positions, `yAxis.data` team names.

Run: `./.venv/bin/python -m pytest tests/test_heatmap.py -q` → 1 passed.

```bash
git add app/model/heatmap.py app/ui/heatmap.py tests/test_heatmap.py
git commit -m "Add league heatmap of positional surplus"
```

---

### Task 9: Player cards screen

**Files:**
- Create: `app/ui/players.py`, `app/ui/cards.py`

**Interfaces:**
- `app/ui/cards.py`: `player_card(row: dict, max_market: float, max_ros: float) -> None` renders one `ui.card` with class `ff-card` and a left border in the team color: headshot (`ui.image`, 64px, rounded), name, position badge (`ui.badge` with accent), NFL team and injury status, two horizontal bars (market value as share of `max_market`, projected ROS as share of `max_ros`) with labels, intrinsic value with sign color, and a sparkline `ui.echart` (line, no axes, 40px tall) of `trend`.
- `app/ui/players.py`: page `/players` with filters (position `ui.select`, owner `ui.select` including "Free agents", search `ui.input`), sorted by intrinsic descending, cards in a responsive grid (`ui.grid(columns=3)`), paginated 30 at a time with a "more" button.

- [ ] **Step 1: Implement, launch, commit**

No unit tests. Launch the app, open `/players`, confirm cards render with images and sparklines. Commit:

```bash
git add app/ui/players.py app/ui/cards.py
git commit -m "Add player cards screen"
```

---

### Task 10: Buy-low / sell-high scatter

**Files:**
- Create: `app/model/scatter.py`, `app/ui/scatter.py`
- Test: `tests/test_scatter.py`

**Interfaces:**
- `app/model/scatter.py`: `scatter_points(values: pl.DataFrame) -> pl.DataFrame` with `sleeper_id, name, position, roster_id, market_pct, intrinsic_pct, gap` where `market_pct` and `intrinsic_pct` are percentile ranks (0 to 100) among players with a market value, and `gap = intrinsic_pct - market_pct`; `quadrant_labels(points) -> dict[str, list[str]]` with keys `buy_low` (gap >= 15) and `sell_high` (gap <= -15), each a list of names sorted by |gap| descending, top 10.
- Page `/scatter`: ECharts scatter with position color, diagonal reference line from (0,0) to (100,100) as a `markLine`, tooltip with name and both percentiles, and two side cards listing buy-low and sell-high names. Only rostered players by default with a toggle for free agents.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_scatter.py
import polars as pl

from app.model.scatter import quadrant_labels, scatter_points


def test_scatter_percentiles_and_quadrants() -> None:
    values = pl.DataFrame({"sleeper_id": ["a", "b", "c", "d"], "name": ["A", "B", "C", "D"], "position": ["RB"] * 4,
                           "roster_id": [1, 1, 2, None], "market_value": [100.0, 50.0, 10.0, None], "intrinsic": [5.0, 50.0, 40.0, 1.0]})
    pts = scatter_points(values).sort("sleeper_id")
    assert pts["sleeper_id"].to_list() == ["a", "b", "c"]
    assert pts.filter(pl.col("sleeper_id") == "a")["market_pct"][0] == 100.0
    labels = quadrant_labels(pts)
    assert "C" in labels["buy_low"] and "A" in labels["sell_high"]
```

- [ ] **Step 2: Implement, run test, commit**

```python
# app/model/scatter.py
from __future__ import annotations

import polars as pl

GAP_THRESHOLD = 15.0


def _pct(col: str) -> pl.Expr:
    return (pl.col(col).rank("average") - 1) / (pl.len() - 1).clip(lower_bound=1) * 100.0


def scatter_points(values: pl.DataFrame) -> pl.DataFrame:
    with_market = values.filter(pl.col("market_value").is_not_null() & pl.col("intrinsic").is_not_null())
    return with_market.with_columns(_pct("market_value").alias("market_pct"), _pct("intrinsic").alias("intrinsic_pct")).with_columns(
        (pl.col("intrinsic_pct") - pl.col("market_pct")).alias("gap")
    ).select("sleeper_id", "name", "position", "roster_id", "market_pct", "intrinsic_pct", "gap")


def quadrant_labels(points: pl.DataFrame) -> dict[str, list[str]]:
    ordered = points.with_columns(pl.col("gap").abs().alias("mag")).sort("mag", descending=True)
    return {
        "buy_low": ordered.filter(pl.col("gap") >= GAP_THRESHOLD)["name"].head(10).to_list(),
        "sell_high": ordered.filter(pl.col("gap") <= -GAP_THRESHOLD)["name"].head(10).to_list(),
    }
```

Run: `./.venv/bin/python -m pytest tests/test_scatter.py -q` → 1 passed.

```bash
git add app/model/scatter.py app/ui/scatter.py tests/test_scatter.py
git commit -m "Add buy-low sell-high scatter"
```

---

### Task 11: Trade screen

**Files:**
- Create: `app/ui/trade.py`

**Interfaces:**
- Page `/trade`: left column = my roster (`ui.select` for roster, default the roster whose owner display name matches the Sleeper username in `config.local.toml` key `username` if present, else roster 1), right column = opponent roster. Each side lists startable players as selectable chips (`ui.chip` with `selectable=True`); selected chips are the players given. Center: an ECharts gauge pair (or two large numbers with sign colors) showing `delta_a` and `delta_b` from `trade_delta`, recomputed on every chip toggle, plus before/after totals and a "fair for both" badge when both deltas exceed `MIN_GAIN`. A note shows the projection source.

- [ ] **Step 1: Implement, launch, commit**

```bash
git add app/ui/trade.py
git commit -m "Add trade screen with live lineup delta"
```

---

### Task 12: Trade finder screen

**Files:**
- Create: `app/ui/finder.py`

**Interfaces:**
- Page `/finder`: my roster select, opponent select (or "all opponents"), a "find" button that runs `find_trades` via `run.cpu_bound` or `run.io_bound` with a spinner, then renders each deal as a card: give names, get names, both deltas with sign colors, and a "open in trade screen" link (`/trade?me=1&opp=2&give=a,b&get=c`). The trade page reads those query params to preselect chips.

- [ ] **Step 1: Implement, launch, commit**

```bash
git add app/ui/finder.py app/ui/trade.py
git commit -m "Add trade finder screen"
```

---

### Task 13: Manager profiles screen

**Files:**
- Create: `app/ui/managers.py`

**Interfaces:**
- Page `/managers`: one card per `ManagerProfile` (avatar, team name, display name, trades per season, FAAB spent, overpay total with sign color, two small ECharts bar charts: acquired vs given by position, and adds by position; partner list as chips). Profiles are computed on first visit via `run.io_bound(profiles, ...)` with the real SleeperClient (prior seasons may need network) and cached on the state for the session.

- [ ] **Step 1: Implement, launch, commit**

```bash
git add app/ui/managers.py
git commit -m "Add manager profiles screen"
```

---

### Task 14: Launch, drive, verify, fix

**Files:**
- Modify: whatever the drive reveals.

- [ ] **Step 1: Full suite**

Run: `./.venv/bin/python -m pytest -q` → all passed.

- [ ] **Step 2: Launch in browser mode for driving**

Run: `FF_NATIVE=0 ./.venv/bin/python -m app run` in the background (the shell must honor `FF_NATIVE=0` by passing `native=False, show=False, port=8765`; add that switch to `run_app`). Open each of the six routes with a browser automation tool, take a screenshot of each, and check: no traceback in the server log, every chart renders, refresh button works and notifies, source toggle changes numbers, trade chips update the delta live, finder returns deals for at least one opponent, manager cards render.

- [ ] **Step 3: Fix what is broken, commit each fix with a plain message, then native launch**

Run: `./.venv/bin/python -m app run` and confirm the native window opens (pywebview). Commit any final fix.

---

## Dependency graph for parallel execution

- Wave 1 (independent): Task 1, Task 4.
- Wave 2: Task 2 (needs 1 and Plan A Task 14 availability module), Task 5 (needs 1, 4), Task 6 (needs 1).
- Wave 3: Task 3 (needs 2), Task 7 (needs 1, 2, 3 interfaces; can start after 2 with a stub of values), Task 8 (needs 2, 4).
- Wave 4: Task 9, 10 (need 3, 7), Task 11, 12 (need 5, 7), Task 13 (need 6, 7).
- Wave 5: Task 14.
