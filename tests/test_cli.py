from pathlib import Path

import httpx

import app.__main__ as cli_module
from app.__main__ import build_parser, cmd_refresh, week_range
from app.config import Config


def test_backtest_parser_defaults() -> None:
    args = build_parser().parse_args(["backtest"])
    assert args.tune_seasons == [2021, 2022, 2023]
    assert args.eval_seasons == [2024, 2025]
    assert args.weeks == "2-14"
    assert args.quick is False


def test_week_range_parses_inclusive_span() -> None:
    assert week_range("2-4") == [2, 3, 4]
    assert week_range("7") == [7]


class _DownSleeperClient:
    def __init__(self, store: object, refresh: bool = False) -> None:
        pass

    def state(self) -> dict[str, object]:
        raise httpx.ConnectError("down")


def test_cmd_refresh_reports_sleeper_failure(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli_module, "SleeperClient", _DownSleeperClient)
    cfg = Config(league_id="1", root=tmp_path, stats_from=2025, detail_from=2025, current_season=2025)
    result = cmd_refresh(cfg)
    assert result == 1
    assert "refresh failed" in capsys.readouterr().out
