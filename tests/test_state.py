from dataclasses import replace
from pathlib import Path

import polars as pl
import pytest

from app.config import SOURCES as CONFIG_SOURCES, Config, load_config
from app.model.league import LeagueContext, Roster
from app.model.live import SOURCES, Projection
from app.model.standings import team_week_projections
from app.ui.state import (
    SCOPE_ORDER,
    AppState,
    LeagueState,
    NOT_CACHED_MARKER,
    friendly_error,
    settings_scopes,
    startup_plan,
)
from tests.test_standings import A, B, POINTS, ctx_with, weekly


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
                  usernames=(username,) if username else ())


def _state_with(ctx: LeagueContext, username: str = "ann", **league: object) -> AppState:
    state = AppState(cfg=replace(_cfg(username), league_id=ctx.league_id))
    fields = dict(ctx=ctx, projections={}, values=pl.DataFrame(), matchups={}, team_weeks={}, last_refresh=None)
    fields.update(league)
    state.leagues[ctx.league_id] = LeagueState(**fields)  # type: ignore[arg-type]
    return state


def test_my_roster_id_matches_username_case_insensitively() -> None:
    rosters = (Roster(4, "u1", "Ann's Army", "Ann", None, (), (), ()),
               Roster(7, "u2", "Bobcats", "garretN4", None, (), (), ()))
    state = _state_with(_ctx(rosters), "Garretn4")
    assert state.my_roster_id() == 7


def test_my_roster_id_falls_back_to_first_roster() -> None:
    rosters = (Roster(4, "u1", "A", "Ann", None, (), (), ()), Roster(7, "u2", "B", "Bob", None, (), (), ()))
    assert _state_with(_ctx(rosters), "nobody").my_roster_id() == 4
    assert _state_with(_ctx(rosters), "").my_roster_id() == 4


def test_my_roster_id_is_one_before_the_league_loads() -> None:
    assert AppState(cfg=_cfg("x")).my_roster_id() == 1


def test_my_roster_id_walks_usernames_in_order() -> None:
    rosters = (Roster(4, "u1", "A", "bob", None, (), (), ()), Roster(7, "u2", "B", "ann", None, (), (), ()))
    state = _state_with(_ctx(rosters), "zed")
    state.cfg = replace(state.cfg, usernames=("zed", "ann", "bob"))
    assert state.my_roster_id() == 7


def test_config_and_live_agree_on_sources() -> None:
    assert CONFIG_SOURCES == SOURCES


def test_settings_scopes_by_field_group() -> None:
    base = _cfg("ann")
    assert settings_scopes(base, base) == set()
    assert settings_scopes(base, replace(base, usernames=("bob",))) == {"league"}
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
    monkeypatch.setattr(state, "_rediscover", lambda: calls.append("discover"))
    monkeypatch.setattr(state, "_recompute_model", lambda: calls.append("model"))
    monkeypatch.setattr(state, "_rebuild_values", lambda: calls.append("values"))

    assert state.apply_settings(replace(state.cfg, source_default="consensus")) == "source"
    assert state.source == "consensus" and calls == ["values"]
    assert load_config(tmp_path, env={}).source_default == "consensus"

    assert state.apply_settings(replace(state.cfg, blend_k=9.0)) == "model"
    assert calls == ["values", "model"]

    assert state.apply_settings(replace(state.cfg, blend_b=0.9, stats_from=2010)) == "history"
    assert calls == ["values", "model", "model"]

    assert state.apply_settings(replace(state.cfg, usernames=("bob",), blend_a=0.2)) == "league"
    assert calls == ["values", "model", "model", "discover"]
    assert state.cfg.usernames == ("bob",)

    assert state.apply_settings(state.cfg) == "none"
    assert SCOPE_ORDER == ("league", "history", "model", "source")


def test_apply_settings_source_only_with_no_ctx(tmp_path: Path) -> None:
    assert AppState(cfg=replace(_cfg("ann"), root=tmp_path)).apply_settings(replace(_cfg("ann"), root=tmp_path, source_default="consensus")) == "source"


def test_recompute_model_refreshes_only_the_model_team_weeks(monkeypatch) -> None:
    ctx = ctx_with((A, B), ["RB", "FLEX"])
    weekly_frame = weekly(POINTS, [3])
    model_proj = Projection("model", weekly_frame, pl.DataFrame())
    sentinel_frame = pl.DataFrame({"roster_id": [9], "week": [3], "points": [1.0]})
    state = _state_with(
        ctx, "ann",
        team_weeks={"consensus": sentinel_frame, "model": pl.DataFrame()},
        projections={"consensus": Projection("consensus", weekly_frame, pl.DataFrame()), "model": model_proj},
    )
    monkeypatch.setattr(state, "_connect", lambda: (None, None))
    monkeypatch.setattr("app.ui.state.project", lambda *a, **k: model_proj)
    monkeypatch.setattr("app.ui.state.player_values", lambda *a, **k: pl.DataFrame())

    state._recompute_model()

    assert state.team_weeks["consensus"] is sentinel_frame
    expected = team_week_projections(ctx, model_proj)
    assert state.team_weeks["model"].equals(expected)


