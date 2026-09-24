# Three-way Trade Finder Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add ring-shaped 3-way trade search to the finder, and let the trade screen hold a ring of two or three rosters with every team's delta live.

**Architecture:** A team's delta in a ring depends only on what it gives and what it receives. So we grade every 2-team package pair once into sparse "pair tables" (about 26 s for 10 teams, cached per league and projection source, warmed in the background). Then we find 3-way rings by joining three of those tables with dictionary lookups and no lineup evaluations. The trade screen and finder links move to one URL form that encodes a ring.

**Tech Stack:** Python 3.12, polars, NiceGUI, pytest, uv.

**Spec:** `docs/superpowers/specs/2026-09-24-three-way-trade-finder-design.md`

## Execution setup

- **Verified before handoff.** A scratch prototype of Tasks 2 and 3 found exactly 72 rings on the three-team fixture, with a best minimum gain of 26. The partner test's random 5-team league yields 4 rings. The 10-team full-size build took 24 s and the join took 0.02 s.

- Start from the current head of `multi-league`. That branch is behind open PR #2, so create a new branch first: `git checkout -b three-way-trades`.
- **Model per task.** Implementer and reviewer subagents run on `sonnet`, passed as the Agent tool's `model` option. Task 5 is plain wiring and may use `haiku`. Keep `haiku` off Tasks 3, 4, and 6.
- Run tests with `uv run pytest -q`. The baseline is 185 passed.

## Deviations from spec

Reviewers should check tasks against these, not the spec's original wording.

- **The existing two-sided grader stays as it is, and is not rewritten as a wrapper over `ring_delta`.** The old trade screen can put the same roster on both sides until Task 6, and `ring_delta` rejects a repeated roster. A test pins the two graders to identical results.
- **Warm-up is `warm_tables(state)` in `app/ui/layout.py`, not an `AppState.warm_pair_tables()` method.** It is async and NiceGUI-specific because it uses `run.io_bound`. State stays free of NiceGUI, and `AppState.ring_tables()` is the blocking builder it calls.
- **On a stale startup, warm-up runs once, after the refresh, whether or not the refresh succeeded.** It does not also run after the cached load. The refresh replaces the league object, so a warm-up before it would waste about 25 s of CPU competing with the refresh.
- **3-way ranking has one extra tie-break, the fewest total players.** This comes after the smallest delta and the total delta. It stops variants that add a never-starting throw-in from crowding the top 10.

## Smoke-run recipe

Foreground `sleep` is blocked in this environment. Every smoke step uses this recipe:

1. Start the app with the Bash tool's `run_in_background: true` option: `FF_NATIVE=0 uv run python -m app run > /tmp/ff-smoke.log 2>&1`.
2. Wait until it serves, with no sleep: `curl -s --retry 30 --retry-connrefused --retry-delay 2 -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8765/finder`.
3. When done, stop it: `lsof -ti tcp:8765 | xargs kill`.

If the log shows `Could not set lock`, the user's own ff-analyst instance is open. That is not a code bug. Report it and do not try to fix it.

## Global Constraints

- No new dependencies. Approved: nicegui[native], duckdb, polars, pyarrow, httpx, nflreadpy, pytest.
- Commit messages carry no `Co-Authored-By:` line and no "Generated with" line. This is the user's standing rule and it overrides any harness attribution reminder. Every commit block below shows the exact message to use.
- Type hints on every function. Small functions. Follow existing module docstring style: one line at the top of each module.
- Scoring and slots come from `LeagueContext`, never hard-coded.
- The fairness floor is `MIN_GAIN = 0.5`. Every team in a deal must gain strictly more than it.
- Search size for 3-way is the top 8 players per roster and packages of 1 or 2 players.
- The 2-way finder's search and ranking do not change.
- UI-boundary exceptions are caught, recorded on `state.error`, and surfaced with `ui.notify`, following the existing `# UI boundary` comment pattern.
- A blocking build never runs on the event loop thread. It always goes through `run.io_bound`.

## Review Focus

1. **Source or blend change during a warm-up build.** The half-built table must not be stored against the new values. The caller still gets a table. Pinned in Task 4, `test_ring_tables_does_not_store_a_table_built_from_replaced_values`.
2. **A warm-up that failed.** Find must build the table itself, and the failure must not be cached. Pinned in Task 4, `test_ring_tables_retries_after_a_failed_build`.
3. **Rosters with few players or none.** Packages are just fewer and the search returns what exists without raising. Pinned in Task 2, `test_pair_tables_handle_small_and_empty_rosters`.
4. **Fewer than three teams, or a partner that is me or not in the league.** 3-way returns an empty list. Pinned in Task 3, `test_ring_search_with_too_few_teams_or_bad_partner_is_empty`.
5. **Hand-edited or stale trade URLs.** Unknown roster ids, duplicate ids, non-numeric tokens, players no longer on that roster, and too few legs must all fall back cleanly. Pinned in Task 6, `test_parse_ring_*`.

---

## File map

| File | Change | Responsibility |
|---|---|---|
| `app/model/trade.py` | modify | `MIN_GAIN`, `Leg`, `Ring`, `TeamResult`, `ring_delta` beside the existing `trade_delta` |
| `app/model/ring.py` | create | Packages, pair tables, the ring join |
| `app/model/finder.py` | modify | Import `MIN_GAIN` and package helpers from their new homes. Behavior unchanged |
| `app/ui/state.py` | modify | `LeagueState.pair_tables`, `AppState.ring_tables`, lock, invalidation |
| `app/ui/layout.py` | modify | `warm_tables` background task and a call after refresh |
| `app/main.py` | modify | Warm after startup load and after startup refresh |
| `app/ui/ring_url.py` | create | Encode and parse the ring trade URL, with no nicegui import |
| `app/ui/trade.py` | rewrite | Ring trade screen of two or three sides |
| `app/ui/finder.py` | modify | Ring links for 2-way cards, the 2-way/3-way toggle, 3-way cards |
| `pyproject.toml` | modify | `slow` marker, default excludes it |
| `tests/test_trade.py` | modify | `ring_delta` tests |
| `tests/test_ring.py` | create | Pair tables and join tests |
| `tests/test_ring_url.py` | create | URL encode and parse tests |
| `tests/test_state.py` | modify | Table cache, lock, and invalidation tests |

---

### Task 1: Ring grading and the fairness-floor move

**Files:**
- Modify: `app/model/trade.py`
- Modify: `app/model/finder.py`, which currently defines `MIN_GAIN = 0.5` at line 12
- Modify: `app/ui/trade.py`, where line 10 imports `MIN_GAIN` from `app.model.finder`
- Test: `tests/test_trade.py`

**Interfaces:**
- Consumes: `startable(roster) -> set[str]` and `strength_from_index(slots, index, ids) -> float`, both existing.
- Produces:
  - `MIN_GAIN: float = 0.5` in `app.model.trade`.
  - `Leg(roster_id: int, gives: frozenset[str])`, a frozen dataclass.
  - `Ring = tuple[Leg, ...]`.
  - `TeamResult(before: float, after: float, delta: float)`, a frozen dataclass.
  - `ring_delta(ctx: LeagueContext, index: WeeklyIndex, ring: Ring, before: Mapping[int, float] | None = None) -> dict[int, TeamResult]`.
  - Each leg gives its players to the next leg, and the last leg gives to the first.

`MIN_GAIN` moves into the trade module because the new ring module needs it. The 2-way finder will import helpers from the ring module, so leaving the constant in the finder would create a circular import. `trade_delta` stays exactly as it is. The current trade screen can put the same roster on both sides until Task 6, and `ring_delta` rejects that.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_trade.py`:

```python
import pytest

from app.model.lineup import index_weekly, strength_from_index
from app.model.trade import Leg, MIN_GAIN, ring_delta, startable


