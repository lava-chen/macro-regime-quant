# macro-regime-quant

A from-scratch, long-horizon macro regime research framework for China, the US, and global cross-asset markets.

The project is intentionally layered:

1. **Data** — provenance-aware, release-date-aware macro and market data.
2. **Backtest** — explicit execution lag, turnover and transaction costs.
3. **Macro factors** — growth / inflation / liquidity / real-rate measurement.
4. **Regimes** — interpretable rule baseline first, probabilistic models later.
5. **Allocation** — China / US / global cross-asset mapping only after validation.

## Current status

### v0 foundation
- data catalog and provider interfaces;
- FRED / CSV / Yahoo adapters;
- monthly point-in-time alignment contract;
- transparent long-only backtest engine;
- CAGR / vol / Sharpe / max drawdown / Calmar;
- anti-look-ahead synthetic tests.

### v1 in progress
- expanding, past-only z-scores;
- China / US factor definitions;
- four-regime Growth × Inflation baseline;
- runnable US FRED pipeline.

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev,data]"
pytest -q

python -m macro_regime_quant build-us-baseline \
  --start 2000-01-01 \
  --output data/processed/us

python -m macro_regime_quant analyze-us-regimes \
  --start 2000-01-01 \
  --output reports/us_regimes
```

Research outputs include:

```text
data/processed/us/raw_monthly.csv
data/processed/us/factors.csv
data/processed/us/regimes.csv

reports/us_regimes/state_history.csv
reports/us_regimes/asset_prices.csv
reports/us_regimes/forward_returns.csv
reports/us_regimes/regime_return_summary.csv
```

The regime research currently compares SPY, QQQ, TLT, GLD and DBC over 3M / 6M / 12M forward horizons.

## Important research limitation

The runnable US baseline currently uses **latest-vintage FRED history plus explicit approximate release lags**. This is good for pipeline validation and learning, but not yet sufficient to claim historical trading performance because revised macro data can create vintage bias.

Before strategy conclusions, revised US series move to ALFRED / publication vintages and China series move to frozen official release snapshots.

## Design principles

- No same-period signal execution.
- Join macro information by `available_date`, not economic observation date.
- Expanding/rolling statistics never use future observations.
- Missing factor components are not silently treated as zero.
- Factor weights remain transparent priors until walk-forward evidence justifies estimation.
- Default horizon is monthly with 3M / 6M / 12M forward-return research.

See:
- `docs/data_sources.md`
- `docs/backtest_contract.md`
- `docs/factor_contract.md`
- `docs/roadmap.md`
