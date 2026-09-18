"""App-wide state: connections, league context, projections, values, and refresh."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

import duckdb
import httpx
import polars as pl

from app.config import Config, load_config
from app.data.nflverse import connect, table_exists
from app.data.store import Store
from app.model.league import LeagueContext, load_league_context
from app.model.live import SOURCES, Projection, project
from app.model.values import player_values
from app.refresh import RefreshSummary, refresh as run_refresh
from app.sleeper import SleeperClient

DEFAULT_SOURCE = "model"
STALE_AFTER = timedelta(hours=1)
ROOT = Path(__file__).resolve().parent.parent.parent
LOCK_ERROR_MARKER = "Could not set lock"


@dataclass
class AppState:
    cfg: Config = field(default_factory=lambda: load_config(ROOT, os.environ))
    store: Store | None = None
    con: duckdb.DuckDBPyConnection | None = None
    ctx: LeagueContext | None = None
    projections: dict[str, Projection] = field(default_factory=dict)
    values: pl.DataFrame = field(default_factory=pl.DataFrame)
    source: str = DEFAULT_SOURCE
    last_refresh: datetime | None = None
    error: str | None = None
    refreshing: bool = False

    def _connect(self) -> tuple[Store, duckdb.DuckDBPyConnection]:
        if self.store is None:
            self.store = Store(self.cfg.sqlite_path)
        if self.con is None:
            self.con = connect(self.cfg.duckdb_path)
        return self.store, self.con

    def load(self) -> None:
        """Rebuild the league context, both projection sources, and the value table."""
        store, con = self._connect()
        self.ctx = load_league_context(store, self.cfg.league_id)
        self.projections = {source: project(con, store, self.ctx, source) for source in SOURCES}
        self.values = player_values(self.ctx, con, store, self.primary(), self.secondary())
        self.last_refresh = self._league_refreshed_at()

    def refresh(self) -> RefreshSummary:
        """Pull fresh data from Sleeper and nflverse, then reload derived state."""
        store, con = self._connect()
        client = SleeperClient(store, refresh=True)
        summary = run_refresh(self.cfg, client, con, store, httpx.Client(timeout=30.0))
        self.load()
        return summary

    def primary(self) -> Projection:
        return self.projections[self.source]

    def secondary(self) -> Projection:
        other = SOURCES[0] if self.source == SOURCES[1] else SOURCES[1]
        return self.projections[other]

    def is_stale(self) -> bool:
        """True when the last Sleeper league fetch is missing or older than one hour."""
        last = self._league_refreshed_at()
        return last is None or datetime.now() - last > STALE_AFTER

    def has_cache(self) -> bool:
        """True when a cached league snapshot exists, regardless of staleness."""
        return self._league_refreshed_at() is not None

    def _league_refreshed_at(self) -> datetime | None:
        _, con = self._connect()
        if not table_exists(con, "league_meta"):
            return None
        row = con.execute("SELECT refreshed_at FROM league_meta ORDER BY refreshed_at DESC LIMIT 1").fetchone()
        return datetime.fromisoformat(row[0]) if row else None

    def my_roster_id(self) -> int:
        """Return the roster whose display_name equals cfg.username (case-insensitive), else the first roster's id."""
        if self.ctx is None or not self.ctx.rosters:
            return 1
        if self.cfg.username:
            for roster in self.ctx.rosters:
                if roster.display_name.lower() == self.cfg.username.lower():
                    return roster.roster_id
        return self.ctx.rosters[0].roster_id


def startup_plan(has_cache: bool, stale: bool) -> tuple[bool, bool]:
    """(load_now, refresh_in_background) for the startup sequence.

    A cached league loads immediately so screens render right away; a refresh
    runs in the background whenever the cache is stale or missing entirely.
    """
    return has_cache, stale or not has_cache


def friendly_error(msg: str) -> str:
    """Rewrite the DuckDB cross-process lock error into an actionable message."""
    if LOCK_ERROR_MARKER in msg:
        return "another ff-analyst instance is already running; close it and press Retry"
    return msg


STATE = AppState()


def get_state() -> AppState:
    return STATE