def _three_team_ctx() -> tuple[LeagueContext, pl.DataFrame]:
    """A gives RB to B, B gives WR to C, C gives QB to A: each fills a hole. Weeks 3 and 4."""
    points = {
        "a_qb": ("QB", 5.0), "a_rb1": ("RB", 20.0), "a_rb2": ("RB", 18.0), "a_wr": ("WR", 25.0),
        "b_qb": ("QB", 25.0), "b_rb": ("RB", 5.0), "b_wr1": ("WR", 20.0), "b_wr2": ("WR", 18.0),
        "c_qb1": ("QB", 20.0), "c_qb2": ("QB", 18.0), "c_rb": ("RB", 25.0), "c_wr": ("WR", 5.0),
    }
    rosters = tuple(
        Roster(rid, f"u{rid}", f"T{rid}", f"T{rid}", None, tuple(p for p in points if p.startswith(prefix)), (), ())
        for rid, prefix in ((1, "a_"), (2, "b_"), (3, "c_"))
    )
    ctx = LeagueContext(league_id="1", name="t", season=2026, week=3, n_teams=3, scoring={},
                        roster_positions=["QB", "RB", "WR", "BN"], slots=["QB", "RB", "WR"], playoff_week_start=15,
                        last_week=4, trade_deadline=12, rosters=rosters)
    frame = pl.DataFrame([(pid, pos, w, pts) for pid, (pos, pts) in points.items() for w in (3, 4)],
                         schema={"sleeper_id": pl.Utf8, "position": pl.Utf8, "week": pl.Int64, "points": pl.Float64},
                         orient="row")
    return ctx, frame


def test_min_gain_lives_in_trade_module() -> None:
    from app.model import finder
    assert MIN_GAIN == 0.5
    assert finder.MIN_GAIN is MIN_GAIN


def test_ring_delta_grades_each_team_by_what_it_gives_and_receives() -> None:
    ctx, frame = _three_team_ctx()
    index = index_weekly(frame)
    ring = (Leg(1, frozenset({"a_rb2"})), Leg(2, frozenset({"b_wr2"})), Leg(3, frozenset({"c_qb2"})))
    results = ring_delta(ctx, index, ring)
    assert {rid: r.delta for rid, r in results.items()} == {1: 26.0, 2: 26.0, 3: 26.0}
    a = startable(ctx.rosters[0])
    assert results[1].before == strength_from_index(ctx.slots, index, a)
    assert results[1].after == strength_from_index(ctx.slots, index, (a - {"a_rb2"}) | {"c_qb2"})


def test_two_leg_ring_matches_trade_delta() -> None:
    ctx, frame = _three_team_ctx()
    proj = Projection("consensus", frame, pl.DataFrame())
    a, b = ctx.rosters[0], ctx.rosters[1]
    legacy = trade_delta(ctx, proj, a, b, {"a_rb2"}, {"b_qb"})
    ring = ring_delta(ctx, index_weekly(frame), (Leg(1, frozenset({"a_rb2"})), Leg(2, frozenset({"b_qb"}))))
    assert ring[1].delta == legacy.delta_a
    assert ring[2].delta == legacy.delta_b


def test_ring_delta_uses_supplied_before_strengths() -> None:
    ctx, frame = _three_team_ctx()
    ring = (Leg(1, frozenset({"a_rb2"})), Leg(2, frozenset({"b_wr2"})), Leg(3, frozenset({"c_qb2"})))
    results = ring_delta(ctx, index_weekly(frame), ring, before={1: 0.0, 2: 0.0, 3: 0.0})
    assert all(r.delta == r.after for r in results.values())


def test_ring_delta_ignores_given_players_not_startable_for_the_giver() -> None:
    ctx, frame = _three_team_ctx()
    ring = (Leg(1, frozenset({"not_on_roster"})), Leg(2, frozenset()), Leg(3, frozenset()))
    results = ring_delta(ctx, index_weekly(frame), ring)
    assert results[2].delta == 0.0


