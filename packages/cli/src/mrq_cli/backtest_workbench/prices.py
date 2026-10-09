from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
from mrq_data.catalog import load_catalog
from mrq_data.providers.yahoo import YahooProvider

_SYMBOL_PATTERN = re.compile(r"^[A-Z0-9.^=_-]{1,24}$")
_DATE_COLUMNS = ("observation_date", "date", "trade_date", "datetime", "timestamp")
_PRICE_COLUMNS = ("adjusted_close", "adj_close", "adjclose", "close", "value")


@dataclass(frozen=True)
class PriceSource:
    symbol: str
    provider: str
    price_basis: str
    source_path: str | None
    source_url: str | None
    first_date: str
    last_date: str
    observations: int
    retrieved_at: str
    content_sha256: str

    def to_dict(self) -> dict[str, object]:
        return {
            "symbol": self.symbol,
            "provider": self.provider,
            "price_basis": self.price_basis,
            "source_path": self.source_path,
            "source_url": self.source_url,
            "first_date": self.first_date,
            "last_date": self.last_date,
            "observations": self.observations,
            "retrieved_at": self.retrieved_at,
            "content_sha256": self.content_sha256,
        }


def available_market_symbols(project_root: str | Path | None = None) -> list[dict[str, str]]:
    root = _project_root(project_root)
    catalog = load_catalog(root / "config" / "data_catalog.yaml")
    known: dict[str, dict[str, str]] = {}
    for spec in catalog.values():
        if spec.provider == "yahoo" and spec.kind in {"market", "commodity", "fx"}:
            known[spec.symbol.upper()] = {
                "symbol": spec.symbol,
                "kind": spec.kind,
                "provider": spec.provider,
            }
    for path in (root / "data" / "raw" / "market").glob("*.csv"):
        symbol = path.stem.replace("_", "=").upper()
        if _SYMBOL_PATTERN.fullmatch(symbol):
            known.setdefault(symbol, {"symbol": symbol, "kind": "market", "provider": "csv"})
    return [known[key] for key in sorted(known)]


