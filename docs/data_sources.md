# Data-source policy

The model is only as good as the historical information set it reconstructs. Provider choice is part of the research method, not plumbing.

## Priority hierarchy

### United States macro

1. **FRED / ALFRED** — convenient baseline and vintage history.
2. **BEA / BLS / Federal Reserve** — primary sources when publication-calendar precision matters.

The runnable US baseline currently uses latest-vintage FRED history plus approximate release lags. That is sufficient for pipeline validation, not for claiming historical alpha.

### China macro

Canonical v1 inputs are **frozen official snapshots**, not a scraping library:

1. **National Bureau of Statistics (NBS)** — industrial production, retail sales, fixed-asset investment, CPI/PPI, PMI.
2. **PBOC** — M1/M2 and aggregate financing stock growth.
3. **China Money / interbank market source** — government-bond yields.
4. **Customs / SAFE** — later trade, FX and external-balance factors.

AKShare can be used as a convenience cross-check or download helper, but it is not the canonical provenance layer.

The current source owner/portal map is in:

```text
config/china_sources.yaml
```

## China snapshot contract

Each series lives under data/raw/china/ as:

```text
<series>.csv
<series>.meta.yaml
```

Preferred CSV schema:

```csv
observation_date,available_date,availability_basis,availability_evidence_url,value
2026-08-31,2026-09-15,official_release,https://www.stats.gov.cn/sj/zxfb/...,5.2
2026-07-31,,unknown,,5.0
```

- observation_date: economic period the number refers to.
- available_date: actual release date only when confirmed; otherwise blank for unknown dates.
- availability_basis: row-level evidence class (`official_release`, `official_schedule`, `fixed_lag`, `unverified`, or `unknown`).
- availability_evidence_url: row-level official release/calendar URL for documented dates.
- value: the reported numeric value.

The metadata sidecar must include at least:

```yaml
series_key: cn_industrial_production
source_name: National Bureau of Statistics of China
source_url: https://...
frequency: monthly
unit: percent_yoy
downloaded_at: 2026-10-07T13:30:00+08:00
reported_as: yoy_percent
revision_policy: frozen_release_snapshot
availability_lag_days: 18 # only needed when rows use fixed_lag
```

Validate with:

```bash
python -m macro_regime_quant validate-snapshot \
  data/raw/china/industrial_production_yoy.csv
```

`official_release` means the linked release itself supports the date. `official_schedule` records a planned date and is not proof of publication. `fixed_lag` is an estimate reproducible from `availability_lag_days`. `unknown` rows keep `available_date` empty. A dated legacy row without evidence is marked `unverified` until checked.

The China pipeline defaults to `official_release_only`. Broader runs must explicitly select `include_schedule`, `include_estimates`, or `include_unverified`; keep that policy visible in the research report. A row marked `unknown` is never assigned the catalog fallback lag.

## Important China transformation rule

Many Chinese official macro series used here are already published as **YoY or YTD-YoY percentages**. They are therefore entered into the factor model with transform: level.

Do not calculate another 12-month percentage change on:

- industrial production YoY;
- retail sales YoY;
- fixed-asset investment YTD YoY;
- CPI/PPI YoY;
- M1/M2 YoY;
- aggregate financing stock YoY.

PMI new orders is a diffusion index, so the model uses value - 50. The 10Y government-bond yield is a level and can be differenced over 3 months for the liquidity factor.

## Market prices

- Yahoo Finance is acceptable for early prototyping of liquid ETFs, FX and commodity proxies.
- Later replace critical tests with exchange/vendor/official data where practical.
- Adjusted prices must be explicit; survivorship and delisting bias matter if the universe expands to individual securities.

## Point-in-time rule

Do **not** align a macro observation to its economic period and assume the market knew it then.

If an August observation is released in September, August's value only enters the information set on its September available_date.

## Canonical research table

Each transformed series should ultimately expose:

- observation_date
- available_date
- value
- source
- series_key
- unit
- transform
- downloaded_at

Feature engineering joins on **available_date**, which is the primary defense against macro look-ahead bias.
