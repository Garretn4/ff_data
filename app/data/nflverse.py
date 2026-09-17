"""Load nflverse tables into DuckDB. Loaders are injectable so tests stay offline."""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Iterable, Mapping

import duckdb
import nflreadpy as nfl
import polars as pl

SeasonLoader = Callable[[list[int]], pl.DataFrame]
StaticLoader = Callable[[], pl.DataFrame]

SEASON_LOADERS: dict[str, SeasonLoader] = {
    "player_stats": lambda seasons: nfl.load_player_stats(seasons=seasons),
    "snap_counts": lambda seasons: nfl.load_snap_counts(seasons=seasons),
    "ff_opportunity": lambda seasons: nfl.load_ff_opportunity(seasons=seasons),
    "schedules": lambda seasons: nfl.load_schedules(seasons=seasons),
    "rosters": lambda seasons: nfl.load_rosters(seasons=seasons),
    "injuries": lambda seasons: nfl.load_injuries(seasons=seasons),
}

STATIC_LOADERS: dict[str, StaticLoader] = {
    "ff_playerids": lambda: nfl.load_ff_playerids(),
    "ff_rankings": lambda: nfl.load_ff_rankings(type="all"),
}

DATE_COLUMNS: dict[str, tuple[str, ...]] = {
    "schedules": ("gameday",),
    "ff_rankings": ("scrape_date",),
}


def connect(path: Path) -> duckdb.DuckDBPyConnection:
    path.parent.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(str(path))


def table_exists(con: duckdb.DuckDBPyConnection, table: str) -> bool:
    row = con.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_name = ?", [table]
    ).fetchone()
    return bool(row and row[0])


def loaded_seasons(con: duckdb.DuckDBPyConnection, table: str) -> list[int]:
    if not table_exists(con, table):
        return []
    rows = con.execute(f"SELECT DISTINCT season FROM {table} ORDER BY season").fetchall()
    return [int(r[0]) for r in rows]


def _cast_dates(table: str, df: pl.DataFrame) -> pl.DataFrame:
    cols = [c for c in DATE_COLUMNS.get(table, ()) if c in df.columns]
    return df.with_columns([pl.col(c).cast(pl.Utf8).str.to_date(strict=False) for c in cols])


def replace_table(con: duckdb.DuckDBPyConnection, table: str, df: pl.DataFrame) -> int:
    frame = _cast_dates(table, df)
    con.register("incoming", frame)
    con.execute(f"CREATE OR REPLACE TABLE {table} AS SELECT * FROM incoming")
    con.unregister("incoming")
    return frame.height


def replace_season_rows(
    con: duckdb.DuckDBPyConnection, table: str, df: pl.DataFrame, seasons: Iterable[int]
) -> int:
    frame = _cast_dates(table, df)
    if not table_exists(con, table):
        return replace_table(con, table, frame)
    season_list = list(seasons)
    con.execute(
        f"DELETE FROM {table} WHERE season IN ({', '.join('?' for _ in season_list)})", season_list
    )
    con.register("incoming", frame)
    con.execute(f"INSERT INTO {table} BY NAME SELECT * FROM incoming")
    con.unregister("incoming")
    return frame.height


def ingest_seasons(
    con: duckdb.DuckDBPyConnection,
    seasons: list[int],
    loaders: Mapping[str, SeasonLoader] = SEASON_LOADERS,
) -> dict[str, int]:
    return {table: replace_season_rows(con, table, load(seasons), seasons) for table, load in loaders.items()}


def ingest_static(
    con: duckdb.DuckDBPyConnection, loaders: Mapping[str, StaticLoader] = STATIC_LOADERS
) -> dict[str, int]:
    return {table: replace_table(con, table, load()) for table, load in loaders.items()}