def test_ring_delta_rejects_bad_rings() -> None:
    ctx, frame = _three_team_ctx()
    index = index_weekly(frame)
    with pytest.raises(ValueError, match="repeated"):
        ring_delta(ctx, index, (Leg(1, frozenset()), Leg(1, frozenset())))
    with pytest.raises(ValueError, match="at least two"):
        ring_delta(ctx, index, (Leg(1, frozenset()),))
    with pytest.raises(ValueError, match="not in league"):
        ring_delta(ctx, index, (Leg(1, frozenset()), Leg(99, frozenset())))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_trade.py -q`
Expected: collection error, `ImportError: cannot import name 'Leg' from 'app.model.trade'`.

- [ ] **Step 3: Implement**

In `app/model/trade.py`, change the imports and add the new types and function. Leave `TradeResult`, `startable`, and `trade_delta` exactly as they are.

```python
"""Grade a trade by the change in each side's optimal-lineup points over remaining weeks."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from app.model.league import LeagueContext, Roster
from app.model.lineup import WeeklyIndex, index_weekly, strength_from_index
from app.model.live import Projection

MIN_GAIN = 0.5


@dataclass(frozen=True)
class Leg:
    """One team in a ring: the roster and the players it sends to the next leg."""
    roster_id: int
    gives: frozenset[str]


Ring = tuple[Leg, ...]


@dataclass(frozen=True)
class TeamResult:
    before: float
    after: float
    delta: float
```

After `trade_delta`, add:

```python
def _check_ring(ctx: LeagueContext, ring: Ring) -> dict[int, Roster]:
    if len(ring) < 2:
        raise ValueError("a ring needs at least two legs")
    ids = [leg.roster_id for leg in ring]
    if len(set(ids)) != len(ids):
        raise ValueError(f"roster repeated in ring: {ids}")
    by_id = {roster.roster_id: roster for roster in ctx.rosters}
    missing = [rid for rid in ids if rid not in by_id]
    if missing:
        raise ValueError(f"roster {missing[0]} not in league")
    return by_id


def ring_delta(ctx: LeagueContext, index: WeeklyIndex, ring: Ring,
               before: Mapping[int, float] | None = None) -> dict[int, TeamResult]:
    """Each leg gives its players to the next leg; the last gives to the first."""
    by_id = _check_ring(ctx, ring)
    results: dict[int, TeamResult] = {}
    for i, leg in enumerate(ring):
        previous = ring[i - 1]
        own = startable(by_id[leg.roster_id])
        incoming = previous.gives & startable(by_id[previous.roster_id])
        after_ids = (own - leg.gives) | incoming
        start = before[leg.roster_id] if before is not None and leg.roster_id in before \
            else strength_from_index(ctx.slots, index, own)
        end = strength_from_index(ctx.slots, index, after_ids)
        results[leg.roster_id] = TeamResult(start, end, end - start)
    return results
```

In `app/model/finder.py`, delete the line `MIN_GAIN = 0.5` and change the trade import to:

```python
from app.model.trade import MIN_GAIN, TradeResult, startable, trade_delta
```

In `app/ui/trade.py`, change `from app.model.finder import MIN_GAIN` to `from app.model.trade import MIN_GAIN`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest -q`
Expected: all pass, 191 tests.

- [ ] **Step 5: Commit**

```bash
git add app/model/trade.py app/model/finder.py app/ui/trade.py tests/test_trade.py
git commit -m "Grade a ring of trading teams by what each gives and receives"
```

---

### Task 2: Packages and pair tables

**Files:**
- Create: `app/model/ring.py`
- Modify: `app/model/finder.py` to import `_top_players` and `_packages` from `app.model.ring` and delete its own copies. Keep its `_roster` helper.
- Modify: `pyproject.toml`
- Test: `tests/test_ring.py`

**Interfaces:**
- Consumes: `MIN_GAIN` and `startable` from `app.model.trade`, plus `index_weekly` and `strength_from_index`.
- Produces:
  - `Package = tuple[str, ...]`.
  - `TOP_N = 8` and `MAX_SIDE = 2`.
  - `top_players(values: pl.DataFrame, roster: Roster, top_n: int) -> list[str]`.
  - `packages(players: list[str], max_side: int) -> list[Package]`.
  - `PairTables(accepts, by_get)`, a frozen dataclass:
    - `accepts: dict[tuple[int, int], dict[Package, dict[Package, float]]]` holds `accepts[(x, y)][give_x][get_from_y] = delta_x`, and only where `delta_x > MIN_GAIN`.
    - `by_get: dict[tuple[int, int], dict[Package, tuple[Package, ...]]]` holds `by_get[(x, y)][get_from_y]`, the tuple of `give_x` values that x accepts for that get.
  - `build_pair_tables(ctx: LeagueContext, proj: Projection, values: pl.DataFrame, top_n: int = TOP_N, max_side: int = MAX_SIDE) -> PairTables`.
  - The finder module keeps working through aliases: `_top_players = top_players` and `_packages = packages`, imported from `app.model.ring`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_ring.py`:

```python
import random
import time
from dataclasses import replace

import polars as pl
import pytest

from app.model.league import LeagueContext, Roster
from app.model.live import Projection
from app.model.ring import PairTables, build_pair_tables, packages, top_players
from app.model.trade import MIN_GAIN, trade_delta
from tests.test_trade import _team, _three_team_ctx, _weekly_frame


def three_team() -> tuple[LeagueContext, Projection, pl.DataFrame]:
    ctx, frame = _three_team_ctx()
    owner = {pid: r.roster_id for r in ctx.rosters for pid in r.players}
    week3 = frame.filter(pl.col("week") == 3)
    values = pl.DataFrame({
        "sleeper_id": week3["sleeper_id"], "roster_id": [owner[p] for p in week3["sleeper_id"]],
        "intrinsic": week3["points"], "ros_primary": week3["points"],
    })
    return ctx, Projection("consensus", frame, pl.DataFrame()), values


def random_league(n_teams: int, seed: int = 7) -> tuple[LeagueContext, Projection, pl.DataFrame]:
    rng = random.Random(seed)
    teams = {rid: _team(f"t{rid}_") for rid in range(1, n_teams + 1)}
    rosters = tuple(Roster(rid, f"u{rid}", f"T{rid}", f"T{rid}", None, tuple(pos), (), ()) for rid, pos in teams.items())
    ctx = LeagueContext(league_id="1", name="t", season=2026, week=3, n_teams=n_teams, scoring={"rec": 1.0},
                        roster_positions=["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "K", "DEF", "BN"],
                        slots=["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "K", "DEF"], playoff_week_start=15,
                        last_week=17, trade_deadline=12, rosters=rosters)
    weeks = list(range(3, 18))
    frame = pl.concat([_weekly_frame(rng, pos, weeks) for pos in teams.values()])
    ids = [pid for pos in teams.values() for pid in pos]
    values = pl.DataFrame({
        "sleeper_id": ids,
        "roster_id": [rid for rid, pos in teams.items() for _ in pos],
        "intrinsic": [round(rng.uniform(0, 40), 2) for _ in ids],
        "ros_primary": [round(rng.uniform(0, 40), 2) for _ in ids],
    })
    return ctx, Projection("consensus", frame, pl.DataFrame()), values


def test_packages_are_singles_then_pairs() -> None:
    assert packages(["a", "b", "c"], 2) == [("a",), ("b",), ("c",), ("a", "b"), ("a", "c"), ("b", "c")]
    assert packages([], 2) == []


def test_top_players_ranks_by_intrinsic() -> None:
    ctx, _, values = three_team()
    assert top_players(values, ctx.rosters[0], 2) == ["a_wr", "a_rb1"]


def test_pair_table_entries_match_trade_delta_and_respect_the_floor() -> None:
    ctx, proj, values = three_team()
    tables = build_pair_tables(ctx, proj, values)
    by_id = {r.roster_id: r for r in ctx.rosters}
    checked = 0
    for (x, y), table in tables.accepts.items():
        for give_x, gets in table.items():
            for get_y, delta in gets.items():
                expected = trade_delta(ctx, proj, by_id[x], by_id[y], set(give_x), set(get_y)).delta_a
                assert delta == expected
                assert delta > MIN_GAIN
                checked += 1
    assert checked > 0
    for x, y in ((1, 2), (2, 1)):
        for give_x in packages(top_players(values, by_id[x], 8), 2):
            for get_y in packages(top_players(values, by_id[y], 8), 2):
                delta = trade_delta(ctx, proj, by_id[x], by_id[y], set(give_x), set(get_y)).delta_a
                stored = tables.accepts[(x, y)].get(give_x, {}).get(get_y)
                assert (stored is not None) == (delta > MIN_GAIN)


def test_by_get_is_the_reverse_of_accepts() -> None:
    ctx, proj, values = three_team()
    tables = build_pair_tables(ctx, proj, values)
    for pair, table in tables.accepts.items():
        pairs_forward = {(g, r) for g, gets in table.items() for r in gets}
        pairs_reverse = {(g, r) for r, gives in tables.by_get[pair].items() for g in gives}
        assert pairs_forward == pairs_reverse


def test_pair_tables_handle_small_and_empty_rosters() -> None:
    ctx, proj, values = three_team()
    empty = Roster(4, "u4", "T4", "T4", None, (), (), ())
    ctx = replace(ctx, rosters=ctx.rosters + (empty,), n_teams=4)
    tables = build_pair_tables(ctx, proj, values, top_n=8)
    assert tables.accepts[(4, 1)] == {}
    assert tables.accepts[(1, 4)] == {}
    assert isinstance(tables, PairTables)


@pytest.mark.slow
def test_full_size_build_is_under_a_minute_for_ten_teams() -> None:
    ctx, proj, values = random_league(10)
    start = time.perf_counter()
    build_pair_tables(ctx, proj, values)
    assert time.perf_counter() - start < 60.0
```

`_team`, `_weekly_frame`, and `CYCLE` already exist in `tests/test_trade.py`. `_three_team_ctx` comes from Task 1.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_ring.py -q`
Expected: collection error, `ModuleNotFoundError: No module named 'app.model.ring'`.

- [ ] **Step 3: Register the slow marker**

Append to `[tool.pytest.ini_options]` in `pyproject.toml`:

```toml
markers = ["slow: full-size pair-table build; run with `uv run pytest -m slow`"]
addopts = "-m 'not slow'"
```

- [ ] **Step 4: Implement**

Create `app/model/ring.py`:

```python
"""Pair tables of mutually graded packages, and the 3-team ring search that joins them."""
from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations

import polars as pl

from app.model.league import LeagueContext, Roster
from app.model.lineup import index_weekly, strength_from_index
from app.model.live import Projection
from app.model.trade import MIN_GAIN, startable

Package = tuple[str, ...]
TOP_N = 8
MAX_SIDE = 2


def top_players(values: pl.DataFrame, roster: Roster, top_n: int) -> list[str]:
    score = pl.coalesce(pl.col("intrinsic"), pl.col("ros_primary"), pl.lit(0.0))
    mine = (
        values.filter(pl.col("roster_id") == roster.roster_id)
        .with_columns(score.alias("score"))
        .sort("score", descending=True)
    )
    return mine["sleeper_id"].head(top_n).to_list()


def packages(players: list[str], max_side: int) -> list[Package]:
    return [combo for size in range(1, max_side + 1) for combo in combinations(players, size)]


@dataclass(frozen=True)
class PairTables:
    # accepts[(x, y)][give_x][get_from_y] = delta_x, kept only where delta_x > MIN_GAIN
    accepts: dict[tuple[int, int], dict[Package, dict[Package, float]]]
    # by_get[(x, y)][get_from_y] = every give_x that x accepts for that get
    by_get: dict[tuple[int, int], dict[Package, tuple[Package, ...]]]


def _reverse(table: dict[Package, dict[Package, float]]) -> dict[Package, tuple[Package, ...]]:
    reverse: dict[Package, list[Package]] = {}
    for give, gets in table.items():
        for get in gets:
            reverse.setdefault(get, []).append(give)
    return {get: tuple(gives) for get, gives in reverse.items()}


def build_pair_tables(ctx: LeagueContext, proj: Projection, values: pl.DataFrame,
                      top_n: int = TOP_N, max_side: int = MAX_SIDE) -> PairTables:
    """Grade every package pair for every roster pair once. Slow: about 26 s for 10 teams at the defaults."""
    index = index_weekly(proj.weekly)
    own = {r.roster_id: startable(r) for r in ctx.rosters}
    before = {rid: strength_from_index(ctx.slots, index, ids) for rid, ids in own.items()}
    offers = {r.roster_id: packages(top_players(values, r, top_n), max_side) for r in ctx.rosters}
    accepts: dict[tuple[int, int], dict[Package, dict[Package, float]]] = {}
    for x, y in combinations(sorted(own), 2):
        x_table = accepts.setdefault((x, y), {})
        y_table = accepts.setdefault((y, x), {})
        for give_x in offers[x]:
            for give_y in offers[y]:
                x_after = (own[x] - set(give_x)) | (set(give_y) & own[y])
                y_after = (own[y] - set(give_y)) | (set(give_x) & own[x])
                delta_x = strength_from_index(ctx.slots, index, x_after) - before[x]
                delta_y = strength_from_index(ctx.slots, index, y_after) - before[y]
                if delta_x > MIN_GAIN:
                    x_table.setdefault(give_x, {})[give_y] = delta_x
                if delta_y > MIN_GAIN:
                    y_table.setdefault(give_y, {})[give_x] = delta_y
    return PairTables(accepts=accepts, by_get={pair: _reverse(table) for pair, table in accepts.items()})
```

In `app/model/finder.py`:
- Delete the `_top_players` and `_packages` function bodies.
- Delete the now-unused `from itertools import combinations` line.
- Add `from app.model.ring import packages as _packages, top_players as _top_players`.
- Leave `find_trades` untouched.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest -q`
Expected: all pass. The slow test shows as deselected.

Then run: `uv run pytest -m slow -q`
Expected: 1 passed, in under 60 s.

- [ ] **Step 6: Commit**

```bash
git add app/model/ring.py app/model/finder.py pyproject.toml tests/test_ring.py
git commit -m "Build pair tables of mutually graded trade packages"
```

---

### Task 3: The ring join

**Files:**
- Modify: `app/model/ring.py`
- Test: `tests/test_ring.py`

**Interfaces:**
- Consumes: `PairTables` and `Package` from Task 2, and `Leg` and `ring_delta` from Task 1.
- Produces:
  - `RingDeal(ring: tuple[Leg, Leg, Leg], deltas: dict[int, float])`, a frozen dataclass. `ring[0]` is always me. Each leg gives to the next, and the last gives to me.
  - `find_ring_trades(tables: PairTables, me: int, opponents: Sequence[int], partner: int | None = None, limit: int = 10) -> list[RingDeal]`.
  - Results are ranked by the smallest delta, descending. Ties break by the largest total delta, then the fewest total players, then roster ids and sorted player ids, so the order is deterministic.

**Why the join is exact.** Take me as `a`, with `a` giving to `b`, `b` to `c`, and `c` to `a`.
- a's delta is `accepts[(a, c)][give_a][give_c]`, because a gives `give_a` and receives `give_c`.
- b's delta is `accepts[(b, a)][give_b][give_a]`, because b gives `give_b` and receives `give_a`.
- c's delta is `accepts[(c, b)][give_c][give_b]`, because c gives `give_c` and receives `give_b`.

A swapped key pair is the most likely bug here. The cross-check test catches it.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_ring.py`:

```python
from app.model.finder import find_trades
from app.model.lineup import index_weekly
from app.model.ring import RingDeal, find_ring_trades
from app.model.trade import ring_delta


def test_three_team_fixture_has_no_fair_two_way_but_has_rings() -> None:
    ctx, proj, values = three_team()
    for me in (1, 2, 3):
        for opp in (1, 2, 3):
            if me != opp:
                assert find_trades(ctx, proj, values, me, opp, top_n=8, max_side=2) == []
    deals = find_ring_trades(build_pair_tables(ctx, proj, values), 1, [2, 3], limit=1000)
    assert len(deals) == 72  # brute force over every ring on this fixture finds exactly 72 fair rings
    assert min(deals[0].deltas.values()) == 26.0
    wanted = (frozenset({"a_rb2"}), frozenset({"b_wr2"}), frozenset({"c_qb2"}))
    assert any(tuple(leg.gives for leg in d.ring) == wanted and [leg.roster_id for leg in d.ring] == [1, 2, 3]
               for d in deals)


def test_every_ring_deal_matches_direct_grading() -> None:
    ctx, proj, values = three_team()
    index = index_weekly(proj.weekly)
    for deal in find_ring_trades(build_pair_tables(ctx, proj, values), 1, [2, 3], limit=1000):
        assert deal.ring[0].roster_id == 1
        direct = ring_delta(ctx, index, deal.ring)
        assert {rid: r.delta for rid, r in direct.items()} == deal.deltas
        assert all(delta > MIN_GAIN for delta in deal.deltas.values())


def test_ring_results_are_ranked_and_limited() -> None:
    ctx, proj, values = three_team()
    deals = find_ring_trades(build_pair_tables(ctx, proj, values), 1, [2, 3], limit=5)
    assert len(deals) == 5
    worst = [min(d.deltas.values()) for d in deals]
    assert worst == sorted(worst, reverse=True)
    again = find_ring_trades(build_pair_tables(ctx, proj, values), 1, [2, 3], limit=5)
    assert [d.ring for d in again] == [d.ring for d in deals]


def test_partner_restricts_rings_to_that_team() -> None:
    ctx, proj, values = random_league(5)
    tables = build_pair_tables(ctx, proj, values, top_n=4)
    deals = find_ring_trades(tables, 1, [2, 3, 4, 5], partner=3, limit=1000)
    assert deals, "expected rings on a random 5-team league"
    assert all(3 in {leg.roster_id for leg in d.ring} for d in deals)


def test_ring_search_with_too_few_teams_or_bad_partner_is_empty() -> None:
    ctx, proj, values = three_team()
    tables = build_pair_tables(ctx, proj, values)
    assert find_ring_trades(tables, 1, [2]) == []
    assert find_ring_trades(tables, 1, []) == []
    assert find_ring_trades(tables, 1, [2, 3], partner=1) == []
    assert find_ring_trades(tables, 1, [2, 3], partner=99) == []
    assert find_ring_trades(tables, 1, [1, 2, 3]) == find_ring_trades(tables, 1, [2, 3])


def test_ring_join_is_fast_on_a_ten_team_league() -> None:
    ctx, proj, values = random_league(10)
    tables = build_pair_tables(ctx, proj, values, top_n=4)
    start = time.perf_counter()
    deals = find_ring_trades(tables, 1, list(range(2, 11)))
    assert time.perf_counter() - start < 5.0
    assert all(isinstance(d, RingDeal) for d in deals)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_ring.py -q`
Expected: `ImportError: cannot import name 'RingDeal'`.

- [ ] **Step 3: Implement**

Add to `app/model/ring.py`. Extend the imports:

```python
from itertools import combinations, permutations
from typing import Sequence

from app.model.trade import MIN_GAIN, Leg, startable
```

Then append:

```python
@dataclass(frozen=True)
class RingDeal:
    ring: tuple[Leg, Leg, Leg]  # me -> b -> c -> me
    deltas: dict[int, float]


def _rings(tables: PairTables, a: int, b: int, c: int) -> list[RingDeal]:
    """Every fair ring where a gives to b, b gives to c, and c gives to a."""
    a_gets_from_c = tables.accepts.get((a, c), {})
    b_gives_for_a = tables.by_get.get((b, a), {})
    b_gets_from_a = tables.accepts.get((b, a), {})
    c_gets_from_b = tables.accepts.get((c, b), {})
    found: list[RingDeal] = []
    for give_a, from_c in a_gets_from_c.items():
        b_options = b_gives_for_a.get(give_a, ())
        if not b_options:
            continue
        for give_c, delta_a in from_c.items():
            c_accepts = c_gets_from_b.get(give_c)
            if not c_accepts:
                continue
            for give_b in b_options:
                delta_c = c_accepts.get(give_b)
                if delta_c is None:
                    continue
                ring = (Leg(a, frozenset(give_a)), Leg(b, frozenset(give_b)), Leg(c, frozenset(give_c)))
                found.append(RingDeal(ring, {a: delta_a, b: b_gets_from_a[give_b][give_a], c: delta_c}))
    return found


def _rank(deal: RingDeal) -> tuple:
    legs = tuple((leg.roster_id, tuple(sorted(leg.gives))) for leg in deal.ring)
    players = sum(len(leg.gives) for leg in deal.ring)
    return (-min(deal.deltas.values()), -sum(deal.deltas.values()), players, legs)


def find_ring_trades(tables: PairTables, me: int, opponents: Sequence[int], partner: int | None = None,
                     limit: int = 10) -> list[RingDeal]:
    """Fair 3-team rings with me as the first leg, both directions around every opponent pair."""
    others = [rid for rid in dict.fromkeys(opponents) if rid != me]
    deals: list[RingDeal] = []
    for b, c in permutations(others, 2):
        if partner is not None and partner not in (b, c):
            continue
        deals.extend(_rings(tables, me, b, c))
    deals.sort(key=_rank)
    return deals[:limit]
```

A `partner` equal to `me`, or one that is not in `opponents`, never matches `(b, c)`, so the result is empty.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add app/model/ring.py tests/test_ring.py
git commit -m "Find fair three-team rings by joining pair tables"
```

---

### Task 4: Cache pair tables per league and source

**Files:**
- Modify: `app/ui/state.py`
- Test: `tests/test_state.py`

**Interfaces:**
- Consumes: `build_pair_tables` and `PairTables` from `app.model.ring`.
- Produces:
  - A new last field on `LeagueState`: `pair_tables: dict[str, PairTables] = field(default_factory=dict)`. It is last and defaulted, so existing constructors keep working.
  - `AppState.ring_tables(self, source: str | None = None) -> PairTables`. It blocks, and must only be called through `run.io_bound`.
  - `AppState.ring_tables_ready(self) -> bool`. True when the active league already has the current source's table.
  - `AppState._rebuild_values` also clears the active league's `pair_tables`.

**Race rule.** `ring_tables` does the following:
1. It captures the league-state object, its `values` frame, and `projections[source]` when it starts.
2. It builds the table while holding one `threading.Lock` for the whole build. A concurrent caller waits instead of building a second copy.
3. It stores the result only if `league.values is values` and `league.projections.get(source) is proj` are both still true. `_rebuild_values` and `_recompute_model` replace those objects, so the identity check catches a rebuild that happened mid-build.
4. It always returns the table it built.

It stores into the league object captured at the start, even if the active league changed meanwhile. A failed build caches nothing.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_state.py`:

```python
import threading

from app.model.ring import PairTables


def _tables() -> PairTables:
    return PairTables(accepts={}, by_get={})


def _state_with_projection(monkeypatch) -> AppState:
    ctx = ctx_with((A, B), ["RB", "FLEX"])
    proj = Projection("consensus", weekly(POINTS, [3]), pl.DataFrame())
    state = _state_with(ctx, "ann", projections={s: proj for s in SOURCES}, values=pl.DataFrame({"x": [1]}))
    return state


def test_ring_tables_builds_once_and_reuses(monkeypatch) -> None:
    state = _state_with_projection(monkeypatch)
    calls: list[str] = []

    def fake_build(ctx, proj, values):
        calls.append(proj.source)
        return _tables()

    monkeypatch.setattr("app.ui.state.build_pair_tables", fake_build)
    assert not state.ring_tables_ready()
    first = state.ring_tables()
    assert state.ring_tables() is first
    assert state.ring_tables_ready()
    assert len(calls) == 1


def test_rebuild_values_clears_pair_tables(monkeypatch) -> None:
    state = _state_with_projection(monkeypatch)
    monkeypatch.setattr("app.ui.state.build_pair_tables", lambda *a: _tables())
    state.ring_tables()
    monkeypatch.setattr(state, "_compute_values", lambda ctx, projections: pl.DataFrame({"x": [2]}))
    state._rebuild_values()
    assert not state.ring_tables_ready()


def test_ring_tables_does_not_store_a_table_built_from_replaced_values(monkeypatch) -> None:
    state = _state_with_projection(monkeypatch)
    league = state.leagues[state.active]
    built = _tables()

    def build_while_values_change(ctx, proj, values):
        league.values = pl.DataFrame({"x": [3]})  # a source switch landed mid-build
        return built

    monkeypatch.setattr("app.ui.state.build_pair_tables", build_while_values_change)
    assert state.ring_tables() is built
    assert league.pair_tables == {}


def test_ring_tables_stores_into_the_league_it_started_on(monkeypatch) -> None:
    state = _state_with_projection(monkeypatch)
    first = state.leagues[state.active]
    other = LeagueState(ctx=replace(first.ctx, league_id="2"), projections=first.projections, values=first.values,
                        matchups={}, team_weeks={}, last_refresh=None)
    state.leagues["2"] = other

    def build_then_switch(ctx, proj, values):
        state.active = "2"
        return _tables()

    monkeypatch.setattr("app.ui.state.build_pair_tables", build_then_switch)
    state.ring_tables()
    assert state.source in first.pair_tables
    assert other.pair_tables == {}


def test_ring_tables_retries_after_a_failed_build(monkeypatch) -> None:
    state = _state_with_projection(monkeypatch)
    outcomes = [RuntimeError("boom"), _tables()]

    def flaky(ctx, proj, values):
        result = outcomes.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr("app.ui.state.build_pair_tables", flaky)
    with pytest.raises(RuntimeError):
        state.ring_tables()
    assert not state.ring_tables_ready()
    state.ring_tables()
    assert state.ring_tables_ready()


def test_concurrent_ring_tables_calls_build_once(monkeypatch) -> None:
    state = _state_with_projection(monkeypatch)
    started = threading.Event()
    release = threading.Event()
    calls: list[int] = []

    def slow_build(ctx, proj, values):
        calls.append(1)
        started.set()
        release.wait(5)
        return _tables()

    monkeypatch.setattr("app.ui.state.build_pair_tables", slow_build)
    worker = threading.Thread(target=state.ring_tables)
    worker.start()
    started.wait(5)
    second = threading.Thread(target=state.ring_tables)
    second.start()
    release.set()
    worker.join(5)
    second.join(5)
    assert calls == [1]


def test_ring_tables_without_a_league_raises() -> None:
    with pytest.raises(ValueError):
        AppState(cfg=_cfg("x")).ring_tables()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_state.py -q`
Expected: `ImportError`, or `AttributeError: 'AppState' object has no attribute 'ring_tables'`.

- [ ] **Step 3: Implement**

In `app/ui/state.py`:

Add these imports:

```python
import threading

from app.model.ring import PairTables, build_pair_tables
```

Add the last field to `LeagueState`:

```python
    # source -> pair tables for 3-way search; cleared by _rebuild_values, filled by AppState.ring_tables.
    pair_tables: dict[str, PairTables] = field(default_factory=dict)
```

Add a field to `AppState`, after `refreshing`:

```python
    _tables_lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)
```

Add these methods to `AppState`, after `secondary`:

```python
    def ring_tables_ready(self) -> bool:
        current = self._current()
        return current is not None and self.source in current.pair_tables

    def ring_tables(self, source: str | None = None) -> PairTables:
        """Active league's 3-way pair tables, built on first use. Blocking: call through run.io_bound only."""
        wanted = source or self.source
        with self._tables_lock:
            league = self._current()
            if league is None:
                raise ValueError(NO_LEAGUE_MESSAGE)
            cached = league.pair_tables.get(wanted)
            if cached is not None:
                return cached
            values, proj = league.values, league.projections[wanted]
            tables = build_pair_tables(league.ctx, proj, values)
            if league.values is values and league.projections.get(wanted) is proj:
                league.pair_tables[wanted] = tables
            return tables
