# ff-analyst: local fantasy football trade and league analysis tool

Date: 2026-09-16
Status: approved design, pre-implementation

## Purpose

A local desktop tool for one Sleeper league (10 teams, PPR, redraft) that
values players from usage data, compares those values to the public market,
grades trades by their effect on each side's optimal starting lineup, finds
mutually beneficial trades, and profiles other managers from their
transaction history. Runs entirely on the user's Mac. No cloud, no auth, no
deployment, no scheduler.

The user also wants a practice copy of the data for learning Python and R,
so the data layer exports itself in formats both languages open natively.

## Constraints

- Sleeper API is read-only and unauthenticated. Stay under 1000 calls per
  minute. Cache the players list at most once per day.
- Scoring rules and roster slots are read from the league settings at
  runtime. Nothing about scoring or slots is hardcoded.
- Market values come from FantasyCalc's public values endpoint, matched to
  Sleeper player IDs. Parameters (team count, PPR, redraft, QB count) are
  derived from league settings.
- Phase 2 is a gate. If the backtest shows no edge over the market
  baseline, work stops and the numbers are reported.
- Type hints everywhere, small functions, no dead code.
- No AI attribution anywhere: no generated-with comments, no co-author
  trailers, no signatures, no watermarks.
- Dependencies are limited to the approved list below. Any addition is
  asked for first.

## Decisions made during design

| Question | Decision | Reason |
|---|---|---|
| Historical stats DB | None exists; build from nflverse | User has no DB |
| nflverse library | nflreadpy, not nfl_data_py | nfl_data_py last released 2024-09; nflreadpy is maintained and polars-native |
| Native window | pywebview via nicegui[native] | Required for native mode |
| polars to DuckDB bridge | pyarrow | Verified: DuckDB cannot register polars frames without it |
| Backtest market baseline | Weekly FantasyPros redraft consensus from the nflverse rankings archive (2021-2025) plus FantasyFootballCalculator preseason ADP | FantasyCalc has no historical endpoint (three URL forms return 404). The archive holds weekly snapshots from 2020-10-16 onward |
| Value model | Sample-size shrinkage blend | Transparent, no new deps; regression is the upgrade path if the gate fails |
| Trade grading | Per-week exact optimal lineup summed over remaining weeks | Handles 2-for-1 and byes correctly |
| Practice export | DuckDB file plus parquet folder plus CSVs | DuckDB opens in Python and R; parquet and CSV cover arrow, pandas, readr |
| Project location | ~/code/ff-analyst, local git, remote added later | User preference |
| Toolchain | uv-managed Python 3.12 virtualenv | Isolates from the Anaconda 3.10 system install |

## Approved dependencies

nicegui[native], duckdb, polars, pyarrow, httpx, nflreadpy, pytest.

## Verified data availability

Probed 2026-09-16 with nflreadpy 0.1.5.

- Weekly player stats: targets, target_share, air_yards_share, carries,
  receptions, yards, touchdowns, fumbles, fantasy_points_ppr, per season
  and week, regular and post season.
- Snap counts per game keyed by pfr_player_id, with offense_pct.
- ffopportunity: expected fantasy points per player per week from usage
  and field position (total_fantasy_points_exp and components). This
  stands in for a separate red-zone feature.
- ff_playerids: 12,494 players; 6,394 with sleeper_id; also gsis_id,
  fantasypros_id, pfr_id, birthdate, age, draft info.
- ff_rankings type "all": 1.83 million rows, scrape_date from 2019-12-27
  to 2026-09-11, roughly weekly. page_type includes redraft-overall,
  redraft-qb/rb/wr/te, with ecr, sd, best, worst, and fantasypros id.
- Schedules with week, home and away teams, gameday.
- Rosters with birth_date, sleeper_id, status, years_exp.
- FantasyCalc current values include sleeperId per player.
- FantasyFootballCalculator ADP endpoint returns per-season PPR ADP with
  player name, position, team. The 2023 response reported 12 teams even
  when 10 was requested; the import records the team count the API
  actually returned.

## Architecture

Three stores, one direction of flow. nflverse data lands in a DuckDB
file. Sleeper responses, FantasyCalc snapshots, and app settings land in
SQLite. The model layer reads both and writes projections to DuckDB. The
UI reads only from the model layer and never touches the network.

```
ff-analyst/
  pyproject.toml
  app/
    __main__.py      CLI: refresh | backtest | export | run
    main.py          NiceGUI entry: native window, dark theme, top bar with Refresh
    config.py        league id, paths, tunables from env or config.local.toml
    sleeper.py       httpx client, token-bucket limiter, per-endpoint TTL cache in SQLite
    market.py        FantasyCalc current values, FFC preseason ADP, nflverse ECR archive
    data/
      nflverse.py    nflreadpy loaders -> DuckDB tables
      scoring.py     Sleeper scoring_settings -> weekly fantasy points from nflverse columns
      store.py       SQLite schema and access for app state and caches
      export.py      practice copy to ~/ff-analyst-data
    model/
      replacement.py roster_positions x team count -> replacement PPG per position
      projections.py shrinkage blend -> per-player per-week projected points
      lineup.py      exact optimal lineup for one week given slot list
      trade.py       lineup-point delta for both sides across remaining weeks
      finder.py      enumerate deals per opponent, keep mutual gains
      managers.py    transaction history -> manager profile
      backtest.py    walk-forward harness and report writer
    ui/
      theme.py       dark palette, one accent, ECharts base options
      layout.py      shell, navigation, refresh button
      heatmap.py     teams x positions surplus/need
      cards.py       player card component
      scatter.py     market vs intrinsic scatter
      trade.py       trade screen with drop zones and center gauge
      finder.py      trade finder screen
      managers.py    manager profiles screen
  tests/
  cache/             DuckDB and SQLite files, gitignored
  docs/backtest/     generated report
  docs/superpowers/  specs and plans
```

