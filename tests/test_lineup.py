import polars as pl

from app.model.lineup import eligible, optimal_lineup, team_strength


def test_eligibility() -> None:
    assert eligible("FLEX", "RB") and eligible("FLEX", "TE") and not eligible("FLEX", "QB")
    assert eligible("SUPER_FLEX", "QB") and eligible("DEF", "DEF") and not eligible("K", "DEF")


def test_optimal_lineup_uses_flex_for_best_leftover() -> None:
    slots = ["RB", "WR", "FLEX"]
    players = [("r1", "RB", 20.0), ("r2", "RB", 15.0), ("w1", "WR", 10.0), ("w2", "WR", 14.0), ("t1", "TE", 5.0)]
    total, assignment = optimal_lineup(slots, players)
    assert total == 49.0
    assert set(assignment.values()) == {"r1", "r2", "w2"}


def test_optimal_lineup_skips_unfillable_slot() -> None:
    total, assignment = optimal_lineup(["QB", "RB"], [("r1", "RB", 9.0)])
    assert total == 9.0 and assignment == {"1": "r1"}


def test_two_for_one_only_counts_if_it_starts() -> None:
    slots = ["RB", "RB"]
    weekly = pl.DataFrame({"sleeper_id": ["a", "b", "c"] * 2, "position": ["RB"] * 6,
                           "week": [1, 1, 1, 2, 2, 2], "points": [20.0, 15.0, 5.0, 20.0, 15.0, 5.0]})
    assert team_strength(slots, weekly, {"a", "b"}) == 70.0
    assert team_strength(slots, weekly, {"a", "b", "c"}) == 70.0
    assert team_strength(slots, weekly, {"a", "c"}) == 50.0
