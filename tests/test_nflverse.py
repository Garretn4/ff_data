from pathlib import Path

import polars as pl

from app.data.nflverse import (
    connect, ingest_seasons, ingest_static, loaded_seasons, replace_season_rows, replace_table, table_exists,
)


def stats_frame(season: int, n: int) -> pl.DataFrame:
    return pl.DataFrame({
        "player_id": [f"00-{i:07d}" for i in range(n)],
        "season": [season] * n,
        "week": list(range(1, n + 1)),
        "position": ["RB"] * n,
        "receiving_yards": [float(10 * i) for i in range(n)],
    })


def test_replace_season_rows_is_idempotent(tmp_path: Path) -> None:
    con = connect(tmp_path / "s.duckdb")
    assert replace_season_rows(con, "player_stats", stats_frame(2024, 3), [2024]) == 3
    assert replace_season_rows(con, "player_stats", stats_frame(2025, 2), [2025]) == 2
    assert replace_season_rows(con, "player_stats", stats_frame(2024, 4), [2024]) == 4
    assert loaded_seasons(con, "player_stats") == [2024, 2025]
    assert con.execute("SELECT count(*) FROM player_stats").fetchone()[0] == 6


def test_ingest_seasons_uses_injected_loaders_and_casts_dates(tmp_path: Path) -> None:
    con = connect(tmp_path / "s.duckdb")
    loaders = {
        "player_stats": lambda seasons: pl.concat([stats_frame(s, 2) for s in seasons]),
        "schedules": lambda seasons: pl.DataFrame({
            "season": seasons, "week": [1] * len(seasons), "game_type": ["REG"] * len(seasons),
            "home_team": ["DET"] * len(seasons), "away_team": ["MIN"] * len(seasons),
            "gameday": [f"{s}-09-07" for s in seasons],
        }),
    }
    counts = ingest_seasons(con, [2024, 2025], loaders=loaders)
    assert counts == {"player_stats": 4, "schedules": 2}
    dtype = con.execute("SELECT typeof(gameday) FROM schedules LIMIT 1").fetchone()[0]
    assert dtype == "DATE"


def test_ingest_static_replaces_whole_table(tmp_path: Path) -> None:
    con = connect(tmp_path / "s.duckdb")
    loaders = {
        "ff_rankings": lambda: pl.DataFrame({
            "id": ["1", "2"], "page_type": ["redraft-overall"] * 2, "ecr": [1.0, 2.0],
            "pos": ["RB", "WR"], "scrape_date": ["2024-09-01", "2024-09-01"],
        }),
        "ff_playerids": lambda: pl.DataFrame({"gsis_id": ["00-1"], "sleeper_id": [4046]}),
    }
    ingest_static(con, loaders=loaders)
    ingest_static(con, loaders=loaders)
    assert con.execute("SELECT count(*) FROM ff_rankings").fetchone()[0] == 2
    assert con.execute("SELECT typeof(scrape_date) FROM ff_rankings LIMIT 1").fetchone()[0] == "DATE"
    assert table_exists(con, "ff_playerids")
    assert not table_exists(con, "nope")


def test_season_and_week_are_normalized_to_int(tmp_path: Path) -> None:
    con = connect(tmp_path / "s.duckdb")
    df = pl.DataFrame({
        "season": ["2024", "2025"],
        "week": [1.0, 2.0],
        "other": ["a", "b"],
    })
    replace_table(con, "test_table", df)
    assert con.execute("SELECT typeof(season) FROM test_table LIMIT 1").fetchone()[0] == "BIGINT"
    assert con.execute("SELECT typeof(week) FROM test_table LIMIT 1").fetchone()[0] == "BIGINT"
    assert loaded_seasons(con, "test_table") == [2024, 2025]
    df2 = pl.DataFrame({
        "season": ["2024"],
        "week": [3.0],
        "other": ["c"],
    })
    assert replace_season_rows(con, "test_table", df2, ["2024"]) == 1
    assert con.execute("SELECT count(*) FROM test_table").fetchone()[0] == 2
