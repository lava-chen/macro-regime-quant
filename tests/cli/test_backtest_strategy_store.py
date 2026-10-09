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
    with pytest.raises(ValueError, match="strategy_id"):
        store.get("../private")
