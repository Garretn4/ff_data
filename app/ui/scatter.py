from __future__ import annotations

import html
import polars as pl
from nicegui import ui

from app.model.scatter import quadrant_labels, scatter_points
from app.ui.layout import frame
from app.ui.state import get_state
from app.ui.theme import ACCENT, CARD, MUTED, TEXT, echart_base, team_color


@ui.page("/scatter")
def scatter_page() -> None:
    with frame("Buy-Low Sell-High"):
        state = get_state()
        ctx = state.ctx
        values = state.values
        source = state.source

        show_free = ui.checkbox("Include free agents", value=False)

        def render() -> None:
            for child in container.children[:]:
                child.delete()
            filtered = values.filter(~pl.col("roster_id").is_null()) if not show_free.value else values
            pts = scatter_points(filtered)
            labels = quadrant_labels(pts)

            positions = sorted(set(pts["position"].to_list()))
            pos_to_idx = {pos: i for i, pos in enumerate(positions)}
            series_data = [{"value": [row["market_pct"], row["intrinsic_pct"], html.escape(row["name"])], "itemStyle": {"color": team_color(None)}} for row in pts.to_dicts()]

            options = {
                **echart_base,
                "title": {"text": "Market Value vs Intrinsic Value", "left": "center"},
                "tooltip": {
                    "formatter": ":(p) => '<div>' + p.value[2] + '</div><div>Market ' + p.value[0].toFixed(0) + '%</div><div>Intrinsic ' + p.value[1].toFixed(0) + '%</div>'",
                    "backgroundColor": CARD,
                    "borderColor": "#262626",
                    "textStyle": {"color": TEXT},
                },
                "grid": {"height": "80%", "top": 40},
                "xAxis": {
                    "type": "value",
                    "name": "Market Value Percentile",
                    "axisLabel": {"color": MUTED, "fontSize": 11},
                    "axisLine": {"lineStyle": {"color": "#262626"}},
                },
                "yAxis": {
                    "type": "value",
                    "name": "Intrinsic Value Percentile",
                    "axisLabel": {"color": MUTED, "fontSize": 11},
                    "axisLine": {"lineStyle": {"color": "#262626"}},
                },
                "series": [
                    {
                        "type": "scatter",
                        "data": series_data,
                        "symbolSize": 8,
                        "emphasis": {"itemStyle": {"borderColor": ACCENT, "borderWidth": 2}},
                    },
                    {
                        "type": "line",
                        "data": [[0, 0], [100, 100]],
                        "lineStyle": {"color": MUTED, "type": "dashed"},
                        "smooth": False,
                    },
                ],
            }

            with container:
                ui.echart(options)
                with ui.row().classes("w-full gap-4"):
                    with ui.card().classes("ff-card flex-1"):
                        ui.label("Buy Low").classes("ff-label")
                        for name in labels["buy_low"]:
                            ui.label(name).classes("text-sm")
                    with ui.card().classes("ff-card flex-1"):
                        ui.label("Sell High").classes("ff-label")
                        for name in labels["sell_high"]:
                            ui.label(name).classes("text-sm")

        container = ui.element("div").classes("w-full")
        show_free.on_change(render)
        render()
