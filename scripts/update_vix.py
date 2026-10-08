"""Refresh CBOE VIX history and normalized point-in-time research snapshots.

Inspired by https://github.com/datasets/finance-vix, but validates the complete
CBOE history and emits MRQ-compatible close series. Stdlib only.
"""
from __future__ import annotations

import argparse
import csv
import io
import sys
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.request import Request, urlopen

CBOE_VIX_URL = "https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv"
HEADERS = ("DATE", "OPEN", "HIGH", "LOW", "CLOSE")
SNAPSHOT_HEADER = "observation_date,value,available_date,availability_basis"


def _parse_date(value: str) -> date:
    cleaned = value.strip()
    try:
        return date.fromisoformat(cleaned)
    except ValueError:
        try:
            month, day, year = (int(part) for part in cleaned.split("/"))
            return date(year, month, day)
        except ValueError as exc:
            raise ValueError(f"Invalid VIX trading date: {value!r}") from exc


def parse_daily(source_csv: str, *, min_rows: int = 5000) -> list[tuple[date, ...]]:
    """Parse complete OHLC history; refuse truncated, duplicate or malformed data."""
    reader = csv.DictReader(io.StringIO(source_csv.lstrip("\ufeff")))
    if not reader.fieldnames or tuple(col.strip().upper() for col in reader.fieldnames) != HEADERS:
        raise ValueError(f"Expected CBOE VIX columns {HEADERS}, got {reader.fieldnames}")
    records: list[tuple[date, ...]] = []
    observed: set[date] = set()
    for row in reader:
        trading_date = _parse_date(row["DATE"])
        if trading_date in observed:
            raise ValueError(f"Duplicate trading date: {trading_date}")
        observed.add(trading_date)
        values = []
        for field in HEADERS[1:]:
            try:
                value = Decimal(row[field].strip())
            except (InvalidOperation, AttributeError) as exc:
                raise ValueError(f"Invalid {field} for {trading_date}") from exc
            if not value.is_finite() or value <= 0:
                raise ValueError(f"Non-positive or non-finite {field} for {trading_date}")
            values.append(value)
        if values[1] < values[2]:
            raise ValueError(f"HIGH below LOW for {trading_date}")
        records.append((trading_date, *values))
    if len(records) < min_rows:
        raise ValueError(f"Refusing incomplete VIX history: only {len(records)} rows")
    records.sort(key=lambda record: record[0])
    return records


def _daily_text(records: list[tuple[date, ...]]) -> str:
    output = io.StringIO()
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(HEADERS)
    for trading_date, *values in records:
        writer.writerow([trading_date.isoformat(), *(f"{v:.6f}" for v in values)])
    return output.getvalue()


def _monthly(records: list[tuple[date, ...]]) -> list[tuple[date, Decimal]]:
    by_month: dict[str, tuple[date, Decimal]] = {}
    for trading_date, _open, _high, _low, close in records:
        by_month[trading_date.strftime("%Y-%m")] = (trading_date, close)
    return [by_month[month] for month in sorted(by_month)]


def _monthly_text(monthly: list[tuple[date, Decimal]]) -> str:
    output = io.StringIO()
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(("Date", "Close"))
    for trading_date, close in monthly:
        writer.writerow((trading_date.isoformat(), f"{close:.6f}"))
    return output.getvalue()


def _close_text(records: list[tuple[date, Decimal]]) -> str:
    output = [SNAPSHOT_HEADER]
    for trading_date, close in records:
        # VIX close is observable after the trading session, not before it.
        # A conservative +1 calendar day is an estimate, NOT verified publication time.
        available_date = trading_date + timedelta(days=1)
        output.append(f"{trading_date.isoformat()},{close:.6f},"
                      f"{available_date.isoformat()},fixed_lag")
    return "\n".join(output) + "\n"


def generate_payloads(source_csv: str, *, min_rows: int = 5000) -> dict[str, str]:
    daily = parse_daily(source_csv, min_rows=min_rows)
    monthly = _monthly(daily)
    # The most recent month may be in progress. Only publish completed-month
    # research observations once a newer month has at least one trading record.
    completed_months = monthly[:-1]
    return {
        "vix-daily.csv": _daily_text(daily),
        "vix-monthly.csv": _monthly_text(monthly),
        "vix_close.csv": _close_text([(r[0], r[4]) for r in daily]),
        "vix_monthly_close.csv": _close_text(completed_months),
    }


def check_existing_history(output_dir: Path, new_payloads: dict[str, str]) -> None:
    old_path = output_dir / "vix-daily.csv"
    if not old_path.exists():
        return
    old = parse_daily(old_path.read_text(encoding="utf-8"), min_rows=1)
    new = parse_daily(new_payloads["vix-daily.csv"], min_rows=1)
    if len(new) < len(old) or new[0][0] > old[0][0] or new[-1][0] < old[-1][0]:
        raise ValueError("Refusing to replace committed VIX history with shorter/stale history")


def fetch_source() -> str:
    req = Request(CBOE_VIX_URL, headers={"User-Agent": "macro-regime-quant VIX sync"})
    with urlopen(req, timeout=45) as response:
        return response.read().decode("utf-8-sig")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Refresh complete CBOE VIX OHLC history")
    parser.add_argument("--source", type=Path, help="Use a local CSV instead of downloading")
    parser.add_argument("--output-dir", type=Path, default=Path("data/raw/vix"))
    args = parser.parse_args(argv)

    raw = args.source.read_text(encoding="utf-8-sig") if args.source else fetch_source()
    payloads = generate_payloads(raw)
    check_existing_history(args.output_dir, payloads)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for name, payload in payloads.items():
        (args.output_dir / name).write_text(payload, encoding="utf-8", newline="")
    records = parse_daily(payloads["vix-daily.csv"])
    print(f"CBOE VIX: {len(records)} days, {records[0][0]} to {records[-1][0]}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, OSError) as exc:
        print(f"VIX update failed: {exc}", file=sys.stderr)
        sys.exit(1)
