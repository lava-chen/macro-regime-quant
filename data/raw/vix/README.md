# CBOE VIX historical snapshots

Seeded from [datasets/finance-vix](https://github.com/datasets/finance-vix)
(1990-01-02 onward); future refreshes download the canonical [CBOE VIX history CSV](https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv)
directly using scripts/update_vix.py. The upstream project labels its package
ODC-PDDL-1.0, but explicitly says CBOE did not state an obvious license.
**That label is not proof of a CBOE redistribution grant.** Review original
provider terms before commercial redistribution.

Tracked files:
- vix-daily.csv: full daily DATE, OPEN, HIGH, LOW, CLOSE, original finance-vix layout.
- vix-monthly.csv: last observed trading-day CLOSE for each month, including the
  latest *possibly incomplete* month (faithful to finance-vix).
- vix_close.csv: MRQ CsvProvider-compatible daily CLOSE observation.
- vix_monthly_close.csv: only **completed** months' final trading-day CLOSE;
  latest observed month is intentionally withheld until a subsequent month appears.

In MRQ close series, available_date is trading_date + 1 *calendar* day,
availability_basis=fixed_lag. This is a **conservative model assumption**, not
evidence of the exact CBOE publication time, and prevents same-session trading
on a close that was not yet known. The catalog sets release_lag_days=1 to match.
For production, execute strictly after available_date; do not trade a VIX
closing value at the same session's closing price.

CBOE's early OHLC fields (through mid-2004) may repeat closing prices rather
than represent actual opening/intraday observations. Treat historical VIX OHLC
cautiously. **VIX is not a directly tradable price series** and must not be
used as a forward-return target. Changes to CBOE's historical methodology or
revisions are not covered by an ALFRED-like as-of vintage archive; Git commits
preserve whatever historical source changes the updater encounters.

Refresh manually:
    python scripts/update_vix.py

A scheduled GitHub Actions workflow (.github/workflows/refresh-vix.yml)
runs on Tuesday-Saturday at 05:30 UTC, commits only the four expected CSV files
when their content changes, and refuses truncated or stale full downloads.
This workflow requires GitHub Actions write permission via GITHUB_TOKEN.
