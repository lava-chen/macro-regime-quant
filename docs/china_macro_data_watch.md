# China Macro Data Watch v1

## Run it

```bash
uv sync --locked --all-packages --group dev
uv run --locked macro-regime-quant refresh-china-macro
uv run --locked macro-regime-quant refresh-china-macro --full-rescan
uv run --locked macro-regime-quant report-china-macro --as-of 2025-12-31
```

`refresh-china-macro` is also available under the legacy alias `collect-china-snapshots`. It stages source responses, appends new releases and revisions to `data/raw/china/china_macro_snapshots.csv`, then writes `reports/china_macro/latest.json` and `latest.md`. Failures still write an audit and return a non-zero exit code.

The first refresh seeds the ledger from the 863 PR #13 official observations, then scans the official NBS and PBOC archives. Later runs look back through the previous year; a full archive scan runs at least every 180 days. Failed scans do not advance the successful checkpoint. The next run repeats the same window, and identical rows are ignored.

## Record contract

Every ledger row includes:

`series_id`, `observation_period`, `observation_date`, `available_date`, `availability_basis`, `retrieved_at`, `value`, `unit`, `source_value_url`, `availability_evidence_url`, `vintage`, `quality_flags`, and `source_title`.

The initial snapshot has a `vintage` equal to the evidenced original release date. If a previously stored period changes, its new row receives the date the change was first observed. Historical rows remain in the ledger. At each month end, the report selects only the latest version whose release and vintage dates are both known by that date. Unknown release dates remain visible in the archive but do not enter PIT factors.

The NBS collector keeps January–February joint periods intact and does not create a separate January observation. Retail sales is split into explicit single-month YoY and cumulative YTD YoY series; cumulative releases never enter the monthly retail growth factor. The original mixed-period source snapshot remains unchanged, with derived partitions preserving its values, periods, and evidence links. Fixed asset investment remains cumulative year-to-date growth. Core CPI is recorded only when the official NBS article explicitly states a core CPI value. Values are not inferred from CPI.

## Sources and current coverage

The refresh command reuses the official NBS and PBOC archive collectors in `mrq_data.china_harvest`.

| Source | Collected indicators | Coverage in the frozen baseline |
|---|---|---|
| NBS | Industrial production, single-month and cumulative retail sales, fixed asset investment, PMI new orders, CPI, PPI; core CPI when explicitly present | 7 observed series; indexed releases begin around 2021; PMI extends through 2026-09 |
| PBOC | M1, M2, social financing stock growth | 3 series; M1/M2 begin 2009-11; TSF begins 2014-12 and has known gaps |

The PR #13 baseline contains 863 rows with original value URLs and release-date evidence. Core CPI has not yet been backfilled in that frozen baseline. GDP, industrial enterprise profits, unemployment, property, and the China 10-year yield remain next-phase source work; no missing series is filled with an estimate.

The collector retries failed HTTP/PDF requests. A run is marked failed when either source fails or a candidate release cannot be parsed. Partial successful rows are retained for audit, but the workflow fails after committing its audit output. The workflow runs at 22:00 Asia/Shanghai and supports `workflow_dispatch`; GitHub scheduled jobs can be delayed, so the schedule is a polling cadence rather than a release calendar.

## Factors and historical replay

Growth, Inflation, Liquidity, and Real Rate use the existing factor engine and expanding standardization. Every z-score uses observations through `t-1`, never the current row's mean or standard deviation. The real-rate factor is an ex-post proxy (`China 10Y nominal yield − CPI YoY`) and remains `insufficient_data` until both evidenced input series exist.

The existing four-quadrant regime classifier uses Growth and Inflation. Liquidity and Real Rate are reported as contextual factors and are not silently substituted when Growth or Inflation are missing. When those two factor histories do not meet the configured minimum, the report returns `insufficient_data` and no regime label.

The report separates observed values and citations from model inference. Cross-asset statements are testable research hypotheses, not buy/sell instructions.
