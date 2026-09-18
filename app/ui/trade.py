"""Trade screen: pick players from two rosters and see the live lineup-strength delta."""
from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import polars as pl
from nicegui import ui

from app.model.finder import MIN_GAIN
from app.model.league import LeagueContext, Roster
from app.model.lineup import index_weekly
from app.model.trade import trade_delta
from app.ui.layout import ensure_loaded, frame
from app.ui.state import get_state
from app.ui.theme import ACCENT, CARD, MUTED, NEGATIVE, POSITIVE, TEXT


@dataclass
class _Side:
    roster_id: int
    given: set[str] = field(default_factory=set)


def _split_ids(raw: str) -> set[str]:
    return {p for p in raw.split(",") if p}


def _read_username(root: Path) -> str | None:
    path = root / "config.local.toml"
    if not path.exists():
        return None
    with path.open("rb") as fh:
        data = tomllib.load(fh)
    username = data.get("username")
    return str(username) if username else None


def _resolve_me(ctx: LeagueContext, root: Path, me: int) -> int:
    """The `me` param wins when it differs from the bare default; otherwise try the configured username."""
    if me != 1:
        return me
    username = _read_username(root)
    if username is None:
        return 1
    for roster in ctx.rosters:
        if roster.display_name.lower() == username.lower():
            return roster.roster_id
    return 1


def _roster_by_id(ctx: LeagueContext, roster_id: int) -> Roster:
    for roster in ctx.rosters:
        if roster.roster_id == roster_id:
            return roster
    raise ValueError(f"roster {roster_id} not in league")


def _ranked_players(values: pl.DataFrame, roster: Roster) -> list[tuple[str, str]]:
    ids = list(roster.players)
    if not ids:
        return []
    frame_ids = pl.DataFrame({"sleeper_id": ids})
    score = pl.coalesce(pl.col("intrinsic"), pl.col("ros_primary"), pl.lit(0.0))
    joined = frame_ids.join(values.select("sleeper_id", "name", "intrinsic", "ros_primary"), on="sleeper_id", how="left")
    ranked = joined.with_columns(
        pl.coalesce(pl.col("name"), pl.col("sleeper_id")).alias("name"), score.alias("score"),
    ).sort("score", descending=True, nulls_last=True)
    return list(zip(ranked["sleeper_id"].to_list(), ranked["name"].to_list()))


def _chip_colors(selected: bool) -> tuple[str, str]:
    return (ACCENT, "#000000") if selected else (CARD, TEXT)


def _render_side(ctx: LeagueContext, values: pl.DataFrame, side: _Side, title: str,
                 on_roster_change: Callable[[int], None], on_toggle: Callable[[str], None]) -> None:
    with ui.column().classes("ff-card q-pa-md gap-2").style("flex:1"):
        ui.label(title).classes("ff-label")
        options = {roster.roster_id: roster.team_name for roster in ctx.rosters}
        ui.select(options, value=side.roster_id, on_change=lambda e: on_roster_change(e.value)).classes("w-full")
        roster = _roster_by_id(ctx, side.roster_id)
        with ui.row().classes("gap-2 flex-wrap"):
            for player_id, name in _ranked_players(values, roster):
                background, text_color = _chip_colors(player_id in side.given)
                ui.chip(name, selectable=True, selected=player_id in side.given, color=background, text_color=text_color,
                        on_click=lambda _, pid=player_id: on_toggle(pid))


def _delta_block(label: str, delta: float, before: float, after: float) -> None:
    color = POSITIVE if delta > 0 else NEGATIVE if delta < 0 else MUTED
    with ui.column().classes("items-center gap-1"):
        ui.label(label).classes("ff-label")
        ui.label(f"{delta:+.1f}").style(f"color:{color};font-size:32px;font-weight:700")
        ui.label(f"{before:.1f} -> {after:.1f}").classes("ff-label")


@ui.page("/trade")
def trade_page(me: int = 1, opp: int = 2, give: str = "", get: str = "") -> None:
    with frame("Trade"):
        state = get_state()
        if not ensure_loaded(state):
            return
        ctx = state.ctx
        proj = state.primary()
        values = state.values
        index = index_weekly(proj.weekly)

        side_a = _Side(roster_id=_resolve_me(ctx, state.cfg.root, me), given=_split_ids(give))
        side_b = _Side(roster_id=opp, given=_split_ids(get))

        @ui.refreshable
        def summary() -> None:
            roster_a = _roster_by_id(ctx, side_a.roster_id)
            roster_b = _roster_by_id(ctx, side_b.roster_id)
            result = trade_delta(ctx, proj, roster_a, roster_b, side_a.given, side_b.given, index=index)
            with ui.column().classes("ff-card q-pa-lg gap-4 items-center").style("min-width:320px"):
                with ui.row().classes("gap-12"):
                    _delta_block("MY DELTA", result.delta_a, result.before_a, result.after_a)
                    _delta_block("OPP DELTA", result.delta_b, result.before_b, result.after_b)
                if result.delta_a > MIN_GAIN and result.delta_b > MIN_GAIN:
                    ui.label("FAIR FOR BOTH").style(f"color:#000000;background:{POSITIVE}").classes("ff-label q-px-md q-py-xs rounded")
                ui.label(f"Projection: {proj.source}").classes("ff-label")

        @ui.refreshable
        def view_a() -> None:
            _render_side(ctx, values, side_a, "MY ROSTER", _on_roster_a, _on_toggle_a)

        @ui.refreshable
        def view_b() -> None:
            _render_side(ctx, values, side_b, "OPPONENT ROSTER", _on_roster_b, _on_toggle_b)

        def _on_roster_a(roster_id: int) -> None:
            side_a.roster_id = roster_id
            side_a.given.clear()
            view_a.refresh()
            summary.refresh()

        def _on_roster_b(roster_id: int) -> None:
            side_b.roster_id = roster_id
            side_b.given.clear()
            view_b.refresh()
            summary.refresh()

        def _on_toggle_a(player_id: str) -> None:
            side_a.given.symmetric_difference_update({player_id})
            view_a.refresh()
            summary.refresh()

        def _on_toggle_b(player_id: str) -> None:
            side_b.given.symmetric_difference_update({player_id})
            view_b.refresh()
            summary.refresh()

        with ui.row().classes("w-full gap-4 items-start"):
            view_a()
            summary()
            view_b()
