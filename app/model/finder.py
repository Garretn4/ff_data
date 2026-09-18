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
