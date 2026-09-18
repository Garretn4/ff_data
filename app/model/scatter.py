from __future__ import annotations

import polars as pl

GAP_THRESHOLD = 15.0


def _pct(col: str) -> pl.Expr:
    return (pl.col(col).rank("average") - 1) / (pl.len() - 1).clip(lower_bound=1) * 100.0


def scatter_points(values: pl.DataFrame) -> pl.DataFrame:
    with_market = values.filter(pl.col("market_value").is_not_null() & pl.col("intrinsic").is_not_null())
    return with_market.with_columns(_pct("market_value").alias("market_pct"), _pct("intrinsic").alias("intrinsic_pct")).with_columns(
        (pl.col("intrinsic_pct") - pl.col("market_pct")).alias("gap")
    ).select("sleeper_id", "name", "position", "roster_id", "market_pct", "intrinsic_pct", "gap")


def quadrant_labels(points: pl.DataFrame) -> dict[str, list[str]]:
    ordered = points.with_columns(pl.col("gap").abs().alias("mag")).sort("mag", descending=True)
    return {
        "buy_low": ordered.filter(pl.col("gap") >= GAP_THRESHOLD)["name"].head(10).to_list(),
        "sell_high": ordered.filter(pl.col("gap") <= -GAP_THRESHOLD)["name"].head(10).to_list(),
    }