def load_market_prices(
    symbols: list[str],
    start: str | None = None,
    end: str | None = None,
    *,
    project_root: str | Path | None = None,
) -> tuple[pd.DataFrame, list[PriceSource]]:
    """Load adjusted close series from a local snapshot or the configured Yahoo adapter.

    Local market snapshots use `data/raw/market/<SYMBOL>.csv` and contain a date column
    plus adjusted_close (preferred), adj_close, close, or value. No prices are filled.
    """

    if not symbols:
        raise ValueError("At least one market symbol is required")
    normalized = [str(symbol).strip().upper() for symbol in symbols]
    if len(set(normalized)) != len(normalized):
        raise ValueError("Market symbols must be unique")
    if any(not _SYMBOL_PATTERN.fullmatch(symbol) for symbol in normalized):
        raise ValueError("One or more market symbols contain unsupported characters")

    root = _project_root(project_root)
    catalog = load_catalog(root / "config" / "data_catalog.yaml")
    specs_by_symbol = {spec.symbol.upper(): spec for spec in catalog.values()}
    series: list[pd.Series] = []
    sources: list[PriceSource] = []
    retrieved_at = datetime.now(UTC).isoformat()

    for symbol in normalized:
        local_path = _local_market_file(root, symbol)
        if local_path is not None:
            values, basis, payload = _read_market_csv(local_path, start=start, end=end)
            provider = "local_csv"
            source_path = str(local_path.relative_to(root))
            source_url = None
        else:
            spec = specs_by_symbol.get(symbol)
            if spec is None or spec.provider != "yahoo" or spec.kind not in {
                "market",
                "commodity",
                "fx",
            }:
                raise ValueError(
                    f"{symbol} has no configured market series. Add it to config/data_catalog.yaml "
                    "or place a normalized snapshot at data/raw/market/<SYMBOL>.csv."
                )
            yahoo_end = None
            if end:
                yahoo_end = (pd.Timestamp(end) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
            try:
                fetched = YahooProvider().fetch(spec, start=start, end=yahoo_end)
            except (RuntimeError, ValueError) as exc:
                raise RuntimeError(f"Yahoo market-data fetch failed for {symbol}: {exc}") from exc
            except Exception as exc:
                raise RuntimeError(
                    f"Yahoo market-data request failed for {symbol}: {type(exc).__name__}: {exc}"
                ) from exc
            if not {"observation_date", "value"}.issubset(fetched.columns):
                raise ValueError(f"Yahoo response for {symbol} is missing observation_date/value")
            fetched["observation_date"] = pd.to_datetime(fetched["observation_date"], errors="coerce")
            fetched["value"] = pd.to_numeric(fetched["value"], errors="coerce")
            if fetched[["observation_date", "value"]].isna().any().any():
                raise ValueError(f"Yahoo response for {symbol} contains invalid dates or prices")
            fetched = fetched.sort_values("observation_date")
            if start:
                fetched = fetched[fetched["observation_date"] >= pd.Timestamp(start)]
            if end:
                fetched = fetched[fetched["observation_date"] <= pd.Timestamp(end)]
            values = pd.Series(
                fetched["value"].to_numpy(dtype=float),
                index=pd.DatetimeIndex(fetched["observation_date"], name="date"),
                name=symbol,
            )
            basis = "Yahoo auto_adjust=True adjusted close"
            provider = "yahoo_finance"
            source_path = None
            source_url = f"https://finance.yahoo.com/quote/{symbol}/history/"
            payload = values.to_csv().encode("utf-8")

        if values.empty:
            raise ValueError(f"No price observations found for {symbol} in the requested date range")
        if values.index.has_duplicates:
            raise ValueError(f"Price observations for {symbol} contain duplicate dates")
        if values.isna().any() or (values <= 0).any():
            raise ValueError(f"Price observations for {symbol} must be positive and complete")
        values.name = symbol
        series.append(values.sort_index())
        sources.append(
            PriceSource(
                symbol=symbol,
                provider=provider,
                price_basis=basis,
                source_path=source_path,
                source_url=source_url,
                first_date=values.index.min().date().isoformat(),
                last_date=values.index.max().date().isoformat(),
                observations=int(values.size),
                retrieved_at=retrieved_at,
                content_sha256=hashlib.sha256(payload).hexdigest(),
            )
        )

    prices = pd.concat(series, axis=1, join="outer").sort_index()
    if start:
        prices = prices.loc[prices.index >= pd.Timestamp(start)]
    if end:
        prices = prices.loc[prices.index <= pd.Timestamp(end)]
    return prices, sources


def _read_market_csv(
    path: Path,
    *,
    start: str | None,
    end: str | None,
) -> tuple[pd.Series, str, bytes]:
    payload = path.read_bytes()
    frame = pd.read_csv(path)
    normalized_columns = {str(column).strip().lower().replace(" ", "_"): column for column in frame.columns}
    date_column = next((normalized_columns[name] for name in _DATE_COLUMNS if name in normalized_columns), None)
    price_column = next((normalized_columns[name] for name in _PRICE_COLUMNS if name in normalized_columns), None)
    if date_column is None or price_column is None:
        raise ValueError(f"{path} must contain a date column and an adjusted-close/close/value column")

    parsed_dates = pd.to_datetime(frame[date_column], errors="coerce", utc=True)
    frame["_date"] = parsed_dates.dt.tz_convert(None)
    frame["_price"] = pd.to_numeric(frame[price_column], errors="coerce")
    if frame[["_date", "_price"]].isna().any().any():
        raise ValueError(f"{path} contains invalid dates or prices")
    if frame["_date"].duplicated().any():
        raise ValueError(f"{path} contains duplicate dates")
    frame = frame.sort_values("_date")
    if start:
        frame = frame[frame["_date"] >= pd.Timestamp(start)]
    if end:
        frame = frame[frame["_date"] <= pd.Timestamp(end)]
    prices = pd.Series(
        frame["_price"].to_numpy(dtype=float),
        index=pd.DatetimeIndex(frame["_date"], name="date"),
    )
    basis = str(price_column)
    if str(price_column).strip().lower() in {"adj_close", "adjusted_close", "adjclose"}:
        basis = f"local CSV {price_column} (adjusted close)"
    else:
        basis = f"local CSV {price_column} (basis supplied by user; not independently verified)"
    return prices, basis, payload


def _local_market_file(root: Path, symbol: str) -> Path | None:
    slug = symbol.replace("=", "_").replace("^", "_").replace(".", "_")
    candidates = [root / "data" / "raw" / "market" / f"{symbol}.csv"]
    if slug != symbol:
        candidates.append(root / "data" / "raw" / "market" / f"{slug}.csv")
    return next((path for path in candidates if path.is_file()), None)


def _project_root(value: str | Path | None) -> Path:
    if value is not None:
        return Path(value).expanduser().resolve()
    return Path(os.environ.get("MRQ_PROJECT_ROOT", Path.cwd())).expanduser().resolve()
