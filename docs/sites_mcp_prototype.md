# Sites/MCP connection status — 2026-10-12

## Published Site and plugin

The owner-private Macro Quant Terminal Site has MCP-enabled version 8 published. The Site version was built from source commit `b8b2696cc4dfa6ec4951df51eb46a5aa9c54bfe4`. The owner has installed its private plugin in ChatGPT.

A direct ChatGPT tool-discovery and read-only call was verified on 2026-10-12. ChatGPT exposed all eight tools; `list_assets`, `get_data_status`, and `list_strategies` reached the Site Worker but returned `backend_unconfigured`, because the Site has no Python API URL or token configured. The real calculation and report path has not yet been verified.

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

## Required API deployment

The Python API is in this repository. Its async queue, reports, strategies, and price snapshots live under `MRQ_STATE_DIR`. It uses SQLite and one worker thread in one API process, so the host must provide persistent disk and run a single instance.

A production Docker/Caddy setup and step-by-step instructions are provided in [deploy_backtest_api.md](deploy_backtest_api.md). The API host needs a public HTTPS hostname and a long random bearer token.

The Site production environment currently has no variables configured. Set these keys after the API is reachable:

- `MRQ_BACKTEST_API_URL`: root HTTPS URL of the Python API.
- `MRQ_BACKTEST_API_TOKEN`: API bearer token, stored as a Site secret.

Updating Site runtime variables requires redeploying the saved MCP-enabled version before the new runtime revision is active. Keep the Site owner-private.

## Acceptance check after connecting the API

1. From the installed plugin, call `list_assets`, `get_data_status`, and `list_strategies`; these should return API data rather than `backend_unconfigured`.
2. Save a small declarative test strategy and submit one short backtest.
3. Poll its `run_id`, fetch the report, and verify that its inputs and report remain available after the API container is restarted.

## Reproduce local Worker checks

From the Site source checkout:

```bash
npm run build
npm run validate
node --test-reporter=spec tests/worker.test.mjs
node --check live-backtests.js
```

Ten Node tests passed against a mocked HTTPS Python API. They cover MCP discovery/tool inventory, snapshot-quality merge, zero-yield `CASH`, saved strategies, idempotent run submission, status/report/compare, dashboard links, missing/misconfigured backend fail-closed behavior, no identity-header forwarding, compressed asset serving, and rejected methods. These are prototype integration tests only; they do not prove a live API calculation.
