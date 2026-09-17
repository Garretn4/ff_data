from datetime import date
from pathlib import Path

from app.config import Config, current_nfl_season, load_config


def test_current_nfl_season_rolls_in_september() -> None:
    assert current_nfl_season(date(2026, 9, 16)) == 2026
    assert current_nfl_season(date(2026, 3, 1)) == 2025


def test_load_config_reads_toml_and_env_override(tmp_path: Path) -> None:
    (tmp_path / "config.local.toml").write_text('league_id = "111"\n')
    cfg = load_config(tmp_path, env={})
    assert cfg.league_id == "111"
    assert cfg.cache_dir == tmp_path / "cache"
    assert cfg.duckdb_path == tmp_path / "cache" / "stats.duckdb"
    assert cfg.sqlite_path == tmp_path / "cache" / "app.sqlite"
    over = load_config(tmp_path, env={"FF_LEAGUE_ID": "222"})
    assert over.league_id == "222"


def test_load_config_without_file_has_empty_league(tmp_path: Path) -> None:
    cfg = load_config(tmp_path, env={})
    assert cfg.league_id == ""
    assert cfg.stats_from == 2012
    assert cfg.detail_from == 2019
