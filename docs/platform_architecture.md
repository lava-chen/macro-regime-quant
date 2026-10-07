# Platform architecture

The repository is evolving from a macro-only research project into a modular financial research platform. The design goal is not one monolithic "all-knowing" model. It is a set of independently testable engines that share a common point-in-time data and entity layer.

## Core engines

1. **Macro Engine**
   - Growth / Inflation / Liquidity / Real Rate
   - China / US regimes
   - global transmission variables
2. **Valuation Engine**
   - DCF / Reverse DCF for general companies
   - later: residual income for banks
   - later: embedded value for insurers
   - later: AFFO / NAV for REITs
   - later: normalized-cycle valuation for commodity producers
3. **Flow Engine**
   - observed flows
   - inferred flows
   - ownership and positioning
   - stock-flow reconciliation
4. **Risk Engine**
   - volatility, drawdown, correlation, liquidity, concentration
5. **Portfolio Engine**
   - combines expected return, macro state, valuation, flow and risk
   - owns portfolio constraints and capital allocation

## Shared foundations

### Entity layer

A company is not the same object as a listed security.

Examples:

- company: US:AAPL
- security: NASDAQ:AAPL
- company: CN:TENCENT
- security: HKEX:0700

All fundamentals, securities, fund holdings and flow observations should join through stable entity identifiers rather than ticker strings alone.

### Point-in-time layer

Every piece of information that can change a historical decision needs an information-availability timestamp.

For fundamentals:

~~~text
company_id
metric
period_end
available_date
value
unit
source
~~~

The production rule is:

~~~text
available_date <= decision_time
~~~

The same discipline already used by the macro pipeline applies to company accounts, holdings and fund flows.

## Research versus production

Research models can change frequently. Production models cannot.

Recommended lifecycle:

~~~text
experiment
  -> candidate
  -> walk-forward / PIT validation
  -> approved model version
  -> production
~~~

Every production result should eventually be traceable to:

~~~text
model_version
git_commit_sha
config_version
data_cutoff
data_vintage
run_id
~~~

## Signal composition

Do not hide all engines inside a single opaque score.

Preferred structure:

~~~text
valuation_score
quality_score
growth_score
macro_score
flow_score
momentum_score
risk_score
~~~

A combined score may be used for ranking, but each component must remain inspectable and independently backtestable.

## Update cadence

Slow layer:
- fundamentals: earnings/report driven
- valuation: report driven / weekly refresh
- macro: release driven / monthly
- strategic portfolio: monthly / quarterly

Fast layer:
- prices: daily
- ETF/fund flow: daily when available
- futures/options positioning: daily / weekly
- credit/FX/liquidity: daily
- tactical overlay: daily

The slow and fast layers must not be forced into the same refresh frequency.

## Deployment direction

Long-term intended split:

~~~text
GitHub -> code, model versions, CI
Supabase/Postgres -> canonical observations, entities, model runs, signals
Object storage -> raw snapshots and large research outputs
Worker -> ingestion, factor computation, valuation, flow inference, backtests
Vercel -> dashboard and API
Notion -> human research notes, decisions and model-change proposals
~~~

Notion is a control/research surface, not the canonical time-series database.
