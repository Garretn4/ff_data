# League, Matchups, and Settings screens

Date: 2026-09-18
Status: approved design, pre-implementation
Builds on: `2026-09-16-ff-analyst-design.md` and plan B (`2026-09-17-plan-b-engine-and-ui.md`)

## Purpose

The app grades trades well but shows little about the league as a whole.
This adds three screens so the user can see, at a glance, where every team
stands, what this week's games look like, and tune the app without editing
files:

- **League**: standings, power rankings, and rest-of-season team projections
  in one table, with model and consensus side by side.
- **Matchups**: every head-to-head for a chosen week with projected totals,
  the user's own matchup expanded to a per-slot lineup.
- **Settings**: edit league id, username, default projection source, history
  range, and model blend weights, then save and apply without a restart.

All constraints from the original spec still apply: no new dependencies,
scoring and slots read from league settings, type hints, small functions,
no AI attribution.

## Decisions made during design

| Question | Decision | Reason |
|---|---|---|
| What does "league ranker" mean | Team power rankings | User picked it over playoff odds and a player board |
| Matchup scope | All matchups, mine on top, optimal lineups | User picked it over own-matchup-only and actual-starters |
| Which settings are editable | League id, username, default source, history range, blend weights | User picked all four groups |
| Model vs consensus | Shown side by side on League and Matchups, not only via the toggle | User said they like the comparison view |
| Screen layout | Two new screens plus Settings, not one dashboard | Each screen keeps one job; a dashboard needs tabs to fit |
| Playoff odds | Out of scope | Not asked for; power rankings answer the question for now |

## Data facts the design relies on

Verified against the code and cache on 2026-09-18:

- `app/model/lineup.py` exposes `weekly_strength(slots, weekly, player_ids)`
  returning one row per week with the optimal-lineup points, and
  `optimal_lineup(slots, players)` returning the total and a slot-to-player
  assignment. Team projections reuse both.
- `Projection.weekly` has columns `sleeper_id, gsis_id, position, week, points`
  for every remaining week, for both sources.
- Refresh caches Sleeper matchups only for weeks 1 through the current week.
  Each cached row carries `roster_id, matchup_id, points, starters,
  players_points`.
- `Config` is a frozen dataclass read from `config.local.toml` by
  `load_config`. There is no write path. The TOML file today holds
  `league_id` and `username`.
- `project(con, store, ctx, source, params=BlendParams())` accepts blend
  weights `k, a, b` with defaults `4.0, 0.6, 0.5`.
- `AppState.my_roster_id()` resolves the user's roster from the configured
  username.

## Refresh change

`_pull_league` in `app/refresh.py` fetches matchups for
`range(1, playoff_week_start)` instead of `range(1, week + 1)`, so future
regular-season pairings are cached. Transactions keep the current range.

Assumption to confirm on the first real fetch: Sleeper returns `matchup_id`
for future regular-season weeks. If a future week comes back with null
matchup ids, the Matchups screen shows "Schedule not published for week N"
for that week and the power score falls back to projection only. No other
behaviour depends on it.

## Data layer: `app/model/standings.py`

Pure functions. No UI imports. Every function takes the league context and
plain frames or dicts; the store is touched only by `load_matchups`.

```python
@dataclass(frozen=True)
class Record:
    roster_id: int
    wins: int
    losses: int
    ties: int
    points_for: float
    points_against: float

@dataclass(frozen=True)
class Pairing:
    week: int
    matchup_id: int
    home: int   # roster_id
    away: int | None   # roster_id, or None when the schedule has a bye
```

- `load_matchups(store, league_id, weeks) -> dict[int, list[dict]]`
  reads cached matchup JSON for each week, skipping weeks with no cache.
- `records(matchups, through_week) -> list[Record]`
  pairs rows by `matchup_id` within each played week (week < current week),
  compares `points`, and tallies wins, losses, ties, points for and against.
  A roster with no opponent that week gets no result.
- `schedule(matchups, week) -> list[Pairing]`
  pairs rows by `matchup_id` for one week. Odd rows become a Pairing with
  `away=None`.
- `team_week_projections(ctx, proj) -> pl.DataFrame`
  columns `roster_id, week, points`. For each roster, calls `weekly_strength`
  over the roster's `players` (not just starters) with `ctx.slots`.
- `ros_averages(team_weeks) -> dict[int, float]`
  mean projected points per remaining week per roster.
- `power_scores(records, ros_avg) -> dict[int, float]`
  z-score blend: 0.6 × z(ros_avg) + 0.4 × z(points_for per game). When no
  roster has played a game, weight is 1.0 on projection. A z-score over a
  constant column is 0.
- `lineup_for(ctx, proj, roster, week) -> list[tuple[str, str, float]]`
  returns `(slot, sleeper_id, points)` in slot order for one roster and week
  using `optimal_lineup`. An unfilled slot returns `(slot, "", 0.0)`.
- `win_probability(points_a, points_b) -> float`
  Φ((a − b) / (SIGMA · √2)) with `SIGMA = 20.0` points per team-week. A
  fixed spread is honest at this stage; a fitted one is a later change.

`AppState` gains a cached `matchups: dict[int, list[dict]]` loaded in
`load()` for weeks 1 through `playoff_week_start − 1`.

## League screen: `/league`

One table, one row per roster, sorted by power score descending by default.
Columns: rank, team, record (W-L or W-L-T), PF, PA, model ROS avg,
consensus ROS avg, power. The user's row is highlighted with the accent
colour. Column headers are clickable to sort. Below the table, one line of
caption explains the power score weights.

