import json
from pathlib import Path

from app.data.store import Store
from app.model.standings import Pairing, Record, actual_points, load_matchups, records, schedule


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
