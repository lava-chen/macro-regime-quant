# Sites/MCP prototype status — 2026-10-10

## What exists

The connected Macro Quant Terminal Site has a new source version saved as version 8, built from source commit `b8b2696cc4dfa6ec4951df51eb46a5aa9c54bfe4`. The Site stays owner-private. Version 8 was **saved but not deployed**; the current published version remains v7, a static page without an MCP server.

The Worker conversion preserves the existing terminal, embeds 15 static UI/data assets as gzip-compressed bytes (about 1.05 MB source to 169 KB gzip), and adds eight stateless MCP tools:

- `list_assets`
- `list_strategies`
- `save_strategy`
- `run_backtest`
- `get_backtest_status`
- `get_backtest_report`
- `compare_backtests`
- `get_data_status`

The Site UI adds a multi-asset allocation form and a `run_id` report lookup. The MCP `dashboard_url` points to that same run ID so the Site reads the Python API's persisted report. `CASH` is explicitly modeled at 0% annual yield; the tool does not describe it as a deposit or treasury yield.

The Worker is a fixed HTTPS relay. It validates run IDs, keeps the API bearer secret server-side, rejects redirects and untrusted API URLs, and does not forward Site identity headers to the Python API. Strategy input is declarative; the tools cannot execute Python or place orders.

## Reproduce local checks

From the Site source checkout:

```bash
npm run build
npm run validate
node --test-reporter=spec tests/worker.test.mjs
node --check live-backtests.js
```

Ten Node tests passed against a mocked HTTPS Python API. They cover MCP discovery/tool inventory, snapshot-quality merge, zero-yield `CASH`, saved strategies, idempotent run submission, status/report/compare, dashboard links, missing/misconfigured backend fail-closed behavior, no identity-header forwarding, compressed asset serving, and rejected methods. These are prototype integration tests only; they do not prove a live Sites dispatch or ChatGPT plugin call.

## Connection required before publishing

The Python API is in this repository. PR #26 added `POST /backtest-runs`, `GET /backtest-runs/{run_id}`, and `GET /data-status`. Its SQLite queue and report/snapshot files require a persistent filesystem and a single API process. They are not suitable for an ephemeral filesystem or horizontal scaling as currently implemented.

The Site Worker needs runtime secrets:

- `MRQ_BACKTEST_API_URL`: root HTTPS URL for the Python API.
- `MRQ_BACKTEST_API_TOKEN`: API bearer token.

Neither value is configured. Without them, `server/discover` and `tools/list` respond, but API-backed MCP and UI calls return `backend_unconfigured`. The Site has not been published with MCP, so there is no provisioned or installable plugin yet. OpenAI's Site flow creates the private plugin when the owner publishes an MCP-enabled Site, then refreshes its tool list on republish: <https://help.openai.com/en/articles/12584461-hosting-a-plugin-with-chatgpt-sites>.

Before publication, choose a Python execution/storage host, configure the two secrets, keep user reports and price snapshots private, and verify one real API run through both MCP and the Site page. The current Site version is unchanged pending those deployment and secret decisions.
