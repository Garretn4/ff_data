# Three-way trade finder

Date: 2026-09-24
Status: approved design, pre-implementation
Builds on: `2026-09-16-ff-analyst-design.md` (trade engine), `2026-09-18-multi-league-design.md` (per-league state)

## Purpose

The finder only proposes two-team trades. Many real deals need a third
team: the package I want from C is not something C wants from me, but C
wants something B has, and B wants something I have. This adds a 3-way
search to the finder and lets the trade screen hold a ring of three
rosters so a proposed deal can be tweaked with all three deltas live.

All constraints from the original spec still apply: no new dependencies,
scoring and slots read from league settings, type hints, small functions,
no AI attribution.

## Decisions made during design

| Question | Decision | Reason |
|---|---|---|
| Which topologies | Ring only: A gives to B, B to C, C to A | Covers the classic 3-way; general topologies need a per-player destination picker and blow up the search |
| 4-way | Out of scope | User chose 3-way only |
| Fairness rule | Every team gains more than `MIN_GAIN`, ranked by the smallest gain | Same rule as 2-way, so the two modes are comparable |
| Search method | Precomputed pairwise tables joined around the ring | Brute force is 37 minutes at the chosen search size; the join needs no lineup evaluations |
| Search size | Top 8 players per roster, packages of 1 or 2 | About 117k evaluations, 26 seconds for 10 teams on the benchmark fixture |
| When to precompute | In the background after refresh and startup load; on demand under a lock if Find beats it | User chose background; the lock keeps Find from duplicating a running build |
| Where 3-way appears | A 2-way / 3-way toggle on the existing finder | User chose one screen over a second page |
| Partner choice | Optional fixed partner; the third team is always searched | User wanted to target a manager they already have in mind |
| What a deal opens | A three-column trade screen | User chose editable deals over a read-only card |
| 2-way finder | Unchanged | It searches a larger space (top 12) and changing it would change its results |

## Grading model

`app/model/trade.py` gains a ring grader. A ring is an ordered tuple of
two or three legs. Each leg names a roster and the players it sends to
the next leg in the tuple; the last leg sends to the first.

```
@dataclass(frozen=True)
class Leg:
    roster_id: int
    gives: frozenset[str]

@dataclass(frozen=True)
class TeamResult:
    before: float
    after: float
    delta: float

Ring = tuple[Leg, ...]

def ring_delta(ctx, index, ring, before=None) -> dict[int, TeamResult]
```

For each leg, the team's after-roster is its startable set minus what it
gives plus what the previous leg gives, intersected with that previous
team's startable set. A given player that is not on the giver's startable
roster is ignored, which matches the current two-sided behavior. `before`
is an optional map of roster id to precomputed strength, used by the
table builder to avoid recomputing it per combination.

`trade_delta` keeps its signature and result type and becomes a wrapper
that builds a two-leg ring and unpacks the two results. The existing
tests pass unchanged. Roster ids in a ring must be distinct; a repeat
raises `ValueError`.

## Search

New module `app/model/ring.py`.

**Packages.** A package is a sorted tuple of player ids. For each roster,
candidates are its top 8 players by the same score the 2-way
finder uses (intrinsic, falling back to rest-of-season primary), and
packages are every 1- or 2-player combination of them, so 36 per roster.
`_top_players` and `_packages` move from `finder.py` into `ring.py` and
`finder.py` imports them, with `top_n` and `max_side` as parameters.

**Pair tables.** `build_pair_tables(ctx, proj, values, top_n=8, max_side=2)`
grades every package pair for every unordered roster pair once, with the
per-roster before-strength computed once. The result keeps, per ordered
pair (giver, counterparty), only the entries where the giver clears
`MIN_GAIN`:

```
@dataclass(frozen=True)
class PairTables:
    # accepts[(x, y)][give_x][get_from_y] = delta_x, only where delta_x > MIN_GAIN
    accepts: dict[tuple[int, int], dict[Package, dict[Package, float]]]
    # by_get[(x, y)][get_from_y] = packages give_x that x accepts against that get
    by_get: dict[tuple[int, int], dict[Package, tuple[Package, ...]]]
```

`by_get` is the reverse index that makes the join linear in the number
of matches.

**Ring join.** `find_ring_trades(tables, me, opponents, partner=None, limit=10)`
returns ranked `RingDeal` values:

```
@dataclass(frozen=True)
class RingDeal:
    ring: tuple[Leg, Leg, Leg]     # ordered me -> b -> c -> me
    deltas: dict[int, float]
```

For every ordered pair (b, c) of distinct opponents, restricted to pairs
containing `partner` when one is given, and with me as `a`:

