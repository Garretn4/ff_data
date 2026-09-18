"""League screen: standings, power rankings, and rest-of-season projections for every roster."""
from __future__ import annotations

from nicegui import ui

from app.model.standings import PROJECTION_WEIGHT, league_rows
from app.ui.layout import ensure_loaded, frame
from app.ui.state import get_state
from app.ui.theme import ACCENT

COLUMNS: list[dict[str, object]] = [
    {"name": "rank", "label": "#", "field": "rank", "align": "right", "sortable": True},
    {"name": "team", "label": "Team", "field": "team", "align": "left", "sortable": True},
    {"name": "record", "label": "Record", "field": "record", "align": "center"},
    {"name": "pf", "label": "PF", "field": "pf", "align": "right", "sortable": True},
    {"name": "pa", "label": "PA", "field": "pa", "align": "right", "sortable": True},
    {"name": "model", "label": "Model ROS/wk", "field": "model", "align": "right", "sortable": True},
    {"name": "consensus", "label": "Consensus ROS/wk", "field": "consensus", "align": "right", "sortable": True},
    {"name": "power", "label": "Power", "field": "power", "align": "right", "sortable": True},
]


def _row_slot() -> str:
    return (
        '<q-tr :props="props" :style="props.row.mine ? \'color:' + ACCENT + ';font-weight:600\' : \'\'">'
        '<q-td v-for="col in props.cols" :key="col.name" :props="props">{{ col.value }}</q-td>'
        "</q-tr>"
    )


@ui.page("/league")
def league_page() -> None:
    with frame("League"):
        state = get_state()
        if not ensure_loaded(state):
            return
        try:
            rows = league_rows(state.ctx, state.projections, state.matchups, state.my_roster_id())
        except Exception as exc:  # UI boundary: caught, recorded, and surfaced, never swallowed
            state.error = str(exc)
            ui.notify(f"League table failed: {exc}", type="negative")
            return
        table = ui.table(columns=COLUMNS, rows=rows, row_key="id").classes("w-full ff-card")
        table.props("flat dense dark")
        table.add_slot("body", _row_slot())
        pct = int(PROJECTION_WEIGHT * 100)
        ui.label(
            f"Power = {pct}% z-score of model projected points per remaining week "
            f"+ {100 - pct}% z-score of points for per game. Projection only until games are played."
        ).classes("ff-label")
