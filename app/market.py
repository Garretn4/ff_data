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


def snapshot_source(league_id: str) -> str:
    return f"fantasycalc:{league_id}"


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
    return store.put_snapshot(snapshot_source(str(league["league_id"])), taken_at, frame.to_dicts())
