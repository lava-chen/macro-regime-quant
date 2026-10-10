import pytest
from mrq_cli.backtest_workbench.prices import load_market_prices, market_data_status


def test_local_price_snapshot_precedes_network_and_is_inclusive(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "data_catalog.yaml").write_text("series: {}\n", encoding="utf-8")
    (tmp_path / "data" / "raw" / "market").mkdir(parents=True)
    (tmp_path / "data" / "raw" / "market" / "QQQ.csv").write_text(
        "date,adjusted_close\n2025-01-01,100\n2025-01-02,101\n2025-01-03,102\n",
        encoding="utf-8",
    )
    (tmp_path / "data" / "raw" / "market" / "QQQ.csv.meta.json").write_text(
        '{"origin_provider":"yahoo_finance","source_url":"https://finance.yahoo.com/quote/QQQ/history/","retrieved_at":"2025-01-04T00:00:00Z"}',
        encoding="utf-8",
    )
    prices, sources = load_market_prices(
        ["QQQ"], start="2025-01-02", end="2025-01-02", project_root=tmp_path
    )
    assert list(prices["QQQ"]) == [101.0]
    assert sources[0].provider == "local_csv"
    assert sources[0].first_date == "2025-01-02"
    assert sources[0].content_sha256
    assert sources[0].origin_provider == "yahoo_finance"
    assert sources[0].source_url == "https://finance.yahoo.com/quote/QQQ/history/"
    assert sources[0].retrieved_at == "2025-01-04T00:00:00Z"


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


def test_market_data_status_distinguishes_snapshot_from_configured_provider(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "data_catalog.yaml").write_text(
        """series:
  gold:
    provider: yahoo
    symbol: GLD
    kind: commodity
    frequency: daily
  nasdaq:
    provider: yahoo
    symbol: QQQ
    kind: market
    frequency: daily
""",
        encoding="utf-8",
    )
    market = tmp_path / "data" / "raw" / "market"
    market.mkdir(parents=True)
    snapshot = market / "GLD.csv"
    snapshot.write_text(
        "date,adjusted_close\n2025-01-02,100\n2025-01-03,101\n",
        encoding="utf-8",
    )
    (market / "GLD.csv.meta.json").write_text(
        '{"source_url":"https://finance.yahoo.com/quote/GLD/history/",'
        '"retrieved_at":"2025-01-04T00:00:00Z"}',
        encoding="utf-8",
    )

    status = market_data_status(tmp_path)
    assets = {asset["symbol"]: asset for asset in status["assets"]}

    assert assets["GLD"]["availability"] == "snapshot_available"
    assert assets["GLD"]["coverage_start"] == "2025-01-02"
    assert assets["GLD"]["observations"] == 2
    assert assets["GLD"]["quality"] == "schema_validated_no_fills"
    assert assets["GLD"]["point_in_time_vintage"] is False
    assert assets["QQQ"]["availability"] == "provider_configured"
    assert assets["QQQ"]["coverage_start"] is None
    assert assets["QQQ"]["quality"] == "not_fetched"


def test_market_data_status_hashes_invalid_snapshot_without_exposing_price_rows(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "data_catalog.yaml").write_text(
        """series:
  gold:
    provider: yahoo
    symbol: GLD
    kind: commodity
    frequency: daily
""",
        encoding="utf-8",
    )
    market = tmp_path / "data" / "raw" / "market"
    market.mkdir(parents=True)
    invalid = market / "GLD.csv"
    invalid.write_text("not,a,price,file\n", encoding="utf-8")

    result = market_data_status(tmp_path)["assets"][0]

    assert result["availability"] == "invalid_snapshot"
    assert result["quality"] == "failed_validation"
    assert result["content_sha256"]
    assert "not,a,price,file" not in str(result)
