from app.__main__ import build_parser, week_range


def test_backtest_parser_defaults() -> None:
    args = build_parser().parse_args(["backtest"])
    assert args.tune_seasons == [2021, 2022, 2023]
    assert args.eval_seasons == [2024, 2025]
    assert args.weeks == "2-14"
    assert args.quick is False


def test_week_range_parses_inclusive_span() -> None:
    assert week_range("2-4") == [2, 3, 4]
    assert week_range("7") == [7]
