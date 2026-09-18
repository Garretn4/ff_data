import json
from pathlib import Path

from app.data.store import Store
from app.model.standings import (
    Pairing, Record, actual_lineup, actual_points, load_matchups, player_points, records, schedule,
)


def rows(*triples: tuple[int, int, float]) -> list[dict]:
    """(roster_id, matchup_id, points) -> Sleeper matchup rows."""
    return [{"roster_id": r, "matchup_id": m, "points": p, "players_points": {}} for r, m, p in triples]


MATCHUPS = {
    1: rows((1, 1, 100.0), (2, 1, 90.0), (3, 2, 80.0), (4, 2, 80.0)),
    2: rows((1, 1, 70.0), (3, 1, 75.0), (2, 2, 60.0), (4, 2, 50.0)),
    3: rows((1, 1, 0.0), (4, 1, 0.0), (2, 2, 0.0), (3, 2, 0.0)),
}


def test_load_matchups_skips_weeks_without_cache(tmp_path: Path) -> None:
    store = Store(tmp_path / "app.sqlite")
    store.put_cached("sleeper:/league/1/matchups/1", json.dumps(MATCHUPS[1]), now=1.0)
    store.put_cached("sleeper:/league/1/matchups/3", json.dumps(MATCHUPS[3]), now=1.0)
    loaded = load_matchups(store, "1", [1, 2, 3])
    assert set(loaded) == {1, 3} and loaded[1][0]["roster_id"] == 1


def test_records_tally_wins_losses_ties_and_points_before_through_week() -> None:
    recs = records(MATCHUPS, through_week=3)
    assert recs[1] == Record(1, 1, 1, 0, 170.0, 165.0)
    assert recs[2] == Record(2, 1, 1, 0, 150.0, 150.0)
    assert recs[3] == Record(3, 1, 0, 1, 155.0, 150.0)
    assert recs[4] == Record(4, 0, 1, 1, 130.0, 140.0)


def test_records_ignore_the_current_week_and_unpaired_rows() -> None:
    lonely = {1: rows((1, 1, 50.0)), 2: rows((1, 1, 99.0), (2, 1, 1.0))}
    assert records(lonely, through_week=2) == {}
    assert records(MATCHUPS, through_week=1) == {}


def test_record_empty_and_text() -> None:
    assert Record.empty(9) == Record(9, 0, 0, 0, 0.0, 0.0)
    assert Record(1, 3, 2, 0, 0.0, 0.0).text == "3-2"
    assert Record(1, 3, 2, 1, 0.0, 0.0).text == "3-2-1"
    assert Record(1, 3, 2, 1, 0.0, 0.0).games == 6


def test_schedule_pairs_by_matchup_id_and_marks_byes() -> None:
    assert schedule(MATCHUPS, 2) == [Pairing(2, 1, 1, 3), Pairing(2, 2, 2, 4)]
    assert schedule({5: rows((7, 3, 0.0))}, 5) == [Pairing(5, 3, 7, None)]
    assert schedule(MATCHUPS, 9) == []


def test_schedule_skips_rows_without_a_matchup_id() -> None:
    unscheduled = {6: [{"roster_id": 1, "matchup_id": None, "points": 0.0}]}
    assert schedule(unscheduled, 6) == []


def test_actual_points_by_roster() -> None:
    assert actual_points(MATCHUPS, 1) == {1: 100.0, 2: 90.0, 3: 80.0, 4: 80.0}
    assert actual_points(MATCHUPS, 9) == {}


