# League, Matchups, and Settings Screens Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a League screen (standings, power rankings, rest-of-season team projections), a Matchups screen (weekly head-to-heads with optimal lineups), and a Settings screen (edit and apply `config.local.toml` without a restart).

**Architecture:** One new pure model module, `app/model/standings.py`, derives records, schedule, team projections, power scores, and per-slot lineups from the league context, the cached Sleeper matchup JSON, and the existing `Projection` frames. `Config` gains four fields plus a writer and form validator. `AppState` caches matchups, threads blend weights into the model projection, and applies saved settings by scope. Three NiceGUI page modules render the results using the existing `frame`/`ensure_loaded` pattern.

**Tech Stack:** Python 3.12, polars, NiceGUI 3.17 (Quasar `q-table`), pytest, stdlib `tomllib`/`json`/`math`/`statistics`. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-09-18-league-matchups-settings-design.md`

## Global Constraints

- No new dependencies. Approved list is in `pyproject.toml`.
- No AI attribution anywhere: no co-author trailers, no generated-with comments.
- Type hints everywhere, small functions, no dead code.
- Scoring and slots come from the league context at runtime; never hardcode them.
- Every UI boundary catches exceptions, stores the message in `state.error`, and notifies. Never swallow silently.
- Run tests with `uv run pytest -q`. Run the app in browser mode with `FF_NATIVE=0 uv run python -m app run` (port 8765).
- Commit messages: plain imperative sentence, no trailer.

## Deviations from the spec (decided while planning)

- `records()` returns `dict[int, Record]` keyed by roster id instead of a list, so callers can `.get(roster_id, EMPTY)` for rosters with no games.
- Refresh fetches matchups through `max(current_week, playoff_week_start - 1)` so playoff-week scoring keeps working once the playoffs start.
- Form validation and the form-to-config conversion live in `app/config.py` (not the UI module) so they are unit-tested without importing NiceGUI.
- `Config.blend` imports `BlendParams` inside the property body to avoid an import cycle between `app.config` and `app.model`.

## File map

| File | Responsibility |
|---|---|
| `app/config.py` (modify) | New fields, `blend`, `save_config`, `validate_settings`, `config_from_form`, `SAVED_KEYS` |
| `app/refresh.py` (modify) | Fetch matchups through the last regular-season week |
| `app/model/standings.py` (create) | Records, schedule, actual points, team projections, ROS averages, power scores, lineup by slot, win probability, league table rows |
| `app/ui/state.py` (modify) | `matchups` cache, blend params in `load`, `source_default`, `settings_scopes`, `apply_settings` |
| `app/ui/layout.py` (modify) | Public `on_refresh`, three new nav links |
| `app/ui/screens.py` (modify) | Register three new modules |
| `app/ui/league.py` (create) | League table page |
| `app/ui/matchups.py` (create) | Matchups page |
| `app/ui/settings.py` (create) | Settings form page |
| `tests/test_config.py`, `tests/test_refresh.py`, `tests/test_state.py` (modify), `tests/test_standings.py` (create) | Tests |

---

### Task 1: Config fields, writer, and form helpers

**Files:**
- Modify: `app/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: nothing new.
- Produces:
  - `Config` fields `source_default: str = "model"`, `blend_k: float = 4.0`, `blend_a: float = 0.6`, `blend_b: float = 0.5`; property `blend -> BlendParams`.
  - `SAVED_KEYS: tuple[str, ...]` = the eight keys written to the file.
  - `save_config(root: Path, cfg: Config) -> Path`
  - `validate_settings(form: Mapping[str, object], current_season: int) -> dict[str, str]` (field name to error; empty when valid)
  - `config_from_form(form: Mapping[str, object], base: Config) -> Config`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_config.py`:

```python
from app.config import SAVED_KEYS, config_from_form, save_config, validate_settings


def _cfg(root: Path, **over: object) -> Config:
    base = dict(league_id="123", root=root, stats_from=2015, detail_from=2020, current_season=2026,
                username="ann", source_default="consensus", blend_k=6.0, blend_a=0.4, blend_b=0.7)
    base.update(over)
    return Config(**base)  # type: ignore[arg-type]


def test_load_config_defaults_for_new_fields(tmp_path: Path) -> None:
    cfg = load_config(tmp_path, env={})
    assert (cfg.source_default, cfg.blend_k, cfg.blend_a, cfg.blend_b) == ("model", 4.0, 0.6, 0.5)
    assert (cfg.blend.k, cfg.blend.a, cfg.blend.b) == (4.0, 0.6, 0.5)


def test_save_config_round_trips_through_load_config(tmp_path: Path) -> None:
    written = save_config(tmp_path, _cfg(tmp_path))
    assert written == tmp_path / "config.local.toml"
    loaded = load_config(tmp_path, env={}, today=date(2026, 9, 18))
    for key in SAVED_KEYS:
        assert getattr(loaded, key) == getattr(_cfg(tmp_path), key), key
    assert list(tmp_path.glob("*.tmp")) == []


def test_save_config_env_override_still_wins(tmp_path: Path) -> None:
    save_config(tmp_path, _cfg(tmp_path))
    assert load_config(tmp_path, env={"FF_LEAGUE_ID": "999"}).league_id == "999"


def test_save_config_drops_unknown_keys(tmp_path: Path) -> None:
    (tmp_path / "config.local.toml").write_text('league_id = "1"\nmystery = 5\n')
    save_config(tmp_path, _cfg(tmp_path))
    assert "mystery" not in (tmp_path / "config.local.toml").read_text()


def _form(**over: object) -> dict[str, object]:
    form: dict[str, object] = dict(league_id="123", username="ann", source_default="model", stats_from=2015,
                                   detail_from=2020, blend_k=4.0, blend_a=0.6, blend_b=0.5)
    form.update(over)
    return form


def test_validate_settings_accepts_a_good_form() -> None:
    assert validate_settings(_form(), current_season=2026) == {}


def test_validate_settings_rejects_bad_fields() -> None:
    errors = validate_settings(_form(league_id="abc", source_default="magic", stats_from=2021, detail_from=2020,
                                     blend_k=0.0, blend_a=1.5, blend_b=-0.1), current_season=2026)
    assert set(errors) == {"league_id", "source_default", "stats_from", "blend_k", "blend_a", "blend_b"}


def test_validate_settings_rejects_missing_numbers() -> None:
    errors = validate_settings(_form(stats_from=None, blend_k=None), current_season=2026)
    assert "stats_from" in errors and "blend_k" in errors


def test_config_from_form_keeps_root_and_season(tmp_path: Path) -> None:
    base = _cfg(tmp_path)
    cfg = config_from_form(_form(league_id=" 777 ", username=" bob ", blend_k="8"), base)
    assert cfg.root == tmp_path and cfg.current_season == 2026
    assert (cfg.league_id, cfg.username, cfg.blend_k) == ("777", "bob", 8.0)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_config.py -q`
Expected: ImportError on `SAVED_KEYS` (collection fails).

- [ ] **Step 3: Implement**

Replace the top of `app/config.py` (imports through `Config`) and add the new functions after `load_config`:

```python
"""Runtime configuration: league id, paths, season range, projection defaults, and the settings writer."""
from __future__ import annotations

import json
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.model.projections import BlendParams

STATS_FROM = 2012
DETAIL_FROM = 2019
SOURCE_DEFAULT = "model"
SOURCES = ("consensus", "model")
MIN_SEASON = 2000
CONFIG_FILE = "config.local.toml"
SAVED_KEYS: tuple[str, ...] = (
    "league_id", "username", "source_default", "stats_from", "detail_from", "blend_k", "blend_a", "blend_b",
)
FILE_HEADER = "# Written by the Settings screen. Unknown keys are dropped on the next save.\n"


def current_nfl_season(today: date) -> int:
    """NFL season year: September onward belongs to the new season."""
    return today.year if today.month >= 9 else today.year - 1


@dataclass(frozen=True)
class Config:
    league_id: str
    root: Path
    stats_from: int
    detail_from: int
    current_season: int
    username: str = ""
    source_default: str = SOURCE_DEFAULT
    blend_k: float = 4.0
    blend_a: float = 0.6
    blend_b: float = 0.5

    @property
    def blend(self) -> BlendParams:
        from app.model.projections import BlendParams  # local import: app.model imports app.config indirectly

        return BlendParams(k=self.blend_k, a=self.blend_a, b=self.blend_b)
