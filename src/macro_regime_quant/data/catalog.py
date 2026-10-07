from __future__ import annotations

import difflib
from pathlib import Path
from typing import Any

import yaml

from .models import CATALOG_FIELDS, SeriesSpec

#: Top-level keys allowed alongside ``series`` for human-facing catalog metadata.
CATALOG_METADATA_KEYS: frozenset[str] = frozenset({"version", "description", "generated_at"})


class CatalogError(ValueError):
    """A catalog file could not be turned into a valid set of SeriesSpec.

    Carries the source path and the offending series key so the message points at
    something the author can actually find in a few-hundred-line YAML file.
    """

    def __init__(self, message: str, *, path: Path | None = None, series_key: str | None = None):
        self.path = path
        self.series_key = series_key
        where = ""
        if path is not None:
            where = f"{path}"
        if series_key:
            where = f"{where} :: series.{series_key}" if where else f"series.{series_key}"
        super().__init__(f"{where}: {message}" if where else message)


def _read_catalog(path: str | Path) -> dict[str, Any]:
    file_path = Path(path)
    if not file_path.exists():
        raise CatalogError("catalog file not found", path=file_path)
    try:
        raw = yaml.safe_load(file_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise CatalogError(f"invalid YAML: {exc}", path=file_path) from exc
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise CatalogError(
            f"top level must be a mapping, got {type(raw).__name__}", path=file_path
        )
    return raw


def _build_spec(key: str, cfg: Any, file_path: Path) -> SeriesSpec:
    if not isinstance(cfg, dict):
        raise CatalogError(
            f"entry must be a mapping, got {type(cfg).__name__}", path=file_path, series_key=key
        )

    if "key" in cfg:
        raise CatalogError(
            f"the mapping key {key!r} already IS this series' key; delete the 'key:' "
            "field (or rename the mapping key).",
            path=file_path,
            series_key=key,
        )

    unknown = set(cfg) - CATALOG_FIELDS
    if unknown:
        suggestions = _suggest(sorted(unknown), CATALOG_FIELDS)
        raise CatalogError(
            f"unknown field(s) {sorted(unknown)}."
            + (f" Did you mean {suggestions}?" if suggestions else ""),
            path=file_path,
            series_key=key,
        )

    missing = {"provider", "symbol", "kind", "frequency"} - set(cfg)
    if missing:
        raise CatalogError(
            f"missing required field(s) {sorted(missing)}", path=file_path, series_key=key
        )

    if "release_lag_days" in cfg and not isinstance(cfg["release_lag_days"], int):
        raw_lag = cfg["release_lag_days"]
        raise CatalogError(
            f"release_lag_days must be an integer number of days, got {raw_lag!r} "
            f"({type(raw_lag).__name__}). Quote-free YAML: a bare 10 is an int, '10' is not.",
            path=file_path,
            series_key=key,
        )

    try:
        return SeriesSpec(key=key, **cfg)
    except (TypeError, ValueError) as exc:
        raise CatalogError(str(exc), path=file_path, series_key=key) from exc


def _suggest(unknown: list[str], valid: frozenset[str] | set[str]) -> list[str]:
    out: list[str] = []
    for name in unknown:
        close = difflib.get_close_matches(name, sorted(valid), n=1, cutoff=0.6)
        if close:
            out.append(close[0])
    return out


def load_catalog(path: str | Path) -> dict[str, SeriesSpec]:
    """Load and validate the research catalog.

    Raises CatalogError with the file path and series key attached, so a typo in a
    long YAML file is a one-line fix instead of a dataclass TypeError.
    """

    file_path = Path(path)
    raw = _read_catalog(file_path)

    unknown_top = set(raw) - {"series"} - CATALOG_METADATA_KEYS
    if unknown_top:
        raise CatalogError(
            f"unknown top-level section(s) {sorted(unknown_top)}; "
            f"allowed: 'series' plus metadata {sorted(CATALOG_METADATA_KEYS)}",
            path=file_path,
        )

    series_cfg = raw.get("series") or {}
    if not isinstance(series_cfg, dict):
        raise CatalogError(
            f"'series' must be a mapping, got {type(series_cfg).__name__}", path=file_path
        )

    return {key: _build_spec(key, cfg, file_path) for key, cfg in series_cfg.items()}


def check_catalog_consistency(specs: dict[str, SeriesSpec]) -> list[str]:
    """Return human-readable warnings about catalog entries that load but look wrong.

    These are advisory, not fatal: a lag that looks unusual is a question for the
    author, not a reason to refuse to run a backtest.
    """

    warnings: list[str] = []
    by_symbol: dict[tuple[str, str], list[str]] = {}
    for key, spec in specs.items():
        by_symbol.setdefault((spec.provider, spec.symbol), []).append(key)

    for (provider, symbol), keys in by_symbol.items():
        if len(keys) > 1:
            warnings.append(f"{symbol} ({provider}) is declared by multiple keys: {sorted(keys)}")

    for key, spec in sorted(specs.items()):
        if spec.release_lag_days == 0 and spec.frequency in {"monthly", "quarterly"}:
            warnings.append(
                f"{key}: {spec.frequency} series with release_lag_days=0; "
                "slow-moving data is rarely knowable the same day it is observed"
            )
        if spec.frequency == "daily" and spec.release_lag_days > 3:
            warnings.append(
                f"{key}: daily series with release_lag_days={spec.release_lag_days}; "
                "this delays the series well past publication"
            )
    return warnings
