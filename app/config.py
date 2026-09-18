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
    "league_id", "usernames", "source_default", "stats_from", "detail_from", "blend_k", "blend_a", "blend_b",
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
    usernames: tuple[str, ...] = ()
    source_default: str = SOURCE_DEFAULT
    blend_k: float = 4.0
    blend_a: float = 0.6
    blend_b: float = 0.5

    @property
    def blend(self) -> BlendParams:
        from app.model.projections import BlendParams  # local import: app.model imports app.config indirectly

        return BlendParams(k=self.blend_k, a=self.blend_a, b=self.blend_b)

    @property
    def cache_dir(self) -> Path:
        return self.root / "cache"

    @property
    def duckdb_path(self) -> Path:
        return self.cache_dir / "stats.duckdb"

    @property
    def sqlite_path(self) -> Path:
        return self.cache_dir / "app.sqlite"

    @property
    def stats_seasons(self) -> list[int]:
        return list(range(self.stats_from, self.current_season + 1))

    @property
    def detail_seasons(self) -> list[int]:
        return list(range(self.detail_from, self.current_season + 1))


def split_names(value: object) -> tuple[str, ...]:
    """Usernames from a comma-separated string or any sequence; blanks dropped, order kept."""
    if not isinstance(value, (str, list, tuple)):
        return ()
    parts = value.split(",") if isinstance(value, str) else list(value)  # type: ignore[call-overload]
    return tuple(p for p in (str(x).strip() for x in parts) if p)


def _read_toml(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    with path.open("rb") as fh:
        return tomllib.load(fh)


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
        usernames=split_names(data.get("usernames", data.get("username"))),
        source_default=str(data.get("source_default", SOURCE_DEFAULT)),
        blend_k=float(data.get("blend_k", 4.0)),
        blend_a=float(data.get("blend_a", 0.6)),
        blend_b=float(data.get("blend_b", 0.5)),
    )


def _toml_scalar(value: object) -> str:
    if isinstance(value, (tuple, list)):
        return "[" + ", ".join(json.dumps(str(v)) for v in value) + "]"
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
    if not split_names(form.get("usernames")):
        errors["usernames"] = "at least one Sleeper username"
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
        usernames=split_names(form.get("usernames")),
        source_default=str(form["source_default"]),
        stats_from=int(form["stats_from"]),  # type: ignore[call-overload]
        detail_from=int(form["detail_from"]),  # type: ignore[call-overload]
        blend_k=float(form["blend_k"]),  # type: ignore[arg-type]
        blend_a=float(form["blend_a"]),  # type: ignore[arg-type]
        blend_b=float(form["blend_b"]),  # type: ignore[arg-type]
    )