Every screen's data comes from a function in app/model that returns plain
polars frames or dataclasses, so the CLI and the tests exercise the same
code as the UI.

## Data layer

### Scoring

Sleeper scoring_settings is a dict of stat key to points. A mapping table
in scoring.py pairs each Sleeper key with the nflverse weekly column that
carries the same stat (for example pass_yd to passing_yards, rec to
receptions, rush_td to rushing_tds, fum_lost to the sum of the
fumbles_lost columns, bonus keys to the threshold columns nflverse
provides). Points for any player-week are the dot product of the
league's weights with those columns. Keys with no nflverse counterpart
are logged once at refresh and ignored. Because points are recomputed
from raw stats, the backtest measures this league's scoring on past
seasons, not generic PPR.

### Identity

The nflverse ID table is the join hub. gsis_id links weekly stats and
rosters, sleeper_id links Sleeper rosters and FantasyCalc, fantasypros_id
links the rankings archive, pfr_id links snap counts. FFC ADP has no
shared ID and is matched on normalized name plus position, with team as a
tiebreaker. Every unmatched row from any source is written to a
diagnostics table with its source and reason, and the refresh summary
shows the counts. Nothing is dropped silently.

### Sleeper client

Endpoints: /league/{id}, /league/{id}/users, /league/{id}/rosters,
/league/{id}/matchups/{week}, /league/{id}/transactions/{week},
/league/{id}/traded_picks, /players/nfl, /state/nfl.

A token bucket allows 600 calls per minute. Every response is stored raw
in SQLite (endpoint, params, fetched_at, body) with a TTL per endpoint:
players 24 hours, state 1 hour, everything else refreshed on demand by
the Refresh button and on launch. The app works offline after one
refresh. The league's previous_league_id chain is followed to collect
prior seasons for manager history.

### nflverse ingest

Loaders pull player stats, snap counts, ffopportunity, ID table, rosters,
schedules, injuries, and the rankings archive for seasons 2019 through
the current season into DuckDB tables. Refresh reloads only the current
season; older seasons are loaded once and kept. Frames move between
polars and DuckDB in memory through pyarrow.

### Practice export

`python -m app export` copies the DuckDB file to ~/ff-analyst-data/
ff_practice.duckdb, writes every table to ~/ff-analyst-data/parquet/
and ~/ff-analyst-data/csv/, and writes a README with the open commands
for Python (duckdb, polars, pandas) and R (duckdb, arrow, readr).

## Value model

### Projected points per game

For player p at week w with position pos:

```
prior      = age_curve(pos, age) * (b * ecr_ppg + (1 - b) * last_season_ppg)
usage_ppg  = mean expected points per game from ffopportunity, weeks 1..w-1
actual_ppg = mean actual points per game under league scoring, weeks 1..w-1
n          = games played this season through week w-1
ppg        = (k * prior + n * (a * usage_ppg + (1 - a) * actual_ppg)) / (k + n)
```

- ecr_ppg is the PPG implied by the player's preseason consensus rank,
  read off the previous season's rank-to-PPG curve for the position.
- last_season_ppg is the player's own PPG last season, or the positional
  replacement PPG for rookies and players with under four games.
- age_curve is a per-position multiplier estimated from year-over-year PPG
  ratios by age bin across 2012-2025 seasonal data, smoothed and clamped
  to 0.7 to 1.1.
- k (prior strength in games), a (usage weight), and b (consensus weight)
  are tuned by grid search on 2021 to 2023 and frozen before evaluation on
  2024 and 2025.

### Per-week projection

Projected points for week w' >= w is ppg times an opponent factor. The
factor is the opponent defense's trailing points allowed to the position
relative to league average, shrunk toward 1.0 by games played, clamped to
0.9 to 1.1. Bye weeks project zero. Sleeper injury status IR projects
zero for all remaining weeks; Out projects zero for the next week only;
other statuses are unadjusted.

### Floor, ceiling, consistency

Floor and ceiling are the 20th and 80th percentiles of the player's
weekly points over the trailing 12 games including last season.
Consistency is the coefficient of variation over the same window. They
appear on player cards and do not enter the value.

### Intrinsic value

