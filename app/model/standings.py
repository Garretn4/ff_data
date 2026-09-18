"""Standings, schedule, team projections, power scores, and matchup lineups from cached Sleeper data."""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import datetime
from statistics import mean, pstdev
from typing import Any
from zoneinfo import ZoneInfo

import duckdb
import polars as pl

from app.data.store import Store
from app.model.league import LeagueContext, Roster
from app.model.lineup import optimal_lineup, weekly_strength
from app.model.live import Projection

Matchups = dict[int, list[dict[str, Any]]]

SIGMA = 20.0             # assumed std dev of one team's weekly score
PROJECTION_WEIGHT = 0.6  # share of the power score from projected strength
TEAM_WEEKS_SCHEMA = {"roster_id": pl.Int64, "week": pl.Int64, "points": pl.Float64}
Slot = tuple[str, str, float]

EASTERN = ZoneInfo("America/New_York")
DEFAULT_KICKOFF = "13:00"
MIN_SIGMA = 0.5   # all starters done: the result is decided, keep erf finite


@dataclass(frozen=True)
class Record:
    roster_id: int
    wins: int
    losses: int
    ties: int
    points_for: float
    points_against: float

    @classmethod
    def empty(cls, roster_id: int) -> Record:
        return cls(roster_id, 0, 0, 0, 0.0, 0.0)

    @property
    def games(self) -> int:
        return self.wins + self.losses + self.ties

    @property
    def text(self) -> str:
        base = f"{self.wins}-{self.losses}"
        return f"{base}-{self.ties}" if self.ties else base


@dataclass(frozen=True)
class Pairing:
    week: int
    matchup_id: int
    home: int
    away: int | None


def load_matchups(store: Store, league_id: str, weeks: list[int]) -> Matchups:
    """Cached Sleeper matchup rows per week; weeks with no cache are omitted."""
    out: Matchups = {}
    for week in weeks:
        body = store.get_cached(f"sleeper:/league/{league_id}/matchups/{week}", None, 0.0)
        if body is not None:
            out[week] = json.loads(body)
    return out


def _by_matchup(rows: list[dict[str, Any]]) -> dict[int, list[dict[str, Any]]]:
    grouped: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        matchup_id = row.get("matchup_id")
        if matchup_id is not None:
            grouped.setdefault(int(matchup_id), []).append(row)
    return grouped


def _points(row: dict[str, Any]) -> float:
    return float(row.get("points") or 0.0)


def records(matchups: Matchups, through_week: int) -> dict[int, Record]:
    """Win/loss/tie and points tallies for every week before through_week, keyed by roster id."""
    tally: dict[int, dict[str, float]] = {}
    for week, rows in matchups.items():
        if week >= through_week:
            continue
        for pair in _by_matchup(rows).values():
            if len(pair) < 2:
                continue
            for me, opp in ((pair[0], pair[1]), (pair[1], pair[0])):
                mine, theirs = _points(me), _points(opp)
                entry = tally.setdefault(int(me["roster_id"]), {"w": 0, "l": 0, "t": 0, "pf": 0.0, "pa": 0.0})
                entry["pf"] += mine
                entry["pa"] += theirs
                entry["w" if mine > theirs else "l" if mine < theirs else "t"] += 1
    return {
        rid: Record(rid, int(e["w"]), int(e["l"]), int(e["t"]), e["pf"], e["pa"])
        for rid, e in sorted(tally.items())
    }


def schedule(matchups: Matchups, week: int) -> list[Pairing]:
    """Head-to-head pairings for one week; a lone roster becomes a bye (away=None)."""
    pairings: list[Pairing] = []
    for matchup_id, pair in sorted(_by_matchup(matchups.get(week, [])).items()):
        home = int(pair[0]["roster_id"])
        away = int(pair[1]["roster_id"]) if len(pair) > 1 else None
        pairings.append(Pairing(week=week, matchup_id=matchup_id, home=home, away=away))
    return pairings


def actual_points(matchups: Matchups, week: int) -> dict[int, float]:
    return {int(row["roster_id"]): _points(row) for row in matchups.get(week, [])}


def _roster_row(matchups: Matchups, week: int, roster_id: int) -> dict[str, Any] | None:
    for row in matchups.get(week, []):
        if int(row["roster_id"]) == roster_id:
            return row
    return None


def actual_lineup(matchups: Matchups, week: int, roster_id: int, slots: list[str]) -> list[Slot]:
    """(slot, sleeper_id, points) from the starters Sleeper recorded for a played week; "" when the slot was empty."""
    row = _roster_row(matchups, week, roster_id)
    starters = list(row.get("starters") or []) if row else []
    points = list(row.get("starters_points") or []) if row else []
    out: list[Slot] = []
    for i, slot in enumerate(slots):
        pid = str(starters[i]) if i < len(starters) and starters[i] not in (None, "", "0") else ""
        pts = float(points[i]) if pid and i < len(points) and points[i] is not None else 0.0
        out.append((slot, pid, pts))
    return out


def player_points(matchups: Matchups, week: int, roster_id: int) -> dict[str, float]:
    """Live points so far per rostered player for one week, from Sleeper's players_points."""
    row = _roster_row(matchups, week, roster_id)
    return {str(pid): float(pts) for pid, pts in ((row or {}).get("players_points") or {}).items() if pts is not None}


def team_week_projections(ctx: LeagueContext, proj: Projection) -> pl.DataFrame:
    """Optimal-lineup projected points per roster per remaining week."""
    frames = [
        weekly_strength(ctx.slots, proj.weekly, set(roster.players))
        .with_columns(pl.lit(roster.roster_id, dtype=pl.Int64).alias("roster_id"))
        .select("roster_id", "week", "points")
        for roster in ctx.rosters
    ]
    if not frames:
        return pl.DataFrame(schema=TEAM_WEEKS_SCHEMA)
    return pl.concat(frames).cast(TEAM_WEEKS_SCHEMA)


