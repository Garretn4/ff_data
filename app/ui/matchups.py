"""Matchups screen: every head-to-head for a week with projected totals; my matchup expanded by slot."""
from __future__ import annotations

from dataclasses import dataclass

import polars as pl
from nicegui import ui

from app.model.league import LeagueContext, Roster
from app.model.live import SOURCES, Projection
from app.model.standings import (
    Matchups, Pairing, actual_lineup, actual_points, lineup_for, player_points, schedule, team_week_projections,
    week_totals, win_probability,
)
from app.ui.layout import ensure_loaded, frame, on_refresh
from app.ui.state import AppState, get_state
from app.ui.theme import ACCENT, MUTED, POSITIVE, TEXT


@dataclass(frozen=True)
class WeekView:
    """Everything the page needs for one week, computed once."""
    week: int
    played: bool
    pairings: list[Pairing]
    totals: dict[str, dict[int, float]]   # source -> roster_id -> projected points
    actual: dict[int, float]
    names: dict[str, str]
    matchups: Matchups
    live: dict[int, dict[str, float]]     # roster_id -> sleeper_id -> live points so far


def _roster(ctx: LeagueContext, roster_id: int) -> Roster:
    for roster in ctx.rosters:
        if roster.roster_id == roster_id:
            return roster
    raise ValueError(f"roster {roster_id} not in league")


def _other(source: str) -> str:
    return SOURCES[0] if source == SOURCES[1] else SOURCES[1]


def build_week(state: AppState, week: int) -> WeekView:
    ctx = state.ctx
    totals = {source: week_totals(team_week_projections(ctx, proj), week) for source, proj in state.projections.items()}
    values: pl.DataFrame = state.values
    names = dict(zip(values["sleeper_id"].to_list(), values["name"].to_list())) if not values.is_empty() else {}
    live = {r.roster_id: player_points(state.matchups, week, r.roster_id) for r in ctx.rosters}
    return WeekView(week=week, played=week < ctx.week, pairings=schedule(state.matchups, week), totals=totals,
                    actual=actual_points(state.matchups, week), names=names, matchups=state.matchups, live=live)


def _my_first(pairings: list[Pairing], me: int) -> list[Pairing]:
    mine = [p for p in pairings if me in (p.home, p.away)]
    return mine + [p for p in pairings if p not in mine]


def _score(view: WeekView, source: str, roster_id: int) -> float:
    if view.played:
        return view.actual.get(roster_id, 0.0)
    return view.totals[source].get(roster_id, 0.0)


def _favourite(view: WeekView, source: str, pairing: Pairing) -> tuple[int, float] | None:
    if pairing.away is None:
        return None
    home, away = _score(view, source, pairing.home), _score(view, source, pairing.away)
    fav = pairing.home if home >= away else pairing.away
    return fav, win_probability(max(home, away), min(home, away))


def _side(ctx: LeagueContext, proj: Projection, view: WeekView, source: str, roster: Roster) -> None:
    with ui.column().classes("flex-1 gap-1"):
        ui.label(roster.team_name).style(f"color:{TEXT};font-weight:600")
        if view.played:
            headline = f"{_score(view, source, roster.roster_id):.1f} actual"
        else:
            so_far = view.actual.get(roster.roster_id, 0.0)
            headline = f"{_score(view, source, roster.roster_id):.1f} {source} · {so_far:.1f} so far"
        ui.label(headline).style(f"color:{ACCENT};font-size:1.25rem")
        if not view.played:
            ui.label(f"{_other(source)} {view.totals[_other(source)].get(roster.roster_id, 0.0):.1f}").classes("ff-label")
        rows = actual_lineup(view.matchups, view.week, roster.roster_id, ctx.slots) if view.played \
            else lineup_for(ctx, proj, roster, view.week)
        live = view.live.get(roster.roster_id, {})
        for slot, pid, pts in rows:
            with ui.row().classes("justify-between w-full no-wrap"):
                ui.label(slot).classes("ff-label").style("min-width:5rem")
                ui.label(view.names.get(pid, pid) if pid else "—").style(f"color:{TEXT}")
                ui.label(f"{pts:.1f}").style(f"color:{MUTED}")
                if not view.played and pid in live:
                    ui.label(f"{live[pid]:.1f} live").style(f"color:{ACCENT}")