```

Change `_rebuild_values` to:

```python
    def _rebuild_values(self) -> None:
        current = self._current()
        if current is not None:
            current.values = self._compute_values(current.ctx, current.projections)
            current.pair_tables.clear()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add app/ui/state.py tests/test_state.py
git commit -m "Cache 3-way pair tables per league and projection source"
```

---

### Task 5: Warm the tables in the background

**Files:**
- Modify: `app/ui/layout.py`
- Modify: `app/main.py`

**Interfaces:**
- Consumes: `AppState.ring_tables()` from Task 4.
- Produces: `async def warm_tables(state: AppState) -> None` in `app.ui.layout`. It never raises. A failure goes to `state.error` and is printed.

The project has no UI or startup tests. Task 4's state tests cover the retry after a failed warm-up. This task is verified with a smoke run.

- [ ] **Step 1: Add `warm_tables` and call it after a successful refresh**

In `app/ui/layout.py`, change the nicegui import to:

```python
from nicegui import app, background_tasks, run, ui
```

Add this below `on_refresh`:

```python
async def warm_tables(state: AppState) -> None:
    """Build the active league's 3-way pair tables off the event loop so Find is instant later."""
    try:
        await run.io_bound(state.ring_tables)
    except Exception as exc:  # background warm-up must never crash the app; surface it instead
        state.error = str(exc)
        print(f"trade table warm-up failed: {exc}")
        return
    print("trade tables ready")