def test_actual_lineup_reads_starters_in_slot_order() -> None:
    matchups = {1: [{"roster_id": 1, "matchup_id": 1, "points": 30.5, "starters": ["a1", "0", "a3"],
                     "starters_points": [20.0, 0.0, 10.5], "players_points": {"a1": 20.0, "a3": 10.5, "a2": 4.0}}]}
    assert actual_lineup(matchups, 1, 1, ["RB", "WR", "FLEX"]) == [("RB", "a1", 20.0), ("WR", "", 0.0), ("FLEX", "a3", 10.5)]
    assert actual_lineup(matchups, 1, 9, ["RB"]) == [("RB", "", 0.0)]
    assert actual_lineup(matchups, 1, 1, ["RB", "WR", "FLEX", "TE"])[3] == ("TE", "", 0.0)


def test_player_points_by_roster() -> None:
    matchups = {2: [{"roster_id": 1, "matchup_id": 1, "points": 24.0, "players_points": {"a1": 24.0, "a2": 0.0}}]}
    assert player_points(matchups, 2, 1) == {"a1": 24.0, "a2": 0.0}
    assert player_points(matchups, 2, 7) == {}
    assert player_points(matchups, 5, 1) == {}


import math

import polars as pl

from app.model.league import LeagueContext, Roster
from app.model.live import Projection
from app.model.standings import (
    PROJECTION_WEIGHT, league_rows, lineup_for, power_scores, ros_averages, team_week_projections, week_totals,
    win_probability,
)


def ctx_with(rosters: tuple[Roster, ...], slots: list[str]) -> LeagueContext:
    return LeagueContext(league_id="1", name="t", season=2026, week=3, n_teams=len(rosters), scoring={},
                         roster_positions=slots + ["BN"], slots=slots, playoff_week_start=15, last_week=17,
                         trade_deadline=99, rosters=rosters)


def weekly(points: dict[str, tuple[str, float]], weeks: list[int]) -> pl.DataFrame:
    """{sleeper_id: (position, points)} repeated for each week."""
    rows = [(pid, pos, w, pts) for w in weeks for pid, (pos, pts) in points.items()]
    return pl.DataFrame(rows, schema={"sleeper_id": pl.Utf8, "position": pl.Utf8, "week": pl.Int64,
                                      "points": pl.Float64}, orient="row")


A = Roster(1, "u1", "A", "A", None, ("a1", "a2", "a3"), (), ())
B = Roster(2, "u2", "B", "B", None, ("b1", "b2"), (), ())
POINTS = {"a1": ("RB", 20.0), "a2": ("RB", 10.0), "a3": ("WR", 9.0), "b1": ("RB", 25.0), "b2": ("WR", 5.0)}


def test_team_week_projections_use_the_optimal_lineup_per_week() -> None:
    ctx = ctx_with((A, B), ["RB", "FLEX"])
    proj = Projection("model", weekly(POINTS, [3, 4]), pl.DataFrame())
    frame = team_week_projections(ctx, proj)
    assert frame.schema == {"roster_id": pl.Int64, "week": pl.Int64, "points": pl.Float64}
    got = {(r, w): p for r, w, p in frame.iter_rows()}
    assert got == {(1, 3): 30.0, (1, 4): 30.0, (2, 3): 30.0, (2, 4): 30.0}


def test_team_week_projections_handles_a_roster_with_no_projected_players() -> None:
    ghost = Roster(3, "u3", "C", "C", None, ("nobody",), (), ())
    ctx = ctx_with((A, ghost), ["RB"])
    frame = team_week_projections(ctx, Projection("model", weekly(POINTS, [3]), pl.DataFrame()))
    assert frame.filter(pl.col("roster_id") == 3).is_empty()
    assert ros_averages(frame) == {1: 20.0}


def test_ros_averages_and_week_totals() -> None:
    frame = pl.DataFrame({"roster_id": [1, 1, 2], "week": [3, 4, 3], "points": [10.0, 20.0, 7.0]})
    assert ros_averages(frame) == {1: 15.0, 2: 7.0}
    assert week_totals(frame, 3) == {1: 10.0, 2: 7.0}
    assert week_totals(frame, 4) == {1: 20.0}
    assert ros_averages(pl.DataFrame(schema={"roster_id": pl.Int64, "week": pl.Int64, "points": pl.Float64})) == {}


