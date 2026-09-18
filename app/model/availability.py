"""Availability multiplier from weekly roster status and injury reports."""
from __future__ import annotations

import polars as pl

ROSTER_ROS: dict[str, float] = {
    "ACT": 1.0, "INA": 1.0, "RES": 0.35, "DEV": 0.25, "TRD": 1.0, "TRC": 1.0,
}
ROSTER_NOW: dict[str, float] = {"ACT": 1.0, "TRD": 1.0, "TRC": 1.0}
INJURY_NOW: dict[str, float] = {"Out": 0.0, "Doubtful": 0.25, "Questionable": 0.85}
INJURY_ROS: dict[str, float] = {"Out": 0.85, "Doubtful": 0.9, "Questionable": 1.0}
SLEEPER_NOW: dict[str, float] = {
    "IR": 0.0, "PUP": 0.0, "Sus": 0.0, "COV": 0.0, "Out": 0.0,
    "Doubtful": 0.25, "Questionable": 0.85, "NA": 0.0, "DNR": 0.0,
}
SLEEPER_ROS: dict[str, float] = {
    "IR": 0.35, "PUP": 0.35, "Sus": 0.5, "COV": 0.9, "Out": 0.85,
    "Doubtful": 0.9, "Questionable": 1.0, "NA": 0.0, "DNR": 0.0,
}

_EMPTY = ""


def _latest_best_roster_status(frame: pl.DataFrame, status_col: str, id_col: str, rank: dict[str, float]) -> pl.DataFrame:
    """Per player, the status from the most recent week on file, ties within a week broken by best status."""
    if frame.height == 0:
        return pl.DataFrame({id_col: [], status_col: []}, schema={id_col: pl.Utf8, status_col: pl.Utf8})
    rank_expr = pl.col(status_col).fill_null(_EMPTY).replace_strict(rank, default=0.0, return_dtype=pl.Float64)
    return (
        frame.with_columns(rank_expr.alias("_rank"))
        .sort(["week", "_rank"], descending=[True, True], maintain_order=True)
        .group_by(id_col, maintain_order=True)
        .agg(pl.col(status_col).first())
    )


def _worst_status(frame: pl.DataFrame, status_col: str, id_col: str, rank: dict[str, float]) -> pl.DataFrame:
    if frame.height == 0:
        return pl.DataFrame({id_col: [], status_col: []}, schema={id_col: pl.Utf8, status_col: pl.Utf8})
    rank_expr = pl.col(status_col).fill_null(_EMPTY).replace_strict(rank, default=1.0, return_dtype=pl.Float64)
    return (
        frame.with_columns(rank_expr.alias("_rank"))
        .sort("_rank", descending=False, maintain_order=True)
        .group_by(id_col, maintain_order=True)
        .agg(pl.col(status_col).first())
    )


def _lookup(column: str, table: dict[str, float], default: float) -> pl.Expr:
    return pl.col(column).fill_null(_EMPTY).replace_strict(table, default=default, return_dtype=pl.Float64)


def availability(
    rosters_weekly: pl.DataFrame, injuries: pl.DataFrame, season: int, week: int, universe: pl.DataFrame
) -> pl.DataFrame:
    """Per-player now/rest-of-season availability multipliers for a season/week.

    Roster status carries forward from the most recent week on file at or before the
    as-of week, so a bye week (no roster snapshot for the player's team) does not read
    as a drop; a player never seen on any roster through the as-of week gets 0.0/0.0.
    """
    roster_through = rosters_weekly.filter(
        (pl.col("season") == season) & (pl.col("week") <= week) & (pl.col("game_type") == "REG")
    )
    injury_week = injuries.filter(
        (pl.col("season") == season) & (pl.col("week") == week) & (pl.col("game_type") == "REG")
    )
    roster_best = _latest_best_roster_status(roster_through, "status", "gsis_id", ROSTER_ROS)
    injury_worst = _worst_status(injury_week, "report_status", "gsis_id", INJURY_NOW)
    base = universe.select("player_id").unique()
    joined = (
        base.join(roster_best, left_on="player_id", right_on="gsis_id", how="left")
        .join(injury_worst, left_on="player_id", right_on="gsis_id", how="left")
    )
    roster_now = _lookup("status", ROSTER_NOW, 0.0)
    roster_ros = _lookup("status", ROSTER_ROS, 0.0)
    injury_now = _lookup("report_status", INJURY_NOW, 1.0)
    injury_ros = _lookup("report_status", INJURY_ROS, 1.0)
    return joined.with_columns(
        (roster_now * injury_now).alias("avail_now"),
        (roster_ros * injury_ros).alias("avail_ros"),
    ).select("player_id", "avail_now", "avail_ros")


def sleeper_overlay(avail: pl.DataFrame, sleeper_players: pl.DataFrame) -> pl.DataFrame:
    """Apply Sleeper's live injury_status as a further multiplier for live use."""
    status = sleeper_players.select("gsis_id", "injury_status")
    joined = avail.join(status, left_on="player_id", right_on="gsis_id", how="left")
    now_mult = _lookup("injury_status", SLEEPER_NOW, 1.0)
    ros_mult = _lookup("injury_status", SLEEPER_ROS, 1.0)
    return joined.with_columns(
        (pl.col("avail_now") * now_mult).alias("avail_now"),
        (pl.col("avail_ros") * ros_mult).alias("avail_ros"),
    ).select("player_id", "avail_now", "avail_ros")
