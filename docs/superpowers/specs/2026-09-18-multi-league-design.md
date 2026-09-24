# Multiple leagues: discovery, list, and switching

Date: 2026-09-18
Status: approved design, pre-implementation
Builds on: `2026-09-18-league-matchups-settings-design.md`
Precedes: `2026-09-18-dynasty-rankings-design.md` (Phase A targets a league chosen in the switcher)

## Purpose

The app holds one league. The user also follows a friend's seven leagues,
four of them dynasty, and shares one league with them. This adds:

- discovery of every league for each configured Sleeper username;
- a league switcher in the header;
- per-league state kept in memory so switching back is instant;
- per-league market snapshots and freshness, so one league's prices and
  refresh time never leak into another.

Every screen keeps reading the same state properties it reads today.

## Decisions made during design

| Question | Decision | Reason |
|---|---|---|
| Where the list comes from | Discovered from Sleeper per configured username, union across usernames | User's leagues are under two accounts; Sleeper lists them for free |
| Hand-added league ids | Not in this change | Not asked for; discovery covers every league the users are in |
| State model | Per-league `LeagueState` behind forwarding properties on `AppState` | Screens unchanged; switching swaps a pointer |
| Loading strategy | Lazy on first switch, kept in memory afterwards | Seven projection builds at boot would be wasteful |
| "My roster" in a league | First configured username with a roster there | Shared league resolves to the user, not the friend |
| Username change | Rediscovers immediately on save, not at the next refresh | User asked that plugging in a username changes the list right away |
| Unknown Sleeper league types | Treated as redraft, labelled with the raw type | Sleeper reports a type 3 the API docs don't define |
| Pre-draft leagues | Listed and loadable; empty rosters render the screens' empty states | No special handling needed |

## Data facts the design relies on

Verified on 2026-09-18 against Sleeper's public API:

- `GET /v1/user/{username}` returns `user_id` and `display_name`
  (`garretn4` → `851577072894713856`, `mosrocks` → `856664637989584896`).
- `GET /v1/user/{user_id}/leagues/nfl/{season}` returns league objects
  with `league_id, name, season, status ("pre_draft" | "in_season" | …),
  total_rosters, settings.type, settings.draft_rounds, scoring_settings`.
  `garretn4` has one 2026 league; `mosrocks` has seven, including the same
  one, four with `type == 2`, one with `type == 3`, one `pre_draft`.
- `SleeperClient._get(path, ttl)` caches under `sleeper:{path}` in the
  SQLite `http_cache` table with a `fetched_at` epoch; `refresh=True`
  bypasses the cache for `ttl=None` paths.
- `Config` (`app/config.py`) has `username: str` and `league_id: str` among
  `SAVED_KEYS`; `save_config` writes exactly those keys.
- `AppState` (`app/ui/state.py`) holds `ctx, projections, values, matchups,
  team_weeks, source, last_refresh, error, refreshing` and methods `load,
  refresh, _rebuild_values, _recompute_model, apply_settings, my_roster_id,
  primary, secondary, is_stale, has_cache`. `load()` computes into locals
  and assigns atomically.
- Market snapshots are stored under `source = "fantasycalc"` and read with
  `Store.latest_snapshot("fantasycalc")` regardless of league.
- `league_meta` in DuckDB holds a single row (last refreshed league) used
  only for `last_refresh`, `is_stale`, `has_cache`.
- The header (`app/ui/layout.py`) renders league name, week, refresh
  status, the source toggle, and the Refresh button.

## Config

`username: str` becomes `usernames: tuple[str, ...]` (saved as a TOML
array; `load_config` also accepts the old scalar `username` key and lifts
it into the tuple for one release). `league_id` keeps its meaning as the
active league and is rewritten on every switch. `SAVED_KEYS` swaps
`username` for `usernames`. The Settings screen shows one text field,
comma-separated, and the validator requires at least one non-empty name.

## Discovery: `app/model/leagues.py`

```python
@dataclass(frozen=True)
class LeagueSummary:
    league_id: str
    name: str
    season: int
    status: str
    n_teams: int
    league_type: int          # raw Sleeper settings.type
    draft_rounds: int
    members: tuple[str, ...]  # configured usernames with a roster here, config order

    @property
    def dynasty(self) -> bool: ...   # league_type in (1, 2)
    @property
    def label(self) -> str: ...      # "Name · dynasty 12" / "Name · redraft 10" / "Name · type 3 18" / "Name · pre-draft"
```

- `SleeperClient` gains `user(username) -> dict` (`/user/{username}`, ttl
  one day) and `user_leagues(user_id, season) -> list[dict]`
  (`/user/{user_id}/leagues/nfl/{season}`, ttl `None`, so refresh refetches).
- `discover(client, usernames, season) -> list[LeagueSummary]`: resolves
  each username, lists its leagues, unions by `league_id` preserving first
  appearance, and records members in config order. An unknown username
  (Sleeper returns null) is skipped and reported in the refresh summary
  errors as `user {name}: not found`.
