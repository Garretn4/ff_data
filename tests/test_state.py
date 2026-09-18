from pathlib import Path

from app.config import Config
from app.model.league import LeagueContext, Roster
from app.ui.state import AppState, friendly_error, startup_plan


def test_startup_plan_loads_cached_league_and_refreshes_when_stale() -> None:
    assert startup_plan(has_cache=True, stale=True) == (True, True)


def test_startup_plan_loads_cached_league_without_refresh_when_fresh() -> None:
    assert startup_plan(has_cache=True, stale=False) == (True, False)


def test_startup_plan_refreshes_in_background_without_cache() -> None:
    assert startup_plan(has_cache=False, stale=True) == (False, True)
    assert startup_plan(has_cache=False, stale=False) == (False, True)


def test_friendly_error_explains_the_duckdb_lock() -> None:
    msg = 'IO Error: Could not set lock on file "cache/stats.duckdb": Conflicting lock is held'
    assert friendly_error(msg) == "another ff-analyst instance is already running; close it and press Retry"


def test_friendly_error_passes_other_messages_through() -> None:
    assert friendly_error("league_id is empty") == "league_id is empty"


def _ctx(rosters: tuple[Roster, ...]) -> LeagueContext:
    return LeagueContext(league_id="1", name="t", season=2026, week=3, n_teams=len(rosters), scoring={},
                         roster_positions=["QB"], slots=["QB"], playoff_week_start=15, last_week=17,
                         trade_deadline=99, rosters=rosters)


def _cfg(username: str) -> Config:
    return Config(league_id="1", root=Path("/tmp/x"), stats_from=2025, detail_from=2025, current_season=2026,
                  username=username)


def test_my_roster_id_matches_username_case_insensitively() -> None:
    rosters = (Roster(4, "u1", "Ann's Army", "Ann", None, (), (), ()),
               Roster(7, "u2", "Bobcats", "garretN4", None, (), (), ()))
    state = AppState(cfg=_cfg("Garretn4"), ctx=_ctx(rosters))
    assert state.my_roster_id() == 7


def test_my_roster_id_falls_back_to_first_roster() -> None:
    rosters = (Roster(4, "u1", "A", "Ann", None, (), (), ()), Roster(7, "u2", "B", "Bob", None, (), (), ()))
    assert AppState(cfg=_cfg("nobody"), ctx=_ctx(rosters)).my_roster_id() == 4
    assert AppState(cfg=_cfg(""), ctx=_ctx(rosters)).my_roster_id() == 4


def test_my_roster_id_is_one_before_the_league_loads() -> None:
    assert AppState(cfg=_cfg("x")).my_roster_id() == 1
