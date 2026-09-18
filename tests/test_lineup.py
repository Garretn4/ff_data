import random
import time
from functools import lru_cache

import polars as pl
import pytest

from app.model.lineup import eligible, optimal_lineup, team_strength, weekly_strength

POSITIONS = ["QB", "RB", "WR", "TE", "K", "DEF"]
SLOTS = ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "FLEX", "K", "DEF"]
SUPER_FLEX_SLOTS = ["QB", "RB", "RB", "WR", "WR", "TE", "SUPER_FLEX", "FLEX", "K", "DEF"]


def test_eligibility() -> None:
    assert eligible("FLEX", "RB") and eligible("FLEX", "TE") and not eligible("FLEX", "QB")
    assert eligible("SUPER_FLEX", "QB") and eligible("DEF", "DEF") and not eligible("K", "DEF")


def test_optimal_lineup_uses_flex_for_best_leftover() -> None:
    slots = ["RB", "WR", "FLEX"]
    players = [("r1", "RB", 20.0), ("r2", "RB", 15.0), ("w1", "WR", 10.0), ("w2", "WR", 14.0), ("t1", "TE", 5.0)]
    total, assignment = optimal_lineup(slots, players)
    assert total == 49.0
    assert set(assignment.values()) == {"r1", "r2", "w2"}


def test_optimal_lineup_skips_unfillable_slot() -> None:
    total, assignment = optimal_lineup(["QB", "RB"], [("r1", "RB", 9.0)])
    assert total == 9.0 and assignment == {"1": "r1"}


def test_two_for_one_only_counts_if_it_starts() -> None:
    slots = ["RB", "RB"]
    weekly = pl.DataFrame({"sleeper_id": ["a", "b", "c"] * 2, "position": ["RB"] * 6,
                           "week": [1, 1, 1, 2, 2, 2], "points": [20.0, 15.0, 5.0, 20.0, 15.0, 5.0]})
    assert team_strength(slots, weekly, {"a", "b"}) == 70.0
    assert team_strength(slots, weekly, {"a", "b", "c"}) == 70.0
    assert team_strength(slots, weekly, {"a", "c"}) == 50.0


def test_team_strength_matches_weekly_strength_sum() -> None:
    slots = ["RB", "RB"]
    weekly = pl.DataFrame({"sleeper_id": ["a", "b", "c"] * 2, "position": ["RB"] * 6,
                           "week": [1, 1, 1, 2, 2, 2], "points": [20.0, 15.0, 5.0, 20.0, 15.0, 5.0]})
    ids = {"a", "b", "c"}
    assert team_strength(slots, weekly, ids) == pytest.approx(weekly_strength(slots, weekly, ids)["points"].sum())


def _random_players(rng: random.Random, count: int) -> list[tuple[str, str, float]]:
    return [(f"p{i}", rng.choice(POSITIONS), round(rng.uniform(0, 30), 2)) for i in range(count)]


def _reference_optimal(slots: list[str], players: list[tuple[str, str, float]]) -> float:
    """Old, unoptimized bitmask DP over every slot and every player; kept only as a test oracle."""
    n = len(players)

    @lru_cache(maxsize=None)
    def best(i: int, used: int) -> float:
        if i == len(slots):
            return 0.0
        result = best(i + 1, used)
        for j in range(n):
            if used & (1 << j) or not eligible(slots[i], players[j][1]):
                continue
            candidate = players[j][2] + best(i + 1, used | (1 << j))
            if candidate > result:
                result = candidate
        return result

    return best(0, 0)


def _assert_valid_assignment(slots: list[str], players: list[tuple[str, str, float]],
                             total: float, assignment: dict[str, str]) -> None:
    by_id = {pid: (pos, pts) for pid, pos, pts in players}
    assert len(set(assignment.values())) == len(assignment), "player used twice"
    running = 0.0
    for slot_i, pid in assignment.items():
        pos, pts = by_id[pid]
        assert eligible(slots[int(slot_i)], pos), f"{pid} ({pos}) not eligible for slot {slot_i} ({slots[int(slot_i)]})"
        running += pts
    assert running == pytest.approx(total)


def test_solver_matches_brute_force_on_random_rosters() -> None:
    for seed in range(50):
        rng = random.Random(seed)
        slots = SUPER_FLEX_SLOTS if seed % 5 == 0 else SLOTS
        count = rng.randint(14, 16)
        players = _random_players(rng, count)
        expected = _reference_optimal(slots, players)
        total, assignment = optimal_lineup(slots, players)
        assert total == pytest.approx(expected), f"seed {seed}: {total} != {expected}"
        _assert_valid_assignment(slots, players, total, assignment)


def test_solver_speed() -> None:
    rng = random.Random(2026)
    players = _random_players(rng, 16)
    start = time.perf_counter()
    for _ in range(1500):
        optimal_lineup(SLOTS, players)
    elapsed = time.perf_counter() - start
    assert elapsed < 1.0, f"1500 calls took {elapsed:.3f}s"
