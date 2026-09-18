from __future__ import annotations

import polars as pl
from nicegui import ui

from app.ui.cards import player_card
from app.ui.layout import frame
from app.ui.state import get_state
from app.ui.theme import MUTED


@ui.page("/players")
def players_page() -> None:
    with frame("Players"):
        state = get_state()
        ctx = state.ctx
        values = state.values
        source = state.source

        positions = sorted(set(values.filter(pl.col("position").is_not_null())["position"].to_list()))
        teams = sorted(set(values.filter(pl.col("team_name").is_not_null())["team_name"].to_list()))

        with ui.row().classes("gap-4"):
            position_filter = ui.select(label="Position", value="", options=[""] + positions).classes("flex-1")
            owner_filter = ui.select(label="Owner", value="", options=["", "Free agents"] + teams).classes("flex-1")
            search_input = ui.input(label="Search", value="").classes("flex-1")

        def render_players() -> None:
            for child in grid.children[:]:
                child.delete()
            for child in load_more_container.children[:]:
                child.delete()

            filtered = values
            if position_filter.value:
                filtered = filtered.filter(pl.col("position") == position_filter.value)
            if owner_filter.value == "Free agents":
                filtered = filtered.filter(pl.col("roster_id").is_null())
            elif owner_filter.value:
                filtered = filtered.filter(pl.col("team_name") == owner_filter.value)
            if search_input.value:
                search_term = search_input.value.lower()
                filtered = filtered.filter(pl.col("name").str.to_lowercase().str.contains(search_term))

            filtered = filtered.sort("intrinsic", descending=True)

            max_market = max(filtered.filter(pl.col("market_value").is_not_null())["market_value"].max() or 0.0, 0.1)
            max_ros = max(filtered.filter(pl.col("ros_primary").is_not_null())["ros_primary"].max() or 0.0, 0.1)

            with grid:
                for row in filtered.head(30).to_dicts():
                    player_card(row, max_market, max_ros)

            if len(filtered) > 30:
                with load_more_container:
                    def load_more() -> None:
                        start_idx = len(grid.children)
                        for row in filtered.slice(start_idx, 30).to_dicts():
                            player_card(row, max_market, max_ros)
                        if start_idx + 30 >= len(filtered):
                            load_more_btn.delete()
                    load_more_btn = ui.button("Load More", on_click=load_more).classes("w-full")

        grid = ui.grid(columns=3).classes("w-full gap-4")
        load_more_container = ui.element("div").classes("w-full")

        position_filter.on_change(render_players)
        owner_filter.on_change(render_players)
        search_input.on_change(render_players)

        render_players()

        with ui.row():
            ui.label(f"Projection: {source}").classes("text-xs").style(f"color: {MUTED}")
