from pathlib import Path

import pandas as pd
import yaml

from macro_regime_quant.cli import _write_baseline, build_parser


def test_china_cli_defaults_to_confirmed_official_release_dates():
    args = build_parser().parse_args(["build-china-baseline"])
    assert args.availability_policy == "official_release_only"


def test_baseline_output_persists_availability_policy(tmp_path: Path):
    idx = pd.date_range("2025-01-31", periods=2, freq="ME")
    raw = pd.DataFrame({"series": [1.0, 2.0]}, index=idx)
    factors = pd.DataFrame({"growth": [0.0, 1.0], "inflation": [0.0, 1.0]}, index=idx)
    regimes = pd.Series(["recession", "reflation"], index=idx, name="regime")

    _write_baseline(
        raw,
        factors,
        regimes,
        output=str(tmp_path),
        run_metadata={"availability_policy": "official_release_only"},
    )

    metadata = yaml.safe_load((tmp_path / "run_metadata.yaml").read_text(encoding="utf-8"))
    assert metadata["availability_policy"] == "official_release_only"
