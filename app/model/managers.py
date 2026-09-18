"""Manager tendencies from the league chain's trades and waiver moves."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any, Callable, Protocol

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

Bucket = dict[str, Any]


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


def _process_trade(
    row: dict[str, Any], positions: dict[str, str], owners: dict[tuple[int, int], str],
    con: duckdb.DuckDBPyConnection, scoring: dict[str, float], hub: pl.DataFrame,
    curve_cache: dict[int, pl.DataFrame], bucket: Callable[[str], Bucket],
) -> None:
    season = int(row["season"])
    adds = dict(zip(row["add_player"], row["add_roster"]))
    flows = _trade_flows(adds, positions)
    values = _rank_values(con, season, int(row["week"]), scoring, hub, curve_cache)
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


def _process_waiver(
    row: dict[str, Any], positions: dict[str, str], owners: dict[tuple[int, int], str],
    bucket: Callable[[str], Bucket],
) -> None:
    season = int(row["season"])
    for player, roster in zip(row["add_player"], row["add_roster"]):
        b = bucket(owners.get((season, int(roster)), ""))
        b["adds"][positions.get(player, "?")] += 1
        b["faab"] += int(row["waiver_bid"] or 0)


def profiles(ctx: LeagueContext, client: Any, con: duckdb.DuckDBPyConnection, store: Store) -> list[ManagerProfile]:
    chain = league_chain(client, ctx.league_id)
    tx = collect_transactions(client, chain, SEASON_WEEKS)
    owners = _owner_maps(chain, client)
    positions = dict(con.execute("SELECT sleeper_id, position FROM sleeper_players").fetchall())
    hub = id_hub(con.execute("SELECT * FROM ff_playerids").pl())
    curve_cache: dict[int, pl.DataFrame] = {}
    stats: dict[str, Bucket] = {}

    def bucket(owner: str) -> Bucket:
        return stats.setdefault(owner, {"trades": 0, "partners": Counter(), "acq": Counter(), "given": Counter(),
                                        "overpay": 0.0, "faab": 0, "adds": Counter()})

    for row in tx.iter_rows(named=True):
        if row["type"] == "trade":
            _process_trade(row, positions, owners, con, ctx.scoring, hub, curve_cache, bucket)
        else:
            _process_waiver(row, positions, owners, bucket)

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