```

In `on_refresh`, after the `ui.notify(f"Refreshed ...")` line and before `ui.navigate.reload()`, add:

```python
    background_tasks.create(warm_tables(state), name="warm_pair_tables")
```

- [ ] **Step 2: Warm after startup load and after startup refresh**

In `app/main.py`, add `from app.ui.layout import warm_tables`. Then replace `_background_refresh`, `_load_then_maybe_refresh`, and the `elif` branch of `_on_startup` as follows:

```python
async def _background_refresh(state: AppState) -> bool:
    state.refreshing = True
    try:
        await run.io_bound(state.refresh)
    except Exception as exc:  # background refresh must never crash the app; surface it instead
        state.error = str(exc)
        print(f"background refresh failed: {exc}")
        return False
    finally:
        state.refreshing = False
    return True


def _warm(state: AppState) -> None:
    background_tasks.create(warm_tables(state), name="warm_pair_tables")


async def _refresh_then_warm(state: AppState) -> None:
    if await _background_refresh(state):
        _warm(state)


async def _load_then_maybe_refresh(state: AppState, refresh_after: bool) -> None:
    """Load the cached league first so screens render, refresh if it's stale, then warm the 3-way tables."""
    try:
        await run.io_bound(state.load)
    except Exception as exc:
        state.error = str(exc)
        print(f"startup load failed: {exc}")
        return
    if refresh_after:
        await _background_refresh(state)  # a failed refresh leaves the cached league loaded and usable
    _warm(state)
