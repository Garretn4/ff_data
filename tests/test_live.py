import polars as pl

from app.model.live import _apply_weeks, _universe, ros_totals


def test_universe_includes_all_def_and_active_kickers() -> None:
    players = pl.DataFrame({
        "sleeper_id": ["p1", "p2", "def1", "def2", "k1", "k2"],
        "position": ["WR", "RB", "DEF", "DEF", "K", "K"],
        "nfl_team": ["DET", "SF", "SEA", "KC", "DAL", None],
        "gsis_id": ["g1", None, None, None, "gk1", "gk2"],
    })
    universe = _universe(players)
    assert set(universe["sleeper_id"].to_list()) == {"p1", "def1", "def2", "k1"}


def test_apply_weeks_uses_now_then_ros_and_byes() -> None:
    ppg = pl.DataFrame({"sleeper_id": ["s1"], "gsis_id": ["g1"], "position": ["WR"], "nfl_team": ["DET"],
                        "ppg": [10.0], "avail_now": [0.0], "avail_ros": [0.85]})
    opponents = pl.DataFrame({"week": [3, 5], "team": ["DET", "DET"], "opponent": ["MIN", "GB"]})
    factors = pl.DataFrame({"defense": ["MIN", "GB"], "position": ["WR", "WR"], "factor": [1.1, 0.9]})
    weekly = _apply_weeks(ppg, opponents, factors, week=3, weeks=[3, 4, 5]).sort("week")
    assert weekly["points"].to_list() == [0.0, 0.0, 7.65]


def test_ros_totals_sums_weeks() -> None:
    weekly = pl.DataFrame({"sleeper_id": ["s1", "s1", "s2"], "gsis_id": ["g1", "g1", None], "position": ["WR", "WR", "DEF"],
                           "week": [3, 4, 3], "points": [5.0, 6.0, 8.0]})
    from app.model.live import Projection
    out = ros_totals(Projection("consensus", weekly, pl.DataFrame())).sort("sleeper_id")
    assert out.rows() == [("s1", "WR", 11.0), ("s2", "DEF", 8.0)]
