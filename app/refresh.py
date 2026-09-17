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
    joined = frame.join(hub.unique(subset=["sleeper_id"], keep="first"), on="sleeper_id", how="left")
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