```

`_refresh_then_warm` warms only when the refresh succeeds. It is used on the no-cache path, where a failed refresh means no league is loaded. A warm-up there would overwrite the refresh error with "no league selected".

In `_on_startup`, change the `elif` branch to:

```python
    elif refresh_in_background:
        background_tasks.create(_refresh_then_warm(state), name="startup_refresh")
```

- [ ] **Step 3: Run the tests and a smoke run**

Run: `uv run pytest -q`
Expected: all pass.

Smoke run, using the Smoke-run recipe in the plan header. This needs `config.local.toml` with a cached league, which exists in this repo.

1. Start the app in the background and wait for it to serve. Expected: `200`.
2. Use the Monitor tool with an until-loop, bounded at 120 s, on `grep -E "trade tables ready|warm-up failed|Traceback" /tmp/ff-smoke.log`.
   Expected: `trade tables ready` appears, with no `warm-up failed` and no `Traceback`.
3. Stop the app.

- [ ] **Step 4: Commit**

```bash
git add app/ui/layout.py app/main.py
git commit -m "Warm 3-way pair tables after startup and refresh"
```

---

### Task 6: Ring trade URL and ring trade screen

**Files:**
- Create: `app/ui/ring_url.py`. It must not import nicegui, so it stays unit-testable.
- Rewrite: `app/ui/trade.py`
- Modify: `app/ui/finder.py`. Only `_trade_link` changes here, so existing 2-way links use the new URL.
- Test: `tests/test_ring_url.py`

**Interfaces:**
- Consumes: `Leg`, `Ring`, `MIN_GAIN`, and `ring_delta` from Task 1.
- Produces:
  - `ring_link(ring: Sequence[Leg]) -> str`. It returns `/trade?ring=1%2C4%2C7&give=...`. Player ids within a leg are sorted, and legs are separated by `|`.
  - `parse_ring(ring: str, give: str, rosters: Mapping[int, frozenset[str]], default: Sequence[int]) -> list[Leg]`.
  - The page is `@ui.page("/trade") def trade_page(ring: str = "", give: str = "")`. The old `me`, `opp`, `give`, and `get` parameters are gone.

`parse_ring` rules:
- It walks the `ring` tokens in order. It skips non-numeric tokens, unknown roster ids, and repeats, but keeps each surviving token's original position for aligning with `give`.
- It keeps at most 3 rosters.
- Each kept roster's gives are the comma-separated ids at the same position in `give`, split on `|`, intersected with that roster's players. A missing position means no gives.
- Fewer than 2 surviving rosters returns `default` with empty gives.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_ring_url.py`:

```python
from urllib.parse import parse_qs, urlparse

from app.model.trade import Leg
from app.ui.ring_url import parse_ring, ring_link

ROSTERS = {1: frozenset({"a", "b"}), 4: frozenset({"c"}), 7: frozenset({"d", "e"})}


def _query(link: str) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(urlparse(link).query, keep_blank_values=True).items()}


def test_ring_link_round_trips() -> None:
    ring = [Leg(1, frozenset({"b", "a"})), Leg(4, frozenset()), Leg(7, frozenset({"e"}))]
    link = ring_link(ring)
    assert link.startswith("/trade?")
    q = _query(link)
    assert q == {"ring": "1,4,7", "give": "a,b||e"}
    assert parse_ring(q["ring"], q["give"], ROSTERS, default=(1, 4)) == ring


def test_parse_ring_drops_unknown_duplicate_and_junk_ids_keeping_alignment() -> None:
    legs = parse_ring("1,99,x,1,7", "a|zz|q|b|d", ROSTERS, default=(1, 4))
    assert legs == [Leg(1, frozenset({"a"})), Leg(7, frozenset({"d"}))]


def test_parse_ring_filters_players_not_on_the_roster() -> None:
    legs = parse_ring("1,4", "a,c,gone|c,a", ROSTERS, default=(1, 4))
    assert legs == [Leg(1, frozenset({"a"})), Leg(4, frozenset({"c"}))]


def test_parse_ring_pads_missing_gives_and_caps_at_three() -> None:
    four = {**ROSTERS, 9: frozenset()}
    legs = parse_ring("1,4,7,9", "a", four, default=(1, 4))
    assert [leg.roster_id for leg in legs] == [1, 4, 7]
    assert legs[1].gives == frozenset() and legs[2].gives == frozenset()


def test_parse_ring_falls_back_to_default_with_fewer_than_two_rosters() -> None:
    assert parse_ring("", "", ROSTERS, default=(1, 4)) == [Leg(1, frozenset()), Leg(4, frozenset())]
    assert parse_ring("7,99", "d", ROSTERS, default=(1, 4)) == [Leg(1, frozenset()), Leg(4, frozenset())]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_ring_url.py -q`
Expected: `ModuleNotFoundError: No module named 'app.ui.ring_url'`.

- [ ] **Step 3: Implement `app/ui/ring_url.py`**

