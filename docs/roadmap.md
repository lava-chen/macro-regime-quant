# Roadmap

## v0 — foundation (current)
- data source policy and catalog;
- point-in-time availability schema;
- CSV/FRED/Yahoo provider adapters;
- long-only backtest engine with execution lag and turnover costs;
- synthetic anti-look-ahead tests.

## v1 — macro factors
- China Growth / Inflation / Credit-Liquidity factors;
- US Growth / Inflation / Real-Rate / Liquidity factors;
- expanding-window z-scores only;
- rule-based four-regime classifier;
- China snapshot validator distinguishes confirmed releases, schedules, estimates, unverified dates, and unknown dates;
- China baseline defaults to confirmed official release dates only.

### v1 remaining data work
- collect and source-check China official historical values and release documents;
- label unrecoverable historical availability dates as unknown, without substituting guessed dates;
- build verified 2005–2026 factor and regime histories before cross-country regime research.

## v2 — cross-market mapping
- forward 3M/6M/12M returns by regime;
- China × US joint-state matrix;
- global USD/commodity transmission factor;
- benchmark comparisons and walk-forward validation.

## v3 — probabilistic regimes
- HMM / Markov switching;
- transition probabilities;
- risk-budgeted allocation;
- model stability and regime sensitivity reports.
