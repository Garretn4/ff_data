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