```python
"""Trade-screen URL for a ring of rosters: encode from legs, parse back defensively."""
from __future__ import annotations

from typing import Mapping, Sequence
from urllib.parse import urlencode

from app.model.trade import Leg

TRADE_PATH = "/trade"
MAX_LEGS = 3


def ring_link(ring: Sequence[Leg]) -> str:
    ids = ",".join(str(leg.roster_id) for leg in ring)
    gives = "|".join(",".join(sorted(leg.gives)) for leg in ring)
    return f"{TRADE_PATH}?{urlencode({'ring': ids, 'give': gives})}"


def _kept_positions(ring: str, rosters: Mapping[int, frozenset[str]]) -> list[tuple[int, int]]:
    kept: list[tuple[int, int]] = []
    seen: set[int] = set()
    for position, token in enumerate(ring.split(",")):
        token = token.strip()
        if not token.isdigit():
            continue
        roster_id = int(token)
        if roster_id in rosters and roster_id not in seen:
            kept.append((position, roster_id))
            seen.add(roster_id)
    return kept[:MAX_LEGS]


def parse_ring(ring: str, give: str, rosters: Mapping[int, frozenset[str]], default: Sequence[int]) -> list[Leg]:
    kept = _kept_positions(ring, rosters)
    if len(kept) < 2:
        return [Leg(roster_id, frozenset()) for roster_id in default]
    parts = give.split("|") if give else []
    legs: list[Leg] = []
    for position, roster_id in kept:
        raw = parts[position] if position < len(parts) else ""
        legs.append(Leg(roster_id, frozenset(p for p in raw.split(",") if p) & rosters[roster_id]))
    return legs
```

- [ ] **Step 4: Run the URL tests**

Run: `uv run pytest tests/test_ring_url.py -q`
Expected: 5 passed.

- [ ] **Step 5: Rewrite `app/ui/trade.py` as a ring screen**

Replace the whole file with:

```python
"""Trade screen: a ring of two or three rosters, each giving its picked players to the next, with live deltas."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import polars as pl
from nicegui import ui

from app.model.league import LeagueContext, Roster
from app.model.lineup import WeeklyIndex, index_weekly
from app.model.trade import MIN_GAIN, Leg, ring_delta
from app.ui.layout import ensure_loaded, frame
from app.ui.ring_url import MAX_LEGS, parse_ring
from app.ui.state import get_state
from app.ui.theme import ACCENT, CARD, MUTED, NEGATIVE, POSITIVE, TEXT


@dataclass
class _Side:
    roster_id: int
    given: set[str] = field(default_factory=set)


def _roster_by_id(ctx: LeagueContext, roster_id: int) -> Roster:
    for roster in ctx.rosters:
        if roster.roster_id == roster_id:
            return roster
    raise ValueError(f"roster {roster_id} not in league")


def _ranked_players(values: pl.DataFrame, roster: Roster) -> list[tuple[str, str]]:
    ids = list(roster.players)
    if not ids:
        return []
    frame_ids = pl.DataFrame({"sleeper_id": ids})
    score = pl.coalesce(pl.col("intrinsic"), pl.col("ros_primary"), pl.lit(0.0))
    joined = frame_ids.join(values.select("sleeper_id", "name", "intrinsic", "ros_primary"), on="sleeper_id", how="left")
    ranked = joined.with_columns(
        pl.coalesce(pl.col("name"), pl.col("sleeper_id")).alias("name"), score.alias("score"),
    ).sort("score", descending=True, nulls_last=True)
    return list(zip(ranked["sleeper_id"].to_list(), ranked["name"].to_list()))


def _chip_colors(selected: bool) -> tuple[str, str]:
    return (ACCENT, "#000000") if selected else (CARD, TEXT)


def _default_pair(ctx: LeagueContext, me: int) -> tuple[int, ...]:
    others = [r.roster_id for r in ctx.rosters if r.roster_id != me]
    return (me, others[0]) if others else (me,)


def _free_roster(ctx: LeagueContext, sides: list[_Side]) -> int | None:
    used = {side.roster_id for side in sides}
    return next((r.roster_id for r in ctx.rosters if r.roster_id not in used), None)


def _delta_block(label: str, delta: float, before: float, after: float) -> None:
    color = POSITIVE if delta > 0 else NEGATIVE if delta < 0 else MUTED
    with ui.column().classes("items-center gap-1"):
        ui.label(label).classes("ff-label")
        ui.label(f"{delta:+.1f}").style(f"color:{color};font-size:32px;font-weight:700")
        ui.label(f"{before:.1f} -> {after:.1f}").classes("ff-label")


def _summary(ctx: LeagueContext, index: WeeklyIndex, sides: list[_Side], source: str) -> None:
    ring = tuple(Leg(side.roster_id, frozenset(side.given)) for side in sides)
    results = ring_delta(ctx, index, ring)
    with ui.column().classes("ff-card q-pa-lg gap-4 items-center w-full"):
        with ui.row().classes("gap-12"):
            for i, side in enumerate(sides):
                label = "MY DELTA" if i == 0 else _roster_by_id(ctx, side.roster_id).team_name.upper()
                result = results[side.roster_id]
                _delta_block(label, result.delta, result.before, result.after)
        if all(r.delta > MIN_GAIN for r in results.values()):
            ui.label("FAIR FOR ALL").style(f"color:#000000;background:{POSITIVE}").classes("ff-label q-px-md q-py-xs rounded")
        ui.label(f"Projection: {source}").classes("ff-label")


def _render_side(ctx: LeagueContext, values: pl.DataFrame, sides: list[_Side], i: int,
                 on_roster: Callable[[int, int], None], on_toggle: Callable[[int, str], None],
                 on_remove: Callable[[], None] | None) -> None:
    side = sides[i]
    receiver = _roster_by_id(ctx, sides[(i + 1) % len(sides)].roster_id)
    taken = {s.roster_id for j, s in enumerate(sides) if j != i}
    options = {r.roster_id: r.team_name for r in ctx.rosters if r.roster_id not in taken}
    with ui.column().classes("ff-card q-pa-md gap-2").style("flex:1"):
        with ui.row().classes("items-center justify-between w-full"):
            ui.label(f"GIVES TO {receiver.team_name.upper()}").classes("ff-label")
            if on_remove is not None:
                ui.button("Remove", on_click=on_remove).props("flat dense")
        ui.select(options, value=side.roster_id, on_change=lambda e: on_roster(i, e.value)).classes("w-full")
        with ui.row().classes("gap-2 flex-wrap"):
            for player_id, name in _ranked_players(values, _roster_by_id(ctx, side.roster_id)):
                background, text_color = _chip_colors(player_id in side.given)
                ui.chip(name, selectable=True, selected=player_id in side.given, color=background, text_color=text_color,
                        on_click=lambda _, pid=player_id: on_toggle(i, pid))


@ui.page("/trade")
def trade_page(ring: str = "", give: str = "") -> None:
    with frame("Trade"):
        state = get_state()
        if not ensure_loaded(state):
            return
        ctx = state.ctx
        if len(ctx.rosters) < 2:
            ui.label("A trade needs at least two rosters in the league.").classes("ff-label")
            return
        proj = state.primary()
        values = state.values
        index = index_weekly(proj.weekly)
        rosters = {r.roster_id: frozenset(r.players) for r in ctx.rosters}
        legs = parse_ring(ring, give, rosters, default=_default_pair(ctx, state.my_roster_id()))
        sides = [_Side(leg.roster_id, set(leg.gives)) for leg in legs]

        def on_roster(i: int, roster_id: int) -> None:
            sides[i] = _Side(roster_id)
            board.refresh()

        def on_toggle(i: int, player_id: str) -> None:
            sides[i].given.symmetric_difference_update({player_id})
            board.refresh()

        def on_add() -> None:
            free = _free_roster(ctx, sides)
            if free is not None and len(sides) < MAX_LEGS:
                sides.append(_Side(free))
                board.refresh()

        def on_remove() -> None:
            del sides[2:]
            board.refresh()

        @ui.refreshable
        def board() -> None:
            _summary(ctx, index, sides, proj.source)
            with ui.row().classes("w-full gap-4 items-start"):
                for i in range(len(sides)):
                    _render_side(ctx, values, sides, i, on_roster, on_toggle, on_remove if i == 2 else None)
            if len(sides) < MAX_LEGS and _free_roster(ctx, sides) is not None:
                ui.button("Add third team", on_click=on_add)

        board()
```

- [ ] **Step 6: Move the 2-way finder link to the ring URL**

In `app/ui/finder.py`:
- Remove `from urllib.parse import quote`.
- Add `from app.model.trade import Leg` and `from app.ui.ring_url import ring_link`.
- Replace `_trade_link` with:

```python
def _trade_link(me: int, opp: int, give: tuple[str, ...], get: tuple[str, ...]) -> str:
    return ring_link((Leg(me, frozenset(give)), Leg(opp, frozenset(get))))
```

- [ ] **Step 7: Run the tests and a smoke run**

Run: `uv run pytest -q`
Expected: all pass.

Smoke run, using the Smoke-run recipe in the plan header. Start the app and wait for it to serve, then run:

```bash
for u in "/trade" "/trade?ring=1,2,3&give=|" "/trade?ring=1,1,999,x&give=zz" "/finder"; do
  curl -s -o /dev/null -w "$u %{http_code}\n" "http://127.0.0.1:8765$u"; done
grep -c Traceback /tmp/ff-smoke.log
```

Expected: four lines ending `200`, then `0`. NiceGUI renders the page body over its websocket, so a traceback in the log is the real failure signal.

Then open `http://127.0.0.1:8765/trade` in a browser, or with the Playwright tools if available, and check two things. "Add third team" should add a third column. Toggling a chip should change the summary deltas. Afterwards, grep the log for `Traceback` again and stop the app.

- [ ] **Step 8: Commit**

```bash
git add app/ui/ring_url.py app/ui/trade.py app/ui/finder.py tests/test_ring_url.py
git commit -m "Trade screen holds a ring of two or three rosters"
```

---

### Task 7: 3-way mode in the finder

**Files:**
- Modify: `app/ui/finder.py`

**Interfaces:**
- Consumes: `find_ring_trades` and `RingDeal` from Task 3, `AppState.ring_tables` and `ring_tables_ready` from Task 4, and `ring_link` from Task 6.
- Produces: `search_ring_deals(tables: PairTables, ctx: LeagueContext, me: int, partner: int) -> list[RingDeal]` in `app.ui.finder`. A `partner` of `ALL_OPPONENTS` means no restriction.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_ring.py`:

```python
def test_search_ring_deals_maps_all_opponents_to_no_partner() -> None:
    from app.ui.finder import ALL_OPPONENTS, search_ring_deals
    ctx, proj, values = three_team()
    tables = build_pair_tables(ctx, proj, values)
    everything = search_ring_deals(tables, ctx, 1, ALL_OPPONENTS)
    assert everything == find_ring_trades(tables, 1, [2, 3])
    assert search_ring_deals(tables, ctx, 1, 1) == []
```

This test imports a nicegui module, which is fine: `app.ui.finder` only registers a page route when imported.

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_ring.py::test_search_ring_deals_maps_all_opponents_to_no_partner -q`
Expected: `ImportError: cannot import name 'search_ring_deals'`.

- [ ] **Step 3: Implement**

In `app/ui/finder.py`, add these imports:

```python
from app.model.ring import PairTables, RingDeal, find_ring_trades
```

Add these constants and helpers after `search_deals`:

```python
TWO_WAY, THREE_WAY = "2-way", "3-way"


def search_ring_deals(tables: PairTables, ctx: LeagueContext, me: int, partner: int) -> list[RingDeal]:
    opponents = [r.roster_id for r in ctx.rosters if r.roster_id != me]
    return find_ring_trades(tables, me, opponents, None if partner == ALL_OPPONENTS else partner)


def _ring_card(lookup: dict[str, str], teams: dict[int, str], me: int, deal: RingDeal) -> None:
    with ui.column().classes("ff-card q-pa-md gap-2"):
        for i, leg in enumerate(deal.ring):
            receiver = deal.ring[(i + 1) % len(deal.ring)].roster_id
            players = _names(tuple(sorted(leg.gives)), lookup)
            ui.label(f"{teams[leg.roster_id]} gives {players} to {teams[receiver]}").style(f"color:{TEXT}")
        with ui.row().classes("gap-6"):
            for leg in deal.ring:
                label = "MY DELTA" if leg.roster_id == me else teams[leg.roster_id].upper()
                _delta_label(label, deal.deltas[leg.roster_id])
        ui.link("Open in trade screen", ring_link(deal.ring)).classes("ff-label")
```

Replace the body of `finder_page`, from `picked = ...` through `results_view()`, with:

```python
        teams = {roster.roster_id: roster.team_name for roster in ctx.rosters}
        picked = {"me": me or state.my_roster_id(), "opp": opp, "mode": TWO_WAY}
        results: list[Deal] | list[RingDeal] = []

        @ui.refreshable
        def results_view() -> None:
            if not results:
                ui.label("No deals found yet.").classes("ff-label")
                return
            for deal in results:
                if isinstance(deal, RingDeal):
                    _ring_card(lookup, teams, picked["me"], deal)
                else:
                    _deal_card(lookup, picked["me"], deal)

        def _search() -> list[Deal] | list[RingDeal]:
            if picked["mode"] == THREE_WAY:
                return search_ring_deals(state.ring_tables(), ctx, picked["me"], picked["opp"])
            return search_deals(ctx, proj, values, picked["me"], picked["opp"])

        async def on_find() -> None:
            nonlocal results
            if picked["opp"] != ALL_OPPONENTS and picked["opp"] == picked["me"]:
                ui.notify("Pick a team other than your own roster", type="warning")
                return
            if picked["mode"] == THREE_WAY and not state.ring_tables_ready():
                ui.notify("Building 3-way trade tables; this takes about half a minute", type="info")
            spinner.visible = True
            try:
                results = await run.io_bound(_search)
            except Exception as exc:  # UI boundary: caught, recorded, and surfaced, never swallowed
                state.error = str(exc)
                ui.notify(f"Trade finder failed: {exc}", type="negative")
                return
            finally:
                spinner.visible = False
            state.error = None
            results_view.refresh()

        def on_mode(value: str) -> None:
            nonlocal results
            picked["mode"] = value
            opp_select.props(f'label="{"Partner" if value == THREE_WAY else "Opponent"}"')
            results = []
            results_view.refresh()

        with ui.row().classes("gap-4 items-end"):
            opp_options = {ALL_OPPONENTS: "All opponents", **teams}
            ui.toggle([TWO_WAY, THREE_WAY], value=TWO_WAY, on_change=lambda e: on_mode(e.value))
            ui.select(teams, value=picked["me"], label="My roster",
                      on_change=lambda e: picked.__setitem__("me", e.value)).classes("min-w-40")
            opp_select = ui.select(opp_options, value=picked["opp"], label="Opponent",
                                   on_change=lambda e: picked.__setitem__("opp", e.value)).classes("min-w-40")
            ui.button("Find", on_click=on_find)
            spinner = ui.spinner()
            spinner.visible = False

        results_view()
```

Also update the module docstring to: `"""Trade finder screen: search for 2-way deals or 3-way rings that help every team involved."""`

- [ ] **Step 4: Run the tests and a smoke run**

Run: `uv run pytest -q`
Expected: all pass.

Smoke run, using the Smoke-run recipe in the plan header. Start the app and wait for it to serve. Expected: `200`, and `grep -c Traceback /tmp/ff-smoke.log` prints `0`.

Then open `/finder` in a browser, or drive it with Playwright if available:
1. Switch to 3-way, leave Partner on All opponents, and press Find.
2. Within about 30 s, cards should appear with three "gives ... to ..." lines and three deltas. Every delta should be positive.
3. The card link should open `/trade` with three columns whose deltas match the card.
4. A second Find should return almost instantly.

If the league has no fair rings, "No deals found yet." is a correct result. Say so in the report instead of treating it as a failure. Stop the app afterwards.

- [ ] **Step 5: Commit**

```bash
git add app/ui/finder.py tests/test_ring.py
git commit -m "Add 3-way ring search to the trade finder"
```

---

## After the last task

- Run the whole suite, slow tests included: `uv run pytest -q && uv run pytest -m slow -q`.
- Per the repo CLAUDE.md, run `graphify update .` only if `graphify-out/graph.json` exists. It does not exist today, so skip it.
- Leave the branch unpushed. The user decides between a PR against `multi-league` and waiting for PR #2 to merge.
