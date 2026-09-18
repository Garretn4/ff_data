# Dynasty rankings, pick valuation, and rookie value

Date: 2026-09-18
Status: approved design, pre-implementation
Builds on: `2026-09-16-ff-analyst-design.md`, `2026-09-18-league-matchups-settings-design.md`

## Purpose

The app values players for one redraft league. The user also plays in a
dynasty league and wants to rank its teams by long-term strength, not just
this season. This adds a dynasty mode that switches on from Sleeper's
league settings and:

- values draft picks as roster assets, owned per Sleeper's traded-picks
  record;
- computes an intrinsic dynasty value per player (multi-season projection
  discounted by the age curve) beside FantasyCalc's dynasty market value,
  keeping the app's model-versus-market comparison;
- values rookies and near-rookies from NFL draft capital fitted on the app's
  own history, refined by an optional download of college production;
- adds dynasty columns and a dynasty power score to the League screen.

Redraft leagues see no change. All constraints from the original spec still
apply: no new dependencies, scoring and slots from league settings, type
hints, small functions, no AI attribution.

## Decisions made during design

| Question | Decision | Reason |
|---|---|---|
| What "rank dynasty leagues" means | Dynasty power rankings for one dynasty league | User's pick over "just make it work" and cross-league views |
| Which league | The user switches league id on Settings; one league loaded at a time | User's pick; avoids multi-league config |
| Rookie value | Draft capital baseline, optional college download refines it | User's pick; works without registration |
| Intrinsic vs market | Both, as the app does for redraft | Keeps buy-low / sell-high working in dynasty |
| College source | collegefootballdata.com REST API via the existing httpx client, free API key | nflverse has no college box scores; no new dependency |
| Keeper leagues | Treated as dynasty for valuation | FantasyCalc offers only dynasty or redraft pricing |
| Multi-season simulation | Out of scope | Approach 3 rejected; inputs would be this design's outputs anyway |

## Data facts the design relies on

Verified on 2026-09-18:

- Sleeper league JSON `settings.type` is 0 redraft, 1 keeper, 2 dynasty
  (the configured league shows 0). `settings.draft_rounds` is present (3
  here). Refresh already fetches and caches `/league/{id}/traded_picks`;
  each row carries `season, round, roster_id (original), owner_id (current),
  previous_owner_id`.
- FantasyCalc `values/current?isDynasty=true` returns 421 rows including
  picks with `position == "PICK"`, `sleeperId` like `FP_2027_1`, and names
  `"2027 1st"`, `"2027 1st (Early|Mid|Late)"`. The app currently hardcodes
  `isDynasty=false` in `fantasycalc_params`.
- nflverse `load_draft_picks()` has `season, round, pick, position, college,
  cfb_player_id, gsis_id, pfr_player_name`; 252 of 257 rows in the 2026
  class carry a college id. `load_combine()` also exists; not needed.
- `app/model/agecurve.py` provides `fit_age_curve` and
  `age_factor(curve, position, age)` clipped to 0.7 … 1.1;
  `player_ages(hub, seasons)` gives age per season from `ff_playerids`.
- `app/model/values.py::player_values` builds the value table with
  `market_value` from the latest `fantasycalc` snapshot and `intrinsic` as
  ROS points above replacement. Every screen reads this table.
- `market_snapshots` stores `sleeper_id, name, position, team, value,
  overall_rank, position_rank, trend30`; PICK rows fit without schema change.

## Phase A: dynasty mode, picks, market, ranking

### League context

`LeagueContext` gains `dynasty: bool` (`settings.type in (1, 2)`),
`draft_rounds: int` (`settings.draft_rounds`, default 4), and
`traded_picks: tuple[TradedPick, ...]` loaded from the cached endpoint.

```python
@dataclass(frozen=True)
class TradedPick:
    season: int
    round: int
    original_roster: int
    owner_roster: int
```

### Market

`fantasycalc_params(league)` sets `isDynasty` from the league type. Snapshot
rows for picks are stored as they come (position `PICK`). `player_values`
excludes PICK rows from the player table; picks are read by the pick module.

### Picks: `app/model/picks.py`

```python
@dataclass(frozen=True)
class Pick:
    season: int
    round: int
    original_roster: int
    owner_roster: int
    label: str        # "2027 1st (Early)" or "2028 2nd"
    value: float      # market value, 0.0 when the market has no row
```

- `owned_picks(ctx, seasons) -> list[Pick]`: every roster owns its own pick
  in each round for each season in `seasons`, then traded picks reassign
  `owner_roster`. Seasons are `ctx.season + 1 … ctx.season + 3`.
- `pick_label(pick, tier)`: the next draft's picks get a tier from the
  original roster's current power rank (bottom third Early, middle Mid, top
  third Late); later seasons use the plain round label. Tier comes from
  `power_scores` in `standings.py`.
- `price_picks(picks, snapshot) -> list[Pick]`: joins labels to the
  snapshot's PICK rows by name; unmatched picks keep 0.0 and are counted in
  a diagnostics list the League screen shows as a caption.

### Team dynasty value and ranking: `app/model/dynasty.py`

- `roster_value(values, roster_id) -> float`: sum of `dynasty_value` (Phase
  B; until then `market_value`) over rostered players.
