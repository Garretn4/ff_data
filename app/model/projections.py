"""Shrinkage-blend projections: prior from consensus and history, data from usage and results."""
from __future__ import annotations

from dataclasses import dataclass

import polars as pl

from app.data.scoring import SKILL_POSITIONS
from app.model.agecurve import age_factor

FACTOR_LOW = 0.9
FACTOR_HIGH = 1.1
CURVE_MIN_GAMES = 8


@dataclass(frozen=True)
class BlendParams:
    k: float = 4.0
    a: float = 0.6
    b: float = 0.5


@dataclass(frozen=True)
class Inputs:
    scored: pl.DataFrame
    opportunity: pl.DataFrame
    hub: pl.DataFrame
    consensus_preseason: pl.DataFrame
    prev_curve: pl.DataFrame
    prev_seasonal: pl.DataFrame
    age_curve: pl.DataFrame
    ages: pl.DataFrame
    replacement: dict[str, float]


def rank_to_ppg_curve(seasonal: pl.DataFrame, season: int) -> pl.DataFrame:
    return (
        seasonal.filter((pl.col("season") == season) & (pl.col("games") >= CURVE_MIN_GAMES))
        .sort("ppg", descending=True)
        .with_columns(pl.col("ppg").rank("ordinal", descending=True).over("position").cast(pl.Int64).alias("pos_rank"))
        .select("position", "pos_rank", "ppg")
        .sort("position", "pos_rank")
    )


def curve_ppg(curve: pl.DataFrame, position: str, pos_rank: int) -> float:
    rows = curve.filter(pl.col("position") == position)
    if rows.height == 0:
        return 0.0
    idx = min(int(pos_rank), rows.height) - 1
    return float(rows["ppg"][idx])


def positional_ranks(consensus: pl.DataFrame) -> pl.DataFrame:
    return consensus.with_columns(
        pl.when(pl.col("pos") == "DST").then(pl.lit("DEF")).otherwise(pl.col("pos")).alias("pos")
    ).with_columns(pl.col("ecr").rank("ordinal").over("pos").cast(pl.Int64).alias("pos_rank"))


def _ecr_prior(inputs: Inputs) -> pl.DataFrame:
    ranked = positional_ranks(inputs.consensus_preseason).filter(pl.col("pos").is_in(SKILL_POSITIONS))
    with_ids = ranked.join(inputs.hub.select("fantasypros_id", "gsis_id"), on="fantasypros_id", how="inner")
    ecr_ppg = [curve_ppg(inputs.prev_curve, str(p), int(r)) for p, r in zip(with_ids["pos"], with_ids["pos_rank"])]
    return with_ids.select(
        pl.col("gsis_id").alias("player_id"), pl.col("pos").alias("position")
    ).with_columns(pl.Series("ecr_ppg", ecr_ppg, dtype=pl.Float64)).unique(subset=["player_id"])


def _last_season(inputs: Inputs) -> pl.DataFrame:
    return inputs.prev_seasonal.filter(pl.col("games") >= 4).select("player_id", pl.col("ppg").alias("last_ppg"))


def _this_season(inputs: Inputs, season: int, week: int) -> pl.DataFrame:
    played = inputs.scored.filter((pl.col("season") == season) & (pl.col("week") < week))
    actual = played.group_by("player_id").agg(pl.col("points").mean().alias("actual_ppg"), pl.len().alias("n"))
    usage = (
        inputs.opportunity.filter((pl.col("season") == season) & (pl.col("week") < week))
        .group_by("player_id").agg(pl.col("exp_points").mean().alias("usage_ppg"))
    )
    return actual.join(usage, on="player_id", how="left")


def build_features(inputs: Inputs, season: int, week: int) -> pl.DataFrame:
    base = _ecr_prior(inputs)
    ages = inputs.ages.select("player_id", "age")
    joined = (
        base.join(_last_season(inputs), on="player_id", how="left")
        .join(ages, on="player_id", how="left")
        .join(_this_season(inputs, season, week), on="player_id", how="left")
    )
    repl = pl.col("position").replace_strict(inputs.replacement, default=0.0, return_dtype=pl.Float64)
    mults = [
        age_factor(inputs.age_curve, str(p), int(a)) if a is not None else 1.0
        for p, a in zip(joined["position"], joined["age"])
    ]
    return joined.with_columns(
        pl.col("last_ppg").fill_null(repl),
        pl.Series("age_mult", mults, dtype=pl.Float64),
        pl.col("n").fill_null(0).cast(pl.Int64),
    ).select("player_id", "position", "ecr_ppg", "last_ppg", "age_mult", "usage_ppg", "actual_ppg", "n")


def blend(features: pl.DataFrame, params: BlendParams) -> pl.DataFrame:
    prior = pl.col("age_mult") * (params.b * pl.col("ecr_ppg") + (1 - params.b) * pl.col("last_ppg"))
    usage = pl.coalesce(pl.col("usage_ppg"), pl.col("actual_ppg"))
    data = params.a * usage + (1 - params.a) * pl.col("actual_ppg")
    n = pl.col("n").cast(pl.Float64)
    ppg = pl.when(n > 0).then((params.k * pl.col("prior") + n * data) / (params.k + n)).otherwise(pl.col("prior"))
    return features.with_columns(prior.alias("prior")).with_columns(ppg.alias("ppg"))


def opponent_factors(scored: pl.DataFrame, through_week: int, shrink_games: int = 6) -> pl.DataFrame:
    played = scored.filter(pl.col("week") <= through_week)
    per_game = played.group_by("opponent_team", "position", "week").agg(pl.col("points").sum().alias("allowed"))
    league_avg = per_game.group_by("position").agg(pl.col("allowed").mean().alias("avg"))
    by_def = per_game.group_by("opponent_team", "position").agg(
        pl.col("allowed").mean().alias("mean_allowed"), pl.len().alias("games")
    )
    raw = by_def.join(league_avg, on="position").with_columns(
        (pl.col("mean_allowed") / pl.col("avg")).alias("raw")
    )
    shrunk = (pl.col("games") * pl.col("raw") + shrink_games) / (pl.col("games") + shrink_games)
    return raw.with_columns(shrunk.clip(FACTOR_LOW, FACTOR_HIGH).alias("factor")).select(
        pl.col("opponent_team").alias("defense"), "position", "factor"
    )


def weekly_projection(
    ppg: pl.DataFrame,
    schedule: pl.DataFrame,
    week_from: int,
    week_to: int,
    teams: pl.DataFrame,
    factors: pl.DataFrame,
    injured: pl.DataFrame,
) -> pl.DataFrame:
    weeks = pl.DataFrame({"week": list(range(week_from, week_to + 1))})
    grid = ppg.join(teams, on="player_id", how="left").join(weeks, how="cross")
    with_opp = grid.join(schedule, on=["week", "team"], how="left")
    with_factor = with_opp.join(
        factors, left_on=["opponent", "position"], right_on=["defense", "position"], how="left"
    ).with_columns(pl.col("factor").fill_null(1.0))
    status = injured.select("player_id", "status")
    with_status = with_factor.join(status, on="player_id", how="left")
    zero = (
        pl.col("opponent").is_null()
        | (pl.col("status") == "IR")
        | ((pl.col("status") == "Out") & (pl.col("week") == week_from))
    )
    points = pl.when(zero).then(0.0).otherwise(pl.col("ppg") * pl.col("factor"))
    return with_status.select("player_id", "week", points.alias("points"))