def test_load_leaves_state_untouched_when_projection_fails(monkeypatch) -> None:
    state = AppState(cfg=_cfg("ann"))
    monkeypatch.setattr(state, "_connect", lambda: (None, None))
    monkeypatch.setattr("app.ui.state.load_league_context", lambda store, league_id: _ctx(()))
    monkeypatch.setattr("app.ui.state.load_summaries", lambda store, usernames, season: [])

    def _boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr("app.ui.state.project", _boom)

    with pytest.raises(RuntimeError):
        state.load()
    assert state.ctx is None
    assert state.projections == {}
    assert state.team_weeks == {}

    other_ctx = _ctx((A,))
    state2 = _state_with(other_ctx)
    monkeypatch.setattr(state2, "_connect", lambda: (None, None))

    with pytest.raises(RuntimeError):
        state2.load()
    assert state2.leagues[other_ctx.league_id].ctx is other_ctx


def test_forwarding_properties_are_empty_before_any_league_loads() -> None:
    state = AppState(cfg=_cfg("ann"))
    assert state.ctx is None and state.projections == {} and state.values.is_empty()
    assert state.matchups == {} and state.team_weeks == {} and state.last_refresh is None


def test_switch_loads_a_league_once_and_persists_the_active_id(tmp_path: Path, monkeypatch) -> None:
    ctx1, ctx2 = _ctx((Roster(1, "u1", "A", "ann", None, (), (), ()),)), replace(_ctx(()), league_id="2")
    state = _state_with(ctx1)
    state.cfg = replace(state.cfg, root=tmp_path)
    built: list[str] = []

    def fake_build(league_id: str) -> LeagueState:
        built.append(league_id)
        return LeagueState(ctx=ctx2, projections={}, values=pl.DataFrame(), matchups={}, team_weeks={}, last_refresh=None)

    monkeypatch.setattr(state, "_build", fake_build)
    state.switch("2")
    assert state.active == "2" and state.ctx is ctx2 and built == ["2"]
    assert load_config(tmp_path, env={}).league_id == "2"
    state.switch("1")
    state.switch("2")
    assert built == ["2"] and state.ctx is ctx2


def test_switch_to_an_unfetched_league_keeps_it_active_and_raises(tmp_path: Path, monkeypatch) -> None:
    state = _state_with(_ctx(()))
    state.cfg = replace(state.cfg, root=tmp_path)

    def fake_build(league_id: str) -> LeagueState:
        raise ValueError(f"league {league_id} {NOT_CACHED_MARKER}; run refresh first")

    monkeypatch.setattr(state, "_build", fake_build)
    with pytest.raises(ValueError):
        state.switch("9")
    assert state.active == "9" and state.cfg.league_id == "9" and state.ctx is None


def test_my_roster_id_follows_username_order() -> None:
    rosters = (Roster(4, "u1", "A", "bob", None, (), (), ()), Roster(7, "u2", "B", "ann", None, (), (), ()))
    state = _state_with(_ctx(rosters))
    state.cfg = replace(state.cfg, usernames=("ann", "bob"))
    assert state.my_roster_id() == 7
    state.cfg = replace(state.cfg, usernames=("zed", "bob"))
    assert state.my_roster_id() == 4


def test_refresh_with_no_league_selected_fails_clearly() -> None:
    state = AppState(cfg=replace(_cfg("ann"), league_id=""))
    with pytest.raises(ValueError, match="no league selected"):
        state.refresh()


def test_staleness_reads_the_league_fetch_time(tmp_path: Path) -> None:
    from datetime import datetime
    state = AppState(cfg=replace(_cfg("ann"), root=tmp_path, league_id="1"))
    assert state.has_cache() is False and state.is_stale() is True
    store, _ = state._connect()
    store.put_cached("sleeper:/league/1", "{}", now=datetime.now().timestamp())
    assert state.has_cache() is True and state.is_stale() is False
    store.put_cached("sleeper:/league/1", "{}", now=datetime.now().timestamp() - 7200)
    assert state.is_stale() is True


def test_rediscover_falls_back_to_the_first_league_when_active_disappears(tmp_path: Path, monkeypatch) -> None:
    import app.ui.state as state_mod
    from app.model.leagues import LeagueSummary

    state = _state_with(_ctx(()))
    state.cfg = replace(state.cfg, root=tmp_path, usernames=("bob",))
    summaries = [LeagueSummary("5", "Five", 2026, "in_season", 10, 0, 3, ("bob",))]
    monkeypatch.setattr(state_mod, "discover", lambda source, names, season: (summaries, ["user zed: not found"]))

    def fake_build(league_id: str) -> LeagueState:
        raise ValueError(f"league {league_id} {NOT_CACHED_MARKER}; run refresh first")

    monkeypatch.setattr(state, "_build", fake_build)
    state._rediscover()
    assert state.summaries == summaries and state.error == "user zed: not found"
    assert state.active == "5"
    assert load_config(tmp_path, env={}).league_id == "5"
    assert "5" not in state.leagues
