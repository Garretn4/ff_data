"""App-wide state: connections, one LeagueState per loaded league, the active league, and refresh."""
from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from pathlib import Path

import duckdb
import httpx
import polars as pl

from app.config import Config, load_config, save_config
from app.data.nflverse import connect
from app.data.store import Store
from app.model.league import LeagueContext, load_league_context
from app.model.leagues import LeagueSummary, discover, load_summaries
from app.model.live import SOURCES, Projection, project
from app.model.standings import Matchups, load_matchups, team_week_projections
from app.model.values import player_values
from app.refresh import RefreshSummary, refresh as run_refresh
from app.sleeper import SleeperClient

SCOPE_ORDER: tuple[str, ...] = ("league", "history", "model", "source")
STALE_AFTER = timedelta(hours=1)
ROOT = Path(__file__).resolve().parent.parent.parent
LOCK_ERROR_MARKER = "Could not set lock"
NOT_CACHED_MARKER = "not cached"


@dataclass
class LeagueState:
    """Everything derived for one league. Built whole by AppState._build, never mutated piecemeal by screens."""
    ctx: LeagueContext
    projections: dict[str, Projection]
    values: pl.DataFrame
    matchups: Matchups
    # source -> team_week_projections(ctx, projections[source]); invalidated only by _build and _recompute_model.
    team_weeks: dict[str, pl.DataFrame]
    last_refresh: datetime | None


