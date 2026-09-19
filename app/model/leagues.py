"""Which leagues the configured Sleeper users are in, from the API or the cache."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from app.data.store import Store
from app.model.league import _cached_json

DYNASTY_TYPES = (1, 2)
DEFAULT_DRAFT_ROUNDS = 4
TYPE_NAMES = {0: "redraft", 1: "keeper", 2: "dynasty"}


@dataclass(frozen=True)
class LeagueSummary:
    league_id: str
    name: str
    season: int
    status: str
    n_teams: int
    league_type: int
    draft_rounds: int
    members: tuple[str, ...]

    @property
    def dynasty(self) -> bool:
        return self.league_type in DYNASTY_TYPES

    @property
    def label(self) -> str:
        kind = TYPE_NAMES.get(self.league_type, f"type {self.league_type}")
        phase = "pre-draft " if self.status == "pre_draft" else ""
        return f"{self.name} · {phase}{kind} {self.n_teams}"


class LeagueSource(Protocol):
    def user(self, username: str) -> dict[str, Any] | None: ...
    def user_leagues(self, user_id: str, season: int) -> list[dict[str, Any]]: ...


class CachedSource:
    """LeagueSource over the HTTP cache only: never touches the network."""

    def __init__(self, store: Store) -> None:
        self._store = store

    def user(self, username: str) -> dict[str, Any] | None:
        return _cached_json(self._store, f"sleeper:/user/{username}")

    def user_leagues(self, user_id: str, season: int) -> list[dict[str, Any]]:
        return _cached_json(self._store, f"sleeper:/user/{user_id}/leagues/nfl/{season}") or []


def _summary(raw: dict[str, Any], members: tuple[str, ...]) -> LeagueSummary:
    settings = raw.get("settings") or {}
    return LeagueSummary(
        league_id=str(raw["league_id"]), name=str(raw.get("name") or raw["league_id"]),
        season=int(raw.get("season") or 0), status=str(raw.get("status") or ""),
        n_teams=int(raw.get("total_rosters") or 0), league_type=int(settings.get("type", 0)),
        draft_rounds=int(settings.get("draft_rounds", DEFAULT_DRAFT_ROUNDS)), members=members,
    )


def discover(source: LeagueSource, usernames: tuple[str, ...], season: int) -> tuple[list[LeagueSummary], list[str]]:
    """Union of every user's leagues, first appearance first; unknown users are reported, not fatal."""
    raws: dict[str, dict[str, Any]] = {}
    members: dict[str, list[str]] = {}
    errors: list[str] = []
    for name in usernames:
        user = source.user(name)
        if not user:
            errors.append(f"user {name}: not found")
            continue
        for raw in source.user_leagues(str(user["user_id"]), season):
            league_id = str(raw["league_id"])
            raws.setdefault(league_id, raw)
            members.setdefault(league_id, []).append(name)
    return [_summary(raw, tuple(members[league_id])) for league_id, raw in raws.items()], errors


def load_summaries(store: Store, usernames: tuple[str, ...], season: int) -> list[LeagueSummary]:
    return discover(CachedSource(store), usernames, season)[0]
