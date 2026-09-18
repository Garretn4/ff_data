"""Trade finder screen: search a roster pair (or every opponent) for deals that help both sides."""
from __future__ import annotations

from urllib.parse import quote

import polars as pl
from nicegui import run, ui

from app.model.finder import find_trades
from app.model.league import LeagueContext
from app.model.live import Projection
from app.model.trade import TradeResult
from app.ui.layout import frame
from app.ui.state import get_state
from app.ui.theme import MUTED, NEGATIVE, POSITIVE, TEXT

ALL_OPPONENTS = 0
Deal = tuple[int, TradeResult, tuple[str, ...], tuple[str, ...]]


def _name_lookup(values: pl.DataFrame) -> dict[str, str]:
    return dict(zip(values["sleeper_id"].to_list(), values["name"].to_list()))


def _names(ids: tuple[str, ...], lookup: dict[str, str]) -> str:
    return ", ".join(lookup.get(pid, pid) for pid in ids)


def _trade_link(me: int, opp: int, give: tuple[str, ...], get: tuple[str, ...]) -> str:
    give_q = quote(",".join(give))
    get_q = quote(",".join(get))
    return f"/trade?me={me}&opp={opp}&give={give_q}&get={get_q}"


def _delta_label(label: str, delta: float) -> None:
    color = POSITIVE if delta > 0 else NEGATIVE if delta < 0 else MUTED
    with ui.column().classes("items-start gap-0"):
        ui.label(label).classes("ff-label")
        ui.label(f"{delta:+.1f}").style(f"color:{color};font-weight:700")


def _deal_card(lookup: dict[str, str], me: int, deal: Deal) -> None:
    opp_roster_id, result, give, get = deal
    with ui.column().classes("ff-card q-pa-md gap-2"):
        ui.label(f"Give: {_names(give, lookup)}").style(f"color:{TEXT}")
        ui.label(f"Get: {_names(get, lookup)}").style(f"color:{TEXT}")
        with ui.row().classes("gap-6"):
            _delta_label("MY DELTA", result.delta_a)
            _delta_label("OPP DELTA", result.delta_b)
        ui.link("Open in trade screen", _trade_link(me, opp_roster_id, give, get)).classes("ff-label")


def search_deals(ctx: LeagueContext, proj: Projection, values: pl.DataFrame, me: int, opp: int) -> list[Deal]:
    opponents = [r.roster_id for r in ctx.rosters if r.roster_id != me] if opp == ALL_OPPONENTS else [opp]
    deals: list[Deal] = []
    for opp_id in opponents:
        deals.extend((opp_id, result, give, get) for result, give, get in find_trades(ctx, proj, values, me, opp_id))
    deals.sort(key=lambda deal: min(deal[1].delta_a, deal[1].delta_b), reverse=True)
    return deals[:10]


@ui.page("/finder")
def finder_page(me: int = 1, opp: int = ALL_OPPONENTS) -> None:
    with frame("Finder"):
        state = get_state()
        ctx = state.ctx
        proj = state.primary()
        values = state.values
        lookup = _name_lookup(values)

        picked = {"me": me, "opp": opp}
        results: list[Deal] = []

        @ui.refreshable
        def results_view() -> None:
            if not results:
                ui.label("No deals found yet.").classes("ff-label")
                return
            for deal in results:
                _deal_card(lookup, picked["me"], deal)

        async def on_find() -> None:
            nonlocal results
            if picked["opp"] not in (ALL_OPPONENTS, ) and picked["opp"] == picked["me"]:
                ui.notify("Pick an opponent other than your own roster", type="warning")
                return
            spinner.visible = True
            try:
                results = await run.io_bound(search_deals, ctx, proj, values, picked["me"], picked["opp"])
            finally:
                spinner.visible = False
            results_view.refresh()

        with ui.row().classes("gap-4 items-end"):
            my_options = {roster.roster_id: roster.team_name for roster in ctx.rosters}
            opp_options = {ALL_OPPONENTS: "All opponents", **my_options}
            ui.select(my_options, value=picked["me"], label="My roster",
                      on_change=lambda e: picked.__setitem__("me", e.value)).classes("min-w-40")
            ui.select(opp_options, value=picked["opp"], label="Opponent",
                      on_change=lambda e: picked.__setitem__("opp", e.value)).classes("min-w-40")
            ui.button("Find", on_click=on_find)
            spinner = ui.spinner()
            spinner.visible = False

        results_view()
