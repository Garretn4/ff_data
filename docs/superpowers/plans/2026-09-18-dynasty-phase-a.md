# Dynasty Phase A Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Detect dynasty leagues from Sleeper settings, price draft picks as roster assets, switch market values to dynasty pricing, and rank teams by a dynasty power score on the League screen.

**Architecture:** `LeagueContext` gains a dynasty flag, draft rounds, and traded picks. A new pure module `app/model/picks.py` derives pick ownership and prices picks from the FantasyCalc snapshot. A new pure module `app/model/dynasty.py` blends this-season power with long-term asset value and labels each team's window. `AppState` computes picks once per load; the League screen adds dynasty columns only when the league is dynasty. Redraft leagues are unchanged.

**Tech Stack:** Python 3.12, polars, NiceGUI 3.17, pytest, stdlib only. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-09-18-dynasty-rankings-design.md` (Phase A sections). Phase B (draft capital, intrinsic dynasty value, college download) is a separate plan.

## Global Constraints

- No new dependencies. No AI attribution anywhere (no co-author trailers, no generated-with comments).
- Type hints everywhere, small functions, no dead code. Scoring and slots come from league settings.
- Every UI boundary catches exceptions, stores `state.error`, and notifies.
- Keeper leagues (`settings.type == 1`) are treated as dynasty for valuation.
- Redraft leagues must render the League screen exactly as today.
- Tests: `uv run pytest -q`. Browser drive: `FF_NATIVE=0 uv run python -m app run` on port 8765.
- Commit messages: plain imperative sentence, no trailer.

## Facts the plan relies on (verified 2026-09-18)

- Cached league JSON: `settings.type` (0 redraft, 1 keeper, 2 dynasty), `settings.draft_rounds`.
- Cached `sleeper:/league/{id}/traded_picks` rows: `{"season": "2027", "round": 1, "roster_id": <original>, "owner_id": <current roster id>, "previous_owner_id": ...}`. Empty list for the configured redraft league.
- FantasyCalc dynasty rows: `player.position == "PICK"`, `player.sleeperId` like `FP_2027_1`, names `"2027 1st"`, `"2027 1st (Early)"`, `"2027 1st (Mid)"`, `"2027 1st (Late)"`, `"2028 2nd"`.
- After the final fix wave on this branch, `league_rows(ctx, team_weeks: dict[str, pl.DataFrame], matchups, me)` and `AppState.team_weeks` exist.
- `Store.latest_snapshot("fantasycalc")` returns columns `source, taken_at, sleeper_id, name, position, team, value, overall_rank, position_rank, trend30`.
- `power_scores(records, ros_avg) -> dict[int, float]` in `app/model/standings.py`.

## File map

| File | Responsibility |
|---|---|
| `app/model/league.py` (modify) | `TradedPick`, `LeagueContext.dynasty/draft_rounds/traded_picks` |
| `app/market.py` (modify) | `isDynasty` from league type |
| `app/model/values.py` (modify) | Exclude PICK rows from the player table |
| `app/model/picks.py` (create) | `Pick`, `owned_picks`, `pick_tiers`, `pick_label`, `price_picks` |
| `app/model/dynasty.py` (create) | `dynasty_power`, `window_label`, `dynasty_rows` |
| `app/ui/state.py` (modify) | `picks: list[Pick]` computed in `load()` |
| `app/ui/league.py` (modify) | Dynasty columns, sort, caption |
| Tests: `tests/test_league.py`, `tests/test_market.py`, `tests/test_values.py`, `tests/test_picks.py` (create), `tests/test_dynasty.py` (create), `tests/test_state.py` |

---

### Task 1: Dynasty fields on the league context

**Files:**
- Modify: `app/model/league.py`
- Test: `tests/test_league.py`

**Interfaces:**
- Produces: `TradedPick(season: int, round: int, original_roster: int, owner_roster: int)` frozen dataclass; `LeagueContext.dynasty: bool`, `.draft_rounds: int`, `.traded_picks: tuple[TradedPick, ...]`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_league.py`, extend `seed` to also cache traded picks, and add tests. `LEAGUE` there has `settings: {playoff_week_start: 15, trade_deadline: 12}` (no `type`, no `draft_rounds`).

