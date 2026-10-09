import pytest
from mrq_cli.backtest_workbench.prices import load_market_prices


def test_local_price_snapshot_precedes_network_and_is_inclusive(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "data_catalog.yaml").write_text("series: {}\n", encoding="utf-8")
    (tmp_path / "data" / "raw" / "market").mkdir(parents=True)
    (tmp_path / "data" / "raw" / "market" / "QQQ.csv").write_text(
        "date,adjusted_close\n2025-01-01,100\n2025-01-02,101\n2025-01-03,102\n",
        encoding="utf-8",
    )
    prices, sources = load_market_prices(
        ["QQQ"], start="2025-01-02", end="2025-01-02", project_root=tmp_path
    )
    assert list(prices["QQQ"]) == [101.0]
    assert sources[0].provider == "local_csv"
    assert sources[0].first_date == "2025-01-02"
    assert sources[0].content_sha256


def test_unknown_symbol_requires_catalog_entry_or_normalized_csv(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "data_catalog.yaml").write_text("series: {}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="no configured market series"):
        load_market_prices(["UNKNOWN"], project_root=tmp_path)


def test_local_price_snapshot_rejects_duplicate_dates(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "data_catalog.yaml").write_text("series: {}\n", encoding="utf-8")
    path = tmp_path / "data" / "raw" / "market"
    path.mkdir(parents=True)
    (path / "GLD.csv").write_text(
        "date,close\n2025-01-01,10\n2025-01-01,11\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="duplicate dates"):
        load_market_prices(["GLD"], project_root=tmp_path)
