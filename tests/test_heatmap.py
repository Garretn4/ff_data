import polars as pl

from app.model.heatmap import position_strength, surplus
from app.model.league import LeagueContext, Roster
from app.model.live import Projection


def test_position_strength_attributes_flex_to_own_position() -> None:
    a = Roster(1, "u1", "A", "A", None, ("r1", "r2", "w1"), (), ())
    b = Roster(2, "u2", "B", "B", None, ("r3", "w2", "w3"), (), ())
    ctx = LeagueContext("1", "t", 2026, 3, 2, {}, ["RB", "WR", "FLEX", "BN"], ["RB", "WR", "FLEX"], 15, 3, 12, (a, b))
    weekly = pl.DataFrame({"sleeper_id": ["r1", "r2", "w1", "r3", "w2", "w3"], "position": ["RB", "RB", "WR", "RB", "WR", "WR"],
                           "week": [3] * 6, "points": [20.0, 15.0, 10.0, 12.0, 18.0, 16.0]})
    strength = position_strength(ctx, Projection("consensus", weekly, pl.DataFrame())).sort("roster_id", "position")
    assert strength.filter(pl.col("roster_id") == 1)["points"].to_list() == [35.0, 10.0]
    assert strength.filter(pl.col("roster_id") == 2)["points"].to_list() == [12.0, 34.0]
    out = surplus(strength)
    assert out.filter((pl.col("roster_id") == 1) & (pl.col("position") == "RB"))["surplus"][0] == 35.0 - 23.5
