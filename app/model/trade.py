"""Grade a trade by the change in each side's optimal-lineup points over remaining weeks."""
from __future__ import annotations

from dataclasses import dataclass

from app.model.league import LeagueContext, Roster
from app.model.lineup import team_strength
from app.model.live import Projection


@dataclass(frozen=True)
class TradeResult:
    delta_a: float
    delta_b: float
    before_a: float
    after_a: float
    before_b: float
    after_b: float


def startable(roster: Roster) -> set[str]:
    return set(roster.players) - set(roster.reserve)


def trade_delta(ctx: LeagueContext, proj: Projection, roster_a: Roster, roster_b: Roster,
                give_a: set[str], give_b: set[str]) -> TradeResult:
    a_before, b_before = startable(roster_a), startable(roster_b)
    a_after = (a_before - give_a) | give_b
    b_after = (b_before - give_b) | give_a
    before_a = team_strength(ctx.slots, proj.weekly, a_before)
    after_a = team_strength(ctx.slots, proj.weekly, a_after)
    before_b = team_strength(ctx.slots, proj.weekly, b_before)
    after_b = team_strength(ctx.slots, proj.weekly, b_after)
    return TradeResult(after_a - before_a, after_b - before_b, before_a, after_a, before_b, after_b)
