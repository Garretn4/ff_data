"""Walk-forward backtest of the value model against market consensus."""
from __future__ import annotations

from dataclasses import dataclass
from itertools import product
from pathlib import Path

import duckdb
import polars as pl

from app.data.identity import id_hub
from app.data.scoring import SKILL_POSITIONS, expected_points, score_weeks
from app.market import consensus_snapshot
from app.model.agecurve import fit_age_curve, player_ages, season_ppg
from app.model.projections import (
    BlendParams, Inputs, blend, build_features, curve_ppg, positional_ranks, rank_to_ppg_curve,
)
from app.model.replacement import replacement_ppg, replacement_ranks, starters_per_position

DEFAULT_GRID: list[BlendParams] = [
    BlendParams(k=k, a=a, b=b)
    for k, a, b in product((2.0, 4.0, 6.0, 8.0, 12.0), (0.3, 0.5, 0.7, 0.9), (0.3, 0.5, 0.7))
]
AGE_CURVE_FROM = 2012


def spearman(pred: pl.Series, actual: pl.Series) -> float:
    if len(pred) < 2:
        return 0.0
    rp = pred.rank("average").cast(pl.Float64)
    ra = actual.rank("average").cast(pl.Float64)
    corr = pl.DataFrame({"a": rp, "b": ra}).select(pl.corr("a", "b")).item()
    return float(corr) if corr is not None else 0.0


def mae(pred: pl.Series, actual: pl.Series) -> float:
    return float((pred - actual).abs().mean()) if len(pred) else 0.0


def precision_at(frame: pl.DataFrame, pred_col: str, actual_col: str, n: int) -> float:
    if frame.height == 0 or n <= 0:
        return 0.0
    top_pred = set(frame.sort(pred_col, descending=True).head(n)["player_id"].to_list())
    top_actual = set(frame.sort(actual_col, descending=True).head(n)["player_id"].to_list())
    return len(top_pred & top_actual) / n


def actual_ros(scored: pl.DataFrame, season: int, week_from: int) -> pl.DataFrame:
    return (
        scored.filter((pl.col("season") == season) & (pl.col("season_type") == "REG") & (pl.col("week") >= week_from))
        .group_by("player_id").agg(pl.col("points").sum().alias("ros_points"))
    )


def remaining_games(schedules: pl.DataFrame, season: int, week_from: int) -> pl.DataFrame:
    games = schedules.filter((pl.col("season") == season) & (pl.col("game_type") == "REG") & (pl.col("week") >= week_from))
    teams = pl.concat([games.select(pl.col("home_team").alias("team")), games.select(pl.col("away_team").alias("team"))])
    return teams.group_by("team").agg(pl.len().alias("games"))


def _games_per_player(teams: pl.DataFrame, per_team: pl.DataFrame) -> pl.DataFrame:
    median = per_team["games"].median() if per_team.height else None
    fallback = int(round(median)) if median is not None else 0
    return teams.join(per_team, on="team", how="left").with_columns(
        pl.col("games").fill_null(fallback)
    ).select("player_id", "games")


def market_prediction(
    consensus: pl.DataFrame, hub: pl.DataFrame, prev_curve: pl.DataFrame, games_per_player: pl.DataFrame
) -> pl.DataFrame:
    ranked = positional_ranks(consensus).filter(pl.col("pos").is_in(SKILL_POSITIONS))
    with_ids = ranked.join(hub.select("fantasypros_id", "gsis_id"), on="fantasypros_id", how="inner")
    ppg = [curve_ppg(prev_curve, str(p), int(r)) for p, r in zip(with_ids["pos"], with_ids["pos_rank"])]
    frame = with_ids.select(pl.col("gsis_id").alias("player_id"), pl.col("pos").alias("position")).with_columns(
        pl.Series("ppg", ppg, dtype=pl.Float64)
    ).unique(subset=["player_id"])
    return frame.join(games_per_player, on="player_id", how="left").with_columns(
        (pl.col("ppg") * pl.col("games").fill_null(0)).alias("pred")
    ).select("player_id", "position", "pred")


def model_prediction(blended: pl.DataFrame, games_per_player: pl.DataFrame) -> pl.DataFrame:
    return blended.join(games_per_player, on="player_id", how="left").with_columns(
        (pl.col("ppg") * pl.col("games").fill_null(0)).alias("pred")
    ).select("player_id", "position", "pred")


def _metrics_row(frame: pl.DataFrame, position: str, n_top: int) -> dict[str, object]:
    return {
        "position": position,
        "spearman": spearman(frame["pred"], frame["ros_points"]),
        "mae": mae(frame["pred"], frame["ros_points"]),
        "precision": precision_at(frame, "pred", "ros_points", n_top),
        "n": frame.height,
    }


def evaluate(pred: pl.DataFrame, actual: pl.DataFrame, starters: dict[str, int]) -> pl.DataFrame:
    joined = pred.join(actual, on="player_id", how="left").with_columns(pl.col("ros_points").fill_null(0.0))
    rows = [
        _metrics_row(joined.filter(pl.col("position") == pos), pos, starters.get(pos, 0))
        for pos in sorted(joined["position"].unique().to_list())
    ]
    pooled = _metrics_row(joined, "ALL", 0)
    pooled["precision"] = sum(r["precision"] for r in rows) / len(rows) if rows else 0.0
    rows.append(pooled)
    return pl.DataFrame(rows)


