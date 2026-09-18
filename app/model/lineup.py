"""Exact optimal starting lineup for a week and team strength over remaining weeks."""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

import polars as pl

from app.model.replacement import FLEX_ELIGIBILITY

WeeklyIndex = dict[int, dict[str, tuple[str, float]]]

Player = tuple[str, str, float]


def eligible(slot: str, position: str) -> bool:
    if slot in FLEX_ELIGIBILITY:
        return position in FLEX_ELIGIBILITY[slot]
    return slot == position


@dataclass(frozen=True)
class _SlotPlan:
    dedicated_indices: dict[str, tuple[int, ...]]
    flex_indices: tuple[int, ...]
    flex_slots: tuple[str, ...]
    flex_caps: dict[str, int]


@lru_cache(maxsize=None)
def _build_plan(slots: tuple[str, ...]) -> _SlotPlan:
    dedicated_indices: dict[str, list[int]] = {}
    flex_indices: list[int] = []
    for i, slot in enumerate(slots):
        if slot in FLEX_ELIGIBILITY:
            flex_indices.append(i)
        else:
            dedicated_indices.setdefault(slot, []).append(i)
    flex_slots = tuple(slots[i] for i in flex_indices)
    positions = {pos for slot in flex_slots for pos in FLEX_ELIGIBILITY[slot]}
    flex_caps = {pos: sum(1 for slot in flex_slots if pos in FLEX_ELIGIBILITY[slot]) for pos in positions}
    return _SlotPlan(
        dedicated_indices={pos: tuple(indices) for pos, indices in dedicated_indices.items()},
        flex_indices=tuple(flex_indices),
        flex_slots=flex_slots,
        flex_caps=flex_caps,
    )


def _ranked(players: list[Player]) -> dict[str, list[Player]]:
    by_position: dict[str, list[Player]] = {}
    for player in players:
        by_position.setdefault(player[1], []).append(player)
    for group in by_position.values():
        group.sort(key=lambda p: (-p[2], p[0]))
    return by_position


def _assign_flex(flex_slots: tuple[str, ...], pool: tuple[Player, ...]) -> tuple[float, tuple[tuple[int, int], ...]]:
    n = len(pool)

    @lru_cache(maxsize=None)
    def best(i: int, used: int) -> tuple[float, tuple[tuple[int, int], ...]]:
        if i == len(flex_slots):
            return 0.0, ()
        result = best(i + 1, used)
        for j in range(n):
            if used & (1 << j) or not eligible(flex_slots[i], pool[j][1]):
                continue
            total, picks = best(i + 1, used | (1 << j))
            candidate = (total + pool[j][2], ((i, j),) + picks)
            if candidate[0] > result[0]:
                result = candidate
        return result

    return best(0, 0)


def optimal_lineup(slots: list[str], players: list[Player]) -> tuple[float, dict[str, str]]:
    plan = _build_plan(tuple(slots))
    by_position = _ranked(players)

    assignment: dict[str, str] = {}
    total = 0.0
    used_ids: set[str] = set()
    for pos, indices in plan.dedicated_indices.items():
        taken = by_position.get(pos, [])[: len(indices)]
        for slot_i, player in zip(indices, taken):
            assignment[str(slot_i)] = player[0]
            total += player[2]
            used_ids.add(player[0])

    pool_by_position = _ranked([p for p in players if p[0] not in used_ids])
    pruned_pool: list[Player] = []
    for pos, group in pool_by_position.items():
        pruned_pool.extend(group[: plan.flex_caps.get(pos, 0)])

    flex_total, flex_picks = _assign_flex(plan.flex_slots, tuple(pruned_pool))
    total += flex_total
    for local_i, local_j in flex_picks:
        assignment[str(plan.flex_indices[local_i])] = pruned_pool[local_j][0]

    return total, assignment


def index_weekly(weekly: pl.DataFrame) -> WeeklyIndex:
    index: WeeklyIndex = {}
    for week, player_id, position, points in zip(
        weekly["week"].to_list(), weekly["sleeper_id"].to_list(), weekly["position"].to_list(), weekly["points"].to_list()
    ):
        index.setdefault(int(week), {})[player_id] = (position, float(points))
    return index


def strength_from_index(slots: list[str], index: WeeklyIndex, player_ids: set[str]) -> float:
    total = 0.0
    for week_players in index.values():
        players = [(pid, week_players[pid][0], week_players[pid][1]) for pid in player_ids if pid in week_players]
        total += optimal_lineup(slots, players)[0]
    return total


def weekly_strength(slots: list[str], weekly: pl.DataFrame, player_ids: set[str]) -> pl.DataFrame:
    mine = weekly.filter(pl.col("sleeper_id").is_in(list(player_ids)))
    rows: list[tuple[int, float]] = []
    for (week,), frame in mine.group_by("week"):
        players = list(zip(frame["sleeper_id"].to_list(), frame["position"].to_list(), frame["points"].to_list()))
        rows.append((int(week), optimal_lineup(slots, players)[0]))
    return pl.DataFrame(rows, schema={"week": pl.Int64, "points": pl.Float64}, orient="row").sort("week")


def team_strength(slots: list[str], weekly: pl.DataFrame, player_ids: set[str]) -> float:
    return strength_from_index(slots, index_weekly(weekly), player_ids)
