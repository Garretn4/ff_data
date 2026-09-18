import polars as pl

from app.model.availability import availability, sleeper_overlay


def _universe(*player_ids: str) -> pl.DataFrame:
    return pl.DataFrame({"player_id": list(player_ids)})


def _rosters(rows: list[tuple[str, int, int, str, str]]) -> pl.DataFrame:
    return pl.DataFrame(
        rows, schema=["gsis_id", "season", "week", "game_type", "status"], orient="row"
    )


def _injuries(rows: list[tuple[str, int, int, str, str]]) -> pl.DataFrame:
    return pl.DataFrame(
        rows, schema=["gsis_id", "season", "week", "game_type", "report_status"], orient="row"
    )


def test_active_player_with_no_injury_is_fully_available() -> None:
    rosters = _rosters([("p1", 2024, 5, "REG", "ACT")])
    injuries = pl.DataFrame(schema=["gsis_id", "season", "week", "game_type", "report_status"])
    out = availability(rosters, injuries, 2024, 5, _universe("p1"))
    row = out.row(0, named=True)
    assert row["avail_now"] == 1.0
    assert row["avail_ros"] == 1.0


def test_reserve_status_zeroes_now_but_keeps_ros_partial() -> None:
    rosters = _rosters([("p1", 2024, 5, "REG", "RES")])
    injuries = pl.DataFrame(schema=["gsis_id", "season", "week", "game_type", "report_status"])
    out = availability(rosters, injuries, 2024, 5, _universe("p1")).row(0, named=True)
    assert out["avail_now"] == 0.0
    assert out["avail_ros"] == 0.35


def test_player_never_on_any_roster_through_the_week_is_zero() -> None:
    rosters = _rosters([("other", 2024, 5, "REG", "ACT")])
    injuries = pl.DataFrame(schema=["gsis_id", "season", "week", "game_type", "report_status"])
    out = availability(rosters, injuries, 2024, 5, _universe("p1")).row(0, named=True)
    assert out["avail_now"] == 0.0
    assert out["avail_ros"] == 0.0


def test_bye_week_gap_carries_forward_the_latest_roster_status() -> None:
    rosters = _rosters([("p1", 2024, 4, "REG", "ACT")])
    injuries = pl.DataFrame(schema=["gsis_id", "season", "week", "game_type", "report_status"])
    out = availability(rosters, injuries, 2024, 5, _universe("p1")).row(0, named=True)
    assert out["avail_now"] == 1.0
    assert out["avail_ros"] == 1.0


def test_lookback_prefers_the_most_recent_week_even_if_earlier_was_better() -> None:
    rosters = _rosters([
        ("p1", 2024, 3, "REG", "ACT"),
        ("p1", 2024, 4, "REG", "RES"),
    ])
    injuries = pl.DataFrame(schema=["gsis_id", "season", "week", "game_type", "report_status"])
    out = availability(rosters, injuries, 2024, 5, _universe("p1")).row(0, named=True)
    assert out["avail_now"] == 0.0
    assert out["avail_ros"] == 0.35


def test_active_and_out_zeroes_now_but_not_all_of_ros() -> None:
    rosters = _rosters([("p1", 2024, 5, "REG", "ACT")])
    injuries = _injuries([("p1", 2024, 5, "REG", "Out")])
    out = availability(rosters, injuries, 2024, 5, _universe("p1")).row(0, named=True)
    assert out["avail_now"] == 0.0
    assert out["avail_ros"] == 0.85


def test_active_and_questionable_shaves_now_only() -> None:
    rosters = _rosters([("p1", 2024, 5, "REG", "ACT")])
    injuries = _injuries([("p1", 2024, 5, "REG", "Questionable")])
    out = availability(rosters, injuries, 2024, 5, _universe("p1")).row(0, named=True)
    assert out["avail_now"] == 0.85
    assert out["avail_ros"] == 1.0


def test_two_roster_rows_same_week_takes_the_best_status() -> None:
    rosters = _rosters([
        ("p1", 2024, 5, "REG", "RES"),
        ("p1", 2024, 5, "REG", "ACT"),
    ])
    injuries = pl.DataFrame(schema=["gsis_id", "season", "week", "game_type", "report_status"])
    out = availability(rosters, injuries, 2024, 5, _universe("p1")).row(0, named=True)
    assert out["avail_now"] == 1.0
    assert out["avail_ros"] == 1.0


def test_ignores_other_seasons_weeks_and_game_types() -> None:
    rosters = _rosters([
        ("p1", 2023, 5, "REG", "ACT"),
        ("p1", 2024, 6, "REG", "ACT"),
        ("p1", 2024, 5, "WC", "ACT"),
        ("p1", 2024, 5, "REG", "CUT"),
    ])
    injuries = pl.DataFrame(schema=["gsis_id", "season", "week", "game_type", "report_status"])
    out = availability(rosters, injuries, 2024, 5, _universe("p1")).row(0, named=True)
    assert out["avail_now"] == 0.0
    assert out["avail_ros"] == 0.0


def test_sleeper_overlay_ir_zeroes_now_and_partials_ros() -> None:
    avail = pl.DataFrame({"player_id": ["p1"], "avail_now": [1.0], "avail_ros": [1.0]})
    sleeper_players = pl.DataFrame(
        {"sleeper_id": ["1"], "gsis_id": ["p1"], "injury_status": ["IR"]}
    )
    out = sleeper_overlay(avail, sleeper_players).row(0, named=True)
    assert out["avail_now"] == 0.0
    assert out["avail_ros"] == 0.35


def test_sleeper_overlay_no_status_leaves_avail_unchanged() -> None:
    avail = pl.DataFrame({"player_id": ["p1"], "avail_now": [0.85], "avail_ros": [0.9]})
    sleeper_players = pl.DataFrame(
        {"sleeper_id": ["1"], "gsis_id": ["p2"], "injury_status": ["Out"]}
    )
    out = sleeper_overlay(avail, sleeper_players).row(0, named=True)
    assert out["avail_now"] == 0.85
    assert out["avail_ros"] == 0.9