Both projection columns are always shown regardless of the source toggle,
so the comparison the user likes is visible without switching.

## Matchups screen: `/matchups`

Top bar: a week select covering `1 … playoff_week_start − 1`, defaulting to
`ctx.week`. Changing it re-renders the page body.

Body, in order:

1. **My matchup card.** Two columns, one per side, each headed by the team
   name and the projected total for the selected source, with the other
   source's total in muted text beneath. Rows are the optimal lineup by slot:
   slot label, player name, projected points. The footer shows the win
   probability for my side. For played weeks, actual points replace the
   projection and the winner is marked.
2. **Other matchups.** Compact rows: home team, projected home points,
   "vs", projected away points, away team, and a chip naming the favoured
   side with its win probability. Both sources shown as
   `model / consensus`. Played weeks show actual scores and the winner.
3. A bye row when `away is None`.

If the selected week has no cached pairings, the body shows the "Schedule
not published" message with a Refresh button.

## Settings screen: `/settings`

### Config additions

`Config` gains:

```python
source_default: str = "model"     # one of SOURCES
blend_k: float = 4.0
blend_a: float = 0.6
blend_b: float = 0.5
```

`load_config` reads them from `config.local.toml` with those defaults.
`Config.blend` property returns a `BlendParams`. `AppState.load()` passes
`cfg.blend` to `project` for the model source and sets `source` to
`cfg.source_default` on first load.

### Writing the file

`save_config(root, cfg) -> Path` in `app/config.py` writes
`config.local.toml` with exactly these flat keys:
`league_id, username, source_default, stats_from, detail_from, blend_k,
blend_a, blend_b`. Strings are quoted with `json.dumps`, which is valid TOML
for the ASCII values involved. Floats and ints are written bare. The file is
written to a temp path in the same directory and renamed over the original
so a crash mid-write cannot truncate it. Any other keys in the old file are
dropped; a comment at the top says the file is managed by the Settings
screen.

`FF_LEAGUE_ID` in the environment still wins over the file on load, as
today. The Settings screen shows a notice when that override is active.

### Applying a change

`AppState.apply_settings(new: Config) -> str` replaces `cfg`, saves the
file, then acts by scope and returns which one ran:

| Changed fields | Scope | Action |
|---|---|---|
| `blend_*` only | `"model"` | Recompute the model projection with new weights, rebuild values |
| `source_default` only | `"source"` | Set `state.source` |
| `league_id` or `username` | `"league"` | Call `load()`; new league may have no cache, in which case the loading screen and background refresh path from Task 14 take over |
| `stats_from` or `detail_from` | `"history"` | Save only; return the scope so the screen can offer a refresh |

Scopes combine in that order: a save that changes both blend and league runs
`load()` once, which already recomputes projections.

### Form

Fields with labels and inline validation:

- League id: text, required, digits only.
- Username: text, optional.
- Default source: select from `SOURCES`.
- Stats from / Detail from: integer inputs, `2000 ≤ stats_from ≤ detail_from
  ≤ current_season`.
- Blend k: float, `0 < k ≤ 50`. Blend a and b: float, `0 ≤ x ≤ 1`.

Save is disabled while any field is invalid. On success a notification says
what was applied. A history change shows a second notification with a
"Refresh now" action that calls the existing background refresh. Errors from
saving or reloading are caught at the UI boundary, stored in `state.error`,
and shown, following the pattern already used on every screen.

## Navigation

Add `("League", "/league")`, `("Matchups", "/matchups")` after Heatmap and
`("Settings", "/settings")` last in `NAV` in `app/ui/layout.py`. The Settings
screen does not call `ensure_loaded`, so it works before a league is loaded
and is the way out of a bad league id.

## Error handling

- A week with no cached matchups is a normal state, not an error.
- A roster whose players are missing from `Projection.weekly` (new signing
  with no projection) contributes zero for those players; the lineup shows
  the slot as empty.
- Config validation errors never reach `save_config`; the form blocks them.
- `save_config` and `apply_settings` failures surface through `state.error`
  and a negative notification, never silently.

## Testing

Unit tests, TDD, in `tests/test_standings.py` and additions to
`tests/test_config.py` and `tests/test_state.py`:

- `records`: two teams, three weeks, one tie; verify W-L-T, PF, PA; a week
  after `through_week` is ignored.
- `schedule`: pairs by matchup id; odd roster yields a bye Pairing.
- `team_week_projections`: two rosters, two weeks, hand-computed optimal
  totals.
- `power_scores`: projection-only before week 1; blended after; constant
  column gives zero z.
- `lineup_for`: slot order and empty-slot handling.
- `win_probability`: symmetric around 0.5, monotone in the spread.
- `save_config` round-trips through `load_config`; env override still wins;
  temp-and-rename leaves no stray files.
- `apply_settings` returns the right scope for each field group and calls
  the right state method (use a small fake state or monkeypatch).
- Refresh: matchups fetched through `playoff_week_start − 1`.

Screens are verified by launching the app and driving each page, as in
Task 14: League sorts and highlights; Matchups switches weeks and shows the
lineup; Settings rejects bad input, saves, and applies each scope.

## Out of scope

Playoff odds simulation, fitted score variance, editing scoring rules or
roster slots, editing lineups on Sleeper, and any redesign of existing
screens.
