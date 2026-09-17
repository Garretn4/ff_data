"""Command line entry: refresh, export, backtest."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import httpx

from app.config import Config, load_config
from app.data.export import export_practice
from app.data.nflverse import connect
from app.data.store import Store
from app.refresh import refresh
from app.sleeper import SleeperClient

ROOT = Path(__file__).resolve().parent.parent


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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ff-analyst")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("refresh")
    export = sub.add_parser("export")
    export.add_argument("--out", type=Path, default=Path.home() / "ff-analyst-data")
    args = parser.parse_args(argv)
    cfg = load_config(ROOT, os.environ)
    if args.command == "refresh":
        return cmd_refresh(cfg)
    return cmd_export(cfg, args.out)


if __name__ == "__main__":
    sys.exit(main())
