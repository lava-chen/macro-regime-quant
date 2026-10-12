# Sites/MCP connection status — 2026-10-12

## Published Site and installed plugin

The owner-private Macro Quant Terminal Site has MCP-enabled v8 published, and the user has installed the private plugin. ChatGPT exposes the eight tools: `list_assets`, `get_data_status`, `list_strategies`, `save_strategy`, `run_backtest`, `get_backtest_status`, `get_backtest_report`, and `compare_backtests`.

The tools are discoverable, but real calculations are not connected yet. On 2026-10-12, read-only calls to `list_assets` and `get_data_status` returned `backend_unconfigured` because `MRQ_BACKTEST_API_URL` and `MRQ_BACKTEST_API_TOKEN` are not set on the Site.

## Deployment decision

The production backend will not use a persistent Python HTTP server. Keep the existing MCP tool definitions and route their HTTP calls to a Supabase Edge Function. Supabase manages the private control plane and artifacts; GitHub Actions runs the existing Python engine on a temporary worker.

| Component | Current state |
|---|---|
| Private Site v8 and plugin | Published and installed |
| Eight MCP tools | Available; backend URL/token not configured |
| Supabase project for this repo | Not provisioned; existing Duya projects are unrelated and must not be reused |
| Edge Function and schema | Prepared in the architecture PR; not deployed |
| GitHub Actions worker | Added to the architecture PR; requires merge and repository secrets |
| Real backtest and report retrieval | Not yet verified |

The full setup and acceptance checklist is in [supabase_github_actions.md](supabase_github_actions.md). After the dedicated Supabase project is ready, configure the Site with:

- `MRQ_BACKTEST_API_URL=https://YOUR_PROJECT_REF.supabase.co/functions/v1/mrq-api`
- `MRQ_BACKTEST_API_TOKEN` equal to the Edge Function's `MRQ_MCP_API_TOKEN`

Redeploy the private MCP-enabled Site version after setting the server-side environment values. Never put the GitHub dispatch token or Supabase secret key in the Site.

## Acceptance checks

1. `list_assets`, `get_data_status`, and `list_strategies` return configured backend data.
2. Save a small strategy, submit a short run, and poll its `run_id` to a terminal state.
3. Retrieve the full report, then compare it with a second run.
4. Confirm the report and price snapshot are in private Supabase Storage and unauthenticated requests are rejected.

The direct plugin call currently fails before reaching a calculation worker. Do not mark the ChatGPT connection complete until the checks above pass against the real Supabase and Actions deployment.
