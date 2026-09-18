"""Live game state with player projections over remaining weeks."""
from __future__ import annotations

from dataclasses import dataclass

import duckdb
import polars as pl

from app.data.identity import id_hub
from app.data.scoring import SKILL_POSITIONS, expected_points, score_weeks
from app.data.store import Store
from app.market import consensus_snapshot
from app.model.agecurve import fit_age_curve, player_ages, season_ppg
from app.model.availability import availability, sleeper_overlay
from app.model.league import LeagueContext, sleeper_points, team_opponents
from app.model.projections import (
    BlendParams, Inputs, blend, build_features, curve_ppg, opponent_factors, positional_ranks, rank_to_ppg_curve,
)
from app.model.replacement import replacement_ppg, replacement_ranks

SOURCES = ("consensus", "model")
FACTOR_MIN_WEEK = 3


@dataclass(frozen=True)
class Projection:
    source: str
    weekly: pl.DataFrame
    ppg: pl.DataFrame


def _players(con: duckdb.DuckDBPyConnection) -> pl.DataFrame:
    return con.execute(
        "SELECT sleeper_id, full_name, position, team AS nfl_team, injury_status, gsis_id FROM sleeper_players"
    ).pl()


def _hub(con: duckdb.DuckDBPyConnection) -> pl.DataFrame:
    return id_hub(con.execute("SELECT * FROM ff_playerids").pl())


def _scored(con: duckdb.DuckDBPyConnection, seasons: list[int], scoring: dict[str, float]) -> pl.DataFrame:
    stats = con.execute(
        "SELECT * FROM player_stats WHERE season IN (SELECT unnest(?)) AND season_type = 'REG'", [seasons]
    ).pl()
    return score_weeks(stats, scoring).select(
        "player_id", "season", "week", "season_type", "position", "team", "opponent_team", "points"
    )


def _prev_curve(con: duckdb.DuckDBPyConnection, ctx: LeagueContext) -> pl.DataFrame:
    seasonal = season_ppg(_scored(con, [ctx.season - 1], ctx.scoring))
    return rank_to_ppg_curve(seasonal, ctx.season - 1)


def _universe(players: pl.DataFrame) -> pl.DataFrame:
    skill = players.filter(pl.col("position").is_in(SKILL_POSITIONS) & pl.col("gsis_id").is_not_null())
    kd = players.filter(
        (pl.col("position") == "DEF")
        | ((pl.col("position") == "K") & pl.col("nfl_team").is_not_null())
    )
    return pl.concat([skill, kd]).unique(subset=["sleeper_id"])


def _consensus_ppg(con: duckdb.DuckDBPyConnection, ctx: LeagueContext, hub: pl.DataFrame, curve: pl.DataFrame) -> pl.DataFrame:
    snap = positional_ranks(consensus_snapshot(con, ctx.season, ctx.week))
    with_ids = snap.join(hub.select("fantasypros_id", "gsis_id"), on="fantasypros_id", how="left")
    ppg = [curve_ppg(curve, str(p), int(r)) for p, r in zip(with_ids["pos"], with_ids["pos_rank"])]
    return with_ids.with_columns(pl.Series("ppg", ppg, dtype=pl.Float64)).select(
        "gsis_id", "fantasypros_id", pl.col("pos").alias("position"), "ppg"
    )


def _model_ppg(con: duckdb.DuckDBPyConnection, ctx: LeagueContext, hub: pl.DataFrame, curve: pl.DataFrame,
               params: BlendParams) -> pl.DataFrame:
    history = _scored(con, list(range(ctx.season - 6, ctx.season)), ctx.scoring)
    seasonal = season_ppg(history)
    prev = seasonal.filter(pl.col("season") == ctx.season - 1)
    ages = player_ages(hub, list(range(ctx.season - 6, ctx.season + 1)))
    scored = _scored(con, [ctx.season], ctx.scoring)
    opportunity = expected_points(
        con.execute("SELECT * FROM ff_opportunity WHERE season = ?", [ctx.season]).pl(), ctx.scoring
    ).select("player_id", "season", "week", "exp_points")
    prev_ppg = prev.select("player_id", "position", "ppg")
    replacement = replacement_ppg(prev_ppg, replacement_ranks(ctx.roster_positions, ctx.n_teams, prev_ppg))
    inputs = Inputs(
        scored=scored, opportunity=opportunity, hub=hub, consensus_preseason=consensus_snapshot(con, ctx.season, 1),
        prev_curve=curve, prev_seasonal=prev, age_curve=fit_age_curve(seasonal, ages),
        ages=ages.filter(pl.col("season") == ctx.season).select("player_id", "age"), replacement=replacement,
    )
    return blend(build_features(inputs, ctx.season, ctx.week), params).select(
        pl.col("player_id").alias("gsis_id"), "position", "ppg"
    )


