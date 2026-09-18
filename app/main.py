"""Entry point: theme, screen registration, startup refresh, and the native/browser window."""
from __future__ import annotations

import os

from nicegui import app, background_tasks, run, ui

from app.ui.screens import register_all
from app.ui.state import AppState, get_state, startup_plan
from app.ui.theme import apply_theme

NATIVE_WINDOW_SIZE = (1440, 900)
BROWSER_PORT = 8765


async def _background_refresh(state: AppState) -> None:
    state.refreshing = True
    try:
        await run.io_bound(state.refresh)
    except Exception as exc:  # background refresh must never crash the app; surface it instead
        state.error = str(exc)
        print(f"background refresh failed: {exc}")
    finally:
        state.refreshing = False


async def _load_then_maybe_refresh(state: AppState, refresh_after: bool) -> None:
    """Load the cached league first so screens render, then refresh if it's stale."""
    try:
        await run.io_bound(state.load)
    except Exception as exc:
        state.error = str(exc)
        print(f"startup load failed: {exc}")
        return
    if refresh_after:
        await _background_refresh(state)


async def _on_startup() -> None:
    state = get_state()
    try:
        has_cache = state.has_cache()
        stale = state.is_stale()
    except Exception as exc:
        state.error = str(exc)
        print(f"startup check failed: {exc}")
        return
    load_now, refresh_in_background = startup_plan(has_cache, stale)
    if load_now:
        background_tasks.create(_load_then_maybe_refresh(state, refresh_in_background), name="startup_load")
    elif refresh_in_background:
        background_tasks.create(_background_refresh(state), name="startup_refresh")


def _register_root() -> None:
    heatmap_registered = any(getattr(route, "path", None) == "/heatmap" for route in app.routes)

    @ui.page("/")
    def index() -> None:
        if heatmap_registered:
            ui.navigate.to("/heatmap")
        else:
            ui.label("screens loading").classes("ff-label")


def run_app() -> None:
    apply_theme()
    register_all()
    _register_root()
    app.on_startup(_on_startup)
    if os.environ.get("FF_NATIVE", "1") == "0":
        ui.run(host="127.0.0.1", port=BROWSER_PORT, title="ff-analyst", dark=True, reload=False, show=False)
    else:
        ui.run(native=True, dark=True, title="ff-analyst", window_size=NATIVE_WINDOW_SIZE, reload=False, show=False)
