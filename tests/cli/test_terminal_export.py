from __future__ import annotations

import json

from mrq_cli.terminal_export import PartialSourceError, SourceResult, export_terminal_data


def _record(series_id: str, domain: str, value: float) -> dict:
    return {
        "id": series_id,
        "domain": domain,
        "points": [{"date": "2026-01-31", "value": value}],
    }


def test_failed_source_keeps_last_good_series_and_records_failure(tmp_path) -> None:
    output = tmp_path / "exports"
    output.mkdir()
    previous = {
        "schema_version": "1.0",
        "generated_at": "2026-01-01T00:00:00Z",
        "series": [_record("market:SPY", "markets", 100.0)],
    }
    (output / "series.json").write_text(json.dumps(previous), encoding="utf-8")
    (output / "regimes.json").write_text(
        json.dumps({"countries": [{"country": "United States", "history": []}]}),
        encoding="utf-8",
    )
    (output / "summary.json").write_text(
        json.dumps({"sources": {"market_prices": {"last_success_at": "2025-12-31T00:00:00Z"}}}),
        encoding="utf-8",
    )

    def market_failure() -> SourceResult:
        raise RuntimeError("rate limited")

    def china_success() -> SourceResult:
        return SourceResult(series=[_record("factor:china:growth", "macro_china", 0.5)])

    result = export_terminal_data(
        output,
        skip_us=True,
        attempted_at="2026-01-02T00:00:00Z",
        source_builders={"market_prices": market_failure, "china_macro": china_success},
    )

    series = json.loads((output / "series.json").read_text(encoding="utf-8"))["series"]
    assert {item["id"] for item in series} == {"market:SPY", "factor:china:growth"}
    assert next(item for item in series if item["id"] == "market:SPY")["points"][0]["value"] == 100.0
    assert result["sources"]["market_prices"]["state"] == "failed"
    assert result["sources"]["market_prices"]["last_success_at"] == "2025-12-31T00:00:00Z"
    assert result["status"] == "partial"


def test_partial_market_refresh_keeps_symbols_not_returned(tmp_path) -> None:
    output = tmp_path / "exports"
    output.mkdir()
    old_records = [
        _record("market:SPY", "markets", 100.0),
        _record("market:QQQ", "markets", 200.0),
    ]
    (output / "series.json").write_text(
        json.dumps({"series": old_records}), encoding="utf-8"
    )

    def partial_with_one_symbol() -> SourceResult:
        raise PartialSourceError(
            "Yahoo omitted QQQ",
            partial=SourceResult(series=[_record("market:SPY", "markets", 101.0)]),
        )

    def china_success() -> SourceResult:
        return SourceResult(series=[_record("factor:china:growth", "macro_china", 0.5)])

    result = export_terminal_data(
        output,
        skip_us=True,
        attempted_at="2026-01-02T00:00:00Z",
        source_builders={
            "market_prices": partial_with_one_symbol,
            "china_macro": china_success,
        },
    )
    series = json.loads((output / "series.json").read_text(encoding="utf-8"))["series"]
    by_id = {item["id"]: item for item in series}
    assert by_id["market:SPY"]["points"][0]["value"] == 101.0
    assert by_id["market:QQQ"]["points"][0]["value"] == 200.0
    assert result["sources"]["market_prices"]["state"] == "failed"
