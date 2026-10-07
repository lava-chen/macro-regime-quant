# macro-regime-quant

A point-in-time-safe financial research platform being built from first principles for China, the US, and global markets.

The macro regime model remains the first production research engine, but the repository now has explicit boundaries for five engines:

1. **Macro** — Growth / Inflation / Liquidity / Real Rate and regime research.
2. **Valuation** — company fundamentals, DCF / Reverse DCF, and business-model-specific valuation.
3. **Flow** — observed and inferred capital flows with stock-flow reconciliation.
4. **Risk** — planned portfolio and market risk layer.
5. **Portfolio** — planned allocation layer that consumes outputs from the other engines.

All engines share the same entity identities, point-in-time data discipline, backtest utilities, and model-versioning principles.

## Current status

### Macro v1
- FRED / CSV / Yahoo data layer;
- observation-date / available-date separation;
- expanding past-only z-scores;
- China / US factor definitions;
- Growth x Inflation four-regime baseline;
- runnable US FRED pipeline;
- China official snapshot contract with row-level release-date evidence;
- regime-conditioned 3M / 6M / 12M cross-asset research.

### Platform v0
- stable Company and Security entity IDs;
- point-in-time company-fundamental observation contract;
- FCFF DCF and Reverse DCF baseline;
- valuation routing contract for banks / insurers / REITs / commodity companies;
- observed-versus-inferred capital-flow evidence model;
- stock-flow reconciliation and valuation-adjusted AUM-flow utility;
- transparent confidence-adjusted cross-engine signal composition.

## Quick start

~~~bash
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

python -m macro_regime_quant validate-snapshot \
  data/raw/china/industrial_production_yoy.csv

python -m macro_regime_quant build-china-baseline \
  --start 2005-01-01 \
  --output data/processed/china
~~~

The China baseline admits only availability_basis=official_release rows by default. Broader availability policies must be selected explicitly and reported with any results.

## Research principles

- No same-period signal execution.
- Join information by when it was knowable, not only by the period it describes.
- Company fundamentals use period_end plus available_date.
- Expanding/rolling statistics never use future observations.
- Missing inputs are not silently treated as zero.
- Observed capital flows and inferred flows remain distinguishable.
- A ticker is not a canonical entity identifier.
- Valuation methods are selected by business model rather than forced through one formula.
- A combined score never replaces the underlying inspectable engine outputs.
- Research models do not become production models without point-in-time and walk-forward validation.

## Architecture

The intended long-run shape is:

~~~text
Market / Macro / Fundamental / Ownership Data
                    |
             Shared Entity + PIT Layer
                    |
        +-----------+-----------+
        |           |           |
      Macro     Valuation      Flow
        |           |           |
        +-----------+-----------+
                    |
                  Risk
                    |
                Portfolio
                    |
            Signals / Reports
~~~

Deployment is expected to separate concerns:

- GitHub: source, CI, model versions;
- Supabase/Postgres: canonical observations, entities, runs and signals;
- object storage: raw snapshots and large outputs;
- worker: ingestion / research computation;
- Vercel: dashboard / API;
- Notion: human research, conclusions and model-change proposals.

See:
- docs/platform_architecture.md
- docs/data_sources.md
- docs/backtest_contract.md
- docs/factor_contract.md
- docs/valuation_contract.md
- docs/flow_contract.md
- docs/roadmap.md
