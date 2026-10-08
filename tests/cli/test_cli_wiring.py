"""CLI wiring tests.

A subcommand can be registered by the parser and still have no branch in
``main()``. That failure is invisible: argparse accepts the command, the
process exits 0, and nothing is printed or written. It happened here once —
``snapshot-vintages`` was declared, dispatched nowhere, and reported success
while doing nothing.

These tests make a registered command and a handled command the same fact.
"""

from __future__ import annotations

import inspect

import pytest
from mrq_cli import cli


def _subcommand_names(parser) -> set[str]:
    for action in parser._actions:
        choices = getattr(action, "choices", None)
        if isinstance(choices, dict) and choices:
            return set(choices)
    raise AssertionError("parser exposes no subcommands")


def test_every_subcommand_reaches_a_dispatch_branch():
    """A command with no branch in main() must fail here, not silently do nothing."""

    source = inspect.getsource(cli.main)
    missing = [
        name
        for name in _subcommand_names(cli.build_parser())
        if f'args.command == "{name}"' not in source
    ]
    assert not missing, (
        f"subcommand(s) declared but never dispatched in main(): {sorted(missing)}. "
        "They would parse fine and exit 0 without doing anything."
    )


def test_dispatch_branches_all_reference_real_subcommands():
    """And the reverse: no branch for a command that does not exist."""

    declared = _subcommand_names(cli.build_parser())
    source = inspect.getsource(cli.main)
    referenced = {
        line.split('"')[1]
        for line in source.splitlines()
        if 'args.command == "' in line
    }
    assert not (referenced - declared), (
        f"main() dispatches on unknown command(s): {sorted(referenced - declared)}"
    )


def test_unknown_command_exits_with_an_error():
    parser = cli.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["definitely-not-a-command"])


def test_snapshot_vintages_writes_files_and_reports(tmp_path, monkeypatch, capsys):
    """End-to-end through the command, so the wiring itself is exercised."""

    catalog = tmp_path / "catalog.yaml"
    catalog.write_text(
        """
series:
  s1:
    provider: alfred
    symbol: TESTSERIES
    kind: macro
    frequency: monthly
    release_lag_days: 10
  not_alfred:
    provider: csv
    symbol: china/x.csv
    kind: macro
    frequency: monthly
    release_lag_days: 10
""",
        encoding="utf-8",
    )
    root = tmp_path / "vintages"

    monkeypatch.setattr(cli, "_snapshot_series", _fake_snapshot, raising=False)
    monkeypatch.setattr("mrq_data.vintages.snapshot_series", _fake_snapshot)

    argv = [
        "snapshot-vintages",
        "--series", "s1,not_alfred",
        "--start", "2020-01-01",
        "--end", "2020-01-01",
        "--catalog", str(catalog),
        "--root", str(root),
    ]
    cli.main(argv)

    out = capsys.readouterr().out
    assert "captured=1" in out
    assert "skipped" in out
    assert "point it at 'alfred'" in out  # the csv series was refused, not silently ignored
    assert list(root.glob("TESTSERIES/*.csv"))


def _fake_snapshot(spec, *, start, end, step_months, root, provider=None, progress=False):
    series_dir = root / spec.symbol
    series_dir.mkdir(parents=True, exist_ok=True)
    (series_dir / f"{start}.csv").write_text(
        "observation_date,value,vintage_date\n2020-01-31,1.0,2020-01-01\n", encoding="utf-8"
    )
    return {"captured": 1, "skipped": 0}
