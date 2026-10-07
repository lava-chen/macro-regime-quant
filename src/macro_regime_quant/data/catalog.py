from __future__ import annotations

from pathlib import Path

import yaml

from .models import SeriesSpec


def load_catalog(path: str | Path) -> dict[str, SeriesSpec]:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    out: dict[str, SeriesSpec] = {}
    for key, cfg in raw.get("series", {}).items():
        out[key] = SeriesSpec(key=key, **cfg)
    return out