def _by_roster(frame: pl.DataFrame, column: str) -> dict[int, float]:
    return {int(r): float(v) for r, v in zip(frame["roster_id"].to_list(), frame[column].to_list())}


def ros_averages(team_weeks: pl.DataFrame) -> dict[int, float]:
    if team_weeks.is_empty():
        return {}
    return _by_roster(team_weeks.group_by("roster_id").agg(pl.col("points").mean().alias("avg")), "avg")


def week_totals(team_weeks: pl.DataFrame, week: int) -> dict[int, float]:
    return _by_roster(team_weeks.filter(pl.col("week") == week), "points")


def _zscores(values: dict[int, float]) -> dict[int, float]:
    if not values:
        return {}
    mu, sd = mean(values.values()), pstdev(values.values())
    if sd == 0.0:
        return {rid: 0.0 for rid in values}
    return {rid: (v - mu) / sd for rid, v in values.items()}


def power_scores(records: dict[int, Record], ros_avg: dict[int, float]) -> dict[int, float]:
    """0.6 z(projected points per week) + 0.4 z(points for per game); projection only before any games."""
    z_proj = _zscores(ros_avg)
    per_game = {rid: rec.points_for / rec.games for rid, rec in records.items() if rec.games > 0}
    if not per_game:
        return z_proj
    z_pf = _zscores(per_game)
    return {
        rid: PROJECTION_WEIGHT * z_proj.get(rid, 0.0) + (1.0 - PROJECTION_WEIGHT) * z_pf.get(rid, 0.0)
        for rid in ros_avg
    }


def lineup_for(ctx: LeagueContext, proj: Projection, roster: Roster, week: int) -> list[Slot]:
    """(slot, sleeper_id, points) in slot order for the roster's optimal lineup that week; "" when unfilled."""
    rows = proj.weekly.filter((pl.col("week") == week) & pl.col("sleeper_id").is_in(list(roster.players)))
    players = list(zip(rows["sleeper_id"].to_list(), rows["position"].to_list(), rows["points"].to_list()))
    _, assignment = optimal_lineup(ctx.slots, players)
    points = {pid: float(pts) for pid, _, pts in players}
    return [(slot, assignment.get(str(i), ""), points.get(assignment.get(str(i), ""), 0.0))
            for i, slot in enumerate(ctx.slots)]


def win_probability(points_a: float, points_b: float, sigma: float = SIGMA) -> float:
    """P(a beats b) when each side's score has std dev sigma: Phi(diff / (sigma * sqrt 2))."""
    return 0.5 * (1.0 + math.erf((points_a - points_b) / (2.0 * sigma)))


def kickoffs(con: duckdb.DuckDBPyConnection, season: int, week: int) -> dict[str, datetime]:
    """NFL team -> kickoff (Eastern) for one regular-season week, from the nflverse schedule."""
    rows = con.execute(
        "SELECT home_team, away_team, gameday, gametime FROM schedules "
        "WHERE season = ? AND week = ? AND game_type = 'REG'", [season, week],
    ).fetchall()
    out: dict[str, datetime] = {}
    for home, away, day, time in rows:
        hour, minute = (time or DEFAULT_KICKOFF).split(":")
        when = datetime(day.year, day.month, day.day, int(hour), int(minute), tzinfo=EASTERN)
        out[str(home)] = when
        out[str(away)] = when
    return out


def blended_total(lineup: list[Slot], live: dict[str, float], teams: dict[str, str],
                  kicks: dict[str, datetime], now: datetime) -> tuple[float, int]:
    """(points, starters still to play): live points once a starter's game has kicked off, projection before."""
    total, remaining = 0.0, 0
    for _, pid, projected in lineup:
        if not pid:
            continue
        kickoff = kicks.get(teams.get(pid, ""))
        if kickoff is not None and kickoff <= now:
            total += live.get(pid, 0.0)
        else:
            total += projected
            remaining += 1
    return total, remaining


def live_sigma(remaining_a: int, remaining_b: int, n_slots: int) -> float:
    """Score spread shrinks with the square root of the share of starters still to play."""
    if n_slots <= 0:
        return SIGMA
    share = (remaining_a + remaining_b) / (2.0 * n_slots)
    return max(SIGMA * math.sqrt(share), MIN_SIGMA)


def league_rows(ctx: LeagueContext, team_weeks: dict[str, pl.DataFrame], matchups: Matchups, me: int) -> list[dict[str, object]]:
    """One row per roster for the League table, ranked by power score."""
    recs = records(matchups, ctx.week)
    averages = {source: ros_averages(frame) for source, frame in team_weeks.items()}
    power = power_scores(recs, averages["model"])
    rows: list[dict[str, object]] = []
    for roster in ctx.rosters:
        rid = roster.roster_id
        rec = recs.get(rid, Record.empty(rid))
        rows.append({
            "id": rid, "team": roster.team_name, "mine": rid == me, "record": rec.text,
            "pf": round(rec.points_for, 1), "pa": round(rec.points_against, 1),
            "model": round(averages["model"].get(rid, 0.0), 1),
            "consensus": round(averages["consensus"].get(rid, 0.0), 1),
            "power": round(power.get(rid, 0.0), 2),
        })
    rows.sort(key=lambda row: (-float(row["power"]), int(row["id"])))
    for rank, row in enumerate(rows, start=1):
        row["rank"] = rank
    return rows
