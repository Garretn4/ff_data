from __future__ import annotations

import polars as pl

from app.model.league import LeagueContext
from app.model.lineup import optimal_lineup
from app.model.live import Projection
from app.model.trade import startable


def position_strength(ctx: LeagueContext, proj: Projection) -> pl.DataFrame:
    rows: list[tuple[int, str, str, float]] = []
    for roster in ctx.rosters:
        mine = proj.weekly.filter(pl.col("sleeper_id").is_in(list(startable(roster))))
        totals: dict[str, float] = {}
        for (_week,), frame in mine.group_by("week"):
            players = list(zip(frame["sleeper_id"].to_list(), frame["position"].to_list(), frame["points"].to_list()))
            by_id = {pid: (pos, pts) for pid, pos, pts in players}
            for pid in optimal_lineup(ctx.slots, players)[1].values():
                pos, pts = by_id[pid]
                totals[pos] = totals.get(pos, 0.0) + pts
        for pos in sorted({s for s in ctx.slots if s not in ("FLEX", "SUPER_FLEX", "REC_FLEX", "WRRB_FLEX")} | set(totals)):
            rows.append((roster.roster_id, roster.team_name, pos, totals.get(pos, 0.0)))
    return pl.DataFrame(rows, schema={"roster_id": pl.Int64, "team_name": pl.Utf8, "position": pl.Utf8, "points": pl.Float64}, orient="row")


def surplus(strength: pl.DataFrame) -> pl.DataFrame:
    median = strength.group_by("position").agg(pl.col("points").median().alias("median"))
    return strength.join(median, on="position").with_columns((pl.col("points") - pl.col("median")).alias("surplus"))