@dataclass(frozen=True)
class SeasonContext:
    season: int
    scored: pl.DataFrame
    prev_seasonal: pl.DataFrame
    prev_curve: pl.DataFrame
    opportunity: pl.DataFrame
    hub: pl.DataFrame
    consensus_preseason: pl.DataFrame
    age_curve: pl.DataFrame
    ages: pl.DataFrame
    schedules: pl.DataFrame
    prev_teams: pl.DataFrame


def _scored_weeks(con: duckdb.DuckDBPyConnection, seasons: list[int], scoring: dict[str, float]) -> pl.DataFrame:
    stats = con.execute(
        "SELECT * FROM player_stats WHERE season IN (SELECT unnest(?)) AND season_type = 'REG'", [seasons]
    ).pl()
    return score_weeks(stats, scoring).select(
        "player_id", "season", "week", "season_type", "position", "team", "opponent_team", "points"
    )


def _prev_season_teams(history: pl.DataFrame, season: int) -> pl.DataFrame:
    return (
        history.filter(pl.col("season") == season - 1)
        .sort("week").group_by("player_id").agg(pl.col("team").last())
    )


def load_season_context(con: duckdb.DuckDBPyConnection, season: int, scoring: dict[str, float]) -> SeasonContext:
    hub = id_hub(con.execute("SELECT * FROM ff_playerids").pl())
    history = _scored_weeks(con, list(range(AGE_CURVE_FROM, season)), scoring)
    seasonal = season_ppg(history)
    ages_all = player_ages(hub, list(range(AGE_CURVE_FROM, season + 1)))
    scored = _scored_weeks(con, [season], scoring)
    opportunity = expected_points(
        con.execute("SELECT * FROM ff_opportunity WHERE season = ?", [season]).pl(), scoring
    ).select("player_id", "season", "week", "exp_points")
    schedules = con.execute("SELECT season, week, game_type, home_team, away_team FROM schedules WHERE season = ?", [season]).pl()
    return SeasonContext(
        season=season,
        scored=scored,
        prev_seasonal=seasonal.filter(pl.col("season") == season - 1),
        prev_curve=rank_to_ppg_curve(seasonal, season - 1),
        opportunity=opportunity,
        hub=hub,
        consensus_preseason=consensus_snapshot(con, season, 1),
        age_curve=fit_age_curve(seasonal, ages_all),
        ages=ages_all.filter(pl.col("season") == season).select("player_id", "age"),
        schedules=schedules,
        prev_teams=_prev_season_teams(history, season),
    )


def _player_teams(ctx: SeasonContext, week: int) -> pl.DataFrame:
    latest = (
        ctx.scored.filter(pl.col("week") < week).sort("week").group_by("player_id").agg(pl.col("team").last())
    )
    universe = ctx.hub.select(pl.col("gsis_id").alias("player_id"))
    with_prev = universe.join(ctx.prev_teams, on="player_id", how="left").rename({"team": "prev_team"})
    return with_prev.join(latest, on="player_id", how="left").select(
        "player_id", pl.coalesce(pl.col("team"), pl.col("prev_team")).alias("team")
    )


def week_features(ctx: SeasonContext, week: int, roster_positions: list[str], n_teams: int) -> pl.DataFrame:
    prev_ppg = ctx.prev_seasonal.select("player_id", "position", "ppg")
    replacement = replacement_ppg(prev_ppg, replacement_ranks(roster_positions, n_teams, prev_ppg))
    inputs = Inputs(
        scored=ctx.scored, opportunity=ctx.opportunity, hub=ctx.hub,
        consensus_preseason=ctx.consensus_preseason, prev_curve=ctx.prev_curve,
        prev_seasonal=ctx.prev_seasonal, age_curve=ctx.age_curve, ages=ctx.ages, replacement=replacement,
    )
    return build_features(inputs, ctx.season, week)


@dataclass(frozen=True)
class WeekContext:
    ctx: SeasonContext
    week: int
    features: pl.DataFrame
    games: pl.DataFrame
    actual: pl.DataFrame
    starters: dict[str, int]
    universe: pl.DataFrame
    market_eval: pl.DataFrame


def _prepare_weeks(
    con: duckdb.DuckDBPyConnection, scoring: dict[str, float], roster_positions: list[str], n_teams: int,
    seasons: list[int], weeks: list[int],
) -> list[WeekContext]:
    prepared: list[WeekContext] = []
    for season in seasons:
        ctx = load_season_context(con, season, scoring)
        for week in weeks:
            features = week_features(ctx, week, roster_positions, n_teams)
            games = _games_per_player(_player_teams(ctx, week), remaining_games(ctx.schedules, ctx.season, week))
            actual = actual_ros(ctx.scored, ctx.season, week)
            starters = starters_per_position(
                roster_positions, n_teams, ctx.prev_seasonal.select("player_id", "position", "ppg")
            )
            market = market_prediction(consensus_snapshot(con, ctx.season, week), ctx.hub, ctx.prev_curve, games)
            market_eval = evaluate(market, actual, starters).with_columns(pl.lit("consensus").alias("source"))
            prepared.append(
                WeekContext(
                    ctx=ctx, week=week, features=features, games=games, actual=actual, starters=starters,
                    universe=market.select("player_id"), market_eval=market_eval,
                )
            )
    return prepared