Rest-of-season projected points minus rest-of-season replacement points.
Replacement PPG per position is the projected PPG of the best player at
that position not on any roster in this league. In the backtest, where
no rosters exist, replacement is the player at rank equal to league-wide
starters at the position plus bench depth, where bench depth is the
league's bench slots times team count allocated across positions in
proportion to starter counts.

Starters per position are computed from roster_positions and team count:
dedicated slots count fully; FLEX and SUPERFLEX slots are allocated to
positions by simulating league-wide optimal lineups from the projections.

## Backtest

Walk forward through seasons 2021 to 2025, weeks 2 to 14. At each
season-week, the model sees only weeks before w plus the preseason
prior. Baselines are the nearest FantasyPros redraft-overall consensus
snapshot dated before that week's first game, and at week 1 the FFC
preseason ADP. Target is actual rest-of-season points under league
scoring.

Metrics, per position and pooled, per week bucket (2-5, 6-9, 10-14):

- Spearman rank correlation between prediction and actual rest-of-season
  points.
- Mean absolute error in points. Consensus and ADP ranks are mapped to
  points through the previous season's rank-to-rest-of-season-points curve
  so the error is comparable.
- Precision at the starter cutoff: of the top N predicted at each position
  (N = league-wide starters), the share that finish top N.

Tuning uses 2021 to 2023 only. The report shows 2024 and 2025.

Gate: the model matches or beats consensus on both Spearman and MAE on
the pooled 2024 and 2025 in-season weeks. If it does not, work stops
after the report and the user decides.

Output: docs/backtest/REPORT.md with metric tables, plus a parquet of the
per-player-week predictions for inspection.

## Trade engine

### Lineup solver

Input: a list of slot types from roster_positions (QB, RB, WR, TE, FLEX,
SUPERFLEX, K, DEF, and whatever else the league uses; BN and IR are
excluded) and a list of (player, position, projected points) for one
week. Output: the maximum total points over valid assignments. Solved
exactly by dynamic programming over slots with a bitmask of used players,
which is small because rosters hold about fifteen players. Eligibility
per slot type is a table in lineup.py.

### Team strength and trade delta

Team strength is the sum of the weekly optimal lineup over remaining
weeks using per-week projections. A trade is a set of players moving
each way. Delta for each side is strength with the swapped roster minus
strength before. A 2-for-1 grades correctly because the extra player
counts only in weeks where he displaces a starter.

### Trade finder

For each opponent, enumerate 1-for-1, 2-for-1, 1-for-2, and 2-for-2
deals drawn from each team's top twelve players by intrinsic value.
Keep deals where both deltas are positive. Rank by the smaller delta so
lopsided deals sink. Show the top ten per opponent with both deltas.

## Manager profiles

From every trade, waiver add, drop, and FAAB bid across the league chain:
trades per season, partner frequency, positions acquired versus given,
overpay per trade measured as consensus rank-value given minus received
at the week of the trade using the rankings archive, adds by position,
FAAB spent. Presented as one card per manager.

## UI

NiceGUI in native pywebview window. Dark theme, one accent color, team
colors only as thin card borders or small badges. Screens: Heatmap,
Players, Scatter, Trade, Finder, Managers. Refresh in the top bar and on
launch; no scheduler. All charts are ECharts through ui.echart.

- Heatmap: teams by position, cell color is the team's surplus or need at
  that position relative to league median starter value.
- Players: cards with Sleeper CDN headshot, position badge, market versus
  intrinsic bars, weekly points sparkline.
- Scatter: market value on X, intrinsic value on Y, diagonal reference
  line, quadrants labeled buy-low and sell-high.
- Trade: two drop zones, live center gauge with the lineup-point delta
  for each side.
- Finder: opponent selector, list of mutual-gain deals with both deltas.
- Managers: one card per manager.

## Error handling

Network failures during refresh leave the previous cache in place and
show the failure in the refresh summary. Missing market values for a
player show as absent rather than zero. Unmatched identities go to the
diagnostics table. The backtest refuses to run if any season in range is
missing stats or consensus snapshots and names the gap.

## Testing

pytest throughout, run scoped during a build step and in full at each
phase gate.

- Sleeper client: httpx MockTransport with recorded fixtures; TTL and
  rate-limit behavior.
- Scoring: hand-computed player-weeks under two different scoring dicts.
- Identity: known matched and unmatched rows land where expected.
- Lineup solver: FLEX and SUPERFLEX cases with known optima; a 2-for-1
  where the third player must not add points.
- Blend: n = 0 returns the prior; large n converges on the data.
- Backtest metrics: synthetic predictions with known Spearman and MAE.
- Export: round-trip a table through DuckDB, parquet, and CSV.

## Phases

1. Sleeper import, data model, cache, nflverse ingest, scoring, identity,
   practice export.
2. Value model and backtest report. Gate.
3. League heatmap.
4. Player cards.
5. Buy-low/sell-high scatter.
6. Trade screen.
7. Trade finder.
8. Manager profiles.

Each phase ends with a demonstration and a stop for go-ahead.

## Open items

- Sleeper league ID: to be supplied before Phase 1 runs against the live
  API. Stored in config.local.toml, which is gitignored.
- GitHub remote: added by the user later; nothing depends on it.
