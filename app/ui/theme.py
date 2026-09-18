"""Palette, NFL team colors, CDN URL helpers, and ECharts base options.

`apply_theme` uses `app.colors` (application-wide) rather than `ui.colors`, and
`ui.add_head_html(..., shared=True)` rather than a bare call: `ui.colors()` and
`ui.dark_mode()` are page-scoped elements and raise when used at import time in an
app that also defines routes with `@ui.page`, which every screen in this app does.
"""
from __future__ import annotations

from nicegui import app, ui

ACCENT = "#F5A524"
BG = "#000000"
CARD = "#0A0A0A"
BORDER = "#262626"
TEXT = "#F2F2F2"
MUTED = "#8A8F98"
FONT = '"JetBrains Mono", "SF Mono", Menlo, Consolas, monospace'
POSITIVE = "#3FB950"
NEGATIVE = "#F85149"

TEAM_COLORS: dict[str, str] = {
    "ARI": "#97233F", "ATL": "#A71930", "BAL": "#241773", "BUF": "#00338D", "CAR": "#0085CA", "CHI": "#0B162A",
    "CIN": "#FB4F14", "CLE": "#311D00", "DAL": "#041E42", "DEN": "#FB4F14", "DET": "#0076B6", "GB": "#203731",
    "HOU": "#03202F", "IND": "#002C5F", "JAX": "#006778", "KC": "#E31837", "LV": "#A5ACAF", "LAC": "#0080C6",
    "LA": "#003594", "LAR": "#003594", "MIA": "#008E97", "MIN": "#4F2683", "NE": "#002244", "NO": "#D3BC8D",
    "NYG": "#0B2265", "NYJ": "#125740", "PHI": "#004C54", "PIT": "#FFB612", "SF": "#AA0000", "SEA": "#002244",
    "TB": "#D50A0A", "TEN": "#0C2340", "WAS": "#5A1414",
}


def team_color(team: str | None) -> str:
    return TEAM_COLORS.get(team or "", MUTED)


def headshot(sleeper_id: str, position: str, nfl_team: str | None) -> str:
    if position == "DEF":
        return f"https://sleepercdn.com/images/team_logos/nfl/{(nfl_team or sleeper_id).lower()}.png"
    return f"https://sleepercdn.com/content/nfl/players/thumb/{sleeper_id}.jpg"


def avatar(avatar_id: str | None) -> str | None:
    return f"https://sleepercdn.com/avatars/thumbs/{avatar_id}" if avatar_id else None


def echart_base() -> dict:
    return {
        "backgroundColor": "transparent",
        "color": [ACCENT, "#4C9AFF", POSITIVE, NEGATIVE, "#B68CFF"],
        "textStyle": {"color": TEXT, "fontFamily": FONT},
        "grid": {"left": 48, "right": 24, "top": 32, "bottom": 40},
    }


def apply_theme() -> None:
    """Set the app-wide palette and inject the base font and card/label CSS."""
    app.colors(primary=ACCENT, secondary=MUTED, accent=ACCENT, dark=CARD, dark_page=BG,
               positive=POSITIVE, negative=NEGATIVE)
    ui.add_head_html(
        f"<style>"
        f"body{{background:{BG};color:{TEXT};font-family:{FONT}}} "
        f".ff-card{{background:{CARD};border:1px solid {BORDER};border-radius:4px}} "
        f".ff-label{{font-size:11px;letter-spacing:.08em;text-transform:uppercase;color:{MUTED}}}"
        f"</style>",
        shared=True,
    )
