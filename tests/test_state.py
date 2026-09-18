from dataclasses import replace
from pathlib import Path

import polars as pl

from app.config import SOURCES as CONFIG_SOURCES, Config, load_config
from app.model.league import LeagueContext, Roster
from app.model.live import SOURCES, Projection
from app.model.standings import team_week_projections
from app.ui.state import SCOPE_ORDER, AppState, friendly_error, settings_scopes, startup_plan
from tests.test_standings import A, B, POINTS, weekly


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


def test_config_and_live_agree_on_sources() -> None:
    assert CONFIG_SOURCES == SOURCES


def test_settings_scopes_by_field_group() -> None:
    base = _cfg("ann")
    assert settings_scopes(base, base) == set()
    assert settings_scopes(base, replace(base, league_id="2")) == {"league"}
    assert settings_scopes(base, replace(base, username="bob")) == {"league"}
    assert settings_scopes(base, replace(base, stats_from=2010)) == {"history"}
    assert settings_scopes(base, replace(base, detail_from=2021)) == {"history"}
    assert settings_scopes(base, replace(base, blend_k=9.0)) == {"model"}
    assert settings_scopes(base, replace(base, source_default="consensus")) == {"source"}
    assert settings_scopes(base, replace(base, blend_a=0.1, stats_from=2010)) == {"history", "model"}


def test_state_source_starts_from_config_default() -> None:
    assert AppState(cfg=replace(_cfg(""), source_default="consensus")).source == "consensus"


def test_apply_settings_saves_and_returns_the_top_scope(tmp_path: Path, monkeypatch) -> None:
    state = AppState(cfg=replace(_cfg("ann"), root=tmp_path))
    calls: list[str] = []
    monkeypatch.setattr(state, "load", lambda: calls.append("load"))
    monkeypatch.setattr(state, "_recompute_model", lambda: calls.append("model"))
    monkeypatch.setattr(state, "_rebuild_values", lambda: calls.append("values"))

    assert state.apply_settings(replace(state.cfg, source_default="consensus")) == "source"
    assert state.source == "consensus" and calls == ["values"]
    assert load_config(tmp_path, env={}).source_default == "consensus"

    assert state.apply_settings(replace(state.cfg, blend_k=9.0)) == "model"
    assert calls == ["values", "model"]

    assert state.apply_settings(replace(state.cfg, blend_b=0.9, stats_from=2010)) == "history"
    assert calls == ["values", "model", "model"]

    assert state.apply_settings(replace(state.cfg, league_id="42", blend_a=0.2)) == "league"
    assert calls == ["values", "model", "model", "load"]   # load recomputes projections, so no extra model call
    assert state.cfg.league_id == "42"

    assert state.apply_settings(state.cfg) == "none"
    assert SCOPE_ORDER == ("league", "history", "model", "source")


def test_apply_settings_source_only_with_no_ctx(tmp_path: Path) -> None:
    assert AppState(cfg=replace(_cfg("ann"), root=tmp_path)).apply_settings(replace(_cfg("ann"), root=tmp_path, source_default="consensus")) == "source"


def test_recompute_model_refreshes_only_the_model_team_weeks(monkeypatch) -> None:
    ctx = _ctx((A, B))
    state = AppState(cfg=_cfg("ann"), ctx=ctx)
    monkeypatch.setattr(state, "_connect", lambda: (None, None))
    weekly_frame = weekly(POINTS, [3])
    model_proj = Projection("model", weekly_frame, pl.DataFrame())
    monkeypatch.setattr("app.ui.state.project", lambda *a, **k: model_proj)
    monkeypatch.setattr("app.ui.state.player_values", lambda *a, **k: pl.DataFrame())
    sentinel_frame = pl.DataFrame({"roster_id": [9], "week": [3], "points": [1.0]})
    state.team_weeks = {"consensus": sentinel_frame, "model": pl.DataFrame()}
    state.projections = {"consensus": Projection("consensus", weekly_frame, pl.DataFrame()), "model": model_proj}

    state._recompute_model()

    assert state.team_weeks["consensus"] is sentinel_frame
    expected = team_week_projections(ctx, model_proj)
    assert state.team_weeks["model"].equals(expected)