```

Keep the existing `cache_dir`, `duckdb_path`, `sqlite_path`, `stats_seasons`, `detail_seasons` properties and `_read_toml` unchanged. Replace `load_config` and add the writer and form helpers:

```python
def load_config(root: Path, env: Mapping[str, str], today: date | None = None) -> Config:
    """Read config.local.toml under root; FF_ROOT overrides root, FF_LEAGUE_ID overrides the file."""
    root = Path(env["FF_ROOT"]) if env.get("FF_ROOT") else root
    data = _read_toml(root / CONFIG_FILE)
    league_id = str(env.get("FF_LEAGUE_ID") or data.get("league_id", ""))
    season = current_nfl_season(today or date.today())
    return Config(
        league_id=league_id,
        root=root,
        stats_from=int(data.get("stats_from", STATS_FROM)),
        detail_from=int(data.get("detail_from", DETAIL_FROM)),
        current_season=season,
        username=str(data.get("username", "")),
        source_default=str(data.get("source_default", SOURCE_DEFAULT)),
        blend_k=float(data.get("blend_k", 4.0)),
        blend_a=float(data.get("blend_a", 0.6)),
        blend_b=float(data.get("blend_b", 0.5)),
    )


def _toml_scalar(value: object) -> str:
    return json.dumps(value) if isinstance(value, str) else repr(value)


def save_config(root: Path, cfg: Config) -> Path:
    """Write the saved keys to config.local.toml via a temp file and atomic rename."""
    path = root / CONFIG_FILE
    body = FILE_HEADER + "".join(f"{key} = {_toml_scalar(getattr(cfg, key))}\n" for key in SAVED_KEYS)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(body)
    tmp.replace(path)
    return path