```python
TRADED = [{"season": "2027", "round": 1, "roster_id": 1, "owner_id": 2, "previous_owner_id": 1}]


def seed(store: Store) -> None:
    ...existing puts...
    store.put_cached("sleeper:/league/1/traded_picks", json.dumps(TRADED), now=1.0)


def test_league_context_defaults_to_redraft_with_four_rounds(tmp_path: Path) -> None:
    store = Store(tmp_path / "app.sqlite")
    seed(store)
    ctx = load_league_context(store, "1")
    assert ctx.dynasty is False and ctx.draft_rounds == 4
    assert ctx.traded_picks == (TradedPick(2027, 1, 1, 2),)


def test_league_context_flags_dynasty_and_keeper(tmp_path: Path) -> None:
    for league_type, expected in ((2, True), (1, True), (0, False)):
        store = Store(tmp_path / f"{league_type}.sqlite")
        seed(store)
        league = {**LEAGUE, "settings": {**LEAGUE["settings"], "type": league_type, "draft_rounds": 3}}
        store.put_cached("sleeper:/league/1", json.dumps(league), now=1.0)
        ctx = load_league_context(store, "1")
        assert ctx.dynasty is expected and ctx.draft_rounds == 3


def test_league_context_without_traded_picks_cache(tmp_path: Path) -> None:
    store = Store(tmp_path / "app.sqlite")
    seed(store)
    store.put_cached("sleeper:/league/1/traded_picks", "null", now=1.0)
    assert load_league_context(store, "1").traded_picks == ()
```

Import `TradedPick` from `app.model.league` at the top.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_league.py -q` → ImportError on `TradedPick`.

- [ ] **Step 3: Implement**

In `app/model/league.py`, add after `NO_TRADE_DEADLINE`:

```python
DEFAULT_DRAFT_ROUNDS = 4
DYNASTY_TYPES = (1, 2)   # Sleeper settings.type: 1 keeper, 2 dynasty


@dataclass(frozen=True)
class TradedPick:
    season: int
    round: int
    original_roster: int
    owner_roster: int
```

Add fields to `LeagueContext` after `rosters`:

```python
    dynasty: bool = False
    draft_rounds: int = DEFAULT_DRAFT_ROUNDS
    traded_picks: tuple[TradedPick, ...] = ()
```

Add a parser and use it in `load_league_context`:

```python
def _traded_picks(raw: list[dict[str, Any]] | None) -> tuple[TradedPick, ...]:
    return tuple(
        TradedPick(season=int(r["season"]), round=int(r["round"]),
                   original_roster=int(r["roster_id"]), owner_roster=int(r["owner_id"]))
        for r in (raw or [])
    )
```

and in the `LeagueContext(...)` call:

```python
        dynasty=int(settings.get("type", 0)) in DYNASTY_TYPES,
        draft_rounds=int(settings.get("draft_rounds", DEFAULT_DRAFT_ROUNDS)),
        traded_picks=_traded_picks(_cached_json(store, f"sleeper:/league/{league_id}/traded_picks")),
```

- [ ] **Step 4: Run tests and commit**

Run: `uv run pytest -q` → all pass (existing `LeagueContext(...)` constructions in tests keep working because the new fields have defaults).

```bash
git add app/model/league.py tests/test_league.py
git commit -m "Read dynasty type, draft rounds, and traded picks into the league context"
```

---

### Task 2: Dynasty market pricing and PICK rows

**Files:**
- Modify: `app/market.py`, `app/model/values.py`
- Test: `tests/test_market.py`, `tests/test_values.py`

**Interfaces:**
- `fantasycalc_params(league)` sets `"isDynasty": "true"` when `league["settings"]["type"]` is 1 or 2.
- `player_values` drops snapshot rows with `position == "PICK"` before joining.

- [ ] **Step 1: Write the failing tests**

`tests/test_market.py`: extend `test_fantasycalc_params_from_league_settings`:

```python
    dynasty = {**LEAGUE, "settings": {"type": 2}}
    assert fantasycalc_params(dynasty)["isDynasty"] == "true"
    keeper = {**LEAGUE, "settings": {"type": 1}}
    assert fantasycalc_params(keeper)["isDynasty"] == "true"
```

`tests/test_values.py` has no end-to-end `player_values` test (it tests `_trend_stats` and `replacement_ros` only), so test a new pure helper. Add:

```python
from app.model.values import market_players


