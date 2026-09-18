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


def test_roster_filters_none_entries(tmp_path: Path) -> None:
    store = Store(tmp_path / "app.sqlite")
    league_with_none = {"league_id": "2", "name": "Test", "season": "2026", "total_rosters": 1,
                        "scoring_settings": {"rec": 1.0}, "roster_positions": ["QB", "RB", "FLEX", "BN", "IR"],
                        "settings": {"playoff_week_start": 15, "trade_deadline": 12}}
    users = [{"user_id": "u1", "display_name": "Alice", "avatar": None, "metadata": {}}]
    rosters = [{"roster_id": 1, "owner_id": "u1", "players": ["p5", None], "starters": ["p5"], "reserve": None}]
    store.put_cached("sleeper:/league/2", json.dumps(league_with_none), now=1.0)
    store.put_cached("sleeper:/league/2/users", json.dumps(users), now=1.0)
    store.put_cached("sleeper:/league/2/rosters", json.dumps(rosters), now=1.0)
    store.put_cached("sleeper:/state/nfl", json.dumps({"week": 1, "season": "2026"}), now=1.0)
    ctx = load_league_context(store, "2")
    assert ctx.rosters[0].players == ("p5",)


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
