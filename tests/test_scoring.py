import polars as pl

from app.data.scoring import expected_points, score_weeks, unmapped_keys

PPR = {"pass_yd": 0.04, "pass_td": 4.0, "pass_int": -1.0, "rush_yd": 0.1, "rush_td": 6.0,
       "rec": 1.0, "rec_yd": 0.1, "rec_td": 6.0, "fum_lost": -2.0, "bonus_rec_te": 0.5}
HALF = {**PPR, "rec": 0.5}


def week_frame() -> pl.DataFrame:
    return pl.DataFrame({
        "player_id": ["qb", "rb", "te"],
        "position": ["QB", "RB", "TE"],
        "passing_yards": [300.0, 0.0, 0.0],
        "passing_tds": [2.0, 0.0, 0.0],
        "passing_interceptions": [1.0, 0.0, 0.0],
        "rushing_yards": [10.0, 80.0, 0.0],
        "rushing_tds": [0.0, 1.0, 0.0],
        "receptions": [0.0, 4.0, 6.0],
        "receiving_yards": [0.0, 30.0, 60.0],
        "receiving_tds": [0.0, 0.0, 1.0],
        "sack_fumbles_lost": [1.0, 0.0, 0.0],
        "rushing_fumbles_lost": [0.0, 0.0, 0.0],
        "receiving_fumbles_lost": [0.0, 0.0, 0.0],
    })


def test_score_weeks_matches_hand_computation() -> None:
    out = score_weeks(week_frame(), PPR)
    pts = dict(zip(out["player_id"], out["points"]))
    assert pts["qb"] == 300 * 0.04 + 2 * 4 - 1 + 10 * 0.1 - 2
    assert pts["rb"] == 80 * 0.1 + 6 + 4 + 30 * 0.1
    assert pts["te"] == 6 + 60 * 0.1 + 6 + 6 * 0.5


def test_half_ppr_changes_only_receptions() -> None:
    full = score_weeks(week_frame(), PPR)["points"].to_list()
    half = score_weeks(week_frame(), HALF)["points"].to_list()
    assert [round(f - h, 6) for f, h in zip(full, half)] == [0.0, 2.0, 3.0]


def test_missing_stat_columns_count_as_zero() -> None:
    frame = pl.DataFrame({"player_id": ["k"], "position": ["K"], "fg_made_50_59": [1.0]})
    out = score_weeks(frame, {"fgm_50p": 5.0, "xpm": 1.0})
    assert out["points"][0] == 5.0


def test_unmapped_keys_are_reported() -> None:
    assert unmapped_keys({"rec": 1.0, "pass_td_40p": 2.0, "def_st_td": 6.0}) == ["def_st_td", "pass_td_40p"]


def test_expected_points_uses_expected_columns() -> None:
    fo = pl.DataFrame({
        "player_id": ["rb"], "position": ["RB"],
        "rush_yards_gained_exp": [50.0], "rush_touchdown_exp": [0.5],
        "receptions_exp": [3.0], "rec_yards_gained_exp": [20.0], "rec_touchdown_exp": [0.1],
        "pass_yards_gained_exp": [0.0], "pass_touchdown_exp": [0.0], "pass_interception_exp": [0.0],
    })
    out = expected_points(fo, PPR)
    assert round(out["exp_points"][0], 4) == round(5.0 + 3.0 + 3.0 + 2.0 + 0.6, 4)


def test_score_weeks_output_schema() -> None:
    frame = week_frame()
    out = score_weeks(frame, PPR)
    assert out.columns == frame.columns + ["points"]


def test_expected_points_output_schema() -> None:
    fo = pl.DataFrame({
        "player_id": ["rb"], "position": ["RB"],
        "rush_yards_gained_exp": [50.0], "rush_touchdown_exp": [0.5],
        "receptions_exp": [3.0], "rec_yards_gained_exp": [20.0], "rec_touchdown_exp": [0.1],
        "pass_yards_gained_exp": [0.0], "pass_touchdown_exp": [0.0], "pass_interception_exp": [0.0],
    })
    out = expected_points(fo, PPR)
    assert out.columns == fo.columns + ["exp_points"]
