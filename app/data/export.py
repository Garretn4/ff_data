"""Write a practice copy of the analytics store for Python and R sessions."""
from __future__ import annotations

from pathlib import Path

import duckdb

_README = """# ff-analyst practice data

Tables: {tables}

## Python

```python
import duckdb
con = duckdb.connect("ff_practice.duckdb", read_only=True)
con.sql("SELECT * FROM player_stats LIMIT 5").show()

import polars as pl
pl.read_parquet("parquet/player_stats.parquet")

import pandas as pd
pd.read_csv("csv/player_stats.csv")
```

## R

```r
library(DBI); library(duckdb)
con <- dbConnect(duckdb::duckdb(), "ff_practice.duckdb", read_only = TRUE)
dbGetQuery(con, "SELECT * FROM player_stats LIMIT 5")

library(arrow)
read_parquet("parquet/player_stats.parquet")

library(readr)
read_csv("csv/player_stats.csv")
```
"""


def _tables(con: duckdb.DuckDBPyConnection) -> list[str]:
    rows = con.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main' ORDER BY table_name"
    ).fetchall()
    return [str(r[0]) for r in rows]


def _sql_path(path: Path) -> str:
    return str(path).replace("'", "''")


def _copy_tables(con: duckdb.DuckDBPyConnection, out_dir: Path) -> dict[str, int]:
    counts: dict[str, int] = {}
    for table in _tables(con):
        con.execute(f"CREATE TABLE practice.{table} AS SELECT * FROM main.{table}")
        con.execute(f"COPY main.{table} TO '{_sql_path(out_dir / 'parquet' / f'{table}.parquet')}' (FORMAT PARQUET)")
        con.execute(f"COPY main.{table} TO '{_sql_path(out_dir / 'csv' / f'{table}.csv')}' (FORMAT CSV, HEADER)")
        counts[table] = int(con.execute(f"SELECT count(*) FROM main.{table}").fetchone()[0])
    return counts


def export_practice(con: duckdb.DuckDBPyConnection, out_dir: Path) -> dict[str, int]:
    (out_dir / "parquet").mkdir(parents=True, exist_ok=True)
    (out_dir / "csv").mkdir(parents=True, exist_ok=True)
    practice_path = out_dir / "ff_practice.duckdb"
    if practice_path.exists():
        practice_path.unlink()
    con.execute(f"ATTACH '{_sql_path(practice_path)}' AS practice")
    try:
        counts = _copy_tables(con, out_dir)
    finally:
        con.execute("DETACH practice")
    (out_dir / "README.md").write_text(_README.format(tables=", ".join(counts)))
    return counts
