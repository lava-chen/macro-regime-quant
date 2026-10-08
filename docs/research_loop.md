# Research loop

Write a signal, find out whether it predicts anything. Two minutes, not an
afternoon.

## The loop

**1. Write a file that exposes one function.**

```python
# ideas/my_idea.py
import pandas as pd

def signal(panel: pd.DataFrame) -> pd.Series:
    """panel is point-in-time: each row holds only what was knowable then."""
    inflation = panel["us_core_cpi"].pct_change(12, fill_method=None)
    growth = panel["us_payrolls"].pct_change(12, fill_method=None)
    return (inflation - growth).rank(pct=True)   # any score, higher = more bullish
```

Your function may be named `signal`, `idea`, `build_signal` or `main`.

**2. Tell it what it may look at, and what to predict.**

```bash
uv run macro-regime-quant test-idea ideas/my_idea.py \
    --series us_core_cpi,us_payrolls \
    --forward-assets us_oil_wti,us_copper_global \
    --start 2000-01-01
```

**3. Read the verdict.**

```
                 IC   RankIC    IC/IR       n
    1m        0.078    0.075     0.16     307
    3m        0.117    0.119     0.25     305
    6m        0.190    0.196     0.26     302
   12m        0.319    0.318     0.77     296

Verdict: best horizon 12m: IC +0.319 (strong, positive), IC/IR +0.77, n=296

Forward return by signal bucket:
 bucket  count mean_return  std_return
      0     60    -2.0341%    0.251133
      1     59    +9.8939%    0.210753
      2     59   +12.4337%    0.280136
      3     59    +9.2207%    0.357198
      4     59   +30.0200%    0.367259
  top-minus-bottom spread: +32.0541%

! IC varies by more than 3x across horizons — a single-horizon result is
  likely a coincidence, not a stable relationship
```

## What the numbers mean

| Column | Reading |
|---|---|
| **IC** | Pearson correlation between your signal and the forward return. Above 0.05 is worth pursuing; below 0.02 is noise. |
| **RankIC** | Same, on ranks — robust to outliers and to any monotone transform of your signal. Prefer it when the two disagree. |
| **IC/IR** | Rolling 12-month IC mean over its standard deviation. Measures whether the relationship *stayed* stable, not just whether it exists. Needs ≥24 observations. |
| **n** | Overlapping samples. Shrinks as the horizon grows, because long horizons need more runway. |
| **buckets** | Your signal sorted into quintiles. Monotonicity across buckets is the real test — a single good level means nothing. |

The tool warns you when the sample is small, when the signal is constant, and
when the IC swings wildly across horizons. Read those before you read the IC.

## Rules the loop enforces for you

**The panel is point-in-time.** Each row contains only what was knowable at that
month-end, so a signal cannot peek forward by construction. This is why the
FRED-backed series matter: use `alfred` where the catalogue offers it, because
`fred` returns revised values that did not exist at the time.

**Forward returns start after the signal.** There is no same-period join, and
the execution lag in the backtester exists for the same reason.

**Targets must be prices.** Rates and yields are rejected outright: a yield of
0.5% has no meaningful percentage return, and the ratio diverges. If your idea
is about rates, test it against something tradable instead.

**`--series` is a whitelist.** Only the columns you name reach your function,
so a result cannot silently depend on a series you did not intend to use.

## What to reach for

| Need | Series |
|---|---|
| Commodities | `us_oil_wti` (1986–), `us_copper_global` (1992–) |
| Dollar | `us_dollar_index_broad` (2006–), `eurusd` (1999–) |
| Equity | `sp500_proxy` via Yahoo — needs `uv sync --all-packages --extra data` |

FRED price series are used instead of Yahoo on purpose: yfinance rate-limits
hard from shared IPs, and a research loop that breaks on a Tuesday is not a
research loop.

## Making the inputs truly point-in-time

The loop is honest about *alignment* — the panel it hands you contains only what
was knowable at each month-end, and forward returns start afterwards. It cannot
fix the *inputs*. A series pulled from FRED today still carries today's revised
values, so a 2015 backtest is reading numbers that did not exist in 2015.

Pointing the catalogue at `alfred` is only half the answer, and on its own it
can be worse than useless: one pull with today's vintage, labelled `alfred`, is
still today's knowledge applied to the past. The missing piece is an archive.

```bash
uv run macro-regime-quant snapshot-vintages \
    --series us_core_cpi --start 2020-01-01 --step-months 3
```

This stores one snapshot per series per quarter under `data/vintages/`. Re-runs
skip what already exists, so the archive grows cheaply. Reading it is then
strictly historical: for a backtest dated *t*, the newest snapshot at or before
*t* is used, and if the archive does not reach back that far the read returns
nothing rather than quietly substituting current data.

The difference is visible. For CPI's 2021-06 observation:

| backtest dated | value visible then |
|---|---|
| 2021-10 | 270.981 |
| 2023-01 | 270.955 |
| 2024-01 | 270.559 |

A single FRED fetch returns 270.559 for all three rows and calls it history.

**Only series whose catalogue provider is `alfred` are snapshotted.** A series
that is never revised gains nothing, and pulling it anyway would cost minutes
for an identical result. Switching a catalogue entry from `fred` to `alfred` is
a deliberate, separate step.

An archive is only as honest as its oldest snapshot. A backtest starting before
the archive begins has no point-in-time data and should not be run.

## After a signal earns its place

Only once the IC is worth something, move to sizing and costs:

```python
from mrq_research.backtest import BacktestConfig, run_backtest

result = run_backtest(prices, target_weights, BacktestConfig(transaction_cost_bps=5))
print(result.metrics)
```

The backtester is long-only, charges on turnover, and delays execution by
`execution_lag_periods` so a signal never fills at the bar that produced it.

## Company fundamentals (US): SEC EDGAR

```python
from mrq_data.providers.sec import SecProvider

sec = SecProvider(user_agent="you <you@example.com>")
METRICS = {
    "NetCashProvidedByUsedInOperatingActivities": "ocf",
    "PaymentsToAcquirePropertyPlantAndEquipment": "capex",
}

# as_of is mandatory — there is no way to ask for "the numbers" without
# saying when you want to know them.
frame = sec.fundamentals_frame("AAPL", METRICS, as_of="2018-01-01")
```

Company filings are point-in-time **by construction** and need no archive.
EDGAR keeps every version ever filed, so `filed` is a real `available_date`
and filtering on it is the whole mechanism:

| vantage point | Apple's 2017-09-30 operating cash flow |
|---|---|
| 2018-01-01 | 63.598 B (filed 2017-11-03) |
| 2019-01-01 | 64.225 B (filed 2018-11-05, restated) |
| 2026-01-01 | 64.225 B (filed 2019-10-31) |

This is the difference from macro data, where a revision replaces history and
has to be archived separately. Here the old number was never overwritten — it
was always there, behind a date.

Facts are cached under `data/cache/sec/`; payloads run to megabytes and are
immutable once written.

### Choosing tags is the part that bites

EDGAR exposes 503 us-gaap tags for a large filer and they do not mean what
their names suggest. Apple's 2017-09-30 balance sheet:

| tag | value |
|---|---|
| `CashAndCashEquivalentsAtCarryingValue` | 20.29 B |
| `AvailableForSaleSecurities` | **268.89 B** |
| `LongTermDebtNoncurrent` | 97.21 B |

Taking only the first and third rows makes a net-cash company look
net-indebted by 77 B when it held 192 B net. The provider reports tags
faithfully; picking the right ones per business model is a modelling decision,
and belongs with the valuation work rather than the fetch layer.
