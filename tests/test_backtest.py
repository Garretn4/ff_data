from pathlib import Path

import polars as pl

from app.model.backtest import (
    actual_ros, evaluate, gate_passes, mae, precision_at, remaining_games, spearman, write_report,
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
