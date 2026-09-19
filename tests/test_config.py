from datetime import date
from pathlib import Path

from app.config import Config, SAVED_KEYS, config_from_form, current_nfl_season, load_config, save_config, split_names, validate_settings


def test_current_nfl_season_rolls_in_september() -> None:
    assert current_nfl_season(date(2026, 9, 16)) == 2026
    assert current_nfl_season(date(2026, 3, 1)) == 2025


def test_load_config_reads_toml_and_env_override(tmp_path: Path) -> None:
    (tmp_path / "config.local.toml").write_text('league_id = "111"\n')
    cfg = load_config(tmp_path, env={})
    assert cfg.league_id == "111"
    assert cfg.cache_dir == tmp_path / "cache"
    assert cfg.duckdb_path == tmp_path / "cache" / "stats.duckdb"
    assert cfg.sqlite_path == tmp_path / "cache" / "app.sqlite"
    over = load_config(tmp_path, env={"FF_LEAGUE_ID": "222"})
    assert over.league_id == "222"


def test_load_config_without_file_has_empty_league(tmp_path: Path) -> None:
    cfg = load_config(tmp_path, env={})
    assert cfg.league_id == ""
    assert cfg.stats_from == 2012
    assert cfg.detail_from == 2019


def test_load_config_ff_root_overrides_the_root(tmp_path: Path) -> None:
    other_root = tmp_path / "other_root"
    other_root.mkdir()
    (other_root / "config.local.toml").write_text('league_id = "333"\n')
    cfg = load_config(tmp_path, env={"FF_ROOT": str(other_root)})
    assert cfg.root == other_root
    assert cfg.league_id == "333"
    assert cfg.cache_dir == other_root / "cache"
    assert cfg.duckdb_path == other_root / "cache" / "stats.duckdb"
    assert cfg.sqlite_path == other_root / "cache" / "app.sqlite"


def test_load_config_reads_username_from_toml(tmp_path: Path) -> None:
    (tmp_path / "config.local.toml").write_text('username = "garretn4"\n')
    cfg = load_config(tmp_path, env={})
    assert cfg.usernames == ("garretn4",)


def test_load_config_username_defaults_to_empty(tmp_path: Path) -> None:
    cfg = load_config(tmp_path, env={})
    assert cfg.usernames == ()


def _cfg(root: Path, **over: object) -> Config:
    base = dict(league_id="123", root=root, stats_from=2015, detail_from=2020, current_season=2026,
                usernames=("ann",), source_default="consensus", blend_k=6.0, blend_a=0.4, blend_b=0.7)
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
    form: dict[str, object] = dict(league_id="123", usernames="ann", source_default="model", stats_from=2015,
                                   detail_from=2020, blend_k=4.0, blend_a=0.6, blend_b=0.5)
    form.update(over)
    return form


def test_validate_settings_accepts_a_good_form() -> None:
    assert validate_settings(_form(), current_season=2026) == {}


def test_validate_settings_rejects_bad_fields() -> None:
    errors = validate_settings(_form(source_default="magic", stats_from=2021, detail_from=2020,
                                     blend_k=0.0, blend_a=1.5, blend_b=-0.1), current_season=2026)
    assert set(errors) == {"source_default", "stats_from", "blend_k", "blend_a", "blend_b"}


def test_validate_settings_accepts_a_blank_league_id_on_first_run() -> None:
    assert validate_settings(_form(league_id="", usernames="ann"), current_season=2026) == {}


def test_validate_settings_rejects_missing_numbers() -> None:
    errors = validate_settings(_form(stats_from=None, blend_k=None), current_season=2026)
    assert "stats_from" in errors and "blend_k" in errors


def test_config_from_form_keeps_root_and_season(tmp_path: Path) -> None:
    base = _cfg(tmp_path)
    cfg = config_from_form(_form(league_id=" 777 ", usernames=" bob ", blend_k="8"), base)
    assert cfg.root == tmp_path and cfg.current_season == 2026
    assert (cfg.league_id, cfg.usernames, cfg.blend_k) == ("777", ("bob",), 8.0)


def test_split_names_from_string_or_sequence() -> None:
    assert split_names(" ann , bob,,") == ("ann", "bob")
    assert split_names(("ann", " bob ")) == ("ann", "bob")
    assert split_names(None) == () and split_names("") == ()
    assert split_names(5) == () and split_names(True) == ()


def test_load_config_reads_usernames_array_and_lifts_legacy_username(tmp_path: Path) -> None:
    (tmp_path / "config.local.toml").write_text('usernames = ["ann", "bob"]\n')
    assert load_config(tmp_path, env={}).usernames == ("ann", "bob")
    (tmp_path / "config.local.toml").write_text('username = "solo"\n')
    assert load_config(tmp_path, env={}).usernames == ("solo",)
    (tmp_path / "config.local.toml").write_text("league_id = \"1\"\n")
    assert load_config(tmp_path, env={}).usernames == ()


def test_save_config_writes_usernames_as_an_array(tmp_path: Path) -> None:
    save_config(tmp_path, _cfg(tmp_path, usernames=("ann", "bob")))
    text = (tmp_path / "config.local.toml").read_text()
    assert 'usernames = ["ann", "bob"]' in text and "username =" not in text.replace("usernames =", "")
    assert load_config(tmp_path, env={}).usernames == ("ann", "bob")


def test_validate_settings_requires_a_username() -> None:
    assert "usernames" in validate_settings(_form(usernames=" , "), current_season=2026)
    assert validate_settings(_form(usernames="ann, bob"), current_season=2026) == {}


def test_config_from_form_parses_usernames(tmp_path: Path) -> None:
    assert config_from_form(_form(usernames="ann, bob"), _cfg(tmp_path)).usernames == ("ann", "bob")
