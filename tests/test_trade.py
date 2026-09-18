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
