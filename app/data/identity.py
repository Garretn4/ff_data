"""One hub frame keyed by gsis_id that carries every other provider's id."""
from __future__ import annotations

import re

import duckdb
import polars as pl

_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}
_NON_ALNUM = re.compile(r"[^a-z0-9 ]")


def normalize_name(name: str) -> str:
    cleaned = _NON_ALNUM.sub("", name.lower().replace("-", " "))
    parts = [p for p in cleaned.split() if p not in _SUFFIXES]
    return " ".join(parts)


def _as_str(col: str) -> pl.Expr:
    return pl.col(col).cast(pl.Int64, strict=False).cast(pl.Utf8)


def id_hub(playerids: pl.DataFrame) -> pl.DataFrame:
    return (
        playerids.filter(pl.col("gsis_id").is_not_null())
        .select(
            pl.col("gsis_id").cast(pl.Utf8),
            _as_str("sleeper_id").alias("sleeper_id"),
            _as_str("fantasypros_id").alias("fantasypros_id"),
            pl.col("pfr_id").cast(pl.Utf8),
            pl.col("name").cast(pl.Utf8),
            pl.col("name").cast(pl.Utf8).map_elements(normalize_name, return_dtype=pl.Utf8).alias("norm_name"),
            pl.col("position").cast(pl.Utf8),
            pl.col("team").cast(pl.Utf8),
            pl.col("birthdate").cast(pl.Utf8).str.to_date(strict=False).alias("birthdate"),
        )
    )


def match_by_name(
    rows: pl.DataFrame, hub: pl.DataFrame, name_col: str, position_col: str
) -> tuple[pl.DataFrame, pl.DataFrame]:
    keyed = rows.with_columns(
        pl.col(name_col).cast(pl.Utf8).map_elements(normalize_name, return_dtype=pl.Utf8).alias("norm_name")
    )
    lookup = hub.select("norm_name", "position", "gsis_id").unique(subset=["norm_name", "position"], keep="first")
    joined = keyed.join(lookup, left_on=["norm_name", position_col], right_on=["norm_name", "position"], how="left")
    matched = joined.filter(pl.col("gsis_id").is_not_null()).drop("norm_name")
    unmatched = joined.filter(pl.col("gsis_id").is_null()).drop("norm_name", "gsis_id")
    return matched, unmatched


def write_diagnostics(
    con: duckdb.DuckDBPyConnection,
    source: str,
    unmatched: pl.DataFrame,
    name_col: str,
    position_col: str,
    reason: str,
) -> int:
    con.execute(
        "CREATE TABLE IF NOT EXISTS identity_diagnostics "
        "(source VARCHAR, key VARCHAR, name VARCHAR, position VARCHAR, reason VARCHAR)"
    )
    frame = unmatched.select(
        pl.lit(source).alias("source"),
        pl.col(name_col).cast(pl.Utf8).alias("key"),
        pl.col(name_col).cast(pl.Utf8).alias("name"),
        pl.col(position_col).cast(pl.Utf8).alias("position"),
        pl.lit(reason).alias("reason"),
    )
    con.register("diag", frame)
    con.execute("INSERT INTO identity_diagnostics SELECT * FROM diag")
    con.unregister("diag")
    return frame.height
