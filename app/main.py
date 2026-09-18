"""Entry point: theme, screen registration, startup refresh, and the native/browser window."""
from __future__ import annotations

import os

from nicegui import app, background_tasks, run, ui

from app.ui.screens import register_all
from app.ui.state import AppState, get_state
from app.ui.theme import apply_theme

NATIVE_WINDOW_SIZE = (1440, 900)
BROWSER_PORT = 8765


async def _background_refresh(state: AppState) -> None:
    try:
        await run.io_bound(state.refresh)
    except Exception as exc:  # startup refresh must never crash the app; surface it instead
        state.error = str(exc)
        print(f"startup refresh failed: {exc}")


async def _on_startup() -> None:
    state = get_state()
    try:
        stale = state.is_stale()
    except Exception as exc:
        state.error = str(exc)
        print(f"startup check failed: {exc}")
        return
    if stale:
        background_tasks.create(_background_refresh(state), name="startup_refresh")
        return
    try:
        state.load()
    except Exception as exc:
        state.error = str(exc)
        print(f"startup load failed: {exc}")


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
