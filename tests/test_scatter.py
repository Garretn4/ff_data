import polars as pl

from app.model.scatter import quadrant_labels, scatter_points


def test_scatter_percentiles_and_quadrants() -> None:
    values = pl.DataFrame({"sleeper_id": ["a", "b", "c", "d"], "name": ["A", "B", "C", "D"], "position": ["RB"] * 4,
                           "roster_id": [1, 1, 2, None], "market_value": [100.0, 50.0, 10.0, None], "intrinsic": [5.0, 50.0, 40.0, 1.0]})
    pts = scatter_points(values).sort("sleeper_id")
    assert pts["sleeper_id"].to_list() == ["a", "b", "c"]
    assert pts.filter(pl.col("sleeper_id") == "a")["market_pct"][0] == 100.0
    labels = quadrant_labels(pts)
    assert "C" in labels["buy_low"] and "A" in labels["sell_high"]
