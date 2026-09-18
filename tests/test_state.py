from app.ui.state import friendly_error, startup_plan


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
