from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from .data.availability import attach_available_date
from .data.catalog import load_catalog
from .data.monthly import monthly_asof
from .data.registry import default_provider_registry
from .data.snapshots import validate_snapshot
from .factors.composite import build_composite_factor
from .factors.transforms import expanding_zscore
from .regimes import classify_regime

AVAILABILITY_POLICY_BASES = {
    "official_release_only": {"official_release"},
    "include_schedule": {"official_release", "official_schedule"},
    "include_estimates": {"official_release", "official_schedule", "fixed_lag"},
    "include_unverified": {
        "official_release",
        "official_schedule",
        "fixed_lag",
        "unverified",
    },
}


def _transform_monthly(series: pd.Series, transform: str, periods: int | None = None) -> pd.Series:
    if transform == "level":
        return series
    if transform == "yoy":
        return series.pct_change(periods=periods or 12, fill_method=None)
    if transform == "diff":
        return series.diff(periods=periods or 1)
    if transform == "pct_change":
        return series.pct_change(periods=periods or 1, fill_method=None)
    if transform == "distance_from_50":
        return series - 50.0
    raise ValueError(f"Unknown transform: {transform}")


def load_monthly_panel(
    catalog_path: str | Path,
    keys: list[str],
    start: str,
    end: str,
    availability_policy: str = "all",
) -> pd.DataFrame:
    """Fetch requested raw series and align them by information availability."""

    if availability_policy != "all" and availability_policy not in AVAILABILITY_POLICY_BASES:
        raise ValueError(
            f"Unknown availability_policy {availability_policy!r}; "
            f"choose 'all' or one of {sorted(AVAILABILITY_POLICY_BASES)}"
        )

    catalog = load_catalog(catalog_path)
    providers = default_provider_registry()
    columns: dict[str, pd.Series] = {}

    for key in keys:
        spec = catalog[key]
        if spec.provider not in providers:
            raise KeyError(f"No provider registered for {spec.provider!r}")

        if spec.provider == "csv" and availability_policy != "all":
            csv_provider = providers["csv"]
            validate_snapshot(
                csv_provider.root / spec.symbol,
                require_available_date=False,
            )

        raw = providers[spec.provider].fetch(spec, start=None, end=end)
        if "available_date" not in raw.columns:
            if "availability_basis" not in raw.columns:
                raw = attach_available_date(raw, spec.release_lag_days)
            else:
                raw = raw.copy()
                raw["available_date"] = pd.NaT
                lagged = raw["availability_basis"].eq("fixed_lag")
                if lagged.any():
                    raw.loc[lagged, "available_date"] = pd.to_datetime(
                        raw.loc[lagged, "observation_date"]
                    ) + pd.to_timedelta(spec.release_lag_days, unit="D")
                date_required = ~raw["availability_basis"].isin({"unknown", "fixed_lag"})
                if date_required.any():
                    raise ValueError(
                        f"{key} has an availability_basis that requires an available_date"
                    )
        else:
            raw = raw.copy()
            raw["available_date"] = pd.to_datetime(raw["available_date"], errors="coerce")
            if "availability_basis" not in raw.columns:
                raw["availability_basis"] = "unknown"
                raw.loc[raw["available_date"].notna(), "availability_basis"] = "unverified"

        if availability_policy != "all":
            allowed = AVAILABILITY_POLICY_BASES[availability_policy]
            raw = raw.loc[raw["availability_basis"].isin(allowed)]

        available = raw
        columns[key] = monthly_asof(available, start=start, end=end)

    return pd.DataFrame(columns).sort_index()


def _component_series(
    raw_panel: pd.DataFrame,
    cfg: dict[str, Any],
    min_z_history: int,
) -> pd.Series:
    source = cfg["source"]
    transformed = _transform_monthly(
        raw_panel[source],
        transform=cfg.get("transform", "level"),
        periods=cfg.get("periods"),
    )
    oriented = transformed * float(cfg.get("sign", 1.0))
    return expanding_zscore(oriented, min_periods=min_z_history)


def build_country_factors(
    raw_panel: pd.DataFrame,
    country_cfg: dict[str, Any],
    min_z_history: int = 36,
) -> pd.DataFrame:
    """Build transparent composite factors from a monthly point-in-time panel."""

    result: dict[str, pd.Series] = {}

    for factor_name, factor_cfg in country_cfg.items():
        components: dict[str, pd.Series] = {}
        weights: dict[str, float] = {}

        for component_name, component_cfg in factor_cfg["components"].items():
            components[component_name] = _component_series(
                raw_panel,
                component_cfg,
                min_z_history=min_z_history,
            )
            weights[component_name] = float(component_cfg.get("weight", 1.0))

        component_frame = pd.DataFrame(components)
        result[factor_name] = build_composite_factor(
            component_frame,
            weights=weights,
            min_components=int(factor_cfg.get("min_components", len(components))),
        )

    return pd.DataFrame(result)


def load_factor_config(path: str | Path) -> dict[str, Any]:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}


def required_sources(country_cfg: dict[str, Any]) -> list[str]:
    keys: set[str] = set()
    for factor_cfg in country_cfg.values():
        for component_cfg in factor_cfg["components"].values():
            keys.add(component_cfg["source"])
    return sorted(keys)


def _build_country_baseline(
    country_key: str,
    catalog_path: str | Path,
    factor_path: str | Path,
    start: str,
    end: str | None,
    min_z_history: int,
    availability_policy: str = "all",
) -> tuple[pd.DataFrame, pd.DataFrame, pd.Series]:
    end = end or pd.Timestamp.today().strftime("%Y-%m-%d")
    factor_cfg = load_factor_config(factor_path)[country_key]
    sources = required_sources(factor_cfg)
    raw = load_monthly_panel(
        catalog_path,
        sources,
        start=start,
        end=end,
        availability_policy=availability_policy,
    )
    factors = build_country_factors(raw, factor_cfg, min_z_history=min_z_history)
    regimes = classify_regime(factors["growth"], factors["inflation"])
    return raw, factors, regimes


def build_us_baseline(
    catalog_path: str | Path = "config/data_catalog.yaml",
    factor_path: str | Path = "config/factors.yaml",
    start: str = "2000-01-01",
    end: str | None = None,
    min_z_history: int = 36,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.Series]:
    """End-to-end US research baseline.

    Uses latest-vintage FRED history plus conservative release lags. It is suitable
    for pipeline validation, not yet a publication-quality historical trading test.
    """

    return _build_country_baseline(
        "united_states",
        catalog_path,
        factor_path,
        start,
        end,
        min_z_history,
    )


def build_china_baseline(
    catalog_path: str | Path = "config/data_catalog.yaml",
    factor_path: str | Path = "config/factors.yaml",
    start: str = "2005-01-01",
    end: str | None = None,
    min_z_history: int = 36,
    availability_policy: str = "official_release_only",
) -> tuple[pd.DataFrame, pd.DataFrame, pd.Series]:
    """Build China factors from snapshots with an explicit date-quality policy.

    The default only admits dates confirmed by an official release document.
    Broader policies must be selected explicitly for estimated or unverified dates.
    """

    return _build_country_baseline(
        "china",
        catalog_path,
        factor_path,
        start,
        end,
        min_z_history,
        availability_policy,
    )
