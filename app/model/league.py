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
