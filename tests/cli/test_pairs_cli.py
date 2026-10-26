import sys

import numpy as np
import pandas as pd
import pytest
from mrq_cli.cli import _parse_pair, build_parser, main


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


def test_backtest_pair_writes_a_report_and_a_timeseries(monkeypatch, tmp_path):
    index = pd.date_range("2019-01-01", periods=600, freq="B")
    rng = np.random.default_rng(4)
    common = rng.normal(0.0, 1.0, 600).cumsum()
    prices = pd.DataFrame(
        {
            "KO": 60.0 * np.exp(0.4 * common + rng.normal(0.0, 0.08, 600)),
            "PEP": 45.0 * np.exp(0.4 * common + rng.normal(0.0, 0.08, 600)),
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
