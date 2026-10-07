# macro-regime-quant

A point-in-time-safe financial research platform being built from first principles for China, the US, and global markets.

The macro regime model remains the first production research engine, but the repository now has explicit boundaries for five engines:

1. **Macro** — Growth / Inflation / Liquidity / Real Rate and regime research.
2. **Valuation** — company fundamentals, DCF / Reverse DCF, and business-model-specific valuation.
3. **Flow** — observed and inferred capital flows with stock-flow reconciliation.
4. **Risk** — planned portfolio and market risk layer.
5. **Portfolio** — planned allocation layer that consumes outputs from the other engines.

All engines share the same entity identities, point-in-time data discipline, backtest utilities, and model-versioning principles.

## Repository layout

The repository is a [uv](https://docs.astral.sh/uv/) workspace of five packages. Dependencies flow strictly one way:

```
mrq-core  <-  mrq-data  <-  mrq-engines  <-  mrq-research  <-  mrq-cli
```

| Package | Owns | May import |
|---|---|---|
| `mrq-core` | observation-frame contract, as-of primitives, entity identity, series types | numpy, pandas only |
| `mrq-data` | catalog, snapshots, availability policy, providers | `mrq-core` |
| `mrq-engines` | macro, valuation, flow, signals, cross-engine pipeline | `mrq-core`, `mrq-data` |
| `mrq-research` | backtesting, forward returns, regime analysis | all below |
| `mrq-cli` | command-line entry point | all below |

`mrq-core` imports nothing from the other packages by design: the point-in-time vocabulary cannot be changed by engine work. `scripts/check_layering.py` enforces this in CI — Python does not, so without it the layering erodes the first time someone reaches for a convenient import.

Tests live in `tests/<package>/` and can be run per package: `pytest tests/core`.

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
uv sync --all-packages
uv run pytest -q
uv run ruff check packages tests scripts
uv run python scripts/check_layering.py
uv run macro-regime-quant --help

uv run macro-regime-quant build-us-baseline \
  --start 2000-01-01 \
  --output data/processed/us

uv run macro-regime-quant analyze-us-regimes \
  --start 2000-01-01 \
  --output reports/us_regimes

uv run macro-regime-quant validate-snapshot \
  data/raw/china/industrial_production_yoy.csv

uv run macro-regime-quant build-china-baseline \
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
