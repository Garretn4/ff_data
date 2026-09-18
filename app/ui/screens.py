"""Imports each screen module so its `@ui.page` routes register; tolerates screens not built yet."""
from __future__ import annotations

import importlib

SCREEN_MODULES: tuple[str, ...] = (
    "app.ui.heatmap",
    "app.ui.players",
    "app.ui.scatter",
    "app.ui.trade",
    "app.ui.finder",
    "app.ui.managers",
)


def register_all() -> None:
    """Import every screen module in SCREEN_MODULES, skipping any not written yet."""
    for name in SCREEN_MODULES:
        try:
            importlib.import_module(name)
        except ImportError as exc:
            print(f"screens: skipping {name} ({exc})")
