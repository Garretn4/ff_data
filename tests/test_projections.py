import polars as pl

from app.model.projections import (
    BlendParams, blend, curve_ppg, opponent_factors, positional_ranks, rank_to_ppg_curve,
    weekly_projection,
)


def test_rank_curve_orders_within_position_and_clamps() -> None:
    seasonal = pl.DataFrame({
        "player_id": ["a", "b", "c", "d"], "season": [2024] * 4, "position": ["RB", "RB", "WR", "RB"],
        "games": [16, 16, 16, 3], "ppg": [10.0, 15.0, 12.0, 30.0],
    })
    curve = rank_to_ppg_curve(seasonal, 2024)
    assert curve.filter(pl.col("position") == "RB")["ppg"].to_list() == [15.0, 10.0]
    assert curve_ppg(curve, "RB", 1) == 15.0
    assert curve_ppg(curve, "RB", 50) == 10.0
    assert curve_ppg(curve, "TE", 1) == 0.0


def test_positional_ranks_and_dst_alias() -> None:
    snap = pl.DataFrame({"fantasypros_id": ["1", "2", "3"], "pos": ["WR", "DST", "WR"], "ecr": [5.0, 90.0, 2.0]})
    out = positional_ranks(snap).sort("fantasypros_id")
    assert out["pos_rank"].to_list() == [2, 1, 1]
    assert out["pos"].to_list() == ["WR", "DEF", "WR"]


def test_blend_returns_prior_at_zero_games_and_data_at_many() -> None:
    feats = pl.DataFrame({
        "player_id": ["new", "vet"], "position": ["RB", "RB"],
        "ecr_ppg": [12.0, 12.0], "last_ppg": [8.0, 8.0], "age_mult": [1.0, 0.9],
        "usage_ppg": [None, 20.0], "actual_ppg": [None, 16.0], "n": [0, 1000],
    })
    out = blend(feats, BlendParams(k=4.0, a=0.5, b=0.5)).sort("player_id")
    new, vet = out.row(0, named=True), out.row(1, named=True)
    assert new["prior"] == 10.0 and new["ppg"] == 10.0
    assert vet["prior"] == 9.0 and abs(vet["ppg"] - 18.0) < 0.05


def test_blend_uses_actual_when_usage_missing() -> None:
    feats = pl.DataFrame({
        "player_id": ["x"], "position": ["WR"], "ecr_ppg": [10.0], "last_ppg": [10.0], "age_mult": [1.0],
        "usage_ppg": [None], "actual_ppg": [20.0], "n": [4],
    })
    out = blend(feats, BlendParams(k=4.0, a=0.5, b=0.5))
    assert out["ppg"][0] == 15.0


def test_opponent_factors_are_shrunk_and_clamped() -> None:
    rows = []
    for week in range(1, 7):
        rows += [
            {"season": 2025, "week": week, "position": "WR", "team": "A", "opponent_team": "SOFT", "points": 30.0},
            {"season": 2025, "week": week, "position": "WR", "team": "B", "opponent_team": "HARD", "points": 5.0},
            {"season": 2025, "week": week, "position": "WR", "team": "C", "opponent_team": "AVG", "points": 17.5},
        ]
    factors = opponent_factors(pl.DataFrame(rows), through_week=6)
    f = dict(zip(factors["defense"], factors["factor"]))
    assert f["SOFT"] == 1.1 and f["HARD"] == 0.9 and abs(f["AVG"] - 1.0) < 1e-9


def test_weekly_projection_handles_bye_injury_and_factor() -> None:
    ppg = pl.DataFrame({"player_id": ["p", "q"], "position": ["WR", "WR"], "ppg": [10.0, 10.0]})
    teams = pl.DataFrame({"player_id": ["p", "q"], "team": ["A", "B"]})
    schedule = pl.DataFrame({"week": [5, 6, 5], "team": ["A", "A", "B"], "opponent": ["X", "Y", "X"]})
    factors = pl.DataFrame({"defense": ["X", "Y"], "position": ["WR", "WR"], "factor": [1.1, 0.9]})
    injured = pl.DataFrame({"player_id": ["q"], "status": ["Out"]})
    out = weekly_projection(ppg, schedule, 5, 6, teams, factors, injured).sort("player_id", "week")
    assert out.filter(pl.col("player_id") == "p")["points"].to_list() == [11.0, 9.0]
    assert out.filter(pl.col("player_id") == "q")["points"].to_list() == [0.0, 0.0]
