# Macro Quant Terminal data export

The terminal consumes four versioned JSON files from the public
`macro-quant-terminal-data` branch. That branch contains public price and macro
series only. It must not contain account data, holdings, transaction history,
API keys, or private experiments.

| File | Contract |
|---|---|
| `data/exports/terminal-v0.1/summary.json` | Latest attempt state, last successful update per source, code commit, factor config version, coverage and PIT warnings. |
| `data/exports/terminal-v0.1/series.json` | Daily market and monthly macro series. Every point carries its date, value and availability basis; source records carry unit, source, coverage and limitations. |
| `data/exports/terminal-v0.1/regimes.json` | Exported Growth × Inflation regime histories by country. |
| `data/exports/terminal-v0.1/backtests.json` | Explicit `not_exported` state until verified result artifacts exist. No synthetic metrics are supplied. |

`uv run python scripts/export_terminal_data.py` refreshes the public series.
Yahoo history is requested in one batch and subsequent runs re-fetch a short
overlap before merging dates. FRED history is small enough to rebuild as a
monthly panel and is labelled latest-vintage, not PIT-safe. Failed sources
update `summary.json`; their prior series remain intact. The source errors are
visible in Data Status.

`.github/workflows/terminal-data.yml` runs on weekdays at 07:30 Asia/Shanghai,
can be run with `workflow_dispatch`, and refreshes the NBS/PBOC archive monthly
or on a manual run with `refresh_china` selected. The workflow uses the
repository-scoped `GITHUB_TOKEN` to write only public exports and official
snapshots to the data branch. No provider API key is configured or committed.

The web terminal reads this branch directly as a public read-only feed, while
the Site itself is owner-private. The Site's app source is hosted by Sites; the
existing GitHub repository is the data and research source, not a directly
linked Sites deployment repository.

## Research gate

- China snapshots are frozen official release observations with uneven start
  dates and missing components; reported factors are partial.
- US factors use the latest FRED vintage with approximate release lags.
  Historical revisions may change earlier values.
- Draft PR #17 contains ALFRED archive work, but the archive is not connected
  to the historical as-of panel on current `main`.
- Issue #18 remains open. The terminal does not claim PIT-safe backtests,
  walk-forward validation, or out-of-sample performance.