def _kd_from_sleeper(store: Store, ctx: LeagueContext, players: pl.DataFrame) -> pl.DataFrame:
    pts = sleeper_points(store, ctx.league_id, list(range(1, ctx.week)))
    kd = players.filter(pl.col("position").is_in(["K", "DEF"])).select("sleeper_id", "position")
    mean = pts.join(kd, on="sleeper_id", how="inner").group_by("sleeper_id", "position").agg(pl.col("points").mean().alias("ppg"))
    median = mean.group_by("position").agg(pl.col("ppg").median().alias("pos_median"))
    return kd.join(mean, on=["sleeper_id", "position"], how="left").join(median, on="position", how="left").select(
        "sleeper_id", "position", pl.coalesce(pl.col("ppg"), pl.col("pos_median"), pl.lit(0.0)).alias("ppg")
    )


def _availability(con: duckdb.DuckDBPyConnection, ctx: LeagueContext, universe: pl.DataFrame) -> pl.DataFrame:
    rosters_weekly = con.execute(
        "SELECT * FROM rosters_weekly WHERE season = ? AND game_type = 'REG'", [ctx.season]
    ).pl()
    injuries = con.execute("SELECT * FROM injuries WHERE season = ? AND game_type = 'REG'", [ctx.season]).pl()
    ids = universe.filter(pl.col("gsis_id").is_not_null()).select(pl.col("gsis_id").alias("player_id"))
    avail = availability(rosters_weekly, injuries, ctx.season, ctx.week, ids)
    live = con.execute("SELECT sleeper_id, gsis_id, injury_status FROM sleeper_players").pl()
    return sleeper_overlay(avail, live).rename({"player_id": "gsis_id"})


def _apply_weeks(ppg: pl.DataFrame, opponents: pl.DataFrame, factors: pl.DataFrame, week: int, weeks: list[int]) -> pl.DataFrame:
    grid = ppg.join(pl.DataFrame({"week": weeks}), how="cross")
    with_opp = grid.join(opponents, left_on=["week", "nfl_team"], right_on=["week", "team"], how="left")
    with_factor = with_opp.join(
        factors, left_on=["opponent", "position"], right_on=["defense", "position"], how="left"
    ).with_columns(pl.col("factor").fill_null(1.0))
    avail = pl.when(pl.col("week") == week).then(pl.col("avail_now")).otherwise(pl.col("avail_ros"))
    points = pl.when(pl.col("opponent").is_null()).then(0.0).otherwise(pl.col("ppg") * avail * pl.col("factor"))
    return with_factor.select("sleeper_id", "gsis_id", "position", "week", points.alias("points"))


def _factors(con: duckdb.DuckDBPyConnection, ctx: LeagueContext) -> pl.DataFrame:
    if ctx.week < FACTOR_MIN_WEEK:
        return pl.DataFrame({"defense": [], "position": [], "factor": []}, schema={"defense": pl.Utf8, "position": pl.Utf8, "factor": pl.Float64})
    return opponent_factors(_scored(con, [ctx.season], ctx.scoring), through_week=ctx.week - 1)


def project(con: duckdb.DuckDBPyConnection, store: Store, ctx: LeagueContext, source: str,
            params: BlendParams = BlendParams()) -> Projection:
    if source not in SOURCES:
        raise ValueError(f"unknown projection source {source}")
    players = _players(con)
    hub = _hub(con)
    curve = _prev_curve(con, ctx)
    cons = _consensus_ppg(con, ctx, hub, curve)
    universe = _universe(players)
    skill_ppg = cons.select("gsis_id", "ppg") if source == "consensus" else _model_ppg(con, ctx, hub, curve, params).select("gsis_id", "ppg")
    cons_ppg = cons.select("gsis_id", pl.col("ppg").alias("cons_ppg"))
    kd = _kd_from_sleeper(store, ctx, players)
    base = (
        universe.join(skill_ppg, on="gsis_id", how="left")
        .join(cons_ppg, on="gsis_id", how="left")
        .join(kd.select("sleeper_id", pl.col("ppg").alias("kd_ppg")), on="sleeper_id", how="left")
    )
    base = base.with_columns(
        pl.coalesce(pl.col("ppg"), pl.col("cons_ppg"), pl.col("kd_ppg"), pl.lit(0.0)).alias("ppg")
    ).drop("cons_ppg", "kd_ppg")
    avail = _availability(con, ctx, universe)
    ppg = base.join(avail, on="gsis_id", how="left").with_columns(
        pl.col("avail_now").fill_null(1.0), pl.col("avail_ros").fill_null(1.0)
    ).select("sleeper_id", "gsis_id", "position", "nfl_team", "ppg", "avail_now", "avail_ros")
    weekly = _apply_weeks(ppg, team_opponents(con, ctx.season, ctx.remaining_weeks), _factors(con, ctx), ctx.week, ctx.remaining_weeks)
    return Projection(source=source, weekly=weekly, ppg=ppg.drop("avail_now"))


def ros_totals(proj: Projection) -> pl.DataFrame:
    return proj.weekly.group_by("sleeper_id", "position").agg(pl.col("points").sum().alias("ros_points"))
