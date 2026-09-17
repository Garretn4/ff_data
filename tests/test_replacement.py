import polars as pl

from app.model.replacement import (
    dedicated_counts, replacement_from_rosters, replacement_ppg, replacement_ranks, starter_slots,
    starters_per_position,
)

SLOTS = ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "K", "DEF", "BN", "BN", "BN", "BN", "BN", "BN", "IR"]


def pool() -> pl.DataFrame:
    rows = []
    for pos, count, top in (("QB", 30, 22.0), ("RB", 60, 20.0), ("WR", 80, 19.0), ("TE", 30, 12.0), ("K", 30, 8.0), ("DEF", 32, 8.0)):
        for i in range(count):
            rows.append({"player_id": f"{pos}{i}", "position": pos, "ppg": top - i * 0.25})
    return pl.DataFrame(rows)


def test_starter_slots_and_dedicated_counts() -> None:
    assert starter_slots(SLOTS) == ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "K", "DEF"]
    assert dedicated_counts(SLOTS) == {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "K": 1, "DEF": 1}


def test_flex_goes_to_best_remaining_eligible_player() -> None:
    frame = pl.DataFrame({
        "player_id": ["a", "b", "c", "d", "e", "f"],
        "position": ["RB", "RB", "RB", "WR", "WR", "WR"],
        "ppg": [10.0, 9.0, 8.0, 7.0, 6.0, 5.0],
    })
    starters = starters_per_position(["RB", "WR", "FLEX", "BN"], 1, frame)
    assert starters == {"RB": 2, "WR": 1}


def test_flex_types_fill_most_restrictive_first() -> None:
    frame = pl.DataFrame({
        "player_id": ["q1", "q2", "r1", "w1"],
        "position": ["QB", "QB", "RB", "WR"],
        "ppg": [25.0, 24.0, 15.0, 14.0],
    })
    starters = starters_per_position(["QB", "SUPER_FLEX", "FLEX", "BN"], 1, frame)
    assert starters == {"QB": 2, "RB": 1}
    starters_alt = starters_per_position(["QB", "FLEX", "SUPER_FLEX", "BN"], 1, frame)
    assert starters_alt == {"QB": 2, "RB": 1}


def test_replacement_ranks_add_bench_share_to_skill_positions_only() -> None:
    ranks = replacement_ranks(SLOTS, 10, pool())
    starters = starters_per_position(SLOTS, 10, pool())
    assert ranks["K"] == starters["K"] + 1 and ranks["DEF"] == starters["DEF"] + 1
    skill_bench = sum(ranks[p] - starters[p] - 1 for p in ("QB", "RB", "WR", "TE"))
    assert skill_bench == 60


def test_replacement_ppg_reads_rank_and_clamps() -> None:
    frame = pl.DataFrame({"player_id": ["a", "b", "c"], "position": ["TE"] * 3, "ppg": [10.0, 8.0, 6.0]})
    assert replacement_ppg(frame, {"TE": 2}) == {"TE": 8.0}
    assert replacement_ppg(frame, {"TE": 9}) == {"TE": 6.0}


def test_replacement_from_rosters_is_best_unrostered() -> None:
    frame = pl.DataFrame({"player_id": ["a", "b", "c"], "position": ["RB"] * 3, "ppg": [10.0, 8.0, 6.0]})
    assert replacement_from_rosters(frame, {"a", "b"}) == {"RB": 6.0}
    assert replacement_from_rosters(frame, {"a", "b", "c"}) == {"RB": 0.0}
