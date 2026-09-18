"""Standings, schedule, team projections, power scores, and matchup lineups from cached Sleeper data."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from app.data.store import Store

Matchups = dict[int, list[dict[str, Any]]]


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
