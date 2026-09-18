from pathlib import Path

import polars as pl

from app.data.identity import id_hub, match_by_name, normalize_name, write_diagnostics
from app.data.nflverse import connect


def playerids() -> pl.DataFrame:
    return pl.DataFrame({
        "gsis_id": ["00-1", "00-2", None],
        "sleeper_id": [4046, None, 99],
        "fantasypros_id": [1001, 1002, None],
        "pfr_id": ["MahoPa00", "JeffJu00", None],
        "name": ["Patrick Mahomes", "Justin Jefferson", "Nobody Here"],
        "position": ["QB", "WR", "RB"],
        "team": ["KC", "MIN", "FA"],
        "birthdate": ["1995-09-17", "1999-06-16", None],
    })


def test_normalize_name_strips_suffixes_punctuation_and_case() -> None:
    assert normalize_name("D.J. Moore Jr.") == "dj moore"
    assert normalize_name("Kenneth Walker III") == "kenneth walker"
    assert normalize_name("Ja'Marr Chase") == "jamarr chase"


def test_id_hub_stringifies_ids_and_drops_rows_without_gsis() -> None:
    hub = id_hub(playerids())
    assert hub.height == 2
    assert hub["sleeper_id"].to_list() == ["4046", None]
    assert hub["fantasypros_id"].to_list() == ["1001", "1002"]
    assert hub["norm_name"].to_list() == ["patrick mahomes", "justin jefferson"]
    assert str(hub["birthdate"].dtype) == "Date"


def test_match_by_name_splits_matched_and_unmatched() -> None:
    hub = id_hub(playerids())
    rows = pl.DataFrame({"name": ["Justin Jefferson", "Patrick Mahomes II", "Ghost Player"],
                         "position": ["WR", "QB", "TE"], "adp": [1.0, 20.0, 99.0]})
    matched, unmatched = match_by_name(rows, hub, "name", "position")
    assert matched["gsis_id"].to_list() == ["00-2", "00-1"]
    assert unmatched["name"].to_list() == ["Ghost Player"]


def test_write_diagnostics_appends_rows(tmp_path: Path) -> None:
    con = connect(tmp_path / "s.duckdb")
    unmatched = pl.DataFrame({"name": ["Ghost Player"], "position": ["TE"]})
    assert write_diagnostics(con, "ffc_adp", unmatched, "name", "position", "no name match") == 1
    write_diagnostics(con, "ffc_adp", unmatched, "name", "position", "no name match")
    rows = con.execute("SELECT source, name, reason FROM identity_diagnostics").fetchall()
    assert rows == [("ffc_adp", "Ghost Player", "no name match")] * 2
