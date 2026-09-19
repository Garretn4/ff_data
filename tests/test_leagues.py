import json
from pathlib import Path

from app.data.store import Store
from app.model.leagues import CachedSource, LeagueSummary, discover, load_summaries

USERS = {"ann": {"user_id": "u1", "display_name": "Ann"}, "bob": {"user_id": "u2", "display_name": "Bob"}}
L1 = {"league_id": "1", "name": "Shared", "season": "2026", "status": "in_season", "total_rosters": 10,
      "settings": {"type": 0, "draft_rounds": 3}}
L2 = {"league_id": "2", "name": "Dyn", "season": "2026", "status": "in_season", "total_rosters": 12,
      "settings": {"type": 2, "draft_rounds": 4}}
L3 = {"league_id": "3", "name": "Odd", "season": "2026", "status": "pre_draft", "total_rosters": 18,
      "settings": {"type": 3}}
LEAGUES = {"u1": [L1], "u2": [L2, L1, L3]}


class FakeSource:
    def user(self, username: str):
        return USERS.get(username)

    def user_leagues(self, user_id: str, season: int):
        return LEAGUES.get(user_id, [])


def test_discover_unions_leagues_and_records_members_in_config_order() -> None:
    summaries, errors = discover(FakeSource(), ("ann", "bob"), 2026)
    assert errors == []
    assert [s.league_id for s in summaries] == ["1", "2", "3"]
    assert summaries[0].members == ("ann", "bob") and summaries[1].members == ("bob",)
    assert summaries[0] == LeagueSummary("1", "Shared", 2026, "in_season", 10, 0, 3, ("ann", "bob"))


def test_discover_skips_unknown_users_with_an_error() -> None:
    summaries, errors = discover(FakeSource(), ("ghost", "bob"), 2026)
    assert errors == ["user ghost: not found"]
    assert [s.league_id for s in summaries] == ["2", "1", "3"]


def test_labels_and_dynasty_flag() -> None:
    s1, s2, s3 = discover(FakeSource(), ("ann", "bob"), 2026)[0]
    assert (s1.dynasty, s2.dynasty, s3.dynasty) == (False, True, False)
    assert s1.label == "Shared · redraft 10"
    assert s2.label == "Dyn · dynasty 12"
    assert s3.label == "Odd · pre-draft type 3 18"
    assert s3.draft_rounds == 4


def test_cached_source_and_load_summaries(tmp_path: Path) -> None:
    store = Store(tmp_path / "app.sqlite")
    store.put_cached("sleeper:/user/ann", json.dumps(USERS["ann"]), now=1.0)
    store.put_cached("sleeper:/user/u1/leagues/nfl/2026", json.dumps([L1]), now=1.0)
    store.put_cached("sleeper:/user/ghost", "null", now=1.0)
    src = CachedSource(store)
    assert src.user("ann") == USERS["ann"] and src.user("ghost") is None and src.user("bob") is None
    assert src.user_leagues("u1", 2026) == [L1] and src.user_leagues("u2", 2026) == []
    assert [s.league_id for s in load_summaries(store, ("ann", "bob"), 2026)] == ["1"]
