"""Manager profiles screen: trades, acquisitions, overpay analysis."""
from __future__ import annotations

from nicegui import run, ui

from app.model.managers import ManagerProfile, profiles
from app.sleeper import SleeperClient
from app.ui.layout import ensure_loaded, frame
from app.ui.state import get_state
from app.ui.theme import ACCENT, CARD, MUTED, NEGATIVE, POSITIVE, TEXT, avatar, echart_base

_profiles_cache: dict[str, list[ManagerProfile]] = {}


def _find_roster_avatar(ctx, owner_id: str) -> str | None:
    """Find avatar URL for owner_id from rosters in context."""
    for roster in ctx.rosters:
        if roster.owner_id == owner_id:
            return avatar(roster.avatar)
    return None


def _avatar_image(avatar_url: str | None) -> None:
    """Render 40px round avatar or muted placeholder."""
    if avatar_url:
        ui.image(avatar_url).classes("rounded-full w-10 h-10")
    else:
        ui.icon("person").classes("rounded-full w-10 h-10 flex items-center justify-center").style(
            f"background: {CARD}; color: {MUTED}; font-size: 20px;"
        )


def _stats_row(profile: ManagerProfile) -> None:
    """Render a row of key stats: trades per season, FAAB spent, overpay total."""
    overpay_color = NEGATIVE if profile.overpay_total > 0 else POSITIVE if profile.overpay_total < 0 else MUTED
    with ui.row().classes("gap-4 items-center text-xs"):
        with ui.column().classes("gap-1"):
            ui.label("Trades/Season").classes("ff-label")
            ui.label(f"{profile.trades_per_season:.1f}").style(f"color: {TEXT}")
        with ui.column().classes("gap-1"):
            ui.label("FAAB Spent").classes("ff-label")
            ui.label(f"${profile.faab_spent}").style(f"color: {TEXT}")
        with ui.column().classes("gap-1"):
            ui.label("Overpay Total").classes("ff-label")
            ui.label(f"{profile.overpay_total:+.1f}").style(f"color: {overpay_color}")


def _position_chart(title: str, acq_data: dict[str, int], given_data: dict[str, int] | None = None) -> None:
    """Render a bar chart for acquired/given by position, or adds by position."""
    if given_data is not None:
        # Acquired vs Given chart
        positions = sorted(set(acq_data.keys()) | set(given_data.keys()))
        acq_values = [acq_data.get(p, 0) for p in positions]
        given_values = [given_data.get(p, 0) for p in positions]
        series = [
            {"name": "Acquired", "type": "bar", "data": acq_values},
            {"name": "Given", "type": "bar", "data": given_values},
        ]
    else:
        # Adds by position chart
        positions = sorted(acq_data.keys())
        add_values = [acq_data.get(p, 0) for p in positions]
        series = [
            {"name": "Adds", "type": "bar", "data": add_values},
        ]

    options = {
        **echart_base(),
        "title": {"text": title, "left": "center", "textStyle": {"fontSize": 12, "color": TEXT}},
        "tooltip": {"trigger": "axis"},
        "xAxis": {
            "type": "category",
            "data": positions,
            "splitArea": {"show": False},
            "axisLine": {"lineStyle": {"color": "#262626"}},
            "axisLabel": {"color": MUTED, "fontSize": 11},
        },
        "yAxis": {
            "type": "value",
            "splitArea": {"show": False},
            "axisLine": {"lineStyle": {"color": "#262626"}},
            "axisLabel": {"color": MUTED, "fontSize": 11},
        },
        "series": series,
    }
    ui.echart(options).classes("w-full").style("height: 200px;")


def _manager_card(profile: ManagerProfile, ctx) -> None:
    """Render a single manager profile card."""
    avatar_url = _find_roster_avatar(ctx, profile.owner_id)

    with ui.card().classes("ff-card p-4 flex flex-col gap-4").style("min-width: 400px;"):
        with ui.row().classes("gap-3 items-start"):
            _avatar_image(avatar_url)
            with ui.column().classes("flex-1 gap-2"):
                ui.label(profile.team_name).style(f"color: {TEXT}; font-weight: bold;")
                ui.label(profile.display_name).style(f"color: {MUTED}; font-size: 0.875rem;")
                ui.label(f"{profile.seasons} season{'s' if profile.seasons != 1 else ''} • {profile.trades} trades").classes("ff-label")

        _stats_row(profile)

        with ui.row().classes("gap-4 w-full"):
            with ui.column().classes("flex-1"):
                _position_chart("Acquired vs Given", profile.acquired_by_position, profile.given_by_position)
            with ui.column().classes("flex-1"):
                _position_chart("Adds", profile.adds_by_position)

        if profile.partners:
            with ui.column().classes("gap-2"):
                ui.label("Trading Partners").classes("ff-label")
                with ui.row().classes("gap-2 flex-wrap"):
                    for partner_name, count in sorted(profile.partners.items(), key=lambda x: -x[1]):
                        ui.chip(f"{partner_name} ({count})").props(f"color={ACCENT}").classes("text-xs")


@ui.page("/managers")
async def managers_page() -> None:
    with frame("Managers"):
        state = get_state()
        if not ensure_loaded(state):
            return
        ctx = state.ctx
        con = state.con

        league_id = ctx.league_id
        cache = getattr(state, "cache", None) or _profiles_cache

        if league_id not in cache:
            try:
                with ui.spinner(type="dots", size="lg"):
                    computed = await run.io_bound(
                        profiles,
                        ctx,
                        SleeperClient(state.store),
                        con,
                    )
                    cache[league_id] = computed
            except Exception as exc:
                state.error = str(exc)
                ui.notify(str(exc), type="negative")
                return

        profile_list = cache.get(league_id, [])
        if not profile_list:
            ui.label("No manager profiles found").style(f"color: {MUTED}").classes("ff-label")
            return

        with ui.row().classes("gap-4 flex-wrap"):
            for profile in profile_list:
                _manager_card(profile, ctx)
