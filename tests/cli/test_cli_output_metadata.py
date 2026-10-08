from pathlib import Path

import pandas as pd
import yaml
from mrq_cli.cli import _write_baseline, build_parser


def test_china_cli_defaults_to_confirmed_official_release_dates():
    args = build_parser().parse_args(["build-china-baseline"])
    assert args.availability_policy == "official_release_only"
    assert not args.allow_partial_sources


def test_china_cli_partial_mode_and_collector_defaults_are_explicit():
    args = build_parser().parse_args(
        ["build-china-baseline", "--allow-partial-sources", "--availability-policy", "include_unverified"]
    )
    assert args.allow_partial_sources
    assert args.availability_policy == "include_unverified"

    collect = build_parser().parse_args(["collect-china-snapshots"])
    assert collect.start_year == 2005
    assert collect.output == "data/raw/china"
    assert collect.workers == 2


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
