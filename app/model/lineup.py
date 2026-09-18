"""Exact optimal starting lineup for a week and team strength over remaining weeks."""
from __future__ import annotations

from functools import lru_cache

import polars as pl

from app.model.replacement import FLEX_ELIGIBILITY


def eligible(slot: str, position: str) -> bool:
    if slot in FLEX_ELIGIBILITY:
        return position in FLEX_ELIGIBILITY[slot]
    return slot == position


def optimal_lineup(slots: list[str], players: list[tuple[str, str, float]]) -> tuple[float, dict[str, str]]:
    n = len(players)

    @lru_cache(maxsize=None)
    def best(i: int, used: int) -> tuple[float, tuple[tuple[int, int], ...]]:
        if i == len(slots):
            return 0.0, ()
        skip = best(i + 1, used)
        result = skip
        for j in range(n):
            if used & (1 << j) or not eligible(slots[i], players[j][1]):
                continue
            total, picks = best(i + 1, used | (1 << j))
            candidate = (total + players[j][2], ((i, j),) + picks)
            if candidate[0] > result[0]:
                result = candidate
        return result

    total, picks = best(0, 0)
    return total, {str(i): players[j][0] for i, j in picks}


def weekly_strength(slots: list[str], weekly: pl.DataFrame, player_ids: set[str]) -> pl.DataFrame:
    mine = weekly.filter(pl.col("sleeper_id").is_in(list(player_ids)))
    rows: list[tuple[int, float]] = []
    for (week,), frame in mine.group_by("week"):
        players = list(zip(frame["sleeper_id"].to_list(), frame["position"].to_list(), frame["points"].to_list()))
        rows.append((int(week), optimal_lineup(slots, players)[0]))
    return pl.DataFrame(rows, schema={"week": pl.Int64, "points": pl.Float64}, orient="row").sort("week")


def team_strength(slots: list[str], weekly: pl.DataFrame, player_ids: set[str]) -> float:
    return float(weekly_strength(slots, weekly, player_ids)["points"].sum())
