"""Starter counts and replacement level derived from the league's roster slots."""
from __future__ import annotations

from collections import Counter
from typing import Sequence

import polars as pl

from app.data.scoring import SKILL_POSITIONS

NON_STARTER_SLOTS = ("BN", "IR", "TAXI")
FLEX_ELIGIBILITY: dict[str, tuple[str, ...]] = {
    "FLEX": ("RB", "WR", "TE"),
    "SUPER_FLEX": ("QB", "RB", "WR", "TE"),
    "REC_FLEX": ("WR", "TE"),
    "WRRB_FLEX": ("RB", "WR"),
}


def starter_slots(roster_positions: Sequence[str]) -> list[str]:
    return [slot for slot in roster_positions if slot not in NON_STARTER_SLOTS]


def dedicated_counts(roster_positions: Sequence[str]) -> dict[str, int]:
    return dict(Counter(slot for slot in starter_slots(roster_positions) if slot not in FLEX_ELIGIBILITY))


def _flex_slots(roster_positions: Sequence[str]) -> list[str]:
    return [slot for slot in starter_slots(roster_positions) if slot in FLEX_ELIGIBILITY]


def starters_per_position(roster_positions: Sequence[str], n_teams: int, ppg: pl.DataFrame) -> dict[str, int]:
    """Dedicated slots times teams, plus flex slots given to the best remaining eligible players."""
    starters = {pos: count * n_teams for pos, count in dedicated_counts(roster_positions).items()}
    ranked = ppg.sort("ppg", descending=True).with_columns(
        pl.col("ppg").rank("ordinal", descending=True).over("position").alias("pos_rank")
    )
    taken = ranked.filter(pl.col("pos_rank") <= pl.col("position").replace_strict(starters, default=0))
    remaining = ranked.join(taken.select("player_id"), on="player_id", how="anti")
    for slot in _flex_slots(roster_positions):
        eligible = remaining.filter(pl.col("position").is_in(FLEX_ELIGIBILITY[slot])).head(n_teams)
        for pos, count in Counter(eligible["position"].to_list()).items():
            starters[pos] = starters.get(pos, 0) + count
        remaining = remaining.join(eligible.select("player_id"), on="player_id", how="anti")
    return starters


def replacement_ranks(roster_positions: Sequence[str], n_teams: int, ppg: pl.DataFrame) -> dict[str, int]:
    """Rank of the first replacement-level player: starters plus a bench share, then one more."""
    starters = starters_per_position(roster_positions, n_teams, ppg)
    bench_total = sum(1 for slot in roster_positions if slot == "BN") * n_teams
    skill_starters = sum(starters.get(p, 0) for p in SKILL_POSITIONS)
    ranks: dict[str, int] = {}
    allocated = 0
    for pos, count in starters.items():
        share = round(bench_total * count / skill_starters) if pos in SKILL_POSITIONS and skill_starters else 0
        allocated += share
        ranks[pos] = count + share + 1
    if skill_starters:
        ranks["RB"] = ranks.get("RB", 1) + (bench_total - allocated)
    return ranks


def replacement_ppg(ppg: pl.DataFrame, ranks: dict[str, int]) -> dict[str, float]:
    out: dict[str, float] = {}
    for pos, rank in ranks.items():
        values = ppg.filter(pl.col("position") == pos).sort("ppg", descending=True)["ppg"].to_list()
        if values:
            out[pos] = float(values[min(rank, len(values)) - 1])
    return out


def replacement_from_rosters(ppg: pl.DataFrame, rostered_ids: set[str]) -> dict[str, float]:
    free = ppg.filter(~pl.col("player_id").is_in(list(rostered_ids)))
    best = free.group_by("position").agg(pl.col("ppg").max())
    out = {str(pos): float(val) for pos, val in zip(best["position"], best["ppg"])}
    for pos in ppg["position"].unique().to_list():
        out.setdefault(str(pos), 0.0)
    return out
