import random
import time

import polars as pl

from app.model.finder import find_trades
from app.model.league import LeagueContext, Roster
from app.model.live import Projection
from app.model.trade import trade_delta


def ctx_with(rosters: tuple[Roster, ...]) -> LeagueContext:
    return LeagueContext(league_id="1", name="t", season=2026, week=3, n_teams=2, scoring={"rec": 1.0},
                         roster_positions=["RB", "RB", "BN"], slots=["RB", "RB"], playoff_week_start=15,
                         last_week=4, trade_deadline=12, rosters=rosters)


def weekly(points: dict[str, float]) -> pl.DataFrame:
    rows = [(pid, "RB", w, p) for pid, p in points.items() for w in (3, 4)]
    return pl.DataFrame(rows, schema={"sleeper_id": pl.Utf8, "position": pl.Utf8, "week": pl.Int64, "points": pl.Float64}, orient="row")


def test_two_for_one_grades_by_lineup_not_sum() -> None:
    a = Roster(1, "u1", "A", "A", None, ("a1", "a2", "a3"), (), ())
    b = Roster(2, "u2", "B", "B", None, ("b1", "b2"), (), ())
    ctx = ctx_with((a, b))
    proj = Projection("consensus", weekly({"a1": 20.0, "a2": 10.0, "a3": 9.0, "b1": 25.0, "b2": 5.0}), pl.DataFrame())
    result = trade_delta(ctx, proj, a, b, give_a={"a2", "a3"}, give_b={"b1"})
    assert result.delta_a == (25 + 20 - (20 + 10)) * 2
    assert result.delta_b == (10 + 9 - (25 + 5)) * 2


def test_find_trades_returns_mutual_gains_only() -> None:
    a = Roster(1, "u1", "A", "A", None, ("a1", "a2", "a3"), (), ())
    b = Roster(2, "u2", "B", "B", None, ("b1", "b2", "b3"), (), ())
    ctx = LeagueContext(league_id="1", name="t", season=2026, week=3, n_teams=2, scoring={"rec": 1.0},
                        roster_positions=["RB", "WR", "BN"], slots=["RB", "WR"], playoff_week_start=15,
                        last_week=4, trade_deadline=12, rosters=(a, b))
    rows = [("a1", "RB", 20.0), ("a2", "RB", 18.0), ("a3", "WR", 5.0), ("b1", "RB", 8.0), ("b2", "WR", 15.0), ("b3", "WR", 14.0)]
    weekly_data = pl.DataFrame([(pid, pos, w, p) for pid, pos, p in rows for w in (3, 4)],
                          schema={"sleeper_id": pl.Utf8, "position": pl.Utf8, "week": pl.Int64, "points": pl.Float64}, orient="row")
    proj = Projection("consensus", weekly_data, pl.DataFrame())
    values = pl.DataFrame({"sleeper_id": ["a1", "a2", "a3", "b1", "b2", "b3"], "roster_id": [1, 1, 1, 2, 2, 2],
                           "intrinsic": [20.0, 18.0, 5.0, 8.0, 15.0, 14.0], "ros_primary": [40.0, 36, 10, 16, 30, 28]})
    deals = find_trades(ctx, proj, values, my_roster_id=1, opp_roster_id=2)
    assert deals, "expected at least one mutual-gain deal"
    assert all(r.delta_a > 0 and r.delta_b > 0 for r, _, _ in deals)
    assert any(give == ("a2",) and get == ("b3",) for _, give, get in deals)
    best = deals[0][0]
    assert min(best.delta_a, best.delta_b) == 18.0


def test_received_reserve_player_is_not_startable() -> None:
    a = Roster(1, "u1", "A", "A", None, ("a1",), (), ())
    b = Roster(2, "u2", "B", "B", None, ("b1", "b2"), (), ("b2",))
    ctx = ctx_with((a, b))
    proj = Projection("consensus", weekly({"a1": 10.0, "b1": 8.0, "b2": 50.0}), pl.DataFrame())
    result = trade_delta(ctx, proj, a, b, give_a=set(), give_b={"b2"})
    assert result.delta_a == 0.0


CYCLE = ["QB", "RB", "RB", "RB", "WR", "WR", "WR", "WR", "TE", "TE", "K", "DEF", "RB", "WR", "QB"]


def _team(prefix: str) -> dict[str, str]:
    return {f"{prefix}{i}": pos for i, pos in enumerate(CYCLE)}


def _weekly_frame(rng: random.Random, positions: dict[str, str], weeks: list[int]) -> pl.DataFrame:
    rows = [(pid, pos, w, round(rng.uniform(0, 25), 2)) for pid, pos in positions.items() for w in weeks]
    return pl.DataFrame(rows, schema={"sleeper_id": pl.Utf8, "position": pl.Utf8, "week": pl.Int64, "points": pl.Float64},
                        orient="row")


def test_finder_is_interactive() -> None:
    rng = random.Random(7)
    a_positions = _team("a")
    b_positions = _team("b")
    a = Roster(1, "u1", "A", "A", None, tuple(a_positions), (), ())
    b = Roster(2, "u2", "B", "B", None, tuple(b_positions), (), ())
    ctx = LeagueContext(league_id="1", name="t", season=2026, week=3, n_teams=2, scoring={"rec": 1.0},
                        roster_positions=["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "K", "DEF", "BN"],
                        slots=["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "K", "DEF"], playoff_week_start=15,
                        last_week=17, trade_deadline=12, rosters=(a, b))
    weeks = list(range(3, 18))
    weekly_data = pl.concat([_weekly_frame(rng, a_positions, weeks), _weekly_frame(rng, b_positions, weeks)])
    proj = Projection("consensus", weekly_data, pl.DataFrame())
    all_positions = {**a_positions, **b_positions}
    values = pl.DataFrame({
        "sleeper_id": list(all_positions),
        "roster_id": [1] * len(a_positions) + [2] * len(b_positions),
        "intrinsic": [round(rng.uniform(0, 40), 2) for _ in all_positions],
        "ros_primary": [round(rng.uniform(0, 40), 2) for _ in all_positions],
    })
    start = time.perf_counter()
    deals = find_trades(ctx, proj, values, my_roster_id=1, opp_roster_id=2, top_n=12)
    elapsed = time.perf_counter() - start
    assert isinstance(deals, list)
    assert elapsed < 15.0, f"find_trades took {elapsed:.2f}s"
