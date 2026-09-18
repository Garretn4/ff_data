from collections import Counter

import httpx
import polars as pl

from app.model.managers import (
    _overpay, _process_waiver, _receiving_roster, _trade_flows, collect_transactions, league_chain,
)


class FakeClient:
    def __init__(self) -> None:
        self.tx = {("1", 1): [
            {"type": "trade", "transaction_id": "t1", "roster_ids": [1, 2], "adds": {"p1": 2, "p2": 1}, "drops": {"p1": 1, "p2": 2}, "settings": None},
            {"type": "waiver", "transaction_id": "w1", "roster_ids": [1], "adds": {"p9": 1}, "drops": None, "settings": {"waiver_bid": 7}},
        ]}

    def transactions(self, league_id: str, week: int) -> list[dict]:
        return self.tx.get((league_id, week), [])


def test_collect_transactions_flattens() -> None:
    chain = [{"league_id": "1", "season": "2026"}]
    df = collect_transactions(FakeClient(), chain, range(1, 3))
    assert df.height == 2
    trade = df.filter(pl.col("type") == "trade").row(0, named=True)
    assert trade["season"] == 2026 and sorted(trade["add_player"]) == ["p1", "p2"]
    waiver = df.filter(pl.col("type") == "waiver").row(0, named=True)
    assert waiver["waiver_bid"] == 7 and waiver["add_player"] == ["p9"]


def test_trade_flows_and_overpay() -> None:
    flows = _trade_flows({"p1": 2, "p2": 1}, {"p1": 1, "p2": 2})
    assert flows == {2: {"acquired": ["p1"], "given": ["p2"]}, 1: {"acquired": ["p2"], "given": ["p1"]}}
    values = {"p1": 12.0, "p2": 8.0}
    assert _overpay(flows[2], values) == 8.0 - 12.0
    assert _overpay(flows[1], values) == 12.0 - 8.0


def test_trade_flows_three_team_trade() -> None:
    adds = {"pa": 2, "pb": 3, "pc": 1}
    drops = {"pa": 1, "pb": 2, "pc": 3}
    flows = _trade_flows(adds, drops)
    assert flows[1]["given"] == ["pa"] and flows[2]["given"] == ["pb"] and flows[3]["given"] == ["pc"]
    assert flows[1]["acquired"] == ["pc"] and flows[2]["acquired"] == ["pa"] and flows[3]["acquired"] == ["pb"]


def test_waiver_bid_counted_once_per_transaction() -> None:
    positions = {"p1": "RB", "p2": "WR"}
    owners = {(2026, 1): "u1"}
    stats: dict[str, dict] = {}

    def bucket(owner: str) -> dict:
        return stats.setdefault(owner, {"trades": 0, "partners": Counter(), "acq": Counter(), "given": Counter(),
                                        "overpay": 0.0, "faab": 0, "adds": Counter()})

    row = {"season": 2026, "roster_ids": [1], "add_player": ["p1", "p2"], "add_roster": [1, 1], "waiver_bid": 7}
    _process_waiver(row, positions, owners, bucket)
    assert stats["u1"]["faab"] == 7
    assert sum(stats["u1"]["adds"].values()) == 2


def test_receiving_roster_fallback() -> None:
    assert _receiving_roster({"roster_ids": [], "add_roster": [5, 6]}) == 5


class BrokenLinkClient:
    def __init__(self) -> None:
        self.leagues = {"1": {"league_id": "1", "season": "2026", "previous_league_id": "2"}}

    def league(self, league_id: str) -> dict:
        if league_id not in self.leagues:
            raise httpx.HTTPError("gone")
        return self.leagues[league_id]


def test_league_chain_stops_at_broken_link() -> None:
    chain = league_chain(BrokenLinkClient(), "1")
    assert len(chain) == 1