def _as_int(value: object) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _as_float(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def validate_settings(form: Mapping[str, object], current_season: int) -> dict[str, str]:
    """Field name -> error message for a settings form; empty dict means valid."""
    errors: dict[str, str] = {}
    if not str(form.get("league_id", "")).strip().isdigit():
        errors["league_id"] = "digits only"
    if form.get("source_default") not in SOURCES:
        errors["source_default"] = f"one of {', '.join(SOURCES)}"
    stats_from, detail_from = _as_int(form.get("stats_from")), _as_int(form.get("detail_from"))
    if stats_from is None or detail_from is None:
        errors["stats_from"] = "whole years"
    elif not MIN_SEASON <= stats_from <= detail_from <= current_season:
        errors["stats_from"] = f"need {MIN_SEASON} <= stats_from <= detail_from <= {current_season}"
    k, a, b = _as_float(form.get("blend_k")), _as_float(form.get("blend_a")), _as_float(form.get("blend_b"))
    if k is None or not 0.0 < k <= 50.0:
        errors["blend_k"] = "0 < k <= 50"
    if a is None or not 0.0 <= a <= 1.0:
        errors["blend_a"] = "0 <= a <= 1"
    if b is None or not 0.0 <= b <= 1.0:
        errors["blend_b"] = "0 <= b <= 1"
    return errors


def config_from_form(form: Mapping[str, object], base: Config) -> Config:
    """Build a Config from a validated form, keeping root and current_season from base."""
    return replace(
        base,
        league_id=str(form["league_id"]).strip(),
        username=str(form["username"]).strip(),
        source_default=str(form["source_default"]),
        stats_from=int(form["stats_from"]),  # type: ignore[call-overload]
        detail_from=int(form["detail_from"]),  # type: ignore[call-overload]
        blend_k=float(form["blend_k"]),  # type: ignore[arg-type]
        blend_a=float(form["blend_a"]),  # type: ignore[arg-type]
        blend_b=float(form["blend_b"]),  # type: ignore[arg-type]
    )
```

Note: `SOURCES` is duplicated from `app/model/live.py` on purpose so `app.config` does not import the model package. Task 5 asserts they match.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_config.py -q`
Expected: all pass.

- [ ] **Step 5: Run the full suite and commit**

Run: `uv run pytest -q` → all pass.

```bash
git add app/config.py tests/test_config.py
git commit -m "Add projection defaults and a settings writer to Config"
```

---

### Task 2: Refresh fetches the whole regular-season schedule

**Files:**
- Modify: `app/refresh.py:35-43` (`_pull_league`)
- Test: `tests/test_refresh.py`

**Interfaces:**
- Consumes: `SleeperClient.matchups(league_id, week)`.
- Produces: cached keys `sleeper:/league/{id}/matchups/{w}` for `w` in `1 … max(week, playoff_week_start − 1)`.

- [ ] **Step 1: Write the failing test**

In `tests/test_refresh.py`, make the mock handler record paths. Replace `sleeper_handler` with a factory and add a test:

```python
def make_sleeper_handler(seen: list[str]):
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        seen.append(path)
        if path == "/v1/state/nfl":
            return httpx.Response(200, json={"week": 2, "season": "2025"})
        if path == "/v1/league/1":
            return httpx.Response(200, json=LEAGUE)
        if path == "/v1/players/nfl":
            return httpx.Response(200, json={
                "4046": {"full_name": "Patrick Mahomes", "position": "QB", "team": "KC", "age": 30, "injury_status": None},
                "DET": {"full_name": "Detroit Lions", "position": "DEF", "team": "DET"},
            })
        return httpx.Response(200, json=[])
    return handler


def sleeper_handler(request: httpx.Request) -> httpx.Response:
    return make_sleeper_handler([])(request)


def test_refresh_fetches_matchups_through_the_regular_season(tmp_path: Path) -> None:
    seen: list[str] = []
    cfg = Config(league_id="1", root=tmp_path, stats_from=2025, detail_from=2025, current_season=2025)
    store = Store(cfg.sqlite_path)
    con = connect(cfg.duckdb_path)
    client = SleeperClient(store, http=httpx.Client(transport=httpx.MockTransport(make_sleeper_handler(seen))), refresh=True)
    refresh(cfg, client, con, store, httpx.Client(transport=httpx.MockTransport(market_handler)),
            {"player_stats": lambda seasons: pl.DataFrame({"player_id": ["00-1"], "season": seasons[:1], "week": [1],
                                                           "position": ["QB"], "passing_yards": [300.0]})},
            {"ff_playerids": lambda: pl.DataFrame({"gsis_id": ["00-1"], "sleeper_id": [4046], "fantasypros_id": [1],
                                                   "pfr_id": ["x"], "name": ["Patrick Mahomes"], "position": ["QB"],
                                                   "team": ["KC"], "birthdate": ["1995-09-17"]})},
            now=datetime(2025, 9, 16, 12, 0, 0))
    matchup_weeks = sorted(int(p.rsplit("/", 1)[1]) for p in seen if "/matchups/" in p)
    tx_weeks = sorted(int(p.rsplit("/", 1)[1]) for p in seen if "/transactions/" in p)
    assert matchup_weeks == list(range(1, 15))   # LEAGUE has no settings -> playoff_week_start defaults to 15
    assert tx_weeks == [1, 2]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_refresh.py -q`
Expected: the new test fails with `assert [1, 2] == [1, ..., 14]`.

- [ ] **Step 3: Implement**

In `app/refresh.py` replace `_pull_league`:

```python
DEFAULT_PLAYOFF_WEEK_START = 15


def _pull_league(client: SleeperClient, league_id: str, week: int) -> dict[str, Any]:
    league = client.league(league_id)
    client.users(league_id)
    client.rosters(league_id)
    client.traded_picks(league_id)
    last_regular = int((league.get("settings") or {}).get("playoff_week_start", DEFAULT_PLAYOFF_WEEK_START)) - 1
    for w in range(1, max(week, last_regular) + 1):
        client.matchups(league_id, w)
    for w in range(1, week + 1):
        client.transactions(league_id, w)
    return league
```

- [ ] **Step 4: Run tests and commit**

Run: `uv run pytest -q` → all pass.

```bash
git add app/refresh.py tests/test_refresh.py
git commit -m "Fetch matchups for every regular-season week on refresh"
```

---

### Task 3: Standings module: matchups, records, schedule, actual points

**Files:**
- Create: `app/model/standings.py`
- Test: `tests/test_standings.py`

**Interfaces:**
- Consumes: `Store.get_cached(key, None, 0.0)`.
- Produces:
  - `Matchups = dict[int, list[dict[str, Any]]]` (week → raw Sleeper rows)
  - `Record(roster_id, wins, losses, ties, points_for, points_against)` frozen dataclass; `EMPTY_RECORD` helper via `Record.empty(roster_id)`
  - `Pairing(week, matchup_id, home, away: int | None)` frozen dataclass
  - `load_matchups(store, league_id, weeks: list[int]) -> Matchups`
  - `records(matchups, through_week) -> dict[int, Record]`
  - `schedule(matchups, week) -> list[Pairing]`
  - `actual_points(matchups, week) -> dict[int, float]`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_standings.py`:

```python
import json
from pathlib import Path

from app.data.store import Store
from app.model.standings import Pairing, Record, actual_points, load_matchups, records, schedule


def rows(*triples: tuple[int, int, float]) -> list[dict]:
    """(roster_id, matchup_id, points) -> Sleeper matchup rows."""
    return [{"roster_id": r, "matchup_id": m, "points": p, "players_points": {}} for r, m, p in triples]


MATCHUPS = {
    1: rows((1, 1, 100.0), (2, 1, 90.0), (3, 2, 80.0), (4, 2, 80.0)),
    2: rows((1, 1, 70.0), (3, 1, 75.0), (2, 2, 60.0), (4, 2, 50.0)),
    3: rows((1, 1, 0.0), (4, 1, 0.0), (2, 2, 0.0), (3, 2, 0.0)),
}


def test_load_matchups_skips_weeks_without_cache(tmp_path: Path) -> None:
    store = Store(tmp_path / "app.sqlite")
    store.put_cached("sleeper:/league/1/matchups/1", json.dumps(MATCHUPS[1]), now=1.0)
    store.put_cached("sleeper:/league/1/matchups/3", json.dumps(MATCHUPS[3]), now=1.0)
    loaded = load_matchups(store, "1", [1, 2, 3])
    assert set(loaded) == {1, 3} and loaded[1][0]["roster_id"] == 1


def test_records_tally_wins_losses_ties_and_points_before_through_week() -> None:
    recs = records(MATCHUPS, through_week=3)
    assert recs[1] == Record(1, 1, 1, 0, 170.0, 165.0)
    assert recs[2] == Record(2, 1, 1, 0, 150.0, 150.0)
    assert recs[3] == Record(3, 1, 0, 1, 155.0, 150.0)
    assert recs[4] == Record(4, 0, 1, 1, 130.0, 140.0)


def test_records_ignore_the_current_week_and_unpaired_rows() -> None:
    lonely = {1: rows((1, 1, 50.0)), 2: rows((1, 1, 99.0), (2, 1, 1.0))}
    assert records(lonely, through_week=2) == {}
    assert records(MATCHUPS, through_week=1) == {}


def test_record_empty_and_text() -> None:
    assert Record.empty(9) == Record(9, 0, 0, 0, 0.0, 0.0)
    assert Record(1, 3, 2, 0, 0.0, 0.0).text == "3-2"
    assert Record(1, 3, 2, 1, 0.0, 0.0).text == "3-2-1"
    assert Record(1, 3, 2, 1, 0.0, 0.0).games == 6


def test_schedule_pairs_by_matchup_id_and_marks_byes() -> None:
    assert schedule(MATCHUPS, 2) == [Pairing(2, 1, 1, 3), Pairing(2, 2, 2, 4)]
    assert schedule({5: rows((7, 3, 0.0))}, 5) == [Pairing(5, 3, 7, None)]
    assert schedule(MATCHUPS, 9) == []


def test_schedule_skips_rows_without_a_matchup_id() -> None:
    unscheduled = {6: [{"roster_id": 1, "matchup_id": None, "points": 0.0}]}
    assert schedule(unscheduled, 6) == []


def test_actual_points_by_roster() -> None:
    assert actual_points(MATCHUPS, 1) == {1: 100.0, 2: 90.0, 3: 80.0, 4: 80.0}
    assert actual_points(MATCHUPS, 9) == {}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_standings.py -q`
Expected: ModuleNotFoundError for `app.model.standings`.

- [ ] **Step 3: Implement**

Create `app/model/standings.py`:

```python
"""Standings, schedule, team projections, power scores, and matchup lineups from cached Sleeper data."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from app.data.store import Store

Matchups = dict[int, list[dict[str, Any]]]


@dataclass(frozen=True)
class Record:
    roster_id: int
    wins: int
    losses: int
    ties: int
    points_for: float
    points_against: float

    @classmethod
    def empty(cls, roster_id: int) -> Record:
        return cls(roster_id, 0, 0, 0, 0.0, 0.0)

    @property
    def games(self) -> int:
        return self.wins + self.losses + self.ties

    @property
    def text(self) -> str:
        base = f"{self.wins}-{self.losses}"
        return f"{base}-{self.ties}" if self.ties else base


@dataclass(frozen=True)
class Pairing:
    week: int
    matchup_id: int
    home: int
    away: int | None


def load_matchups(store: Store, league_id: str, weeks: list[int]) -> Matchups:
    """Cached Sleeper matchup rows per week; weeks with no cache are omitted."""
    out: Matchups = {}
    for week in weeks:
        body = store.get_cached(f"sleeper:/league/{league_id}/matchups/{week}", None, 0.0)
        if body is not None:
            out[week] = json.loads(body)
    return out


def _by_matchup(rows: list[dict[str, Any]]) -> dict[int, list[dict[str, Any]]]:
    grouped: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        matchup_id = row.get("matchup_id")
        if matchup_id is not None:
            grouped.setdefault(int(matchup_id), []).append(row)
    return grouped


def _points(row: dict[str, Any]) -> float:
    return float(row.get("points") or 0.0)


def records(matchups: Matchups, through_week: int) -> dict[int, Record]:
    """Win/loss/tie and points tallies for every week before through_week, keyed by roster id."""
    tally: dict[int, dict[str, float]] = {}
    for week, rows in matchups.items():
        if week >= through_week:
            continue
        for pair in _by_matchup(rows).values():
            if len(pair) < 2:
                continue
            for me, opp in ((pair[0], pair[1]), (pair[1], pair[0])):
                mine, theirs = _points(me), _points(opp)
                entry = tally.setdefault(int(me["roster_id"]), {"w": 0, "l": 0, "t": 0, "pf": 0.0, "pa": 0.0})
                entry["pf"] += mine
                entry["pa"] += theirs
                entry["w" if mine > theirs else "l" if mine < theirs else "t"] += 1
    return {
        rid: Record(rid, int(e["w"]), int(e["l"]), int(e["t"]), e["pf"], e["pa"])
        for rid, e in sorted(tally.items())
    }


def schedule(matchups: Matchups, week: int) -> list[Pairing]:
    """Head-to-head pairings for one week; a lone roster becomes a bye (away=None)."""
    pairings: list[Pairing] = []
    for matchup_id, pair in sorted(_by_matchup(matchups.get(week, [])).items()):
        home = int(pair[0]["roster_id"])
        away = int(pair[1]["roster_id"]) if len(pair) > 1 else None
        pairings.append(Pairing(week=week, matchup_id=matchup_id, home=home, away=away))
    return pairings


def actual_points(matchups: Matchups, week: int) -> dict[int, float]:
    return {int(row["roster_id"]): _points(row) for row in matchups.get(week, [])}
```

- [ ] **Step 4: Run tests and commit**

Run: `uv run pytest tests/test_standings.py -q` → all pass.

```bash
git add app/model/standings.py tests/test_standings.py
git commit -m "Add standings records and schedule from cached matchups"
```

---

### Task 4: Standings module: projections, power scores, lineups, win probability

**Files:**
- Modify: `app/model/standings.py`
- Test: `tests/test_standings.py`

**Interfaces:**
- Consumes: `weekly_strength(slots, weekly, player_ids) -> pl.DataFrame[week, points]` and `optimal_lineup(slots, players) -> (total, {slot_index_str: sleeper_id})` from `app/model/lineup.py`; `Projection.weekly` with columns `sleeper_id, position, week, points`; `LeagueContext.slots`, `.rosters`; `Roster.players`.
- Produces:
  - `team_week_projections(ctx, proj) -> pl.DataFrame[roster_id: Int64, week: Int64, points: Float64]`
  - `ros_averages(team_weeks) -> dict[int, float]`
  - `week_totals(team_weeks, week) -> dict[int, float]`
  - `power_scores(records: dict[int, Record], ros_avg: dict[int, float]) -> dict[int, float]`
  - `lineup_for(ctx, proj, roster, week) -> list[tuple[str, str, float]]` as `(slot, sleeper_id or "", points)`
  - `win_probability(points_a, points_b) -> float`
  - constants `SIGMA = 20.0`, `PROJECTION_WEIGHT = 0.6`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_standings.py`:

```python
import math

import polars as pl

from app.model.league import LeagueContext, Roster
from app.model.live import Projection
from app.model.standings import (
    PROJECTION_WEIGHT, lineup_for, power_scores, ros_averages, team_week_projections, week_totals, win_probability,
)


def ctx_with(rosters: tuple[Roster, ...], slots: list[str]) -> LeagueContext:
    return LeagueContext(league_id="1", name="t", season=2026, week=3, n_teams=len(rosters), scoring={},
                         roster_positions=slots + ["BN"], slots=slots, playoff_week_start=15, last_week=17,
                         trade_deadline=99, rosters=rosters)


def weekly(points: dict[str, tuple[str, float]], weeks: list[int]) -> pl.DataFrame:
    """{sleeper_id: (position, points)} repeated for each week."""
    rows = [(pid, pos, w, pts) for w in weeks for pid, (pos, pts) in points.items()]
    return pl.DataFrame(rows, schema={"sleeper_id": pl.Utf8, "position": pl.Utf8, "week": pl.Int64,
                                      "points": pl.Float64}, orient="row")


A = Roster(1, "u1", "A", "A", None, ("a1", "a2", "a3"), (), ())
B = Roster(2, "u2", "B", "B", None, ("b1", "b2"), (), ())
POINTS = {"a1": ("RB", 20.0), "a2": ("RB", 10.0), "a3": ("WR", 9.0), "b1": ("RB", 25.0), "b2": ("WR", 5.0)}


def test_team_week_projections_use_the_optimal_lineup_per_week() -> None:
    ctx = ctx_with((A, B), ["RB", "FLEX"])
    proj = Projection("model", weekly(POINTS, [3, 4]), pl.DataFrame())
    frame = team_week_projections(ctx, proj)
    assert frame.schema == {"roster_id": pl.Int64, "week": pl.Int64, "points": pl.Float64}
    got = {(r, w): p for r, w, p in frame.iter_rows()}
    assert got == {(1, 3): 30.0, (1, 4): 30.0, (2, 3): 30.0, (2, 4): 30.0}


def test_team_week_projections_handles_a_roster_with_no_projected_players() -> None:
    ghost = Roster(3, "u3", "C", "C", None, ("nobody",), (), ())
    ctx = ctx_with((A, ghost), ["RB"])
    frame = team_week_projections(ctx, Projection("model", weekly(POINTS, [3]), pl.DataFrame()))
    assert frame.filter(pl.col("roster_id") == 3).is_empty()
    assert ros_averages(frame) == {1: 20.0}


def test_ros_averages_and_week_totals() -> None:
    frame = pl.DataFrame({"roster_id": [1, 1, 2], "week": [3, 4, 3], "points": [10.0, 20.0, 7.0]})
    assert ros_averages(frame) == {1: 15.0, 2: 7.0}
    assert week_totals(frame, 3) == {1: 10.0, 2: 7.0}
    assert week_totals(frame, 4) == {1: 20.0}
    assert ros_averages(pl.DataFrame(schema={"roster_id": pl.Int64, "week": pl.Int64, "points": pl.Float64})) == {}


def test_power_scores_use_projection_only_before_any_games() -> None:
    scores = power_scores({}, {1: 100.0, 2: 80.0, 3: 90.0})
    assert scores[1] > scores[3] > scores[2]
    assert math.isclose(sum(scores.values()), 0.0, abs_tol=1e-9)


def test_power_scores_blend_projection_with_points_per_game() -> None:
    recs = {1: Record(1, 1, 0, 0, 100.0, 50.0), 2: Record(2, 0, 1, 0, 50.0, 100.0)}
    ros = {1: 80.0, 2: 90.0}
    scores = power_scores(recs, ros)
    # z(ros) = (-1, +1); z(pf) = (+1, -1) -> 0.6*(-1) + 0.4*(+1) = -0.2 for roster 1
    assert math.isclose(scores[1], PROJECTION_WEIGHT * -1 + (1 - PROJECTION_WEIGHT) * 1)
    assert math.isclose(scores[2], -scores[1])


def test_power_scores_constant_column_is_zero() -> None:
    assert power_scores({}, {1: 50.0, 2: 50.0}) == {1: 0.0, 2: 0.0}


def test_lineup_for_returns_slots_in_order_with_empty_slots() -> None:
    ctx = ctx_with((A, B), ["RB", "WR", "FLEX", "TE"])
    proj = Projection("model", weekly(POINTS, [3]), pl.DataFrame())
    assert lineup_for(ctx, proj, A, 3) == [("RB", "a1", 20.0), ("WR", "a3", 9.0), ("FLEX", "a2", 10.0), ("TE", "", 0.0)]
    assert lineup_for(ctx, proj, A, 9) == [("RB", "", 0.0), ("WR", "", 0.0), ("FLEX", "", 0.0), ("TE", "", 0.0)]


def test_win_probability_is_symmetric_and_monotone() -> None:
    assert win_probability(100.0, 100.0) == 0.5
    assert math.isclose(win_probability(110.0, 100.0) + win_probability(100.0, 110.0), 1.0)
    assert win_probability(120.0, 100.0) > win_probability(110.0, 100.0) > 0.5
    assert 0.63 < win_probability(110.0, 100.0) < 0.65   # 10 / (20 * sqrt 2) = 0.354 sd -> 0.638
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_standings.py -q`
Expected: ImportError on `PROJECTION_WEIGHT`.

- [ ] **Step 3: Implement**

Add to the imports of `app/model/standings.py`:

```python
import math
from statistics import mean, pstdev

import polars as pl

from app.model.league import LeagueContext, Roster
from app.model.lineup import optimal_lineup, weekly_strength
from app.model.live import Projection
```

Add constants after `Matchups`:

```python
SIGMA = 20.0             # assumed std dev of one team's weekly score
PROJECTION_WEIGHT = 0.6  # share of the power score from projected strength
TEAM_WEEKS_SCHEMA = {"roster_id": pl.Int64, "week": pl.Int64, "points": pl.Float64}
Slot = tuple[str, str, float]
```

Append the functions:

```python
def team_week_projections(ctx: LeagueContext, proj: Projection) -> pl.DataFrame:
    """Optimal-lineup projected points per roster per remaining week."""
    frames = [
        weekly_strength(ctx.slots, proj.weekly, set(roster.players))
        .with_columns(pl.lit(roster.roster_id, dtype=pl.Int64).alias("roster_id"))
        .select("roster_id", "week", "points")
        for roster in ctx.rosters
    ]
    if not frames:
        return pl.DataFrame(schema=TEAM_WEEKS_SCHEMA)
    return pl.concat(frames).cast(TEAM_WEEKS_SCHEMA)


def _by_roster(frame: pl.DataFrame, column: str) -> dict[int, float]:
    return {int(r): float(v) for r, v in zip(frame["roster_id"].to_list(), frame[column].to_list())}


def ros_averages(team_weeks: pl.DataFrame) -> dict[int, float]:
    if team_weeks.is_empty():
        return {}
    return _by_roster(team_weeks.group_by("roster_id").agg(pl.col("points").mean().alias("avg")), "avg")


def week_totals(team_weeks: pl.DataFrame, week: int) -> dict[int, float]:
    return _by_roster(team_weeks.filter(pl.col("week") == week), "points")


def _zscores(values: dict[int, float]) -> dict[int, float]:
    if not values:
        return {}
    mu, sd = mean(values.values()), pstdev(values.values())
    if sd == 0.0:
        return {rid: 0.0 for rid in values}
    return {rid: (v - mu) / sd for rid, v in values.items()}


def power_scores(records: dict[int, Record], ros_avg: dict[int, float]) -> dict[int, float]:
    """0.6 z(projected points per week) + 0.4 z(points for per game); projection only before any games."""
    z_proj = _zscores(ros_avg)
    per_game = {rid: rec.points_for / rec.games for rid, rec in records.items() if rec.games > 0}
    if not per_game:
        return z_proj
    z_pf = _zscores(per_game)
    return {
        rid: PROJECTION_WEIGHT * z_proj.get(rid, 0.0) + (1.0 - PROJECTION_WEIGHT) * z_pf.get(rid, 0.0)
        for rid in ros_avg
    }


def lineup_for(ctx: LeagueContext, proj: Projection, roster: Roster, week: int) -> list[Slot]:
    """(slot, sleeper_id, points) in slot order for the roster's optimal lineup that week; "" when unfilled."""
    rows = proj.weekly.filter((pl.col("week") == week) & pl.col("sleeper_id").is_in(list(roster.players)))
    players = list(zip(rows["sleeper_id"].to_list(), rows["position"].to_list(), rows["points"].to_list()))
    _, assignment = optimal_lineup(ctx.slots, players)
    points = {pid: float(pts) for pid, _, pts in players}
    return [(slot, assignment.get(str(i), ""), points.get(assignment.get(str(i), ""), 0.0))
            for i, slot in enumerate(ctx.slots)]


def win_probability(points_a: float, points_b: float) -> float:
    """P(a beats b) when each side's score has std dev SIGMA: Phi(diff / (SIGMA * sqrt 2))."""
    return 0.5 * (1.0 + math.erf((points_a - points_b) / (2.0 * SIGMA)))
```

(Φ(x) = ½(1 + erf(x/√2)); with x = diff/(SIGMA·√2) the erf argument is diff/(2·SIGMA).)

- [ ] **Step 4: Run tests and commit**

Run: `uv run pytest tests/test_standings.py -q` → all pass. Then `uv run pytest -q` → all pass.

```bash
git add app/model/standings.py tests/test_standings.py
git commit -m "Add team projections, power scores, lineups, and win probability"
```

---

### Task 5: App state: matchups cache, blend weights, settings scopes

**Files:**
- Modify: `app/ui/state.py`
- Test: `tests/test_state.py`

**Interfaces:**
- Consumes: `load_matchups`, `Config.blend`, `Config.source_default`, `save_config`, `project(..., params=)`, `player_values`.
- Produces:
  - `AppState.matchups: Matchups` (populated by `load()`)
  - `AppState.source` initialised from `cfg.source_default`
  - `settings_scopes(old: Config, new: Config) -> set[str]` pure; values from `{"league", "history", "model", "source"}`
  - `AppState.apply_settings(new: Config) -> str` returns the highest-priority scope in order league, history, model, source, or `"none"`
  - `SCOPE_ORDER: tuple[str, ...] = ("league", "history", "model", "source")`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_state.py`:

```python
from dataclasses import replace

from app.config import SOURCES as CONFIG_SOURCES, load_config
from app.model.live import SOURCES
from app.ui.state import SCOPE_ORDER, settings_scopes


def test_config_and_live_agree_on_sources() -> None:
    assert CONFIG_SOURCES == SOURCES


def test_settings_scopes_by_field_group() -> None:
    base = _cfg("ann")
    assert settings_scopes(base, base) == set()
    assert settings_scopes(base, replace(base, league_id="2")) == {"league"}
    assert settings_scopes(base, replace(base, username="bob")) == {"league"}
    assert settings_scopes(base, replace(base, stats_from=2010)) == {"history"}
    assert settings_scopes(base, replace(base, detail_from=2021)) == {"history"}
    assert settings_scopes(base, replace(base, blend_k=9.0)) == {"model"}
    assert settings_scopes(base, replace(base, source_default="consensus")) == {"source"}
    assert settings_scopes(base, replace(base, blend_a=0.1, stats_from=2010)) == {"history", "model"}


def test_state_source_starts_from_config_default() -> None:
    assert AppState(cfg=replace(_cfg(""), source_default="consensus")).source == "consensus"


def test_apply_settings_saves_and_returns_the_top_scope(tmp_path: Path, monkeypatch) -> None:
    state = AppState(cfg=replace(_cfg("ann"), root=tmp_path))
    calls: list[str] = []
    monkeypatch.setattr(state, "load", lambda: calls.append("load"))
    monkeypatch.setattr(state, "_recompute_model", lambda: calls.append("model"))

    assert state.apply_settings(replace(state.cfg, source_default="consensus")) == "source"
    assert state.source == "consensus" and calls == []
    assert load_config(tmp_path, env={}).source_default == "consensus"

    assert state.apply_settings(replace(state.cfg, blend_k=9.0)) == "model"
    assert calls == ["model"]

    assert state.apply_settings(replace(state.cfg, blend_b=0.9, stats_from=2010)) == "history"
    assert calls == ["model", "model"]

    assert state.apply_settings(replace(state.cfg, league_id="42", blend_a=0.2)) == "league"
    assert calls == ["model", "model", "load"]   # load recomputes projections, so no extra model call
    assert state.cfg.league_id == "42"

    assert state.apply_settings(state.cfg) == "none"
    assert SCOPE_ORDER == ("league", "history", "model", "source")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_state.py -q`
Expected: ImportError on `SCOPE_ORDER`.

- [ ] **Step 3: Implement**

In `app/ui/state.py`:

Add imports:

```python
from app.config import Config, load_config, save_config
from app.model.standings import Matchups, load_matchups
```

Remove `DEFAULT_SOURCE = "model"` and add:

```python
SCOPE_ORDER: tuple[str, ...] = ("league", "history", "model", "source")
```

Change the dataclass fields and add `__post_init__`:

```python
    matchups: Matchups = field(default_factory=dict)
    source: str = ""
    ...

    def __post_init__(self) -> None:
        if not self.source:
            self.source = self.cfg.source_default
```

Replace `load()`:

```python
    def load(self) -> None:
        """Rebuild the league context, both projection sources, the value table, and cached matchups."""
        store, con = self._connect()
        self.ctx = load_league_context(store, self.cfg.league_id)
        self.projections = {source: project(con, store, self.ctx, source, params=self.cfg.blend) for source in SOURCES}
        self.values = player_values(self.ctx, con, store, self.primary(), self.secondary())
        self.matchups = load_matchups(store, self.cfg.league_id, list(range(1, self.ctx.playoff_week_start)))
        self.last_refresh = self._league_refreshed_at()
```

Add after `my_roster_id`:

```python
    def _recompute_model(self) -> None:
        """Re-run only the model projection with the current blend weights, then rebuild values."""
        if self.ctx is None:
            return
        store, con = self._connect()
        self.projections["model"] = project(con, store, self.ctx, "model", params=self.cfg.blend)
        self.values = player_values(self.ctx, con, store, self.primary(), self.secondary())

    def apply_settings(self, new: Config) -> str:
        """Save new config, apply it by scope, and return the most significant scope (or "none")."""
        old, self.cfg = self.cfg, new
        save_config(new.root, new)
        scopes = settings_scopes(old, new)
        if "source" in scopes:
            self.source = new.source_default
        if "league" in scopes:
            self.load()
        elif "model" in scopes:
            self._recompute_model()
        return next((scope for scope in SCOPE_ORDER if scope in scopes), "none")
```

Add the pure function at module level (before `startup_plan`):

```python
def settings_scopes(old: Config, new: Config) -> set[str]:
    """Which parts of the app a config change touches."""
    scopes: set[str] = set()
    if (old.league_id, old.username) != (new.league_id, new.username):
        scopes.add("league")
    if (old.stats_from, old.detail_from) != (new.stats_from, new.detail_from):
        scopes.add("history")
    if (old.blend_k, old.blend_a, old.blend_b) != (new.blend_k, new.blend_a, new.blend_b):
        scopes.add("model")
    if old.source_default != new.source_default:
        scopes.add("source")
    return scopes
```

Note `project` ignores `params` for the consensus source, so passing it for both is harmless (verified in `app/model/live.py:134-144`).

- [ ] **Step 4: Run tests and commit**

Run: `uv run pytest -q` → all pass.

```bash
git add app/ui/state.py tests/test_state.py
git commit -m "Cache matchups, thread blend weights, and apply settings by scope"
```

---

### Task 6: League screen

**Files:**
- Modify: `app/model/standings.py` (add `league_rows`)
- Create: `app/ui/league.py`
- Modify: `app/ui/layout.py:14-21` (`SCREEN_LINKS`), `app/ui/screens.py:6-13` (`SCREEN_MODULES`)
- Test: `tests/test_standings.py`

**Interfaces:**
- Consumes: `records`, `team_week_projections`, `ros_averages`, `power_scores`, `Record.text`, `AppState.matchups`, `AppState.projections`, `AppState.my_roster_id()`.
- Produces: `league_rows(ctx, projections: dict[str, Projection], matchups, me: int) -> list[dict[str, object]]` with keys `id, rank, team, mine, record, pf, pa, model, consensus, power`, sorted by power descending. Route `/league`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_standings.py`:

```python
from app.model.standings import league_rows


def test_league_rows_rank_by_power_and_flag_me() -> None:
    ctx = ctx_with((A, B), ["RB", "FLEX"])
    strong = {"a1": ("RB", 20.0), "a2": ("RB", 10.0), "a3": ("WR", 9.0), "b1": ("RB", 5.0), "b2": ("WR", 5.0)}
    projections = {"model": Projection("model", weekly(strong, [3]), pl.DataFrame()),
                   "consensus": Projection("consensus", weekly(POINTS, [3]), pl.DataFrame())}
    rows = league_rows(ctx, projections, MATCHUPS, me=2)
    assert [r["id"] for r in rows] == [1, 2]
    assert rows[0]["rank"] == 1 and rows[0]["mine"] is False and rows[1]["mine"] is True
    assert rows[0]["record"] == "1-1" and rows[0]["pf"] == 170.0 and rows[0]["pa"] == 165.0
    assert rows[0]["model"] == 30.0 and rows[0]["consensus"] == 30.0
    assert rows[1]["model"] == 10.0 and rows[1]["consensus"] == 30.0
    assert rows[0]["power"] > rows[1]["power"]


def test_league_rows_before_any_games() -> None:
    ctx = ctx_with((A, B), ["RB"])
    proj = Projection("model", weekly(POINTS, [3]), pl.DataFrame())
    rows = league_rows(ctx, {"model": proj, "consensus": proj}, {}, me=1)
    assert [r["record"] for r in rows] == ["0-0", "0-0"] and [r["id"] for r in rows] == [2, 1]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_standings.py -q`
Expected: ImportError on `league_rows`.

- [ ] **Step 3: Implement `league_rows`**

Append to `app/model/standings.py`:

```python
def league_rows(ctx: LeagueContext, projections: dict[str, Projection], matchups: Matchups, me: int) -> list[dict[str, object]]:
    """One row per roster for the League table, ranked by power score."""
    recs = records(matchups, ctx.week)
    averages = {source: ros_averages(team_week_projections(ctx, proj)) for source, proj in projections.items()}
    power = power_scores(recs, averages["model"])
    rows: list[dict[str, object]] = []
    for roster in ctx.rosters:
        rid = roster.roster_id
        rec = recs.get(rid, Record.empty(rid))
        rows.append({
            "id": rid, "team": roster.team_name, "mine": rid == me, "record": rec.text,
            "pf": round(rec.points_for, 1), "pa": round(rec.points_against, 1),
            "model": round(averages["model"].get(rid, 0.0), 1),
            "consensus": round(averages["consensus"].get(rid, 0.0), 1),
            "power": round(power.get(rid, 0.0), 2),
        })
    rows.sort(key=lambda row: (-float(row["power"]), int(row["id"])))
    for rank, row in enumerate(rows, start=1):
        row["rank"] = rank
    return rows
```

Run: `uv run pytest tests/test_standings.py -q` → all pass.

- [ ] **Step 4: Create the page**

Create `app/ui/league.py`:

```python
"""League screen: standings, power rankings, and rest-of-season projections for every roster."""
from __future__ import annotations

from nicegui import ui

from app.model.standings import PROJECTION_WEIGHT, league_rows
from app.ui.layout import ensure_loaded, frame
from app.ui.state import get_state
from app.ui.theme import ACCENT

COLUMNS: list[dict[str, object]] = [
    {"name": "rank", "label": "#", "field": "rank", "align": "right", "sortable": True},
    {"name": "team", "label": "Team", "field": "team", "align": "left", "sortable": True},
    {"name": "record", "label": "Record", "field": "record", "align": "center"},
    {"name": "pf", "label": "PF", "field": "pf", "align": "right", "sortable": True},
    {"name": "pa", "label": "PA", "field": "pa", "align": "right", "sortable": True},
    {"name": "model", "label": "Model ROS/wk", "field": "model", "align": "right", "sortable": True},
    {"name": "consensus", "label": "Consensus ROS/wk", "field": "consensus", "align": "right", "sortable": True},
    {"name": "power", "label": "Power", "field": "power", "align": "right", "sortable": True},
]


def _team_cell_slot() -> str:
    return (
        '<q-td :props="props" :style="props.row.mine ? \'color:' + ACCENT + ';font-weight:600\' : \'\'">'
        "{{ props.value }}</q-td>"
    )


@ui.page("/league")
def league_page() -> None:
    with frame("League"):
        state = get_state()
        if not ensure_loaded(state):
            return
        try:
            rows = league_rows(state.ctx, state.projections, state.matchups, state.my_roster_id())
        except Exception as exc:  # UI boundary: caught, recorded, and surfaced, never swallowed
            state.error = str(exc)
            ui.notify(f"League table failed: {exc}", type="negative")
            return
        table = ui.table(columns=COLUMNS, rows=rows, row_key="id").classes("w-full ff-card")
        table.props("flat dense dark")
        table.add_slot("body-cell-team", _team_cell_slot())
        pct = int(PROJECTION_WEIGHT * 100)
        ui.label(
            f"Power = {pct}% z-score of model projected points per remaining week "
            f"+ {100 - pct}% z-score of points for per game. Projection only until games are played."
        ).classes("ff-label")
```

- [ ] **Step 5: Register the route and nav link**

In `app/ui/layout.py` replace `SCREEN_LINKS`:

```python
SCREEN_LINKS: tuple[tuple[str, str], ...] = (
    ("League", "/league"),
    ("Matchups", "/matchups"),
    ("Heatmap", "/heatmap"),
    ("Players", "/players"),
    ("Scatter", "/scatter"),
    ("Trade", "/trade"),
    ("Finder", "/finder"),
    ("Managers", "/managers"),
    ("Settings", "/settings"),
)
```

In `app/ui/screens.py` replace `SCREEN_MODULES`:

```python
SCREEN_MODULES: tuple[str, ...] = (
    "app.ui.league",
    "app.ui.matchups",
    "app.ui.heatmap",
    "app.ui.players",
    "app.ui.scatter",
    "app.ui.trade",
    "app.ui.finder",
    "app.ui.managers",
    "app.ui.settings",
)
```

`_header` only links routes that are registered, so the Matchups and Settings links stay hidden until their modules exist (Tasks 7 and 8).

- [ ] **Step 6: Launch and check**

Run in the background: `FF_NATIVE=0 uv run python -m app run`. Open `http://127.0.0.1:8765/league` with the browser tool. Confirm: the table renders one row per roster, my row is amber, clicking the "PF" header re-sorts, no traceback in the server log. Stop the server.

- [ ] **Step 7: Run tests and commit**

Run: `uv run pytest -q` → all pass.

```bash
git add app/model/standings.py app/ui/league.py app/ui/layout.py app/ui/screens.py tests/test_standings.py
git commit -m "Add league screen with standings, projections, and power rankings"
```

---

### Task 7: Matchups screen

**Files:**
- Modify: `app/ui/layout.py` (rename `_on_refresh` to `on_refresh`)
- Create: `app/ui/matchups.py`

**Interfaces:**
- Consumes: `schedule`, `actual_points`, `team_week_projections`, `week_totals`, `lineup_for`, `win_probability`, `Pairing`; `AppState.matchups`, `.projections`, `.source`, `.values` (columns `sleeper_id`, `name`), `.my_roster_id()`; `on_refresh` from layout.
- Produces: route `/matchups?week=N` (`week=0` means the current week).

- [ ] **Step 1: Make the refresh handler public**

In `app/ui/layout.py` rename `_on_refresh` to `on_refresh` (definition, the call in `_on_retry`, and the `ui.button("Refresh", on_click=on_refresh)` in `_header`). Run `uv run pytest -q` → all pass (nothing tests the name, this confirms nothing else imports it).

- [ ] **Step 2: Create the page**

Create `app/ui/matchups.py`:

```python
"""Matchups screen: every head-to-head for a week with projected totals; my matchup expanded by slot."""
from __future__ import annotations

from dataclasses import dataclass

import polars as pl
from nicegui import ui

from app.model.league import LeagueContext, Roster
from app.model.live import SOURCES, Projection
from app.model.standings import (
    Matchups, Pairing, actual_points, lineup_for, schedule, team_week_projections, week_totals, win_probability,
)
from app.ui.layout import ensure_loaded, frame, on_refresh
from app.ui.state import AppState, get_state
from app.ui.theme import ACCENT, MUTED, POSITIVE, TEXT


@dataclass(frozen=True)
class WeekView:
    """Everything the page needs for one week, computed once."""
    week: int
    played: bool
    pairings: list[Pairing]
    totals: dict[str, dict[int, float]]   # source -> roster_id -> projected points
    actual: dict[int, float]
    names: dict[str, str]


def _roster(ctx: LeagueContext, roster_id: int) -> Roster:
    for roster in ctx.rosters:
        if roster.roster_id == roster_id:
            return roster
    raise ValueError(f"roster {roster_id} not in league")


def _other(source: str) -> str:
    return SOURCES[0] if source == SOURCES[1] else SOURCES[1]


def build_week(state: AppState, week: int) -> WeekView:
    ctx = state.ctx
    totals = {source: week_totals(team_week_projections(ctx, proj), week) for source, proj in state.projections.items()}
    values: pl.DataFrame = state.values
    names = dict(zip(values["sleeper_id"].to_list(), values["name"].to_list())) if not values.is_empty() else {}
    return WeekView(week=week, played=week < ctx.week, pairings=schedule(state.matchups, week), totals=totals,
                    actual=actual_points(state.matchups, week), names=names)


def _my_first(pairings: list[Pairing], me: int) -> list[Pairing]:
    mine = [p for p in pairings if me in (p.home, p.away)]
    return mine + [p for p in pairings if p not in mine]


def _score(view: WeekView, source: str, roster_id: int) -> float:
    if view.played:
        return view.actual.get(roster_id, 0.0)
    return view.totals[source].get(roster_id, 0.0)


def _favourite(view: WeekView, source: str, pairing: Pairing) -> tuple[int, float] | None:
    if pairing.away is None:
        return None
    home, away = _score(view, source, pairing.home), _score(view, source, pairing.away)
    fav = pairing.home if home >= away else pairing.away
    return fav, win_probability(max(home, away), min(home, away))


def _side(ctx: LeagueContext, proj: Projection, view: WeekView, source: str, roster: Roster) -> None:
    with ui.column().classes("flex-1 gap-1"):
        ui.label(roster.team_name).style(f"color:{TEXT};font-weight:600")
        headline = f"{_score(view, source, roster.roster_id):.1f} {'actual' if view.played else source}"
        ui.label(headline).style(f"color:{ACCENT};font-size:1.25rem")
        if not view.played:
            ui.label(f"{_other(source)} {view.totals[_other(source)].get(roster.roster_id, 0.0):.1f}").classes("ff-label")
        for slot, pid, pts in lineup_for(ctx, proj, roster, view.week):
            with ui.row().classes("justify-between w-full no-wrap"):
                ui.label(slot).classes("ff-label").style("min-width:5rem")
                ui.label(view.names.get(pid, pid) if pid else "—").style(f"color:{TEXT}")
                ui.label(f"{pts:.1f}").style(f"color:{MUTED}")


def _my_card(state: AppState, view: WeekView, pairing: Pairing, me: int) -> None:
    ctx, proj, source = state.ctx, state.primary(), state.source
    opp_id = pairing.away if pairing.home == me else pairing.home
    with ui.column().classes("ff-card q-pa-lg gap-3 w-full"):
        ui.label("MY MATCHUP").classes("ff-label")
        if opp_id is None:
            ui.label("Bye week").style(f"color:{MUTED}")
            return
        with ui.row().classes("gap-8 w-full no-wrap"):
            _side(ctx, proj, view, source, _roster(ctx, me))
            _side(ctx, proj, view, source, _roster(ctx, opp_id))
        fav = _favourite(view, source, pairing)
        if fav is not None and not view.played:
            prob = fav[1] if fav[0] == me else 1.0 - fav[1]
            ui.label(f"Win probability {prob:.0%}").style(f"color:{POSITIVE if prob >= 0.5 else MUTED}")
        elif view.played:
            winner = me if _score(view, source, me) > _score(view, source, opp_id) else opp_id
            ui.label(f"Winner: {_roster(ctx, winner).team_name}").style(f"color:{POSITIVE}")


def _compact_row(ctx: LeagueContext, view: WeekView, source: str, pairing: Pairing) -> None:
    home = _roster(ctx, pairing.home)
    with ui.row().classes("ff-card q-pa-sm items-center gap-4 w-full no-wrap"):
        ui.label(home.team_name).style(f"color:{TEXT};min-width:10rem")
        if pairing.away is None:
            ui.label("bye").classes("ff-label")
            return
        away = _roster(ctx, pairing.away)
        for rid in (pairing.home, pairing.away):
            text = f"{view.actual.get(rid, 0.0):.1f}" if view.played else \
                f"{view.totals['model'].get(rid, 0.0):.1f} / {view.totals['consensus'].get(rid, 0.0):.1f}"
            ui.label(text).style(f"color:{ACCENT};min-width:7rem;text-align:right")
            if rid == pairing.home:
                ui.label("vs").classes("ff-label")
        ui.label(away.team_name).style(f"color:{TEXT};min-width:10rem")
        fav = _favourite(view, source, pairing)
        if fav is not None:
            label = "won" if view.played else f"{fav[1]:.0%}"
            ui.chip(f"{_roster(ctx, fav[0]).team_name} {label}").props(f"color={ACCENT} outline").classes("text-xs")


def _week_select(ctx: LeagueContext, week: int) -> None:
    options = {w: f"Week {w}" for w in range(1, ctx.playoff_week_start)}
    ui.select(options, value=week, label="Week",
              on_change=lambda e: ui.navigate.to(f"/matchups?week={e.value}")).classes("min-w-32")


@ui.page("/matchups")
def matchups_page(week: int = 0) -> None:
    with frame("Matchups"):
        state = get_state()
        if not ensure_loaded(state):
            return
        ctx = state.ctx
        week = week or ctx.week
        _week_select(ctx, week)
        try:
            view = build_week(state, week)
        except Exception as exc:  # UI boundary: caught, recorded, and surfaced, never swallowed
            state.error = str(exc)
            ui.notify(f"Matchups failed: {exc}", type="negative")
            return
        if not view.pairings:
            ui.label(f"Schedule not published for week {week}").style(f"color:{MUTED}")
            ui.button("Refresh", on_click=on_refresh)
            return
        me = state.my_roster_id()
        ordered = _my_first(view.pairings, me)
        if me in (ordered[0].home, ordered[0].away):
            _my_card(state, view, ordered[0], me)
            ordered = ordered[1:]
        ui.label("Both projections shown as model / consensus").classes("ff-label")
        for pairing in ordered:
            _compact_row(ctx, view, state.source, pairing)
```

- [ ] **Step 3: Launch and check**

Run in the background: `FF_NATIVE=0 uv run python -m app run`. Open `http://127.0.0.1:8765/matchups`, then `/matchups?week=1`, then `/matchups?week=8`. Confirm:
- Current week: my card on top with a per-slot lineup for both sides, totals for the active source with the other source beneath, a win probability line. Other matchups as compact rows with `model / consensus` totals and a favourite chip.
- Week 1: actual scores, "won" chips, winner line on my card.
- Week 8: either pairings (Sleeper published the schedule) or the "Schedule not published" message with a Refresh button. Record which in the commit message.
- Server log has no traceback.

If a future week shows no pairings, run a refresh from the header and reload; the cache from Task 2 should now hold weeks through 14.

- [ ] **Step 4: Run tests and commit**

Run: `uv run pytest -q` → all pass.

```bash
git add app/ui/layout.py app/ui/matchups.py
git commit -m "Add matchups screen with weekly projections and lineups"
```

---

### Task 8: Settings screen

**Files:**
- Create: `app/ui/settings.py`

**Interfaces:**
- Consumes: `SAVED_KEYS`, `validate_settings`, `config_from_form`, `SOURCES`; `AppState.apply_settings`, `AppState.cfg`; `on_refresh` from layout.
- Produces: route `/settings`.

- [ ] **Step 1: Create the page**

Create `app/ui/settings.py`:

```python
"""Settings screen: edit config.local.toml and apply the change without a restart."""
from __future__ import annotations

import os

from nicegui import run, ui

from app.config import SAVED_KEYS, SOURCES, config_from_form, validate_settings
from app.ui.layout import frame, on_refresh
from app.ui.state import get_state
from app.ui.theme import MUTED, NEGATIVE, POSITIVE

MESSAGES = {
    "league": "League saved and reloaded",
    "history": "History range saved. Refresh to pull the new seasons.",
    "model": "Blend weights saved and projections recomputed",
    "source": "Default source saved",
    "none": "Nothing changed",
}
FIELD_HELP = {
    "league_id": "Sleeper league id (digits)",
    "username": "Your Sleeper display name; picks your roster on Trade, Finder, and Matchups",
    "source_default": "Projection source the app opens with",
    "stats_from": "First season of box-score history to load",
    "detail_from": "First season of play-by-play detail to load",
    "blend_k": "Shrinkage strength: games of evidence that equal the prior",
    "blend_a": "Weight on consensus rank within the prior",
    "blend_b": "Weight on last season within the prior",
}


@ui.page("/settings")
def settings_page() -> None:
    with frame("Settings"):
        state = get_state()
        form: dict[str, object] = {key: getattr(state.cfg, key) for key in SAVED_KEYS}

        if os.environ.get("FF_LEAGUE_ID"):
            ui.label("FF_LEAGUE_ID is set in the environment and overrides the saved league id on the next start.") \
                .style(f"color:{NEGATIVE}")

        with ui.column().classes("ff-card q-pa-lg gap-2").style("max-width:520px"):
            error_label = ui.label("").style(f"color:{NEGATIVE};white-space:pre-line")
            refresh_row = ui.row().classes("items-center gap-3")
            refresh_row.visible = False

            def check() -> bool:
                errors = validate_settings(form, state.cfg.current_season)
                error_label.text = "\n".join(f"{key}: {msg}" for key, msg in errors.items())
                save.enabled = not errors
                return not errors

            ui.input("League id", on_change=lambda _: check()).bind_value(form, "league_id").props("dark")
            ui.label(FIELD_HELP["league_id"]).classes("ff-label")
            ui.input("Username", on_change=lambda _: check()).bind_value(form, "username").props("dark")
            ui.label(FIELD_HELP["username"]).classes("ff-label")
            ui.select(list(SOURCES), label="Default source", on_change=lambda _: check()) \
                .bind_value(form, "source_default").props("dark")
            ui.label(FIELD_HELP["source_default"]).classes("ff-label")
            with ui.row().classes("gap-4"):
                ui.number("Stats from", format="%d", on_change=lambda _: check()).bind_value(form, "stats_from").props("dark")
                ui.number("Detail from", format="%d", on_change=lambda _: check()).bind_value(form, "detail_from").props("dark")
            ui.label(f"{FIELD_HELP['stats_from']}. {FIELD_HELP['detail_from']}.").classes("ff-label")
            with ui.row().classes("gap-4"):
                ui.number("Blend k", step=0.5, on_change=lambda _: check()).bind_value(form, "blend_k").props("dark")
                ui.number("Blend a", step=0.05, on_change=lambda _: check()).bind_value(form, "blend_a").props("dark")
                ui.number("Blend b", step=0.05, on_change=lambda _: check()).bind_value(form, "blend_b").props("dark")
            ui.label(f"{FIELD_HELP['blend_k']}. {FIELD_HELP['blend_a']}. {FIELD_HELP['blend_b']}.").classes("ff-label")

            async def on_save() -> None:
                if not check():
                    return
                new_cfg = config_from_form(form, state.cfg)
                save.enabled = False
                try:
                    scope = await run.io_bound(state.apply_settings, new_cfg)
                except Exception as exc:  # UI boundary: caught, recorded, and surfaced, never swallowed
                    state.error = str(exc)
                    ui.notify(f"Could not save settings: {exc}", type="negative")
                    return
                finally:
                    save.enabled = True
                state.error = None
                ui.notify(MESSAGES[scope], type="positive" if scope != "none" else "info")
                refresh_row.visible = scope == "history"

            save = ui.button("Save", on_click=on_save)
            with refresh_row:
                ui.label("New history range is saved but not loaded.").style(f"color:{MUTED}")
                ui.button("Refresh now", on_click=on_refresh).props(f"color={POSITIVE}")

        ui.label(f"Config file: {state.cfg.root / 'config.local.toml'}").classes("ff-label")
```

Notes for the implementer:
- `save` is referenced inside `check()` before it is created; that is fine because `check()` only runs on change events, which fire after the page is built. Do not move `check()` below `save`, or the inputs cannot reference it.
- `bind_value(form, key)` on a dict is supported by NiceGUI's binding (`nicegui.binding` handles `dict` targets via item access).
- The page deliberately does not call `ensure_loaded`, so it renders with no league loaded.

- [ ] **Step 2: Launch and check**

Run in the background: `FF_NATIVE=0 uv run python -m app run`. Open `http://127.0.0.1:8765/settings`. Confirm each of these, and fix anything that fails before committing:
1. Fields show the current values from `config.local.toml`.
2. Type `abc` in League id: an error line appears and Save is disabled. Restore the value: the error clears.
3. Set Detail from below Stats from: error; restore.
4. Change Blend k to 8 and Save: positive notification "Blend weights saved and projections recomputed"; `cat config.local.toml` shows `blend_k = 8.0`; open `/players` and confirm model numbers changed. Set it back to 4 and Save.
5. Change Default source to consensus and Save: header toggle shows consensus after a reload. Set back.
6. Change Stats from to 2013 and Save: the "Refresh now" row appears. Set back to 2012 and Save; the row hides.
7. Server log has no traceback.

- [ ] **Step 3: Run tests and commit**

Run: `uv run pytest -q` → all pass.

```bash
git add app/ui/settings.py
git commit -m "Add settings screen that saves and applies config"
```

---

### Task 9: Launch, drive every screen, fix

**Files:**
- Modify: whatever the drive reveals.

- [ ] **Step 1: Full suite**

Run: `uv run pytest -q` → all pass.

- [ ] **Step 2: Drive in browser mode**

Run in the background: `FF_NATIVE=0 uv run python -m app run`. With the browser tool, open each of `/league`, `/matchups`, `/matchups?week=1`, `/settings`, then the six existing routes, and take a screenshot of each. Check:
- Nav shows League and Matchups first, Settings last.
- League table sorts, highlights my row, both projection columns populated.
- Matchups shows my card first and compact rows after; week select navigates.
- Settings saves each scope as in Task 8 step 2.
- Header Refresh still works and, after it completes, `/matchups?week=10` shows pairings (confirms Sleeper publishes future weeks) or the not-published message (record which).
- No traceback in the server log across the whole drive.

- [ ] **Step 3: Fix, commit each fix with a plain message, then native launch**

Run: `uv run python -m app run` and confirm the native window opens and the League screen loads. Commit any final fix.

---

## Dependency graph for parallel execution

- Wave 1 (independent): Task 1, Task 2, Task 3.
- Wave 2: Task 4 (needs 3), Task 5 (needs 1, 3).
- Wave 3: Task 6 (needs 4, 5), Task 8 (needs 1, 5). Task 7 (needs 4, 5) can run alongside Task 6 but both touch `app/ui/layout.py`; run 6 first or merge carefully.
- Wave 4: Task 9 (needs everything).