def test_market_players_drops_pick_rows_and_renames() -> None:
    snapshot = pl.DataFrame({
        "sleeper_id": ["4046", "FP_2027_1"], "name": ["Patrick Mahomes", "2027 1st"], "position": ["QB", "PICK"],
        "value": [5000.0, 2811.0], "overall_rank": [30, 31],
    })
    out = market_players(snapshot)
    assert out.columns == ["sleeper_id", "market_value", "market_rank"]
    assert out.to_dicts() == [{"sleeper_id": "4046", "market_value": 5000.0, "market_rank": 30}]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_market.py tests/test_values.py -q` → the two new assertions fail.

- [ ] **Step 3: Implement**

`app/market.py`:

```python
DYNASTY_TYPES = (1, 2)


def _is_dynasty(league: Mapping[str, Any]) -> bool:
    return int((league.get("settings") or {}).get("type", 0)) in DYNASTY_TYPES
```

and in `fantasycalc_params`: `"isDynasty": "true" if _is_dynasty(league) else "false",`.

`app/model/values.py`: add a helper above `player_values` and use it:

```python
def market_players(snapshot: pl.DataFrame) -> pl.DataFrame:
    """Market rows for players only (draft picks are priced elsewhere), renamed for the value table."""
    return snapshot.filter(pl.col("position") != "PICK").select(
        "sleeper_id", pl.col("value").alias("market_value"), pl.col("overall_rank").alias("market_rank")
    )
```

In `player_values`, replace the `market = store.latest_snapshot("fantasycalc").select(...)` statement with `market = market_players(store.latest_snapshot("fantasycalc"))`.

- [ ] **Step 4: Run tests and commit**

Run: `uv run pytest -q` → all pass.

```bash
git add app/market.py app/model/values.py tests/test_market.py tests/test_values.py
git commit -m "Price the market in dynasty mode and keep picks out of the player table"
```

---

### Task 3: Pick ownership and pricing

**Files:**
- Create: `app/model/picks.py`
- Test: `tests/test_picks.py`

**Interfaces:**
- Consumes: `LeagueContext.rosters/season/draft_rounds/traded_picks`, `TradedPick`; a snapshot frame with `name, position, value`.
- Produces:
  - `Pick(season, round, original_roster, owner_roster, label, value)` frozen dataclass
  - `HORIZON = 3` seasons ahead
  - `owned_picks(ctx) -> list[Pick]` (labels plain, value 0.0)
  - `pick_tiers(power: dict[int, float]) -> dict[int, str]` roster → `"Early" | "Mid" | "Late"`
  - `pick_label(season, round, tier: str | None) -> str`
  - `price_picks(picks, snapshot, tiers, next_season) -> tuple[list[Pick], list[str]]` priced picks and the labels with no market row

- [ ] **Step 1: Write the failing tests**

```python
import polars as pl

from app.model.league import LeagueContext, Roster, TradedPick
from app.model.picks import HORIZON, Pick, owned_picks, pick_label, pick_tiers, price_picks


def _ctx(traded: tuple[TradedPick, ...] = (), rounds: int = 2) -> LeagueContext:
    rosters = tuple(Roster(i, f"u{i}", f"T{i}", f"T{i}", None, (), (), ()) for i in (1, 2, 3))
    return LeagueContext(league_id="1", name="t", season=2026, week=3, n_teams=3, scoring={},
                         roster_positions=["QB"], slots=["QB"], playoff_week_start=15, last_week=17,
                         trade_deadline=99, rosters=rosters, dynasty=True, draft_rounds=rounds, traded_picks=traded)


def test_owned_picks_defaults_to_own_picks_for_three_seasons() -> None:
    picks = owned_picks(_ctx())
    assert len(picks) == 3 * 2 * HORIZON
    assert {p.season for p in picks} == {2027, 2028, 2029}
    assert all(p.owner_roster == p.original_roster for p in picks)
    assert picks[0] == Pick(2027, 1, 1, 1, "2027 1st", 0.0)


def test_owned_picks_applies_trades() -> None:
    picks = owned_picks(_ctx(traded=(TradedPick(2027, 1, 1, 3),)))
    moved = [p for p in picks if p.season == 2027 and p.round == 1 and p.original_roster == 1]
    assert moved == [Pick(2027, 1, 1, 3, "2027 1st", 0.0)]
    assert sum(1 for p in picks if p.owner_roster == 3) == 2 * HORIZON + 1