- a's delta is `accepts[(a, c)][give_a][give_c]`
- b's delta is `accepts[(b, a)][give_b][give_a]`
- c's delta is `accepts[(c, b)][give_c][give_b]`

Iterate `give_a` and `give_c` over `accepts[(a, c)]`, look up candidate
`give_b` in `by_get[(b, a)][give_a]`, and keep the triple when
`accepts[(c, b)][give_c]` contains `give_b`. Both orientations of a pair
are covered because (b, c) and (c, b) are both visited. Results are
sorted by the smallest of the three deltas, descending, and cut to
`limit`. A deal is exact, not approximate: each team's delta is the same
number `ring_delta` would compute, because a team's result depends only
on what it gives and what it receives.

## State and precompute

`LeagueState` gains `pair_tables: dict[str, PairTables]` keyed by
projection source, empty on build. Tables depend on the projection and
on the value ranking, so `_rebuild_values` clears the dict (this covers
source switches and model recomputes, which call it) and `_build` starts
from empty.

`AppState.ring_tables(source) -> PairTables` returns the active league's
table for `source`, building and storing it under a `threading.Lock`
when missing. The lock is held for the whole build so a concurrent
caller waits rather than building again. `AppState.warm_pair_tables()`
calls `ring_tables(self.source)` on the league that is active when it
starts, storing into that league's state even if the active league
changes meanwhile.

Warm-up runs as a NiceGUI background task, never awaited by the caller:

- `app/main.py` after the startup load succeeds, and again after a
  startup refresh succeeds.
- `app/ui/layout.py` in `on_refresh` after a successful refresh, before
  the page reload.

A warm-up failure is recorded on `state.error` and printed, matching how
background refresh failures are handled.

## Finder screen

`app/ui/finder.py` adds a mode toggle, `2-way` and `3-way`, next to the
roster selects. In 3-way mode the second select is labelled Partner with
an All option, and Find runs the ring search in an io-bound task:
`state.ring_tables(state.source)` then `find_ring_trades`. The spinner
shows while a table builds. Picking your own roster as partner warns and
returns, as today.

A 3-way deal card shows one line per leg, "Team gives Player, Player to
Team", then three delta blocks: yours labelled MY DELTA, the others
labelled with the team name. The card links to the trade screen with the
ring encoding below. 2-way cards keep their current layout; their link
moves to the same ring encoding with two legs.

## Trade screen

`app/ui/trade.py` holds a list of two or three sides instead of a fixed
pair. Each side is a roster and the set of players it gives to the next
side in the list; the last side gives to the first.

Layout: a summary row on top with one delta block per side and the
FAIR FOR ALL badge when every delta exceeds `MIN_GAIN`, then the side
columns in a row. A column's title is the team name and "gives to" the
next team's name. Each roster select excludes rosters already in the
ring. Changing a roster clears that side's selections.

An Add third team button appears when there are two sides and adds a
column for the first roster not in the ring. A Remove control on the
third column drops it. Both refresh the summary and columns.

URL: `/trade?ring=1,4,7&give=a,b|c|d,e`. `ring` is roster ids in order;
`give` is one comma-separated player list per leg, separated by `|`, in
the same order. A missing or short `give` means empty selections for the
remaining legs. Without `ring`, the page opens with your roster and the
first other roster. The old `me`, `opp`, `give`, `get` parameters are
dropped; only the finder generates trade links and it moves to the new
form for both modes.

## Testing

Model tests in `tests/test_trade.py` and a new `tests/test_ring.py`:

- `ring_delta` on three teams: each team's delta equals what a direct
  before/after strength computation gives, and a two-leg ring reproduces
  `trade_delta`.
- Duplicate roster id in a ring raises.
- Pair-table entries equal `trade_delta` for the same packages, and
  entries below `MIN_GAIN` are absent.
- A constructed three-team league where no fair 2-way exists but a ring
  does: `find_trades` returns nothing for every pair, `find_ring_trades`
  returns the ring, with the right legs and deltas.
- Partner restriction: with `partner` set, every returned ring includes
  that roster.
- Runtime: on a 10-team random fixture built with `top_n=8, max_side=2`,
  the pair-table build stays under 60 seconds and the join under 5
  seconds. The build test is marked slow and excluded from the default
  run if it makes the suite noticeably slower; the join test always runs
  against tables built at `top_n=4`.

State tests in `tests/test_state.py`: `ring_tables` builds once and
reuses, `_rebuild_values` clears the tables, and warm-up stores into the
league it started on.

No UI tests, matching the project.

## Out of scope

4-way trades, non-ring topologies, draft picks in trades, any change to
how the 2-way finder searches or ranks, and persisting pair tables across
app restarts.
