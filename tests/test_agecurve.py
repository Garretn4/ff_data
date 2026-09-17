from datetime import date

import polars as pl

from app.model.agecurve import age_factor, fit_age_curve, player_ages, season_ppg


def test_season_ppg_uses_regular_season_only() -> None:
    scored = pl.DataFrame({
        "player_id": ["a"] * 3, "season": [2024] * 3, "week": [1, 2, 19],
        "season_type": ["REG", "REG", "POST"], "position": ["RB"] * 3, "points": [10.0, 20.0, 50.0],
    })
    out = season_ppg(scored)
    assert out.row(0, named=True) == {"player_id": "a", "season": 2024, "position": "RB", "games": 2, "ppg": 15.0}


def test_fit_age_curve_recovers_decline_and_clamps() -> None:
    rows = []
    for i in range(40):
        rows.append({"player_id": f"p{i}", "season": 2022, "position": "RB", "games": 16, "ppg": 10.0})
        rows.append({"player_id": f"p{i}", "season": 2023, "position": "RB", "games": 16, "ppg": 8.0})
        rows.append({"player_id": f"y{i}", "season": 2022, "position": "RB", "games": 16, "ppg": 10.0})
        rows.append({"player_id": f"y{i}", "season": 2023, "position": "RB", "games": 16, "ppg": 14.0})
    seasonal = pl.DataFrame(rows)
    ages = pl.DataFrame({
        "player_id": [f"p{i}" for i in range(40)] * 2 + [f"y{i}" for i in range(40)] * 2,
        "season": [2022] * 40 + [2023] * 40 + [2022] * 40 + [2023] * 40,
        "age": [30] * 40 + [31] * 40 + [23] * 40 + [24] * 40,
    })
    curve = fit_age_curve(seasonal, ages)
    assert age_factor(curve, "RB", 31) == 0.8
    assert age_factor(curve, "RB", 24) == 1.1
    assert age_factor(curve, "RB", 40) == 1.0
    assert age_factor(curve, "WR", 24) == 1.0


def test_player_ages_from_birthdate() -> None:
    hub = pl.DataFrame({"gsis_id": ["00-1", "00-2"], "birthdate": [date(1995, 9, 17), None]})
    out = player_ages(hub, [2024, 2025])
    assert out.filter(pl.col("player_id") == "00-1").sort("season")["age"].to_list() == [29, 30]
    assert out.filter(pl.col("player_id") == "00-2").height == 0
