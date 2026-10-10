import json

import pytest
from mrq_cli.backtest_workbench.models import StrategySpec
from mrq_cli.backtest_workbench.store import StrategyStore


def test_strategy_spec_normalizes_symbols_and_validates_weights():
    strategy = StrategySpec(name="Gold / Nasdaq", weights={"gld": 0.5, "QQQ": 0.5})
    assert strategy.weights == {"GLD": 0.5, "QQQ": 0.5}

    with pytest.raises(ValueError, match="sum to exactly 1.0"):
        StrategySpec(name="Bad", weights={"GLD": 0.6, "QQQ": 0.5})
    with pytest.raises(ValueError, match="start_date"):
        StrategySpec(name="Bad", weights={"GLD": 1}, start_date="2025-02-30")


def test_strategy_store_round_trip_versioning_and_safe_ids(tmp_path):
    store = StrategyStore(tmp_path / "strategies")
    spec = StrategySpec(name="Gold and Nasdaq", weights={"GLD": 0.5, "QQQ": 0.5})
    first = store.save(spec)
    assert first.strategy_id == "gold-and-nasdaq"
    assert store.get(first.strategy_id).spec == spec
    assert [row.strategy_id for row in store.list()] == [first.strategy_id]

    with pytest.raises(FileExistsError):
        store.save(spec)
    second = store.save(spec, replace=True)
    assert second.version == 2
    revised = StrategySpec(name="Gold and Nasdaq revised", weights={"GLD": 0.6, "QQQ": 0.4})
    third = store.save(revised, strategy_id=first.strategy_id, replace=True)
    assert third.version == 3
    assert store.get(first.strategy_id).spec == revised
    assert store.get_version(first.strategy_id, 1).spec == spec
    assert store.get_version(first.strategy_id, 3).spec == revised
    assert [row.version for row in store.list_versions(first.strategy_id)] == [1, 2, 3]
    with pytest.raises(ValueError, match="strategy_id"):
        store.get("../private")


def test_strategy_store_archives_preexisting_latest_only_record_on_update(tmp_path):
    root = tmp_path / "legacy-strategies"
    root.mkdir()
    original = StrategySpec(name="Legacy allocation", weights={"GLD": 0.5, "QQQ": 0.5})
    (root / "legacy.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "strategy_id": "legacy",
                "version": 1,
                "created_at": "2026-01-01T00:00:00+00:00",
                "updated_at": "2026-01-01T00:00:00+00:00",
                "strategy": original.to_dict(),
            }
        ),
        encoding="utf-8",
    )
    store = StrategyStore(root)

    updated = store.save(
        StrategySpec(name="Legacy revised", weights={"GLD": 0.6, "QQQ": 0.4}),
        strategy_id="legacy",
        replace=True,
    )

    assert updated.version == 2
    assert store.get_version("legacy", 1).spec == original
