"""One row per player: market value, projected points, intrinsic value, and card details."""
from __future__ import annotations

import duckdb
import polars as pl

from app.data.store import Store
from app.model.league import LeagueContext, sleeper_points
from app.model.live import Projection, ros_totals

MIN_TREND_WEEKS = 3


def _trend_stats(points: pl.DataFrame) -> pl.DataFrame:
    grouped = points.sort("week").group_by("sleeper_id").agg(
        pl.col("points").alias("trend"),
        pl.col("points").quantile(0.2, "linear").alias("floor"),
        pl.col("points").quantile(0.8, "linear").alias("ceiling"),
        (pl.col("points").std() / pl.col("points").mean()).alias("consistency"),
        pl.len().alias("weeks"),
    )
    few = pl.col("weeks") < MIN_TREND_WEEKS
    return grouped.with_columns(
        pl.when(few).then(None).otherwise(pl.col("floor")).alias("floor"),
        pl.when(few).then(None).otherwise(pl.col("ceiling")).alias("ceiling"),
        pl.when(few | pl.col("consistency").is_infinite() | pl.col("consistency").is_nan()).then(None)
        .otherwise(pl.col("consistency")).alias("consistency"),
    ).drop("weeks")


def replacement_ros(frame: pl.DataFrame) -> dict[str, float]:
    free = frame.filter(pl.col("roster_id").is_null()).group_by("position").agg(pl.col("ros_primary").max())
    return {str(p): float(v) for p, v in zip(free["position"], free["ros_primary"])}


def _ownership(ctx: LeagueContext) -> pl.DataFrame:
    rows = [(p, r.roster_id, r.team_name) for r in ctx.rosters for p in r.players]
    return pl.DataFrame(rows, schema={"sleeper_id": pl.Utf8, "roster_id": pl.Int64, "team_name": pl.Utf8}, orient="row")


def player_values(ctx: LeagueContext, con: duckdb.DuckDBPyConnection, store: Store,
                  primary: Projection, secondary: Projection | None) -> pl.DataFrame:
    players = con.execute("SELECT sleeper_id, full_name AS name, position, team AS nfl_team, injury_status FROM sleeper_players").pl()
    base = primary.ppg.select("sleeper_id", "gsis_id").join(players, on="sleeper_id", how="left")
    ros_p = ros_totals(primary).select("sleeper_id", pl.col("ros_points").alias("ros_primary"))
    frame = base.join(ros_p, on="sleeper_id", how="left")
    if secondary is not None:
        frame = frame.join(ros_totals(secondary).select("sleeper_id", pl.col("ros_points").alias("ros_secondary")), on="sleeper_id", how="left")
    else:
        frame = frame.with_columns(pl.lit(None, dtype=pl.Float64).alias("ros_secondary"))
    market = store.latest_snapshot("fantasycalc").select(
        "sleeper_id", pl.col("value").alias("market_value"), pl.col("overall_rank").alias("market_rank")
    )
    frame = frame.join(market, on="sleeper_id", how="left").join(_ownership(ctx), on="sleeper_id", how="left")
    repl = replacement_ros(frame)
    frame = frame.with_columns(
        pl.col("position").replace_strict(repl, default=0.0, return_dtype=pl.Float64).alias("replacement_ros")
    ).with_columns((pl.col("ros_primary") - pl.col("replacement_ros")).alias("intrinsic"))
    trend = _trend_stats(sleeper_points(store, ctx.league_id, list(range(1, ctx.week))))
    return frame.join(trend, on="sleeper_id", how="left").select(
        "sleeper_id", "gsis_id", "name", "position", "nfl_team", "roster_id", "team_name", "market_value", "market_rank",
        "ros_primary", "ros_secondary", "replacement_ros", "intrinsic", "floor", "ceiling", "consistency", "trend", "injury_status",
    )