def _my_card(state: AppState, view: WeekView, pairing: Pairing, me: int) -> None:
    ctx, proj, source = state.ctx, state.primary(), state.source
    opp_id = pairing.away if pairing.home == me else pairing.home
    with ui.column().classes("ff-card q-pa-lg gap-3 w-full"):
        ui.label("MY MATCHUP").classes("ff-label")
        if opp_id is None:
            ui.label("Bye week").style(f"color:{MUTED}")
            return
        with ui.row().classes("gap-8 w-full no-wrap"):
            _side(ctx, proj, view, source, _roster(ctx, me))
            _side(ctx, proj, view, source, _roster(ctx, opp_id))
        fav = _favourite(view, source, pairing)
        if fav is not None and not view.played:
            prob = fav[1] if fav[0] == me else 1.0 - fav[1]
            ui.label(f"Win probability {prob:.0%}").style(f"color:{POSITIVE if prob >= 0.5 else MUTED}")
        elif view.played:
            winner = me if _score(view, source, me) > _score(view, source, opp_id) else opp_id
            ui.label(f"Winner: {_roster(ctx, winner).team_name}").style(f"color:{POSITIVE}")


def _compact_row(ctx: LeagueContext, view: WeekView, source: str, pairing: Pairing) -> None:
    home = _roster(ctx, pairing.home)
    with ui.row().classes("ff-card q-pa-sm items-center gap-4 w-full no-wrap"):
        ui.label(home.team_name).style(f"color:{TEXT};min-width:10rem")
        if pairing.away is None:
            ui.label("bye").classes("ff-label")
            return
        away = _roster(ctx, pairing.away)
        for rid in (pairing.home, pairing.away):
            text = f"{view.actual.get(rid, 0.0):.1f}" if view.played else \
                f"{view.totals['model'].get(rid, 0.0):.1f} / {view.totals['consensus'].get(rid, 0.0):.1f}"
            ui.label(text).style(f"color:{ACCENT};min-width:7rem;text-align:right")
            if not view.played:
                ui.label(f"live {view.actual.get(rid, 0.0):.1f}").classes("ff-label")
            if rid == pairing.home:
                ui.label("vs").classes("ff-label")
        ui.label(away.team_name).style(f"color:{TEXT};min-width:10rem")
        fav = _favourite(view, source, pairing)
        if fav is not None:
            label = "won" if view.played else f"{fav[1]:.0%}"
            ui.chip(f"{_roster(ctx, fav[0]).team_name} {label}").props(f"color={ACCENT} outline").classes("text-xs")


def _week_select(ctx: LeagueContext, week: int) -> None:
    options = {w: f"Week {w}" for w in range(1, ctx.playoff_week_start)}
    ui.select(options, value=week, label="Week",
              on_change=lambda e: ui.navigate.to(f"/matchups?week={e.value}")).classes("min-w-32")


@ui.page("/matchups")
def matchups_page(week: int = 0) -> None:
    with frame("Matchups"):
        state = get_state()
        if not ensure_loaded(state):
            return
        ctx = state.ctx
        week = week or ctx.week
        _week_select(ctx, week)
        try:
            view = build_week(state, week)
        except Exception as exc:  # UI boundary: caught, recorded, and surfaced, never swallowed
            state.error = str(exc)
            ui.notify(f"Matchups failed: {exc}", type="negative")
            return
        if not view.pairings:
            ui.label(f"Schedule not published for week {week}").style(f"color:{MUTED}")
            ui.button("Refresh", on_click=on_refresh)
            return
        me = state.my_roster_id()
        ordered = _my_first(view.pairings, me)
        if me in (ordered[0].home, ordered[0].away):
            _my_card(state, view, ordered[0], me)
            ordered = ordered[1:]
        ui.label("Both projections shown as model / consensus").classes("ff-label")
        for pairing in ordered:
            _compact_row(ctx, view, state.source, pairing)
