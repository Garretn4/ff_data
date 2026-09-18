"""Season-level points per game and a per-position age adjustment learned from history."""
from __future__ import annotations

import polars as pl

FACTOR_MIN = 0.7
FACTOR_MAX = 1.1
MIN_GAMES_FOR_PAIR = 8


def season_ppg(scored: pl.DataFrame, min_games: int = 1) -> pl.DataFrame:
    return (
        scored.filter(pl.col("season_type") == "REG")
        .group_by("player_id", "season", "position")
        .agg(pl.len().alias("games"), pl.col("points").mean().alias("ppg"))
        .filter(pl.col("games") >= min_games)
        .sort("player_id", "season")
    )


def player_ages(hub: pl.DataFrame, seasons: list[int]) -> pl.DataFrame:
    base = hub.filter(pl.col("birthdate").is_not_null()).select(
        pl.col("gsis_id").alias("player_id"), pl.col("birthdate").dt.year().alias("birth_year")
    )
    frames = [base.with_columns(pl.lit(s).alias("season")) for s in seasons]
    return pl.concat(frames).select(
        "player_id", "season", (pl.col("season") - pl.col("birth_year")).cast(pl.Int64).alias("age")
    )


def fit_age_curve(seasonal: pl.DataFrame, ages: pl.DataFrame, min_pairs: int = 15) -> pl.DataFrame:
    """Median year-over-year PPG ratio by position and age, clamped to a sane band."""
    eligible = seasonal.filter(pl.col("games") >= MIN_GAMES_FOR_PAIR)
    nxt = eligible.select(
        "player_id", "position", (pl.col("season") - 1).alias("season"), pl.col("ppg").alias("next_ppg")
    )
    pairs = (
        eligible.join(nxt, on=["player_id", "season", "position"], how="inner")
        .with_columns((pl.col("season") + 1).alias("next_season"))
        .join(ages, left_on=["player_id", "next_season"], right_on=["player_id", "season"], how="inner")
        .filter(pl.col("ppg") > 0)
        .with_columns((pl.col("next_ppg") / pl.col("ppg")).alias("ratio"))
    )
    return (
        pairs.group_by("position", "age")
        .agg(pl.col("ratio").median().alias("factor"), pl.len().alias("n"))
        .filter(pl.col("n") >= min_pairs)
        .with_columns(pl.col("factor").clip(FACTOR_MIN, FACTOR_MAX))
        .select("position", "age", "factor")
        .sort("position", "age")
    )


def age_factor(curve: pl.DataFrame, position: str, age: int) -> float:
    hit = curve.filter((pl.col("position") == position) & (pl.col("age") == age))
    return float(hit["factor"][0]) if hit.height else 1.0
