"""Map Sleeper scoring keys onto nflverse columns and compute points per player-week."""
from __future__ import annotations

from typing import Mapping

import polars as pl

SKILL_POSITIONS = ("QB", "RB", "WR", "TE")


def _c(name: str) -> pl.Expr:
    return pl.col(name).cast(pl.Float64).fill_null(0.0)


def _bonus(column: str, threshold: float) -> pl.Expr:
    return (_c(column) >= threshold).cast(pl.Float64)


SCORING_MAP: dict[str, pl.Expr] = {
    "pass_yd": _c("passing_yards"),
    "pass_td": _c("passing_tds"),
    "pass_int": _c("passing_interceptions"),
    "pass_2pt": _c("passing_2pt_conversions"),
    "pass_sack": _c("sacks_suffered"),
    "rush_yd": _c("rushing_yards"),
    "rush_td": _c("rushing_tds"),
    "rush_2pt": _c("rushing_2pt_conversions"),
    "rush_40p": _c("rushing_40"),
    "rec": _c("receptions"),
    "rec_yd": _c("receiving_yards"),
    "rec_td": _c("receiving_tds"),
    "rec_2pt": _c("receiving_2pt_conversions"),
    "rec_40p": _c("receiving_40"),
    "fum": _c("sack_fumbles") + _c("rushing_fumbles") + _c("receiving_fumbles"),
    "fum_lost": _c("sack_fumbles_lost") + _c("rushing_fumbles_lost") + _c("receiving_fumbles_lost"),
    "fum_rec_td": _c("fumble_recovery_tds"),
    "st_td": _c("special_teams_tds"),
    "bonus_pass_yd_300": _bonus("passing_yards", 300),
    "bonus_pass_yd_400": _bonus("passing_yards", 400),
    "bonus_rush_yd_100": _bonus("rushing_yards", 100),
    "bonus_rush_yd_200": _bonus("rushing_yards", 200),
    "bonus_rec_yd_100": _bonus("receiving_yards", 100),
    "bonus_rec_yd_200": _bonus("receiving_yards", 200),
    "bonus_rec_te": pl.when(pl.col("position") == "TE").then(_c("receptions")).otherwise(0.0),
    "bonus_rec_rb": pl.when(pl.col("position") == "RB").then(_c("receptions")).otherwise(0.0),
    "bonus_rec_wr": pl.when(pl.col("position") == "WR").then(_c("receptions")).otherwise(0.0),
    "fgm_0_19": _c("fg_made_0_19"),
    "fgm_20_29": _c("fg_made_20_29"),
    "fgm_30_39": _c("fg_made_30_39"),
    "fgm_40_49": _c("fg_made_40_49"),
    "fgm_50p": _c("fg_made_50_59") + _c("fg_made_60_"),
    "fgmiss": _c("fg_missed"),
    "xpm": _c("pat_made"),
    "xpmiss": _c("pat_missed"),
}

EXPECTED_MAP: dict[str, pl.Expr] = {
    "pass_yd": _c("pass_yards_gained_exp"),
    "pass_td": _c("pass_touchdown_exp"),
    "pass_int": _c("pass_interception_exp"),
    "pass_2pt": _c("pass_two_point_conv_exp"),
    "rush_yd": _c("rush_yards_gained_exp"),
    "rush_td": _c("rush_touchdown_exp"),
    "rush_2pt": _c("rush_two_point_conv_exp"),
    "rec": _c("receptions_exp"),
    "rec_yd": _c("rec_yards_gained_exp"),
    "rec_td": _c("rec_touchdown_exp"),
    "rec_2pt": _c("rec_two_point_conv_exp"),
    "bonus_rec_te": pl.when(pl.col("position") == "TE").then(_c("receptions_exp")).otherwise(0.0),
    "bonus_rec_rb": pl.when(pl.col("position") == "RB").then(_c("receptions_exp")).otherwise(0.0),
    "bonus_rec_wr": pl.when(pl.col("position") == "WR").then(_c("receptions_exp")).otherwise(0.0),
}


def unmapped_keys(scoring: Mapping[str, float]) -> list[str]:
    return sorted(k for k in scoring if k not in SCORING_MAP)


def _needed_columns(exprs: list[pl.Expr]) -> set[str]:
    names: set[str] = set()
    for expr in exprs:
        names.update(expr.meta.root_names())
    return names


def _with_missing_as_zero(frame: pl.DataFrame, exprs: list[pl.Expr]) -> pl.DataFrame:
    missing = [c for c in _needed_columns(exprs) if c not in frame.columns and c != "position"]
    if "position" not in frame.columns:
        frame = frame.with_columns(pl.lit(None, dtype=pl.Utf8).alias("position"))
    return frame.with_columns([pl.lit(0.0).alias(c) for c in missing])


def _weighted_sum(
    frame: pl.DataFrame, scoring: Mapping[str, float], table: Mapping[str, pl.Expr], out: str
) -> pl.DataFrame:
    exprs = [table[k] * float(w) for k, w in scoring.items() if k in table]
    frame = _with_missing_as_zero(frame, list(table.values()))
    total = pl.sum_horizontal(exprs) if exprs else pl.lit(0.0)
    return frame.with_columns(total.cast(pl.Float64).alias(out))


def score_weeks(stats: pl.DataFrame, scoring: Mapping[str, float]) -> pl.DataFrame:
    """Add `points` under the league's scoring to a frame of nflverse weekly stats."""
    return _weighted_sum(stats, scoring, SCORING_MAP, "points")


def expected_points(opportunity: pl.DataFrame, scoring: Mapping[str, float]) -> pl.DataFrame:
    """Add `exp_points`: usage-based expected points under the league's scoring."""
    return _weighted_sum(opportunity, scoring, EXPECTED_MAP, "exp_points")
