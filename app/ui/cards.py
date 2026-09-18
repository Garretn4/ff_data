from __future__ import annotations

from nicegui import ui

from app.ui.theme import ACCENT, CARD, MUTED, NEGATIVE, POSITIVE, TEXT, echart_base, headshot, team_color


def player_card(row: dict, max_market: float, max_ros: float) -> None:
    """Render a single player card with stats and sparkline."""
    market_value = row.get("market_value") or 0.0
    ros_primary = row.get("ros_primary") or 0.0
    market_pct = (market_value / max_market * 100) if max_market > 0 else 0
    ros_pct = (ros_primary / max_ros * 100) if max_ros > 0 else 0
    intrinsic = row.get("intrinsic", 0.0) or 0.0
    trend = row.get("trend", []) or []

    color = POSITIVE if intrinsic >= 0 else NEGATIVE

    with ui.card().classes("ff-card p-4 flex flex-col gap-3").style(
        f"border-left: 4px solid {team_color(row.get('nfl_team'))}"
    ):
        with ui.row().classes("gap-3 items-start"):
            ui.image(headshot(row["sleeper_id"], row["position"], row.get("nfl_team"))).classes("rounded w-16 h-16")

            with ui.column().classes("flex-1 gap-1"):
                ui.label(row["name"]).classes("font-bold text-sm")
                ui.badge(row["position"]).props(f"color={ACCENT}")
                with ui.row().classes("gap-1 items-center text-xs"):
                    ui.label(row.get("nfl_team", "?")).classes("font-mono")
                    if row.get("injury_status"):
                        ui.label(row["injury_status"]).style(f"color: {NEGATIVE}")

        with ui.row().classes("gap-1 items-center"):
            ui.linear_progress(value=market_pct / 100).classes("flex-1")
            ui.label(f"{market_pct:.0f}%").classes("ff-label")

        with ui.row().classes("gap-1 items-center"):
            ui.linear_progress(value=ros_pct / 100).classes("flex-1")
            ui.label(f"{ros_pct:.0f}%").classes("ff-label")

        ui.label(f"Intrinsic: {intrinsic:+.1f}").style(f"color: {color}; font-size: 0.875rem;")

        if trend:
            trend_options = {
                **echart_base(),
                "xAxis": {"type": "category", "show": False},
                "yAxis": {"type": "value", "show": False},
                "grid": {"left": 0, "right": 0, "top": 0, "bottom": 0},
                "series": [{"type": "line", "data": trend, "smooth": True, "lineStyle": {"width": 2}}],
            }
            ui.echart(trend_options).classes("w-full").style("height: 40px;")
