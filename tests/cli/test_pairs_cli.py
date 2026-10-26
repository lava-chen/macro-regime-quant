import sys

import numpy as np
import pandas as pd
import pytest
from mrq_cli.cli import _load_pair_universe, _parse_pair, build_parser, main


def test_pair_argument_is_read_as_two_symbols():
    assert _parse_pair("ko:pep") == ("KO", "PEP")
    assert _parse_pair("KO,PEP") == ("KO", "PEP")

    with pytest.raises(SystemExit):
        _parse_pair("KO")
    with pytest.raises(SystemExit):
        _parse_pair("KO:PEP:XOM")


def test_backtest_pair_defaults_are_explicit():
    args = build_parser().parse_args(["backtest-pair", "KO:PEP"])
    assert args.entry_z == 2.0
    assert args.exit_z == 0.5
    assert args.lookback == 60
    assert args.fee_bps == 5.0
    assert args.slippage_bps == 2.0
    assert args.borrow_bps == 50.0
    assert args.hedge_mode == "beta_neutral"
    assert args.output == "reports/pairs"


def test_pair_universe_is_read_from_yaml(tmp_path):
    path = tmp_path / "pairs.yaml"
    path.write_text("pairs:\n  - [KO, PEP]\n  - [PG, CL]\n", encoding="utf-8")

    assert _load_pair_universe(path) == [("KO", "PEP"), ("PG", "CL")]

    empty = tmp_path / "empty.yaml"
    empty.write_text("pairs: []\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        _load_pair_universe(empty)


def test_walk_forward_screens_on_one_window_and_trades_the_next(monkeypatch, tmp_path):
    index = pd.date_range("2010-01-01", periods=900, freq="B")
    rng = np.random.default_rng(4)
    common = rng.normal(0.0, 0.010, 900).cumsum()
    prices = pd.DataFrame(
        {
            "KO": 60.0 * np.exp(0.4 * common + rng.normal(0.0, 0.010, 900)),
            "PEP": 45.0 * np.exp(0.4 * common + rng.normal(0.0, 0.010, 900)),
        },
        index=index,
    )
    monkeypatch.setattr(
        "mrq_cli.cli.load_market_prices",
        lambda symbols, start=None, end=None, **kwargs: (prices, []),
    )

    pairs_file = tmp_path / "pairs.yaml"
    pairs_file.write_text("pairs:\n  - [KO, PEP]\n", encoding="utf-8")
    output = tmp_path / "out"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "macro-regime-quant",
            "walk-forward-pairs",
            "--pairs-file",
            str(pairs_file),
            "--is-start",
            "2010-01-01",
            "--is-end",
            "2012-12-31",
            "--oos-start",
            "2013-01-01",
            "--oos-end",
            "2015-12-31",
            "--lookback",
            "60",
            "--max-half-life",
            "100000",
            "--output",
            str(output),
        ],
    )
    main()

    assert (output / "walk_forward_results.csv").is_file()
    assert (output / "walk_forward_screen.csv").is_file()
    results = pd.read_csv(output / "walk_forward_results.csv")
    assert list(results["pair"]) == ["KO/PEP"]


def test_backtest_pair_writes_a_report_and_a_timeseries(monkeypatch, tmp_path):
    index = pd.date_range("2019-01-01", periods=600, freq="B")
    rng = np.random.default_rng(4)
    common = rng.normal(0.0, 0.010, 600).cumsum()
    prices = pd.DataFrame(
        {
            "KO": 60.0 * np.exp(0.4 * common + rng.normal(0.0, 0.010, 600)),
            "PEP": 45.0 * np.exp(0.4 * common + rng.normal(0.0, 0.010, 600)),
        },
        index=index,
    )
    monkeypatch.setattr(
        "mrq_cli.cli.load_market_prices",
        lambda symbols, start=None, end=None, **kwargs: (prices, []),
    )

    output = tmp_path / "pairs"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "macro-regime-quant",
            "backtest-pair",
            "KO:PEP",
            "--entry-z",
            "1.5",
            "--output",
            str(output),
        ],
    )
    main()

    assert (output / "KO_PEP_report.md").is_file()
    assert (output / "KO_PEP_report.json").is_file()
    timeseries = pd.read_csv(output / "KO_PEP_timeseries.csv", index_col=0)
    assert {"zscore", "position", "equity"}.issubset(timeseries.columns)
