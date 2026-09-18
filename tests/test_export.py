from pathlib import Path

import duckdb
import polars as pl

from app.data.export import export_practice
from app.data.nflverse import connect, replace_table


def test_export_writes_duckdb_parquet_csv_and_readme(tmp_path: Path) -> None:
    con = connect(tmp_path / "s.duckdb")
    replace_table(con, "player_stats", pl.DataFrame({"season": [2024, 2024], "week": [1, 2], "points": [1.5, 2.5]}))
    replace_table(con, "schedules", pl.DataFrame({"season": [2024], "week": [1], "gameday": ["2024-09-05"]}))
    out = tmp_path / "practice"
    counts = export_practice(con, out)
    assert counts == {"player_stats": 2, "schedules": 1}
    assert pl.read_parquet(out / "parquet" / "player_stats.parquet").height == 2
    assert pl.read_csv(out / "csv" / "schedules.csv").height == 1
    practice = duckdb.connect(str(out / "ff_practice.duckdb"), read_only=True)
    assert practice.execute("SELECT sum(points) FROM player_stats").fetchone()[0] == 4.0
    readme = (out / "README.md").read_text()
    assert "duckdb.connect" in readme and "dbConnect" in readme


def test_export_reruns_after_failure_and_when_file_exists(tmp_path: Path) -> None:
    con = connect(tmp_path / "s.duckdb")
    replace_table(con, "player_stats", pl.DataFrame({"season": [2024], "week": [1], "points": [1.5]}))
    out = tmp_path / "practice"
    counts = export_practice(con, out)
    assert counts == {"player_stats": 1}
    (out / "csv" / "player_stats.csv").unlink()
    (out / "csv" / "player_stats.csv").mkdir()
    try:
        export_practice(con, out)
        assert False, "Expected duckdb.Error"
    except duckdb.Error:
        pass
    (out / "csv" / "player_stats.csv").rmdir()
    counts_rerun = export_practice(con, out)
    assert counts_rerun == {"player_stats": 1}