- `picks_value(picks, roster_id) -> float`.
- `dynasty_power(now: dict[int, float], future: dict[int, float]) ->
  dict[int, float]`: `0.5 × z(now) + 0.5 × z(future)` where `now` is the
  existing power score input (projected points per week) and `future` is
  roster value plus picks value.
- `window_label(now_z, future_z) -> str`: `contender` when now exceeds
  future by more than 0.5 z, `rebuilding` when future exceeds now by more
  than 0.5 z, else `balanced`.

### League screen in dynasty mode

When `ctx.dynasty`, `league_rows` adds `roster_value`, `picks_value`,
`window`, `dynasty_power`, and the table shows those columns after the
existing ones. Default sort becomes dynasty power. A caption lists any
unpriced picks. Redraft leagues render exactly as today.

### Settings

No new fields in Phase A. The user changes the league id to the dynasty
league and saves; `load()` picks up the new context and dynasty flag.

## Phase B: rookie value and optional college download

### Draft picks table

`STATIC_LOADERS` gains `draft_picks` from nflverse. Refresh ingests it like
`ff_playerids`.

### Draft-capital curve

`draft_capital_curve(draft_picks, seasonal_ppg, seasons) ->
pl.DataFrame[position, round, exp_ppg]`: for each draft class in `seasons`
(2012 … current − 3), join each pick's first three NFL seasons of scored
points per game, take the median per position and round, and smooth
monotonically down the rounds. Positions outside QB, RB, WR, TE get no
curve. Fitted once per refresh and stored in DuckDB as `draft_capital`.

### Intrinsic dynasty value

`intrinsic_dynasty(ppg, ages, curve, capital, draft_picks, horizon=3,
discount=0.85) -> pl.DataFrame[gsis_id, dynasty_value]`:

- For a player with at least `MIN_GAMES = 8` scored NFL games, season `k`
  (0 … horizon − 1) contributes `ppg × age_factor(age + k) × discount^k`.
- For a player with fewer games, the same formula uses `exp_ppg` from the
  capital curve for their position and round, shrunk toward observed `ppg`
  by games played over `MIN_GAMES`.
- Value is the sum over the horizon minus replacement, using the existing
  replacement level for the position.
- The optional college adjustment (below) multiplies the capital `exp_ppg`
  by `1 + 0.5 × (dominator − round_median_dominator)`, clipped to ±25%.

`player_values` gains a `dynasty_value` column, null in redraft leagues.
Heatmap, scatter, trade, and finder use `dynasty_value` in place of
`intrinsic` when `ctx.dynasty`.

### College download: `app/data/college.py`

- Config gains `college_stats: bool = False` and `cfbd_api_key: str = ""`,
  both in `SAVED_KEYS`; the Settings screen gets a "College stats" section
  with a toggle and a password-style key field. The key lives in the local
  config file like everything else; the file is already git-ignored.
- `fetch_team_season(http, key, season, team) -> list[dict]` calls
  `GET https://api.collegefootballdata.com/stats/player/season?year=&team=`
  with a bearer header and returns rows `(season, team, player, category,
  stat_type, stat)`.
- Refresh, when dynasty and the toggle are on, collects `(season, college)`
  pairs from draft picks of the last three classes, fetches only pairs not
  already in the DuckDB table `college_stats`, and stops with a summary
  error after the first 401 or 429 without failing the rest of refresh.
- `college_features(stats, draft_picks) -> pl.DataFrame[gsis_id,
  dominator]`: for each drafted player, match by normalised name and
  college to the college rows for the season before their draft, and
  compute `(receiving yards + rushing yards) / team total` for that team
  season. Unmatched players get null and no adjustment.

## Error handling

- A dynasty league with no traded-picks cache behaves as if nothing was
  traded.
- A pick with no market price stays at 0.0 and is listed in the caption.
- A missing or invalid CFBD key is surfaced once per refresh in the summary
  errors and the Settings screen; values fall back to draft capital.
- Everything in Phase B degrades to Phase A values when the draft-picks
  table is absent (first refresh after upgrade).

## Testing

Unit tests, TDD:

- `owned_picks` default ownership and traded reassignment; `pick_label`
  tiers; `price_picks` matching and the unpriced list.
- `dynasty_power` blend and `window_label` boundaries.
- `draft_capital_curve` on a small fixture with a known median and the
  monotone smoothing.
- `intrinsic_dynasty` for a veteran (age factors and discount applied), a
  rookie (capital curve), and a second-year player (shrinkage).
- `college_features` matching and dominator math on fixture rows;
  `fetch_team_season` against a mocked transport including 401 and 429.
- `league_rows` dynasty columns present only when `ctx.dynasty`.
- `fantasycalc_params` sets `isDynasty` from the league type.

Screens verified by entering the dynasty league id in Settings and driving
League, Heatmap, Scatter, and Trade.

## Decomposition

Two implementation plans, each shippable alone:

1. Phase A (dynasty mode, picks, market, ranking).
2. Phase B (draft capital, intrinsic dynasty value, college download).

## Out of scope

Multi-league views, taxi squads, rookie draft simulation, contract or salary
leagues, and historical dynasty value tracking.