def test_power_scores_use_projection_only_before_any_games() -> None:
    scores = power_scores({}, {1: 100.0, 2: 80.0, 3: 90.0})
    assert scores[1] > scores[3] > scores[2]
    assert math.isclose(sum(scores.values()), 0.0, abs_tol=1e-9)


def test_power_scores_blend_projection_with_points_per_game() -> None:
    recs = {1: Record(1, 1, 0, 0, 100.0, 50.0), 2: Record(2, 0, 1, 0, 50.0, 100.0)}
    ros = {1: 80.0, 2: 90.0}
    scores = power_scores(recs, ros)
    # z(ros) = (-1, +1); z(pf) = (+1, -1) -> 0.6*(-1) + 0.4*(+1) = -0.2 for roster 1
    assert math.isclose(scores[1], PROJECTION_WEIGHT * -1 + (1 - PROJECTION_WEIGHT) * 1)
    assert math.isclose(scores[2], -scores[1])


def test_power_scores_constant_column_is_zero() -> None:
    assert power_scores({}, {1: 50.0, 2: 50.0}) == {1: 0.0, 2: 0.0}


def test_lineup_for_returns_slots_in_order_with_empty_slots() -> None:
    ctx = ctx_with((A, B), ["RB", "WR", "FLEX", "TE"])
    proj = Projection("model", weekly(POINTS, [3]), pl.DataFrame())
    assert lineup_for(ctx, proj, A, 3) == [("RB", "a1", 20.0), ("WR", "a3", 9.0), ("FLEX", "a2", 10.0), ("TE", "", 0.0)]
    assert lineup_for(ctx, proj, A, 9) == [("RB", "", 0.0), ("WR", "", 0.0), ("FLEX", "", 0.0), ("TE", "", 0.0)]


def test_win_probability_is_symmetric_and_monotone() -> None:
    assert win_probability(100.0, 100.0) == 0.5
    assert math.isclose(win_probability(110.0, 100.0) + win_probability(100.0, 110.0), 1.0)
    assert win_probability(120.0, 100.0) > win_probability(110.0, 100.0) > 0.5
    assert 0.63 < win_probability(110.0, 100.0) < 0.65   # 10 / (20 * sqrt 2) = 0.354 sd -> 0.638


def test_league_rows_rank_by_power_and_flag_me() -> None:
    ctx = ctx_with((A, B), ["RB", "FLEX"])
    strong = {"a1": ("RB", 20.0), "a2": ("RB", 10.0), "a3": ("WR", 9.0), "b1": ("RB", 5.0), "b2": ("WR", 5.0)}
    projections = {"model": Projection("model", weekly(strong, [3]), pl.DataFrame()),
                   "consensus": Projection("consensus", weekly(POINTS, [3]), pl.DataFrame())}
    rows = league_rows(ctx, projections, MATCHUPS, me=2)
    assert [r["id"] for r in rows] == [1, 2]
    assert rows[0]["rank"] == 1 and rows[0]["mine"] is False and rows[1]["mine"] is True
    assert rows[0]["record"] == "1-1" and rows[0]["pf"] == 170.0 and rows[0]["pa"] == 165.0
    assert rows[0]["model"] == 30.0 and rows[0]["consensus"] == 30.0
    assert rows[1]["model"] == 10.0 and rows[1]["consensus"] == 30.0
    assert rows[0]["power"] > rows[1]["power"]


def test_league_rows_before_any_games() -> None:
    ctx = ctx_with((A, B), ["RB"])
    proj = Projection("model", weekly(POINTS, [3]), pl.DataFrame())
    rows = league_rows(ctx, {"model": proj, "consensus": proj}, {}, me=1)
    assert [r["record"] for r in rows] == ["0-0", "0-0"] and [r["id"] for r in rows] == [2, 1]
