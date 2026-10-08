# China and US macro baseline audit — 2026-10-07

## tl;dr

- The official-source harvest contains **863 rows in nine China snapshots**. All 863 rows have an official-release date and row-level publication evidence; this confirms when the stored values were released, not that the history is complete or revision-free.
- China coverage is still short: NBS activity / prices / PMI begin in 2021, PBOC M1/M2 in 2009, and TSF stock growth in 2014. Under the frozen-snapshot, official-release-only rule baseline, China Growth / Inflation regimes exist for **23 months, November 2024–September 2026**.
- The US latest-vintage FRED baseline covers January 2005–September 2026 as a panel, but complete Growth / Inflation regimes begin only in January 2009. September–December 2008 are unclassified because the configured 12-month changes and 36-month expanding history have not warmed up.
- The US rule baseline classifies March–June 2020 as recession, July as goldilocks, and August–December as recession. It classifies January–October 2022 as reflation and November–December as stagflation. These are interpretable outputs, not an independent validation of the model.
- US cross-asset forward-return analysis did not complete: Yahoo's `yfinance` path returned HTTP 429 on SPY. No regime-return conclusions or HMM recommendation can be supported yet.

The reproducible China snapshot audit is in [the executed notebook](../../notebooks/china_snapshot_quality.ipynb). It validates each tracked snapshot, lists period gaps, rebuilds the partial China factors/states and plots the actual coverage.

## Scope and method

The harvest requested 2005–2026, searched the currently indexed National Bureau of Statistics (NBS) and People's Bank of China (PBOC) releases, and retained source URLs at row level. `available_date` comes from an explicit publication date on the official release page or its official availability evidence; it is never inferred from an article URL or a planned release calendar. For each row, `source_value_url` points to the release that establishes the value, and `availability_evidence_url` points to the page establishing when it was public. A 2011 PBOC M1/M2 release uses the linked PBOC PDF for the value and its official HTML page for the publication date.

The China baseline uses `official_release_only`, a 36-month expanding z-score warm-up, and factor minimum-component counts from `config/factors.yaml`. Missing snapshots are skipped only in explicitly partial mode; this does not lower the configured minimum. Snapshot observations are aligned by their publication date. The collected historical releases preserve the value documented at that release where available, but the project does not yet have a complete vintage/revision database or a full revision audit.

The US baseline uses the current FRED vintage with the repository's conservative approximate release lags. It is useful for pipeline and episode checks, but current FRED history can contain revisions and the release lags are not a substitute for FRED vintage dates. The baseline is not a point-in-time trading backtest. PR #12 (`feat/alfred-catalog-archive`) is open against `main`: it proposes ALFRED for the US catalogue and a quarterly archive for four inflation series. It does not yet provide vintage history for the other 14 US series, so even if merged it would not make the full US macro panel point-in-time complete.

## China source coverage

| Series | Rows | Observation coverage | Date evidence | Gaps and definition notes |
|---|---:|---|---:|---|
| Industrial production YoY | 55 | 2021-09–2026-08 | 55/55 | Separate January rows absent because Jan–Feb is released jointly and assigned to February. |
| Retail sales YoY | 54 | 2021-10–2026-08 | 54/54 | Separate January rows absent for joint Jan–Feb releases. December YoY is extracted from the explicit December sentence in the annual release, not the annual total. |
| Fixed asset investment YTD YoY | 55 | 2021-09–2026-08 | 55/55 | Separate January rows absent for joint Jan–Feb releases. The published cumulative YoY is retained as-is. |
| CPI YoY | 59 | 2021-10–2026-08 | 59/59 | No period gaps within the collected range. |
| PPI YoY | 59 | 2021-10–2026-08 | 59/59 | No period gaps within the collected range. |
| PMI new orders | 60 | 2021-10–2026-09 | 60/60 | Index level; model transforms by subtracting 50. |
| M1 YoY | 201 | 2009-11–2026-08 | 201/201 | 2025-01 is absent from the indexed source archive. |
| M2 YoY | 201 | 2009-11–2026-08 | 201/201 | 2025-01 is absent from the indexed source archive. |
| TSF stock YoY | 119 | 2014-12–2025-08 | 119/119 | Gaps: 2015-01, 2015-02, 2015-04, 2015-05, 2015-07, 2015-08, 2015-10, 2015-11, 2016-02, 2025-01. Annual December reports fill only December, not the missing monthly values. |

All currently stored rows are marked `official_release`; there are **zero unknown release dates** and zero unverified rows. That is a claim about these 863 stored rows, not about periods without a row. NBS Jan–Feb observations were assigned to February so the period is not made available before it was complete. The PBOC archive scan did not recover its omitted January 2025 rows or monthly TSF releases after August 2025.

## China factors and state history

The snapshot inputs align into a 261-month calendar panel from January 2005 to September 2026; the early empty panel is a calendar scaffold, not observed history.

