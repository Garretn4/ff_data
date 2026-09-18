from pathlib import Path

import duckdb
import polars as pl
import pytest

from app.model.backtest import (
    SeasonContext, WeekContext, _eval_params, _games_per_player, _player_teams,
    actual_ros, check_coverage, evaluate, gate_passes, mae, model_prediction, precision_at, remaining_games,
    spearman, write_report,
)
from app.model.projections import BlendParams


def test_spearman_and_mae_on_known_values() -> None:
    assert spearman(pl.Series([1.0, 2.0, 3.0]), pl.Series([10.0, 20.0, 30.0])) == 1.0
    assert spearman(pl.Series([1.0, 2.0, 3.0]), pl.Series([30.0, 20.0, 10.0])) == -1.0
    assert mae(pl.Series([1.0, 2.0]), pl.Series([2.0, 4.0])) == 1.5


def test_precision_at_top_n() -> None:
    frame = pl.DataFrame({"player_id": ["a", "b", "c", "d"], "pred": [10.0, 9.0, 8.0, 1.0],
                          "actual": [100.0, 1.0, 90.0, 80.0]})
    assert precision_at(frame, "pred", "actual", 2) == 0.5


def test_actual_ros_sums_regular_weeks_from_start() -> None:
    scored = pl.DataFrame({
        "player_id": ["a"] * 4, "season": [2024] * 4, "week": [3, 4, 5, 19],
        "season_type": ["REG", "REG", "REG", "POST"], "points": [1.0, 2.0, 4.0, 100.0],
    })
    assert actual_ros(scored, 2024, 4)["ros_points"][0] == 6.0


def test_remaining_games_counts_reg_games_per_team() -> None:
    sched = pl.DataFrame({
        "season": [2024] * 3, "week": [1, 2, 3], "game_type": ["REG"] * 3,
        "home_team": ["A", "A", "C"], "away_team": ["B", "C", "B"],
    })
    out = remaining_games(sched, 2024, 2)
    assert dict(zip(out["team"], out["games"])) == {"A": 1, "B": 1, "C": 2}


def test_evaluate_reports_positions_and_pooled() -> None:
    pred = pl.DataFrame({"player_id": ["a", "b", "c", "d"], "position": ["RB", "RB", "WR", "WR"], "pred": [10.0, 5.0, 8.0, 2.0]})
    actual = pl.DataFrame({"player_id": ["a", "b", "c", "d"], "ros_points": [12.0, 4.0, 9.0, 1.0]})
    out = evaluate(pred, actual, {"RB": 1, "WR": 1})
    assert set(out["position"].to_list()) == {"RB", "WR", "ALL"}
    pooled = out.filter(pl.col("position") == "ALL").row(0, named=True)
    assert pooled["spearman"] == 1.0 and pooled["precision"] == 1.0 and pooled["n"] == 4


def test_gate_compares_pooled_model_to_consensus() -> None:
    metrics = pl.DataFrame({
        "season": [2024, 2024, 2025, 2025], "week": [3] * 4,
        "source": ["model", "consensus", "model", "consensus"], "position": ["ALL"] * 4,
        "spearman": [0.7, 0.6, 0.7, 0.6], "mae": [30.0, 35.0, 31.0, 35.0], "precision": [0.5] * 4, "n": [100] * 4,
    })
    assert gate_passes(metrics, [2024, 2025])
    worse = metrics.with_columns(pl.when(pl.col("source") == "model").then(50.0).otherwise(pl.col("mae")).alias("mae"))
    assert not gate_passes(worse, [2024, 2025])


def test_write_report_renders_tables(tmp_path: Path) -> None:
    rows = [
        {"season": season, "week": week, "source": source, "position": position,
         "spearman": 0.6, "mae": 20.0, "precision": 0.4, "n": 50}
        for season in (2024, 2025)
        for week in (3, 8)
        for source in ("model", "consensus")
        for position in ("RB", "ALL")
    ]
    metrics = pl.DataFrame(rows)
    path = tmp_path / "REPORT.md"
    write_report(metrics, BlendParams(), path, passed=True)
    text = path.read_text()
    assert "Gate: **PASS**" in text
    assert "## Pooled by season" in text
    assert "## By position" in text
    assert "## By week bucket" in text
    assert "| n |" in text


