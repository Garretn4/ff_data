import polars as pl

from app.model.values import _trend_stats, replacement_ros


def test_trend_stats_percentiles_and_consistency() -> None:
    out = _trend_stats(pl.DataFrame({"sleeper_id": ["a"] * 5 + ["b"], "week": [1, 2, 3, 4, 5, 1],
                                     "points": [10.0, 20.0, 30.0, 40.0, 50.0, 9.0]})).sort("sleeper_id")
    a, b = out.row(0, named=True), out.row(1, named=True)
    assert a["trend"] == [10.0, 20.0, 30.0, 40.0, 50.0]
    assert a["floor"] == 18.0 and a["ceiling"] == 42.0
    assert round(a["consistency"], 3) == round((pl.Series([10.0, 20, 30, 40, 50]).std() / 30.0), 3)
    assert b["floor"] is None and b["consistency"] is None


def test_replacement_ros_is_best_unrostered() -> None:
    frame = pl.DataFrame({"sleeper_id": ["a", "b", "c"], "position": ["RB", "RB", "RB"],
                          "roster_id": [1, None, None], "ros_primary": [100.0, 60.0, 70.0]})
    assert replacement_ros(frame) == {"RB": 70.0}


def test_defense_name_fallback() -> None:
    frame = pl.DataFrame({
        "sleeper_id": ["sea", "dh1"],
        "nfl_team": ["SEA", "DH"],
        "name": [None, "Player Name"],
        "position": ["DEF", "RB"],
    })
    result = frame.with_columns(
        pl.coalesce(pl.col("name"), pl.concat_str([pl.col("nfl_team"), pl.lit(" DEF")])).alias("name")
    )
    assert result.row(0, named=True)["name"] == "SEA DEF"
    assert result.row(1, named=True)["name"] == "Player Name"