| Output | Valid months | First valid | Last valid | Interpretation limit |
|---|---:|---|---|---|
| Growth | 24 | 2024-10 | 2026-09 | Available components are industrial production, retail, fixed investment and PMI; enough expanding history is required. |
| Inflation | 23 | 2024-11 | 2026-09 | CPI and PPI only; core CPI is missing. |
| Liquidity | 166 | 2012-12 | 2026-09 | M1/M2/TSF only; the configured China 10-year yield-change component is missing. |
| Growth × Inflation regime | 23 | 2024-11 | 2026-09 | 9 goldilocks, 7 recession, 5 stagflation, 2 reflation months. |

The current China labels cannot explain 2008, 2020 or 2022: there are no relevant Chinese observations in the harvested source panel. The four-state China output is an exploratory short-window baseline, not a historical result.

## US rule baseline episode checks

| Period | Observed baseline state | Factor evidence | Reading and limitation |
|---|---|---|---|
| 2008-09–2008-12 | No complete state | Growth and Inflation are still missing; Real Rate is present. | The current 36-month z-score warm-up plus 12-month transformations do not date the onset of the crisis. The model first emits recession in January 2009. |
| 2009-01–2009-06 | Recession | Growth/inflation factors are strongly below their expanding historical means (January factors: -3.38 / -3.32). | Consistent with a severe contraction, but it follows the event onset. |
| 2020-03–2020-06 | Recession | Growth factor: -3.03, -15.35, -3.05, -1.07. Liquidity rises to 2.94 by May. | The sharp growth drop and supportive liquidity reflect the pandemic shock and policy response. The April score is an extreme observation and deserves sensitivity checks. |
| 2020-07 | Goldilocks | Growth turns just positive (0.05) while inflation remains negative (-0.97); liquidity remains very supportive (2.60). | A one-month label shows the threshold classifier can flicker near zero; there is no persistence rule. |
| 2020-08–2020-12 | Recession | Growth is near/below zero while inflation remains below its expanding mean. | The prolonged recession label is a mechanical factor-quadrant output, not a US recession-dating series. |
| 2022-01–2022-10 | Reflation | Inflation factor falls from 4.99 in January to 3.02 in October; Growth remains above zero (0.19 in October). | The current basket says inflationary expansion, although it does not represent all supply, labor, or policy channels. |
| 2022-11–2022-12 | Stagflation | Growth turns slightly negative (-0.03 / -0.06) while inflation remains elevated (2.75 / 2.37). | The label shift follows the sign threshold and expanding standardization; it is not parameter-optimized. |

US regime counts over the valid January 2009–September 2026 history: 74 goldilocks, 70 reflation, 40 stagflation, 29 recession months. Because historical FRED observations may be revised, these episode labels are not vintage-safe. They are reasonable broad readings for 2020 and late 2022, while the 2008 onset gap and 2020 one-month label flicker demonstrate limitations.

## Point-in-time and research-quality audit

| Check | Current status |
|---|---|
| China value source and release date evidence | Passed for all collected rows; both links retained row-by-row. |
| Unknown dates represented explicitly | No unknown dates among collected rows. Missing periods remain absent, not date-imputed. |
| No double YoY transform on China published growth rates | Passed: industrial production, retail sales, FAI, CPI/PPI, M1/M2 and TSF YoY are configured as levels; FAI remains cumulative YoY. |
| Release-date alignment | Official-release-only policy used in the China baseline; observation months and available dates remain separate. |
| China source completeness | Failed for intended 2005–2026 scope: older NBS history, CN core CPI, China 10-year yields, Customs and SAFE are still missing. |
| Revision/vintage audit | Incomplete. Historical published rows are retained where recovered, but revisions and source vintage lineage are not comprehensively reconstructed. |
| US point-in-time vintages | Incomplete on `main`. PR #12 is still open and its proposed archive covers only four inflation series; the other 14 are not archived. |
| Cross-asset outcomes / costs / benchmarks | Not yet produced. Yahoo `yfinance` returned HTTP 429 on SPY, so the attempted US forward-return run stopped before writing output. |
| Walk-forward / out-of-sample | Not yet completed. |
| HMM / Markov-switching readiness | **No.** First complete source history, repair the US asset-price path, map China/US/global assets, then establish baseline persistence and out-of-sample explanation after costs and benchmark comparisons. |

## Next research gates

1. Recover official NBS observations before 2021 and PBOC monthly observations omitted from the indexed archive. Preserve each source page and actual publication date; explicitly mark unresolved dates unknown if only observation values can be found.
2. Acquire official China core CPI, government-bond yields, Customs and SAFE data with row-level source/value and release evidence.
3. Resolve Yahoo's rate limit with an independently tested chart endpoint fallback and rerun US 3M/6M/12M returns.
4. Build China / US joint states with an explicit global transmission layer (USD, commodities, US real rates and Chinese credit/liquidity), then evaluate Shanghai/Shenzhen, Hong Kong, US equities, duration, gold, commodities and USD/CNY.
5. Add walk-forward splits, a release-lag delay sensitivity, revision/vintage checks, transaction costs, and simple benchmarks before considering any learned or probabilistic state model.