def test_pick_tiers_by_thirds_of_power() -> None:
    power = {1: 2.0, 2: 1.0, 3: 0.0, 4: -1.0, 5: -2.0, 6: -3.0}
    assert pick_tiers(power) == {1: "Late", 2: "Late", 3: "Mid", 4: "Mid", 5: "Early", 6: "Early"}
    assert pick_tiers({}) == {}


def test_pick_label_ordinals_and_tiers() -> None:
    assert pick_label(2027, 1, None) == "2027 1st"
    assert pick_label(2027, 2, "Mid") == "2027 2nd (Mid)"
    assert pick_label(2028, 3, None) == "2028 3rd"
    assert pick_label(2028, 4, "Early") == "2028 4th (Early)"


def _snapshot() -> pl.DataFrame:
    return pl.DataFrame({
        "name": ["2027 1st (Early)", "2027 1st", "2027 2nd", "2028 1st", "Jahmyr Gibbs"],
        "position": ["PICK", "PICK", "PICK", "PICK", "RB"],
        "value": [4516.0, 2811.0, 1200.0, 2500.0, 11487.0],
    })


def test_price_picks_uses_tier_for_next_season_and_plain_otherwise() -> None:
    ctx = _ctx(rounds=2)
    tiers = {1: "Early", 2: "Mid", 3: "Late"}
    priced, unpriced = price_picks(owned_picks(ctx), _snapshot(), tiers, next_season=2027)
    by = {(p.owner_roster, p.season, p.round): p for p in priced}
    assert by[(1, 2027, 1)].label == "2027 1st (Early)" and by[(1, 2027, 1)].value == 4516.0
    assert by[(2, 2027, 1)].label == "2027 1st (Mid)" and by[(2, 2027, 1)].value == 2811.0   # falls back to plain
    assert by[(1, 2027, 2)].value == 1200.0 and by[(1, 2028, 1)].value == 2500.0
    assert by[(1, 2028, 2)].value == 0.0 and by[(1, 2029, 1)].value == 0.0
    assert "2028 2nd" in unpriced and "2029 1st" in unpriced and "2027 1st (Mid)" not in unpriced
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_picks.py -q` → ModuleNotFoundError.

- [ ] **Step 3: Implement**

```python
"""Draft picks as roster assets: default ownership, trades, tiers, and market pricing."""
from __future__ import annotations

from dataclasses import dataclass, replace

import polars as pl

from app.model.league import LeagueContext

HORIZON = 3
TIERS = ("Early", "Mid", "Late")
ORDINALS = {1: "1st", 2: "2nd", 3: "3rd"}


@dataclass(frozen=True)
class Pick:
    season: int
    round: int
    original_roster: int
    owner_roster: int
    label: str
    value: float


def _ordinal(n: int) -> str:
    return ORDINALS.get(n, f"{n}th")


def pick_label(season: int, round: int, tier: str | None) -> str:
    base = f"{season} {_ordinal(round)}"
    return f"{base} ({tier})" if tier else base


def owned_picks(ctx: LeagueContext) -> list[Pick]:
    """Every roster owns its own picks for the next HORIZON seasons, then trades move ownership."""
    owner = {(t.season, t.round, t.original_roster): t.owner_roster for t in ctx.traded_picks}
    picks: list[Pick] = []
    for season in range(ctx.season + 1, ctx.season + 1 + HORIZON):
        for rnd in range(1, ctx.draft_rounds + 1):
            for roster in ctx.rosters:
                rid = roster.roster_id
                picks.append(Pick(season, rnd, rid, owner.get((season, rnd, rid), rid), pick_label(season, rnd, None), 0.0))
    return picks


def pick_tiers(power: dict[int, float]) -> dict[int, str]:
    """Bottom third of the power ranking drafts Early, top third Late, the rest Mid."""
    ordered = sorted(power, key=lambda rid: power[rid])   # weakest first
    n = len(ordered)
    tiers: dict[int, str] = {}
    for i, rid in enumerate(ordered):
        tiers[rid] = "Early" if i * 3 < n else "Late" if i * 3 >= 2 * n else "Mid"
    return tiers


