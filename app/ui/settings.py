"""Settings screen: edit config.local.toml and apply the change without a restart."""
from __future__ import annotations

import os

from nicegui import run, ui

from app.config import CONFIG_FILE, SAVED_KEYS, SOURCES, config_from_form, validate_settings
from app.ui.layout import frame, on_refresh
from app.ui.state import get_state
from app.ui.theme import MUTED, NEGATIVE, POSITIVE

MESSAGES = {
    "league": "League saved and reloaded",
    "history": "History range saved. Refresh to pull the new seasons.",
    "model": "Blend weights saved and projections recomputed",
    "source": "Default source saved",
    "none": "Nothing changed",
}
FIELD_HELP = {
    "league_id": "Sleeper league id (digits)",
    "usernames": "Sleeper display names, comma-separated; the first one with a roster is you",
    "source_default": "Projection source the app opens with",
    "stats_from": "First season of box-score history to load",
    "detail_from": "First season of play-by-play detail to load",
    "blend_k": "Shrinkage strength: games of evidence that equal the prior",
    "blend_a": "Weight on consensus rank within the prior",
    "blend_b": "Weight on last season within the prior",
}


@ui.page("/settings")
def settings_page() -> None:
    with frame("Settings"):
        state = get_state()
        form: dict[str, object] = {key: getattr(state.cfg, key) for key in SAVED_KEYS}
        form["usernames"] = ", ".join(state.cfg.usernames)

        if os.environ.get("FF_LEAGUE_ID"):
            ui.label("FF_LEAGUE_ID is set in the environment and overrides the saved league id on the next start.") \
                .style(f"color:{NEGATIVE}")

        with ui.column().classes("ff-card q-pa-lg gap-2").style("max-width:520px"):
            error_label = ui.label("").style(f"color:{NEGATIVE};white-space:pre-line")
            refresh_row = ui.row().classes("items-center gap-3")
            refresh_row.visible = False

            save: ui.button | None = None

            def check() -> bool:
                errors = validate_settings(form, state.cfg.current_season)
                error_label.text = "\n".join(f"{key}: {msg}" for key, msg in errors.items())
                if save is not None:
                    save.enabled = not errors
                return not errors

            ui.input("League id", on_change=lambda _: check()).bind_value(form, "league_id").props("dark")
            ui.label(FIELD_HELP["league_id"]).classes("ff-label")
            ui.input("Usernames", on_change=lambda _: check()).bind_value(form, "usernames").props("dark")
            ui.label(FIELD_HELP["usernames"]).classes("ff-label")
            ui.select(list(SOURCES), label="Default source", on_change=lambda _: check()) \
                .bind_value(form, "source_default").props("dark")
            ui.label(FIELD_HELP["source_default"]).classes("ff-label")
            with ui.row().classes("gap-4"):
                ui.number("Stats from", format="%d", on_change=lambda _: check()).bind_value(form, "stats_from").props("dark")
                ui.number("Detail from", format="%d", on_change=lambda _: check()).bind_value(form, "detail_from").props("dark")
            ui.label(f"{FIELD_HELP['stats_from']}. {FIELD_HELP['detail_from']}.").classes("ff-label")
            with ui.row().classes("gap-4"):
                ui.number("Blend k", step=0.5, on_change=lambda _: check()).bind_value(form, "blend_k").props("dark")
                ui.number("Blend a", step=0.05, on_change=lambda _: check()).bind_value(form, "blend_a").props("dark")
                ui.number("Blend b", step=0.05, on_change=lambda _: check()).bind_value(form, "blend_b").props("dark")
            ui.label(f"{FIELD_HELP['blend_k']}. {FIELD_HELP['blend_a']}. {FIELD_HELP['blend_b']}.").classes("ff-label")

            async def on_save() -> None:
                if not check():
                    return
                new_cfg = config_from_form(form, state.cfg)
                save.enabled = False
                try:
                    scope = await run.io_bound(state.apply_settings, new_cfg)
                except Exception as exc:  # UI boundary: caught, recorded, and surfaced, never swallowed
                    state.error = str(exc)
                    ui.notify(f"Could not save settings: {exc}", type="negative")
                    return
                finally:
                    save.enabled = True
                state.error = None
                ui.notify(MESSAGES[scope], type="positive" if scope != "none" else "info")
                refresh_row.visible = scope == "history"

            save = ui.button("Save", on_click=on_save)
            check()
            with refresh_row:
                ui.label("New history range is saved but not loaded.").style(f"color:{MUTED}")
                ui.button("Refresh now", on_click=on_refresh).props(f"color={POSITIVE}")

        ui.label(f"Config file: {state.cfg.root / CONFIG_FILE}").classes("ff-label")