- `load_summaries(store, usernames, season) -> list[LeagueSummary]` reads
  the same cached endpoints without the network, for startup.

## Per-league state: `app/ui/state.py`

```python
@dataclass
class LeagueState:
    ctx: LeagueContext
    projections: dict[str, Projection]
    values: pl.DataFrame
    matchups: Matchups
    team_weeks: dict[str, pl.DataFrame]
    last_refresh: datetime | None
```

`AppState` fields become `leagues: dict[str, LeagueState]`, `active: str`
(league id, initialised from `cfg.league_id`), `summaries:
list[LeagueSummary]`, plus the existing `cfg, store, con, source, error,
refreshing`. Read-only forwarding properties keep every screen unchanged:
`ctx`, `projections`, `values`, `matchups`, `team_weeks`, `last_refresh`
return the active `LeagueState`'s fields, or `None` / empty when the active
league isn't loaded.

Methods:

- `load()` builds a `LeagueState` for `active` from the caches (same steps as
  today, into locals, assigned as one object) and loads `summaries`.
- `switch(league_id)`: sets `active`, persists it to config, and loads the
  league if it isn't in memory. Raises the existing "not cached" error when
  the league has never been fetched; the layout handler then runs a refresh
  for it in the background and reloads the page.
- `refresh()` pulls the active league, shared nflverse data, and the market
  snapshot for the active league, then rebuilds that league's state.
- `_rebuild_values()` and `_recompute_model()` act on the active league;
  the source toggle and blend weights apply per league on first use.
- `my_roster_id()` walks `cfg.usernames` in order and returns the first
  roster whose display name matches; else the first roster.
- `apply_settings` scope `"league"` now means the usernames changed:
  rediscover summaries immediately (network, through the client) and, if the
  active league is no longer in the list, switch to the first one.
- `is_stale()` and `has_cache()` use the HTTP cache's `fetched_at` for
  `sleeper:/league/{active}` (new `Store.fetched_at(key) -> float | None`),
  replacing the `league_meta` reads; `league_meta` keeps being written for
  the export.

## Market snapshots per league

`snapshot_fantasycalc` stores under `source = f"fantasycalc:{league_id}"`
and `player_values` reads `latest_snapshot(f"fantasycalc:{ctx.league_id}")`.
The refresh for a league always writes its own snapshot, so switching
never shows another league's prices. Old `"fantasycalc"` rows are left in
place and simply unread.

## Header switcher

In `app/ui/layout.py`, a `ui.select` before the source toggle lists
`summaries` by `label`, value the league id, current value `state.active`.
On change: set `state.error = None`, `await run.io_bound(state.switch, id)`;
on the "not cached" error, run the existing background refresh and reload;
on other errors, store and notify as today; on success reload the page. The
header's league name and week come from the active league as today. When
`summaries` is empty (no usernames resolved yet), the select is hidden and
the header shows "add your Sleeper username in Settings".

## Settings screen

The usernames field sits first in the form. Below the Save button, a
"Leagues" section lists `state.summaries` as rows: label, member usernames,
and a "Switch" button that runs the same switch handler as the header
select; the active league's row is marked. After a save that changes
usernames, the section re-renders with the rediscovered list, so plugging
in a username shows its leagues without leaving the page.

## Startup

`main._on_startup` keeps its plan: load the active league from cache if
present, refresh in the background if stale or missing. Summaries load
from cache alongside; a first run with no cache refreshes, which now also
discovers leagues.

## Error handling

- Unknown username: skipped with a summary error; the rest proceed.
- Active league missing from the cache: loading placeholder, background
  refresh, reload (existing path).
- Sleeper down during a switch to an unfetched league: the refresh error
  banner with Retry, as today; the active id stays on the requested league
  so Retry targets it.
- A league that fails to build (bad scoring keys, etc.) leaves the other
  loaded leagues untouched; the error banner names the league.

## Testing

- Config: usernames round trip, legacy `username` lift, validator rejects
  an empty list.
- Discovery: union and member order from two fake users; unknown user
  skipped with an error; labels for redraft, dynasty, type 3, pre-draft.
- State: `switch` loads once and swaps thereafter (fake loader counts calls);
  forwarding properties before and after load; `my_roster_id` order across
  usernames; `apply_settings` with a username change rediscovers and falls
  back to the first league when the active one disappears; staleness from
  `Store.fetched_at`.
- Market: snapshot source keyed by league; `player_values` reads its own.
- Refresh: summary errors carry the unknown-username message.

Screens verified by adding `mosrocks` in Settings, seeing eight leagues in
the switcher, opening three of them plus the shared one, and confirming the
shared league resolves my roster to `garretn4`.

## Out of scope

Hand-added league ids, per-league settings other than source and blend,
cross-league views, and removing stale league caches.
