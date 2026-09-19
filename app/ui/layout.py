"""Header, navigation, refresh control, and the page frame every screen renders inside."""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from nicegui import app, run, ui

from app.model.live import SOURCES
from app.ui.state import NOT_CACHED_MARKER, AppState, friendly_error, get_state
from app.ui.theme import BORDER, NEGATIVE, TEXT

SCREEN_LINKS: tuple[tuple[str, str], ...] = (
    ("League", "/league"),
    ("Matchups", "/matchups"),
    ("Heatmap", "/heatmap"),
    ("Players", "/players"),
    ("Scatter", "/scatter"),
    ("Trade", "/trade"),
    ("Finder", "/finder"),
    ("Managers", "/managers"),
    ("Settings", "/settings"),
)


def _registered_paths() -> set[str]:
    return {getattr(route, "path", None) for route in app.routes}


def _on_source_change(value: str) -> None:
    """Switch the active projection source and rebuild the value table before reloading."""
    state = get_state()
    try:
        state.source = value
        state._rebuild_values()
        state._evict_others()
    except Exception as exc:  # UI boundary: caught, recorded, and surfaced, never swallowed
        state.error = str(exc)
        ui.notify(f"Could not switch source: {exc}", type="negative")
        return
    state.error = None
    ui.navigate.reload()


async def on_refresh() -> None:
    state = get_state()
    state.refreshing = True
    try:
        summary = await run.io_bound(state.refresh)
    except Exception as exc:  # UI boundary: caught, recorded, and surfaced, never swallowed
        state.error = str(exc)
        ui.notify(friendly_error(state.error), type="negative")
        return
    finally:
        state.refreshing = False
    state.error = None
    ui.notify(f"Refreshed {summary.league_name}: week {summary.week}", type="positive")
    ui.navigate.reload()


async def on_switch(league_id: str) -> None:
    """Switch the active league; a never-fetched league is refreshed in the background first."""
    state = get_state()
    if league_id == state.active:
        return
    state.error = None
    try:
        await run.io_bound(state.switch, league_id)
    except ValueError as exc:
        if NOT_CACHED_MARKER not in str(exc):
            state.error = str(exc)
            ui.notify(friendly_error(state.error), type="negative")
            return
        ui.notify("Fetching this league for the first time", type="info")
        await on_refresh()
        return
    except Exception as exc:  # UI boundary: caught, recorded, and surfaced, never swallowed
        state.error = str(exc)
        ui.notify(friendly_error(state.error), type="negative")
        return
    ui.navigate.reload()


def _league_switcher(state: AppState) -> None:
    if not state.summaries:
        ui.label("add your Sleeper username in Settings").classes("ff-label")
        return
    options = {s.league_id: s.label for s in state.summaries}
    value = state.active if state.active in options else None
    props = "dark dense disable" if state.refreshing else "dark dense"
    ui.select(options, value=value, on_change=lambda e: on_switch(e.value)).classes("min-w-64").props(props)


async def _on_retry() -> None:
    state = get_state()
    state.error = None
    await on_refresh()


def _refresh_status(state: AppState) -> None:
    if state.refreshing:
        ui.label("refreshing…").classes("ff-label")
    elif state.last_refresh is not None:
        ui.label(f"updated {state.last_refresh:%H:%M}").classes("ff-label")


def _header() -> None:
    state = get_state()
    with ui.header().classes("items-center justify-between q-px-md").style(
        f"background:{'#000000'};border-bottom:1px solid {BORDER}"
    ):
        with ui.row().classes("items-center gap-4"):
            ui.label(state.ctx.name if state.ctx else "FF-ANALYST").style(f"color:{TEXT}")
            if state.ctx is not None:
                ui.label(f"WEEK {state.ctx.week}").classes("ff-label")
            _refresh_status(state)
        with ui.row().classes("items-center gap-4"):
            _league_switcher(state)
            ui.toggle(list(SOURCES), value=state.source, on_change=lambda e: _on_source_change(e.value))
            ui.button("Refresh", on_click=on_refresh)
    with ui.row().classes("items-center gap-4 q-px-md").style(f"border-bottom:1px solid {BORDER}"):
        registered = _registered_paths()
        for label, path in SCREEN_LINKS:
            if path in registered:
                ui.link(label, path).classes("ff-label")


def _error_banner() -> None:
    state = get_state()
    if not state.error:
        return
    with ui.row().classes("ff-card q-pa-sm items-center justify-between gap-4 w-full"):
        ui.label(friendly_error(state.error)).style(f"color:{NEGATIVE}")
        ui.button("Retry", on_click=_on_retry)


def ensure_loaded(state: AppState) -> bool:
    """Renders the loading or error block until the league context is ready.

    Returns True when the page should keep rendering, False when it already
    rendered the loading/error placeholder and the caller should stop.
    """
    if state.ctx is not None:
        return True
    if state.error is None:
        ui.label("loading league data").classes("ff-label")
        ui.spinner(size="lg")

        def _poll() -> None:
            if state.ctx is not None or state.error is not None:
                ui.navigate.reload()

        ui.timer(2.0, _poll)
    return False


@contextmanager
def frame(title: str) -> Iterator[ui.column]:
    """Renders the header and nav, then yields a content container for the screen."""
    _header()
    _error_banner()
    with ui.column().classes("w-full q-pa-md gap-4") as content:
        ui.label(title.upper()).classes("ff-label")
        yield content