def _eval_params(wc: WeekContext, params: BlendParams) -> pl.DataFrame:
    model = model_prediction(blend(wc.features, params), wc.games)
    model_eval = evaluate(
        model.join(wc.universe, on="player_id", how="inner"), wc.actual, wc.starters
    ).with_columns(pl.lit("model").alias("source"))
    return pl.concat([model_eval, wc.market_eval]).with_columns(
        pl.lit(wc.ctx.season).alias("season"), pl.lit(wc.week).alias("week")
    )


def run_backtest(
    con: duckdb.DuckDBPyConnection, scoring: dict[str, float], roster_positions: list[str], n_teams: int,
    seasons: list[int], weeks: list[int], params: BlendParams,
) -> pl.DataFrame:
    prepared = _prepare_weeks(con, scoring, roster_positions, n_teams, seasons, weeks)
    frames = [_eval_params(wc, params) for wc in prepared]
    return pl.concat(frames).select("season", "week", "source", "position", "spearman", "mae", "precision", "n")


def _score(metrics: pl.DataFrame) -> float:
    pooled = metrics.filter((pl.col("position") == "ALL") & (pl.col("source") == "model"))
    return float(pooled["mae"].mean()) - 10.0 * float(pooled["spearman"].mean())


def tune(
    con: duckdb.DuckDBPyConnection, scoring: dict[str, float], roster_positions: list[str], n_teams: int,
    seasons: list[int], weeks: list[int], grid: list[BlendParams] = DEFAULT_GRID,
) -> BlendParams:
    prepared = _prepare_weeks(con, scoring, roster_positions, n_teams, seasons, weeks)
    best: tuple[float, BlendParams] | None = None
    for params in grid:
        frames = [_eval_params(wc, params) for wc in prepared]
        score = _score(pl.concat(frames))
        if best is None or score < best[0]:
            best = (score, params)
    if best is None:
        raise ValueError("empty grid")
    return best[1]


def gate_passes(metrics: pl.DataFrame, eval_seasons: list[int]) -> bool:
    pooled = metrics.filter((pl.col("position") == "ALL") & pl.col("season").is_in(eval_seasons))
    by_source = pooled.group_by("source").agg(pl.col("spearman").mean(), pl.col("mae").mean())
    rows = {str(r["source"]): r for r in by_source.iter_rows(named=True)}
    if "model" not in rows or "consensus" not in rows:
        return False
    return rows["model"]["spearman"] >= rows["consensus"]["spearman"] and rows["model"]["mae"] <= rows["consensus"]["mae"]


def _table(frame: pl.DataFrame) -> str:
    header = "| " + " | ".join(frame.columns) + " |"
    sep = "|" + "---|" * len(frame.columns)
    body = ["| " + " | ".join(f"{v:.3f}" if isinstance(v, float) else str(v) for v in row) + " |" for row in frame.iter_rows()]
    return "\n".join([header, sep, *body])


def write_report(metrics: pl.DataFrame, params: BlendParams, path: Path, passed: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    eval_seasons = sorted(metrics["season"].unique().to_list())
    pooled = (
        metrics.filter(pl.col("position") == "ALL")
        .group_by("season", "source").agg(pl.col("spearman").mean(), pl.col("mae").mean(), pl.col("precision").mean())
        .sort("season", "source")
    )
    by_pos = (
        metrics.filter(pl.col("position") != "ALL")
        .group_by("position", "source").agg(pl.col("spearman").mean(), pl.col("mae").mean(), pl.col("precision").mean())
        .sort("position", "source")
    )
    bucket = pl.when(pl.col("week") <= 5).then(pl.lit("w2-5")).when(pl.col("week") <= 9).then(pl.lit("w6-9")).otherwise(pl.lit("w10-14"))
    by_bucket = (
        metrics.filter(pl.col("position") == "ALL").with_columns(bucket.alias("bucket"))
        .group_by("bucket", "source").agg(pl.col("spearman").mean(), pl.col("mae").mean())
        .sort("bucket", "source")
    )
    verdict = "PASS" if passed else "FAIL"
    text = "\n\n".join([
        "# Backtest report",
        f"Seasons: {', '.join(str(s) for s in eval_seasons)}. Params: k={params.k}, a={params.a}, b={params.b}.",
        f"Gate: **{verdict}** (model must match or beat consensus on pooled Spearman and MAE).",
        "## Pooled by season", _table(pooled),
        "## By position", _table(by_pos),
        "## By week bucket", _table(by_bucket),
    ])
    path.write_text(text + "\n")