@dataclass
class AppState:
    cfg: Config = field(default_factory=lambda: load_config(ROOT, os.environ))
    store: Store | None = None
    con: duckdb.DuckDBPyConnection | None = None
    leagues: dict[str, LeagueState] = field(default_factory=dict)
    active: str = ""
    summaries: list[LeagueSummary] = field(default_factory=list)
    source: str = ""
    error: str | None = None
    refreshing: bool = False

    def __post_init__(self) -> None:
        if not self.source:
            self.source = self.cfg.source_default
        if not self.active:
            self.active = self.cfg.league_id

    # ---- forwarding properties: every screen reads these, unchanged from the single-league app ----

    def _current(self) -> LeagueState | None:
        return self.leagues.get(self.active)

    @property
    def ctx(self) -> LeagueContext | None:
        current = self._current()
        return None if current is None else current.ctx

    @property
    def projections(self) -> dict[str, Projection]:
        current = self._current()
        return {} if current is None else current.projections

    @property
    def values(self) -> pl.DataFrame:
        current = self._current()
        return pl.DataFrame() if current is None else current.values

    @property
    def matchups(self) -> Matchups:
        current = self._current()
        return {} if current is None else current.matchups

    @property
    def team_weeks(self) -> dict[str, pl.DataFrame]:
        current = self._current()
        return {} if current is None else current.team_weeks

    @property
    def last_refresh(self) -> datetime | None:
        current = self._current()
        return None if current is None else current.last_refresh

    # ---- connections and building ----

    def _connect(self) -> tuple[Store, duckdb.DuckDBPyConnection]:
        if self.store is None:
            self.store = Store(self.cfg.sqlite_path)
        if self.con is None:
            self.con = connect(self.cfg.duckdb_path)
        return self.store, self.con

    def _secondary_source(self) -> str:
        return SOURCES[0] if self.source == SOURCES[1] else SOURCES[1]

    def _compute_values(self, ctx: LeagueContext, projections: dict[str, Projection]) -> pl.DataFrame:
        store, con = self._connect()
        return player_values(ctx, con, store, projections[self.source], projections[self._secondary_source()])

    def _build(self, league_id: str) -> LeagueState:
        """Build a league's state from the caches. Raises ValueError("... not cached ...") when never fetched."""
        store, con = self._connect()
        ctx = load_league_context(store, league_id)
        projections = {source: project(con, store, ctx, source, params=self.cfg.blend) for source in SOURCES}
        matchups = load_matchups(store, league_id, list(range(1, ctx.playoff_week_start)))
        team_weeks = {source: team_week_projections(ctx, proj) for source, proj in projections.items()}
        values = self._compute_values(ctx, projections)
        return LeagueState(ctx=ctx, projections=projections, values=values, matchups=matchups,
                           team_weeks=team_weeks, last_refresh=self._fetched_at(league_id))

    def _fetched_at(self, league_id: str) -> datetime | None:
        store, _ = self._connect()
        stamp = store.fetched_at(f"sleeper:/league/{league_id}")
        return None if stamp is None else datetime.fromtimestamp(stamp)

    def load(self) -> None:
        """Build the active league from the caches and read the discovered league list."""
        store, _ = self._connect()
        built = self._build(self.active)
        self.leagues[self.active] = built
        self.summaries = load_summaries(store, self.cfg.usernames, self.cfg.current_season)

    def switch(self, league_id: str) -> None:
        """Make league_id active and persist it; build it on first use. Raises when it was never fetched."""
        self.active = league_id
        self.cfg = replace(self.cfg, league_id=league_id)
        save_config(self.cfg.root, self.cfg)
        if league_id not in self.leagues:
            self.leagues[league_id] = self._build(league_id)

    def refresh(self) -> RefreshSummary:
        """Pull the active league, shared nflverse data, and its market snapshot, then rebuild it."""
        store, con = self._connect()
        client = SleeperClient(store, refresh=True)
        summary = run_refresh(self.cfg, client, con, store, httpx.Client(timeout=30.0))
        self.load()
        return summary

    def primary(self) -> Projection:
        return self.projections[self.source]

    def secondary(self) -> Projection:
        return self.projections[self._secondary_source()]

    def is_stale(self) -> bool:
        """True when the active league's Sleeper fetch is missing or older than one hour."""
        last = self._fetched_at(self.active)
        return last is None or datetime.now() - last > STALE_AFTER

    def has_cache(self) -> bool:
        return self._fetched_at(self.active) is not None

    def my_roster_id(self) -> int:
        """First configured username with a roster in the active league, else the first roster; 1 before load."""
        ctx = self.ctx
        if ctx is None or not ctx.rosters:
            return 1
        for name in self.cfg.usernames:
            for roster in ctx.rosters:
                if roster.display_name.lower() == name.lower():
                    return roster.roster_id
        return ctx.rosters[0].roster_id

    def _rebuild_values(self) -> None:
        current = self._current()
        if current is not None:
            current.values = self._compute_values(current.ctx, current.projections)

    def _recompute_model(self) -> None:
        """Re-run only the active league's model projection with the current blend weights."""
        current = self._current()
        if current is None:
            return
        store, con = self._connect()
        model_projection = project(con, store, current.ctx, "model", params=self.cfg.blend)
        model_team_weeks = team_week_projections(current.ctx, model_projection)
        current.projections["model"], current.team_weeks["model"] = model_projection, model_team_weeks
        self._rebuild_values()

    def _rediscover(self) -> None:
        """Re-list leagues for the configured usernames over the network; fall back to the first if the active one is gone."""
        store, _ = self._connect()
        self.summaries, errors = discover(SleeperClient(store, refresh=True), self.cfg.usernames, self.cfg.current_season)
        self.error = "; ".join(errors) if errors else None
        ids = [s.league_id for s in self.summaries]
        if self.summaries and self.active not in ids:
            self.switch(ids[0])

    def apply_settings(self, new: Config) -> str:
        """Save new config, apply it by scope, and return the most significant scope (or "none")."""
        old, self.cfg = self.cfg, new
        save_config(new.root, new)
        scopes = settings_scopes(old, new)
        if "source" in scopes:
            self.source = new.source_default
        if "league" in scopes:
            self._rediscover()
        elif "model" in scopes:
            self._recompute_model()
        elif "source" in scopes:
            self._rebuild_values()
        return next((scope for scope in SCOPE_ORDER if scope in scopes), "none")


def settings_scopes(old: Config, new: Config) -> set[str]:
    """Which parts of the app a config change touches."""
    scopes: set[str] = set()
    if old.usernames != new.usernames:
        scopes.add("league")
    if (old.stats_from, old.detail_from) != (new.stats_from, new.detail_from):
        scopes.add("history")
    if (old.blend_k, old.blend_a, old.blend_b) != (new.blend_k, new.blend_a, new.blend_b):
        scopes.add("model")
    if old.source_default != new.source_default:
        scopes.add("source")
    return scopes


def startup_plan(has_cache: bool, stale: bool) -> tuple[bool, bool]:
    """(load_now, refresh_in_background) for the startup sequence."""
    return has_cache, stale or not has_cache


def friendly_error(msg: str) -> str:
    """Rewrite the DuckDB cross-process lock error into an actionable message."""
    if LOCK_ERROR_MARKER in msg:
        return "another ff-analyst instance is already running; close it and press Retry"
    return msg


STATE = AppState()


def get_state() -> AppState:
    return STATE
