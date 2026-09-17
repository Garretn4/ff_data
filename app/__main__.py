"""Command line entry: refresh, export, backtest."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import httpx

from app.config import Config, load_config
from app.data.export import export_practice
from app.data.nflverse import connect
from app.data.store import Store
from app.model.backtest import BlendParams, DEFAULT_GRID, gate_passes, run_backtest, tune, write_report
from app.refresh import refresh
from app.sleeper import SleeperClient

ROOT = Path(__file__).resolve().parent.parent

QUICK_GRID = [BlendParams(k=4.0, a=0.5, b=0.5), BlendParams(k=8.0, a=0.5, b=0.5)]


def _require_league(cfg: Config) -> None:
    if not cfg.league_id:
        sys.exit("No league id. Put league_id = \"...\" in config.local.toml or set FF_LEAGUE_ID.")


def cmd_refresh(cfg: Config) -> int:
    _require_league(cfg)
    store = Store(cfg.sqlite_path)
    con = connect(cfg.duckdb_path)
    client = SleeperClient(store, refresh=True)
    summary = refresh(cfg, client, con, store, httpx.Client(timeout=30.0))
    print(f"{summary.league_name}: week {summary.week}")
    for table, rows in sorted(summary.tables.items()):
        print(f"  {table:<20} {rows:>8} rows")
    print(f"  market snapshot      {summary.snapshot_rows:>8} rows")
    if summary.unmapped_scoring:
        print(f"  unmapped scoring keys: {', '.join(summary.unmapped_scoring)}")
    for err in summary.errors:
        print(f"  error: {err}")
    return 1 if summary.errors else 0


def cmd_export(cfg: Config, out: Path) -> int:
    con = connect(cfg.duckdb_path)
    counts = export_practice(con, out)
    for table, rows in counts.items():
        print(f"  {table:<20} {rows:>8} rows")
    print(f"written to {out}")
    return 0


def week_range(spec: str) -> list[int]:
    if "-" in spec:
        lo, hi = spec.split("-", 1)
        return list(range(int(lo), int(hi) + 1))
    return [int(spec)]


def _league_settings(cfg: Config) -> tuple[dict[str, float], list[str], int]:
    store = Store(cfg.sqlite_path)
    body = store.get_cached(f"sleeper:/league/{cfg.league_id}", None, 0.0)
    if body is None:
        sys.exit("League not cached. Run `python -m app refresh` first.")
    league = json.loads(body)
    return league["scoring_settings"], list(league["roster_positions"]), int(league["total_rosters"])


def cmd_backtest(cfg: Config, tune_seasons: list[int], eval_seasons: list[int], weeks: list[int], quick: bool) -> int:
    _require_league(cfg)
    scoring, slots, n_teams = _league_settings(cfg)
    con = connect(cfg.duckdb_path)
    grid = QUICK_GRID if quick else DEFAULT_GRID
    params = tune(con, scoring, slots, n_teams, tune_seasons, weeks, grid)
    metrics = run_backtest(con, scoring, slots, n_teams, eval_seasons, weeks, params)
    passed = gate_passes(metrics, eval_seasons)
    out_dir = ROOT / "docs" / "backtest"
    write_report(metrics, params, out_dir / "REPORT.md", passed)
    metrics.write_parquet(out_dir / "metrics.parquet")
    print((out_dir / "REPORT.md").read_text())
    return 0 if passed else 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ff-analyst")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("refresh")
    export = sub.add_parser("export")
    export.add_argument("--out", type=Path, default=Path.home() / "ff-analyst-data")
    bt = sub.add_parser("backtest")
    bt.add_argument("--tune-seasons", type=int, nargs="+", default=[2021, 2022, 2023])
    bt.add_argument("--eval-seasons", type=int, nargs="+", default=[2024, 2025])
    bt.add_argument("--weeks", default="2-14")
    bt.add_argument("--quick", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cfg = load_config(ROOT, os.environ)
    if args.command == "refresh":
        return cmd_refresh(cfg)
    if args.command == "export":
        return cmd_export(cfg, args.out)
    return cmd_backtest(cfg, args.tune_seasons, args.eval_seasons, week_range(args.weeks), args.quick)


if __name__ == "__main__":
    sys.exit(main())
