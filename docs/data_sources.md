# Data-source policy

The model is only as good as the historical information set it reconstructs. Provider choice is therefore part of the research method, not plumbing.

## Priority hierarchy

### United States macro
1. **FRED / ALFRED (Federal Reserve Bank of St. Louis)** — preferred for macro history; ALFRED should be used when vintage/revision bias matters.
2. **BEA / BLS / Federal Reserve** — official primary sources when a series needs publication-calendar precision.

Initial factors: real GDP, CPI/core CPI/PCE, unemployment/payrolls, policy rate, 10Y yield, 10Y real yield, yield curve, credit spreads, M2/liquidity.

### China macro
1. **National Bureau of Statistics (NBS)** — GDP, industrial production, retail sales, CPI/PPI, fixed-asset investment.
2. **PBOC** — M1/M2, aggregate financing, credit, policy/market rates.
3. **MOF / CFETS / SAFE / Customs** — fiscal, FX, reserves, trade.
4. **AKShare** — optional convenience adapter for prototyping, never treated as the canonical source without a frozen snapshot and source metadata.

For v0, China official series should be downloaded/frozen into `data/raw/` as CSV with:

```text
observation_date,value
2020-01-31,...
```

Every snapshot should also have a small metadata sidecar (source URL, download time, units, seasonal-adjustment status, revision notes).

### Market prices
- **Yahoo Finance** is acceptable for early prototyping of liquid ETFs, FX and commodity proxies.
- Later replace critical backtests with exchange/vendor/official data where possible.
- Adjusted prices must be explicit; delisting/survivorship bias must be discussed when moving from ETFs to individual securities.

## Point-in-time rule

Do **not** align a macro observation to its economic period and assume the market knew it then.

Example: an August CPI observation released in September may only influence weights after its actual release timestamp. v0 supports a conservative fixed `release_lag_days`; v1 should use real publication calendars, and US revised macro series should use vintages where relevant.

## Canonical research table

Each transformed series should ultimately expose:

- `observation_date`
- `available_date`
- `value`
- `source`
- `series_key`
- `unit`
- `transform`
- `downloaded_at`

This lets feature engineering join on **available_date**, which is the key defense against macro look-ahead bias.
