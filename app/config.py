"""Runtime configuration: league id, paths, season range."""
from __future__ import annotations

import tomllib
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Mapping

STATS_FROM = 2012
DETAIL_FROM = 2019


def current_nfl_season(today: date) -> int:
    """NFL season year: September onward belongs to the new season."""
    return today.year if today.month >= 9 else today.year - 1


@dataclass(frozen=True)
class Config:
    league_id: str
    root: Path
    stats_from: int
    detail_from: int
    current_season: int

    @property
    def cache_dir(self) -> Path:
        return self.root / "cache"

    @property
    def duckdb_path(self) -> Path:
        return self.cache_dir / "stats.duckdb"

    @property
    def sqlite_path(self) -> Path:
        return self.cache_dir / "app.sqlite"

    @property
    def stats_seasons(self) -> list[int]:
        return list(range(self.stats_from, self.current_season + 1))

    @property
    def detail_seasons(self) -> list[int]:
        return list(range(self.detail_from, self.current_season + 1))


def _read_toml(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    with path.open("rb") as fh:
        return tomllib.load(fh)


def load_config(root: Path, env: Mapping[str, str], today: date | None = None) -> Config:
    """Read config.local.toml under root; FF_LEAGUE_ID in env overrides the file."""
    data = _read_toml(root / "config.local.toml")
    league_id = str(env.get("FF_LEAGUE_ID") or data.get("league_id", ""))
    season = current_nfl_season(today or date.today())
    return Config(
        league_id=league_id,
        root=root,
        stats_from=int(data.get("stats_from", STATS_FROM)),
        detail_from=int(data.get("detail_from", DETAIL_FROM)),
        current_season=season,
    )