def price_picks(picks: list[Pick], snapshot: pl.DataFrame, tiers: dict[int, str],
                next_season: int) -> tuple[list[Pick], list[str]]:
    """Attach market values by label; next season's picks use the original roster's tier, falling back to plain."""
    rows = snapshot.filter(pl.col("position") == "PICK")
    prices = dict(zip(rows["name"].to_list(), rows["value"].to_list()))
    priced: list[Pick] = []
    unpriced: list[str] = []
    for pick in picks:
        tier = tiers.get(pick.original_roster) if pick.season == next_season else None
        label = pick_label(pick.season, pick.round, tier)
        plain = pick_label(pick.season, pick.round, None)
        value = prices.get(label, prices.get(plain))
        if value is None:
            unpriced.append(plain)
            value = 0.0
        priced.append(replace(pick, label=label, value=float(value)))
    return priced, sorted(set(unpriced))
```

Check `test_pick_tiers_by_thirds_of_power` by hand: six rosters sorted weakest first give indices 0,1 Early (0,3 < 6), 4,5 Late (12,15 ≥ 12), 2,3 Mid. Roster 6 (−3) and 5 (−2) → Early; 1 and 2 → Late. ✓

- [ ] **Step 4: Run tests and commit**

Run: `uv run pytest tests/test_picks.py -q` → all pass; `uv run pytest -q` → all pass.

```bash
git add app/model/picks.py tests/test_picks.py
git commit -m "Add draft pick ownership, tiers, and market pricing"
```

---

### Task 4: Dynasty power and window labels

**Files:**
- Create: `app/model/dynasty.py`
- Test: `tests/test_dynasty.py`

**Interfaces:**
- Consumes: `Pick`, `league_rows` output rows (`id, power, ...`), `values` frame (`roster_id, market_value`).
- Produces:
  - `FUTURE_WEIGHT = 0.5`, `WINDOW_GAP = 0.5`
  - `roster_values(values: pl.DataFrame) -> dict[int, float]` sum of `market_value` per `roster_id` (nulls as 0)
  - `picks_values(picks: list[Pick]) -> dict[int, float]` sum of pick value per owner
  - `zscores(values: dict[int, float]) -> dict[int, float]` (move `_zscores` from standings.py here and re-export it there as `_zscores = zscores` to keep the existing import graph; or import from dynasty in standings — pick the first, simpler)
  - `dynasty_power(now: dict[int, float], future: dict[int, float]) -> dict[int, float]`
  - `window_label(now_z: float, future_z: float) -> str`
  - `dynasty_rows(rows, roster_vals, pick_vals) -> list[dict[str, object]]` adds `roster_value, picks_value, window, dynasty_power`, re-sorts by dynasty power, re-ranks

- [ ] **Step 1: Write the failing tests**

```python
import math

import polars as pl

from app.model.dynasty import FUTURE_WEIGHT, dynasty_power, dynasty_rows, picks_values, roster_values, window_label
from app.model.picks import Pick


def test_roster_values_sum_market_value_per_roster() -> None:
    values = pl.DataFrame({"roster_id": [1, 1, 2, None], "market_value": [100.0, None, 50.0, 999.0]})
    assert roster_values(values) == {1: 100.0, 2: 50.0}


def test_picks_values_sum_per_owner() -> None:
    picks = [Pick(2027, 1, 1, 2, "2027 1st", 2811.0), Pick(2027, 2, 1, 1, "2027 2nd", 1200.0), Pick(2028, 1, 2, 2, "2028 1st", 0.0)]
    assert picks_values(picks) == {2: 2811.0, 1: 1200.0}


def test_dynasty_power_blends_now_and_future() -> None:
    scores = dynasty_power({1: 1.0, 2: -1.0}, {1: -1.0, 2: 1.0})
    assert math.isclose(scores[1], 0.0, abs_tol=1e-9) and math.isclose(scores[2], 0.0, abs_tol=1e-9)
    scores = dynasty_power({1: 1.0, 2: -1.0}, {1: 1.0, 2: -1.0})
    assert scores[1] > 0 > scores[2] and FUTURE_WEIGHT == 0.5


def test_window_label_thresholds() -> None:
    assert window_label(1.0, 0.0) == "contender"
    assert window_label(0.0, 1.0) == "rebuilding"
    assert window_label(0.3, 0.0) == "balanced"
    assert window_label(0.0, 0.5) == "balanced"


