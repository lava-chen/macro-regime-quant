# ChatGPT backtest connection

The repository now exposes an authenticated REST API for strategy storage and repeatable backtests. ChatGPT connects through a Custom GPT Action using `chatgpt/openapi.yaml`; the API itself does not receive natural-language instructions. ChatGPT turns a request into the explicit `Strategy` JSON, and the API validates and runs it.

## Local run

```bash
uv sync --locked --all-packages --group dev --all-extras
export MRQ_API_TOKEN="replace-with-a-long-random-secret"
export MRQ_STATE_DIR="$PWD/.local-state"
uv run --locked --package mrq-cli macro-regime-quant serve-backtest-api
```

The local API listens on `127.0.0.1:8000`. Check `/health` for liveness. Every other endpoint requires `Authorization: Bearer $MRQ_API_TOKEN`. The endpoint uses Python's standard-library HTTP server; it adds no application-framework dependency to the five-package workspace.

## Connect a Custom GPT Action

1. Deploy this API to an HTTPS host you control, or use a private-network tunnel. Set `MRQ_API_TOKEN` as a secret on the server and mount persistent storage at `MRQ_STATE_DIR`.
2. Replace `https://YOUR-BACKTEST-HOST.example` in `chatgpt/openapi.yaml` with the deployed base URL, then import the file in the GPT editor's Actions section.
3. Configure Bearer authentication with the same API token. Keep the token out of this repository and out of conversation text.
4. Test `listAssets`, save one named strategy, then call `runBacktest`. A Custom GPT Action is selected by using that GPT; an ordinary chat does not inherit the Action automatically.

The API has no order placement, account access, or arbitrary code execution endpoints. The run endpoint writes a report and exact input price snapshot to persistent local storage so the result can be replayed. If the host has ephemeral disk, mount a persistent volume before using saved strategies.

## Strategy input and defaults

Weights are long-only, must sum to 1, and may include `CASH`. Supported schedules are `buy_and_hold`, `monthly`, `quarterly`, and `annual`. Start and end dates are inclusive. Defaults are monthly rebalancing, USD 10,000 starting capital, and 5 bps transaction cost. For GLD/QQQ the strategy currency is USD; no FX conversion, taxes, fund premiums, or impact costs are modeled.

Example:

```json
{
  "strategy": {
    "name": "Gold and Nasdaq 50-50",
    "weights": {"GLD": 0.5, "QQQ": 0.5},
    "rebalance_frequency": "monthly",
    "start_date": "2010-01-01",
    "initial_capital": 100000,
    "transaction_cost_bps": 5
  }
}
```

An optional `cash_flow` plan adds a weekly deposit, cumulative time-weighted-return take-profit tiers, and peak-to-trough drawdown exposure caps. For example, `return_threshold: 0.20` is a one-time 20% TWR trigger; `sell_fraction: 0.10` sells 10% of risk holdings at the next available close. A `trigger_drawdown: 0.15` rule with `max_invested_weight: 0.50` caps risk assets at 50% of account equity after the next-close execution. Deposits are excluded from TWR and drawdown; the report includes XIRR, all contributions, paid fees, cash balance, trade/event logs, and a same-cash-flow DCA-only benchmark. A drawdown cap reduces exposure but cannot guarantee a maximum loss.

```json
{
  "cash_flow": {
    "weekly_contribution_amount": 50,
    "contribution_day": "FRI",
    "take_profit_tiers": [
      {"return_threshold": 0.20, "sell_fraction": 0.10},
      {"return_threshold": 0.35, "sell_fraction": 0.15},
      {"return_threshold": 0.50, "sell_fraction": 0.20}
    ],
    "drawdown_rules": [
      {"trigger_drawdown": 0.15, "max_invested_weight": 0.50},
      {"trigger_drawdown": 0.25, "max_invested_weight": 0.25}
    ]
  }
}
```

## Data and interpretation

The default market source is the repository's configured Yahoo adapter with `auto_adjust=True`; a normalized local file at `data/raw/market/<SYMBOL>.csv` takes priority. Local files use `date` or `observation_date` plus `adjusted_close`, `adj_close`, `close`, or `value`. The report includes source dates, retrieval time, price basis, input checksum, aligned date range, and missing-row count. Yahoo data is used for the personal backtest, not mirrored into the public repository. Provider history is not a point-in-time vintage, so a run is reproducible from its stored snapshot but does not establish historical publication-time availability of adjusted prices.

The three external repositories `guidebee/china-stock-data`, `henrywuu91/quant-data`, and `modi-hu/stock-data` are currently source references only. No raw files from them are mirrored by this API. Check licensing, schemas, corporate-action conventions, and date coverage before importing any of their files.

## API endpoints

- `GET /health`: liveness check.
- `GET /assets`: configured assets.
- `GET /strategies`, `POST /strategies`, `PUT /strategies/{id}`: list, save, and version updates to strategies.
- `POST /backtests`: run an inline or saved strategy and persist a report/snapshot.
- `GET /backtests/{run_id}`: retrieve a saved report.
