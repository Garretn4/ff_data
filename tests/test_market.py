from datetime import date
from pathlib import Path

import httpx
import polars as pl
import pytest

from app.data.nflverse import connect, replace_table
from app.data.store import Store
from app.market import (
    consensus_snapshot, fantasycalc_params, fetch_fantasycalc, fetch_ffc_adp, first_gameday,
    snapshot_fantasycalc, snapshot_source,
)

LEAGUE = {"league_id": "1", "total_rosters": 10, "scoring_settings": {"rec": 1.0},
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
    assert store.latest_snapshot(snapshot_source(str(LEAGUE["league_id"]))).height == 2
    assert store.latest_snapshot("fantasycalc").is_empty()


def test_first_gameday_raises_without_schedule_rows(tmp_path: Path) -> None:
    con = connect(tmp_path / "s.duckdb")
    replace_table(con, "schedules", pl.DataFrame({
        "season": [2024], "week": [1], "game_type": ["REG"],
        "gameday": ["2024-09-05"],
    }))
    with pytest.raises(ValueError):
        first_gameday(con, 2024, 9)
    with pytest.raises(ValueError):
        consensus_snapshot(con, 2023, 1)