def test_dynasty_rows_adds_columns_and_reranks() -> None:
    rows = [{"id": 1, "rank": 1, "power": 1.0, "team": "A"}, {"id": 2, "rank": 2, "power": -1.0, "team": "B"}]
    out = dynasty_rows(rows, {1: 100.0, 2: 900.0}, {1: 0.0, 2: 300.0})
    assert [r["id"] for r in out] == [2, 1] and [r["rank"] for r in out] == [1, 2]
    assert out[0]["roster_value"] == 900.0 and out[0]["picks_value"] == 300.0
    assert out[0]["window"] == "rebuilding" and out[1]["window"] == "contender"
    assert out[0]["dynasty_power"] > out[1]["dynasty_power"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_dynasty.py -q` → ModuleNotFoundError.

- [ ] **Step 3: Implement**

`app/model/dynasty.py`:

```python
"""Dynasty ranking: long-term asset value blended with this-season strength, and each team's window."""
from __future__ import annotations

from statistics import mean, pstdev

import polars as pl

from app.model.picks import Pick

FUTURE_WEIGHT = 0.5
WINDOW_GAP = 0.5


def zscores(values: dict[int, float]) -> dict[int, float]:
    if not values:
        return {}
    mu, sd = mean(values.values()), pstdev(values.values())
    if sd == 0.0:
        return {rid: 0.0 for rid in values}
    return {rid: (v - mu) / sd for rid, v in values.items()}


def roster_values(values: pl.DataFrame) -> dict[int, float]:
    owned = values.filter(pl.col("roster_id").is_not_null()).group_by("roster_id").agg(
        pl.col("market_value").fill_null(0.0).sum().alias("total")
    )
    return {int(r): float(v) for r, v in zip(owned["roster_id"].to_list(), owned["total"].to_list())}


def picks_values(picks: list[Pick]) -> dict[int, float]:
    out: dict[int, float] = {}
    for pick in picks:
        out[pick.owner_roster] = out.get(pick.owner_roster, 0.0) + pick.value
    return out


def dynasty_power(now: dict[int, float], future: dict[int, float]) -> dict[int, float]:
    z_now, z_future = zscores(now), zscores(future)
    return {rid: (1.0 - FUTURE_WEIGHT) * z_now.get(rid, 0.0) + FUTURE_WEIGHT * z_future.get(rid, 0.0) for rid in now}


def window_label(now_z: float, future_z: float) -> str:
    if now_z - future_z > WINDOW_GAP:
        return "contender"
    if future_z - now_z > WINDOW_GAP:
        return "rebuilding"
    return "balanced"


def dynasty_rows(rows: list[dict[str, object]], roster_vals: dict[int, float],
                 pick_vals: dict[int, float]) -> list[dict[str, object]]:
    """Add dynasty columns to League rows and re-rank by dynasty power."""
    ids = [int(r["id"]) for r in rows]
    now = {rid: float(r["power"]) for rid, r in zip(ids, rows)}
    future = {rid: roster_vals.get(rid, 0.0) + pick_vals.get(rid, 0.0) for rid in ids}
    z_now, z_future = zscores(now), zscores(future)
    power = dynasty_power(now, future)
    out = [dict(r) for r in rows]
    for rid, row in zip(ids, out):
        row["roster_value"] = round(roster_vals.get(rid, 0.0), 0)
        row["picks_value"] = round(pick_vals.get(rid, 0.0), 0)
        row["window"] = window_label(z_now.get(rid, 0.0), z_future.get(rid, 0.0))
        row["dynasty_power"] = round(power.get(rid, 0.0), 2)
    out.sort(key=lambda r: (-float(r["dynasty_power"]), int(r["id"])))
    for rank, row in enumerate(out, start=1):
        row["rank"] = rank
    return out
```

In `app/model/standings.py`, replace the body of `_zscores` with `from app.model.dynasty import zscores` at the top and `_zscores = zscores` at module level, deleting the old function (no duplication). Check there is no import cycle: `dynasty.py` imports `picks.py`, which imports `league.py`; `standings.py` importing `dynasty.py` is fine because `dynasty.py` does not import `standings.py`.

- [ ] **Step 4: Run tests and commit**

Run: `uv run pytest -q` → all pass.

```bash
git add app/model/dynasty.py app/model/standings.py tests/test_dynasty.py
git commit -m "Add dynasty power scores and window labels"
```

---

### Task 5: Picks on app state and the dynasty League screen

**Files:**
- Modify: `app/ui/state.py`, `app/ui/league.py`
- Test: `tests/test_state.py`

**Interfaces:**
- `AppState.picks: list[Pick]` (empty for redraft), computed in `load()` after `team_weeks` and `values` exist, using `pick_tiers(power_scores(records(matchups, ctx.week), ros_averages(team_weeks["model"])))` and `price_picks(owned_picks(ctx), store.latest_snapshot("fantasycalc"), tiers, ctx.season + 1)`; `AppState.unpriced_picks: list[str]`.
- League page: when `ctx.dynasty`, rows come from `dynasty_rows(league_rows(...), roster_values(state.values), picks_values(state.picks))`, four extra columns are appended, and a caption lists unpriced labels when any.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_state.py`:

```python
def test_load_prices_picks_only_for_dynasty_leagues(tmp_path: Path, monkeypatch) -> None:
    from app.model.picks import Pick
    import app.ui.state as state_mod

    dynasty_ctx = replace(_ctx((Roster(1, "u1", "A", "Ann", None, (), (), ()),)), dynasty=True, draft_rounds=1)
    state = AppState(cfg=replace(_cfg("ann"), root=tmp_path))
    monkeypatch.setattr(state, "_connect", lambda: (None, None))
    monkeypatch.setattr(state_mod, "load_league_context", lambda store, league_id: dynasty_ctx)
    monkeypatch.setattr(state_mod, "project", lambda *a, **k: Projection("model", pl.DataFrame(schema={"sleeper_id": pl.Utf8, "position": pl.Utf8, "week": pl.Int64, "points": pl.Float64}), pl.DataFrame()))
    monkeypatch.setattr(state_mod, "load_matchups", lambda *a: {})
    monkeypatch.setattr(state, "_rebuild_values", lambda: None)
    monkeypatch.setattr(state, "_league_refreshed_at", lambda: None)
    snapshot = pl.DataFrame({"name": ["2027 1st"], "position": ["PICK"], "value": [2811.0]})
    monkeypatch.setattr(state, "_market_snapshot", lambda: snapshot)
    state.load()
    assert state.picks[0] == Pick(2027, 1, 1, 1, "2027 1st", 2811.0)
    assert len(state.picks) == 3 and state.unpriced_picks == ["2028 1st", "2029 1st"]

    monkeypatch.setattr(state_mod, "load_league_context", lambda store, league_id: replace(dynasty_ctx, dynasty=False))
    state.load()
    assert state.picks == [] and state.unpriced_picks == []
```

(`_market_snapshot` is a small `AppState` method added in Step 3 so the store can be stubbed; `Projection` and `pl` are already imported in the file after Task 5 of the previous plan — add them if not.)

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_state.py -q` → AttributeError on `_market_snapshot` or `picks`.

- [ ] **Step 3: Implement**

`app/ui/state.py`: imports `from app.model.picks import Pick, owned_picks, pick_tiers, price_picks` and `from app.model.standings import Matchups, load_matchups, power_scores, records, ros_averages, team_week_projections`. Fields:

```python
    picks: list[Pick] = field(default_factory=list)
    unpriced_picks: list[str] = field(default_factory=list)
```

Methods:

```python
    def _market_snapshot(self) -> pl.DataFrame:
        store, _ = self._connect()
        return store.latest_snapshot("fantasycalc")

    def _price_picks(self, ctx: LeagueContext, team_weeks: dict[str, pl.DataFrame],
                     matchups: Matchups) -> tuple[list[Pick], list[str]]:
        if not ctx.dynasty:
            return [], []
        power = power_scores(records(matchups, ctx.week), ros_averages(team_weeks["model"]))
        return price_picks(owned_picks(ctx), self._market_snapshot(), pick_tiers(power), ctx.season + 1)
```

In `load()`, after the locals for `ctx`, `projections`, `matchups`, `team_weeks` are computed and before assignment, compute `picks, unpriced = self._price_picks(ctx, team_weeks, matchups)` and assign `self.picks`, `self.unpriced_picks` together with the other fields.

Also close the residual gap the previous plan's final re-review parked: today `load()` assigns the four fields and then calls `_rebuild_values()`, so a failure inside the value rebuild leaves `values` stale against a new league. Add a helper and use it from both `load()` and `_rebuild_values()`:

```python
    def _compute_values(self, ctx: LeagueContext, projections: dict[str, Projection]) -> pl.DataFrame:
        store, con = self._connect()
        other = SOURCES[0] if self.source == SOURCES[1] else SOURCES[1]
        return player_values(ctx, con, store, projections[self.source], projections[other])

    def _rebuild_values(self) -> None:
        if self.ctx is None:
            return
        self.values = self._compute_values(self.ctx, self.projections)
```

In `load()`, compute `values = self._compute_values(ctx, projections)` into a local with the others and assign all of `ctx, projections, matchups, team_weeks, values, picks, unpriced_picks` in one block; do not call `_rebuild_values()` from `load()` any more. Fix the docstring so it claims only what holds: no field changes unless every step succeeds. Extend `test_load_leaves_state_untouched_when_projection_fails` with a second case where `app.ui.state.player_values` raises and assert `state.values` is the pre-seeded frame (identity) and `state.ctx` is unchanged.

`app/ui/league.py`:

```python
from app.model.dynasty import dynasty_rows, picks_values, roster_values

DYNASTY_COLUMNS: list[dict[str, object]] = [
    {"name": "roster_value", "label": "Roster $", "field": "roster_value", "align": "right", "sortable": True},
    {"name": "picks_value", "label": "Picks $", "field": "picks_value", "align": "right", "sortable": True},
    {"name": "window", "label": "Window", "field": "window", "align": "center", "sortable": True},
    {"name": "dynasty_power", "label": "Dynasty", "field": "dynasty_power", "align": "right", "sortable": True},
]
```

In `league_page`, inside the existing try: after `rows = league_rows(...)`, add

```python
            if state.ctx.dynasty:
                rows = dynasty_rows(rows, roster_values(state.values), picks_values(state.picks))
```

Build the table with `columns=COLUMNS + (DYNASTY_COLUMNS if state.ctx.dynasty else [])`. After the existing power caption, when `state.ctx.dynasty`:

```python
            ui.label("Dynasty = 50% z(power) + 50% z(roster market value + pick value). "
                     "Window: contender when now leads future by 0.5 z, rebuilding when future leads.").classes("ff-label")
            if state.unpriced_picks:
                ui.label("Unpriced picks: " + ", ".join(state.unpriced_picks)).classes("ff-label")
```

- [ ] **Step 4: Run tests, launch, commit**

Run: `uv run pytest -q` → all pass. Launch `FF_NATIVE=0 uv run python -m app run`, open `/league` for the configured redraft league and confirm the table is unchanged (eight columns, no dynasty caption). Kill the server.

```bash
git add app/ui/state.py app/ui/league.py tests/test_state.py
git commit -m "Rank dynasty leagues by long-term value on the league screen"
```

---

### Task 6: Drive with a dynasty league

**Files:**
- Modify: whatever the drive reveals.

- [ ] **Step 1: Get the dynasty league id**

Ask the user for their dynasty league's Sleeper id. Do not guess. If it is not available, stop this task and report BLOCKED with the rest of the plan complete.

- [ ] **Step 2: Switch and refresh**

Launch in browser mode, open `/settings`, enter the dynasty league id, Save (scope "league"; the new league is uncached, so the page shows the loading state), press the header Refresh, and wait for "Refreshed <name>". Confirm the header shows the dynasty league's name.

- [ ] **Step 3: Check the League screen**

`/league` shows twelve columns, sorted by Dynasty, with windows labelled, and a caption listing any unpriced picks (expect none for seasons the market prices, possibly the third season out). Sort by "Picks $" and confirm rosters that traded away picks show lower totals consistent with Sleeper's traded-picks page for that league. Open `/players` and confirm market values are dynasty-scale (young players and rookies rank higher than in redraft).

- [ ] **Step 4: Restore and commit fixes**

Switch back to the redraft league id in Settings, Save, and confirm `/league` is back to eight columns. Kill the server. Commit any fix separately with a plain message.

---

## Dependency graph for parallel execution

- Wave 1 (independent): Task 1, Task 2.
- Wave 2: Task 3 (needs 1), Task 4 (needs 3).
- Wave 3: Task 5 (needs 2, 4, and the final fix wave of the previous plan).
- Wave 4: Task 6.