def test_games_per_player_uses_median_for_unknown_team() -> None:
    teams = pl.DataFrame(
        {"player_id": ["p1", "p2"], "team": ["A", None]}, schema={"player_id": pl.Utf8, "team": pl.Utf8}
    )
    per_team = pl.DataFrame({"team": ["A", "B", "C"], "games": [3, 5, 7]})
    out = _games_per_player(teams, per_team)
    result = dict(zip(out["player_id"], out["games"]))
    assert result["p1"] == 3
    assert result["p2"] == 5


def test_player_teams_prefers_current_then_previous_season() -> None:
    empty = pl.DataFrame()
    ctx = SeasonContext(
        season=2024,
        scored=pl.DataFrame({"player_id": ["p1", "p1"], "week": [1, 2], "team": ["X", "Y"]}),
        prev_seasonal=empty,
        prev_curve=empty,
        opportunity=empty,
        hub=pl.DataFrame({"gsis_id": ["p1", "p2", "p3"]}),
        consensus_preseason=empty,
        age_curve=empty,
        ages=empty,
        schedules=empty,
        prev_teams=pl.DataFrame({"player_id": ["p2"], "team": ["Z"]}),
        rosters_weekly=empty,
        injuries=empty,
    )
    out = _player_teams(ctx, 3)
    result = dict(zip(out["player_id"], out["team"]))
    assert result["p1"] == "Y"
    assert result["p2"] == "Z"
    assert result["p3"] is None


def test_check_coverage_names_missing_season() -> None:
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE player_stats (player_id VARCHAR, season INTEGER, week INTEGER, season_type VARCHAR)")
    con.execute("INSERT INTO player_stats VALUES ('p1', 2024, 1, 'REG'), ('p2', 2024, 2, 'REG')")
    with pytest.raises(ValueError, match="2023") as exc_info:
        check_coverage(con, [2024], [3])
    assert "missing" in str(exc_info.value)


def test_model_prediction_multiplies_by_avail_ros() -> None:
    blended = pl.DataFrame({"player_id": ["a", "b"], "position": ["RB", "RB"], "ppg": [10.0, 10.0]})
    games = pl.DataFrame({"player_id": ["a", "b"], "games": [2, 2]})
    avail = pl.DataFrame({"player_id": ["a"], "avail_now": [1.0], "avail_ros": [0.5]})
    out = model_prediction(blended, games, avail).sort("player_id")
    result = dict(zip(out["player_id"], out["pred"]))
    assert result["a"] == 10.0
    assert result["b"] == 0.0


def test_eval_params_zeroes_prediction_for_unavailable_player() -> None:
    ctx = SeasonContext(
        season=2024,
        scored=pl.DataFrame(),
        prev_seasonal=pl.DataFrame(),
        prev_curve=pl.DataFrame(),
        opportunity=pl.DataFrame(),
        hub=pl.DataFrame(),
        consensus_preseason=pl.DataFrame(),
        age_curve=pl.DataFrame(),
        ages=pl.DataFrame(),
        schedules=pl.DataFrame(),
        prev_teams=pl.DataFrame(),
        rosters_weekly=pl.DataFrame(),
        injuries=pl.DataFrame(),
    )
    features = pl.DataFrame({
        "player_id": ["a", "b"], "position": ["RB", "RB"],
        "ecr_ppg": [10.0, 10.0], "last_ppg": [10.0, 10.0], "age_mult": [1.0, 1.0],
        "usage_ppg": [10.0, 10.0], "actual_ppg": [10.0, 10.0], "n": [4, 4],
    })
    games = pl.DataFrame({"player_id": ["a", "b"], "games": [2, 2]})
    actual = pl.DataFrame({"player_id": ["a", "b"], "ros_points": [20.0, 20.0]})
    universe = pl.DataFrame({"player_id": ["a", "b"]})
    market_eval = pl.DataFrame(schema={
        "position": pl.Utf8, "spearman": pl.Float64, "mae": pl.Float64, "precision": pl.Float64,
        "n": pl.Int64, "source": pl.Utf8,
    })
    avail = pl.DataFrame({"player_id": ["a", "b"], "avail_now": [1.0, 0.0], "avail_ros": [1.0, 0.0]})
    wc = WeekContext(
        ctx=ctx, week=5, features=features, games=games, actual=actual,
        starters={"RB": 1}, universe=universe, market_eval=market_eval, avail=avail,
    )
    out = _eval_params(wc, BlendParams(k=4.0, a=0.5, b=0.5))
    model_rows = out.filter(pl.col("source") == "model")
    assert model_rows.height == 2
    pooled_mae = model_rows.filter(pl.col("position") == "ALL")["mae"][0]
    assert pooled_mae == 10.0
