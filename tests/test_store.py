from pathlib import Path

from app.data.store import Store


def test_cache_respects_max_age(tmp_path: Path) -> None:
    store = Store(tmp_path / "app.sqlite")
    store.put_cached("k", '{"a": 1}', now=100.0)
    assert store.get_cached("k", max_age_s=50.0, now=120.0) == '{"a": 1}'
    assert store.get_cached("k", max_age_s=10.0, now=120.0) is None
    assert store.get_cached("k", max_age_s=None, now=999.0) == '{"a": 1}'
    assert store.get_cached("missing", max_age_s=None, now=0.0) is None


def test_put_cached_overwrites(tmp_path: Path) -> None:
    store = Store(tmp_path / "app.sqlite")
    store.put_cached("k", "one", now=1.0)
    store.put_cached("k", "two", now=2.0)
    assert store.get_cached("k", max_age_s=None, now=3.0) == "two"


def test_snapshot_roundtrip(tmp_path: Path) -> None:
    store = Store(tmp_path / "app.sqlite")
    rows = [
        {"sleeper_id": "1", "name": "A", "position": "RB", "team": "DET",
         "value": 10.0, "overall_rank": 1, "position_rank": 1, "trend30": 0.5},
        {"sleeper_id": "2", "name": "B", "position": "WR", "team": "MIN",
         "value": 9.0, "overall_rank": 2, "position_rank": 1, "trend30": -0.1},
    ]
    assert store.put_snapshot("fantasycalc", "2026-09-16T10:00:00", rows) == 2
    store.put_snapshot("fantasycalc", "2026-09-17T10:00:00", rows[:1])
    latest = store.latest_snapshot("fantasycalc")
    assert latest.height == 1
    assert latest["taken_at"][0] == "2026-09-17T10:00:00"
    assert store.latest_snapshot("nothing").height == 0
