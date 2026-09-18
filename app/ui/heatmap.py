from __future__ import annotations

from nicegui import ui

from app.model.heatmap import position_strength, surplus
from app.ui.layout import frame
from app.ui.state import get_state
from app.ui.theme import ACCENT, CARD, MUTED, NEGATIVE, POSITIVE, TEXT, echart_base


@ui.page("/heatmap")
def heatmap_page() -> None:
    with frame("Heatmap"):
        state = get_state()
        ctx = state.ctx
        proj = state.primary()
        source = state.source

        strength = position_strength(ctx, proj)
        data = surplus(strength)

        positions = sorted({s for s in ctx.slots if s not in ("FLEX", "SUPER_FLEX", "REC_FLEX", "WRRB_FLEX")})
        teams = sorted(data["team_name"].unique().to_list())

        median_max = data.group_by("position").agg(["median"]).select("median").max().item()
        surplus_max = max(abs(data["surplus"].min()), abs(data["surplus"].max()))

        heatmap_data: list[list[int | float]] = []
        for y_idx, team in enumerate(teams):
            team_data = data.filter(data["team_name"] == team)
            for x_idx, pos in enumerate(positions):
                pos_data = team_data.filter(team_data["position"] == pos)
                if len(pos_data) > 0:
                    value = round(pos_data["surplus"][0], 1)
                    heatmap_data.append([x_idx, y_idx, value])

        options = {
            **echart_base,
            "title": {"text": "", "left": "center"},
            "tooltip": {
                "position": "top",
                "formatter": lambda params: (
                    f"{teams[params['value'][1]]} {positions[params['value'][0]]}<br/>"
                    f"Surplus: {params['value'][2]:.1f}"
                ),
                "backgroundColor": CARD,
                "borderColor": "#262626",
                "textStyle": {"color": TEXT},
            },
            "grid": {"height": "80%", "top": 40},
            "xAxis": {
                "type": "category",
                "data": positions,
                "splitArea": {"show": False},
                "axisLine": {"lineStyle": {"color": "#262626"}},
                "axisLabel": {"color": MUTED, "fontSize": 11},
            },
            "yAxis": {
                "type": "category",
                "data": teams,
                "splitArea": {"show": False},
                "axisLine": {"lineStyle": {"color": "#262626"}},
                "axisLabel": {"color": MUTED, "fontSize": 11},
            },
            "visualMap": {
                "min": -surplus_max,
                "max": surplus_max,
                "calculable": True,
                "orient": "vertical",
                "right": "auto",
                "bottom": "auto",
                "inRange": {"color": [NEGATIVE, CARD, POSITIVE]},
                "textStyle": {"color": MUTED},
            },
            "series": [
                {
                    "type": "heatmap",
                    "data": heatmap_data,
                    "label": {"show": True, "color": TEXT, "fontSize": 10},
                    "emphasis": {"itemStyle": {"borderColor": ACCENT, "borderWidth": 2}},
                }
            ],
        }

        ui.echart(options)

        with ui.row():
            ui.label(f"Projection: {source}").classes("text-xs").style(f"color: {MUTED}")
