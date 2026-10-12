# Supabase control plane and GitHub Actions worker

This is the production path for the private Macro Quant Terminal Site. The eight MCP tools stay in the Site; their HTTPS API base changes to the Supabase Edge Function. Supabase owns authentication, strategy versions, run state, report metrics, a per-symbol data-status index, and private artifacts. GitHub Actions starts a temporary Ubuntu runner for each queued backtest and runs the repository's existing Python engine. No persistent Python HTTP server is part of this deployment.

## Request and data flow

1. The Site Worker calls `POST /backtest-runs` with its bearer token.
2. The `mrq-api` Edge Function validates the declarative strategy, stores an idempotent queued run in Postgres, and dispatches `run-backtest.yml` through the GitHub API.
3. The workflow checks out `main`, installs the existing `mrq-cli` workspace, atomically claims the run, and executes `execute_backtest` in a temporary runner directory.
4. The worker uploads `report.json`, `report.md`, and `price_snapshot.csv` to the private `mrq-backtest-artifacts` bucket. It writes report metrics, checksums, and the latest successful per-symbol source coverage to Postgres, then marks the run `succeeded`.
5. The Site Worker polls `GET /backtest-runs/{run_id}` and reads `GET /backtests/{run_id}`. The compare tool can fetch multiple stored reports through the same route.

The adapter keeps the current API paths used by the MCP tools: `/assets`, `/data-status`, `/strategies`, `/strategies/{id}/versions`, `/backtest-runs`, `/backtests`, and `/backtests/{run_id}`. `POST /backtests` is accepted as an asynchronous alias for compatibility.

## Provision the Supabase project

Use a dedicated project for Macro Regime Quant. Do not reuse the unrelated Duya projects. After creating and linking the project with the Supabase CLI, generate a timestamped migration with the CLI and copy the contents of `supabase/schema.sql` into it:

```bash
supabase init
supabase link --project-ref YOUR_PROJECT_REF
supabase migration new mrq_control_plane
# Copy supabase/schema.sql into the new migration file.
supabase db push
```

The SQL creates the strategy/version tables, backtest queue, report metrics table, worker claim functions, supported market symbols, a per-symbol latest source-status index, and a private Storage bucket. The tables have RLS enabled, and direct grants are revoked from `anon` and `authenticated`; only the backend `service_role` can access them. The bucket is private and has no public read policy.

## Configure secrets

Generate a random API bearer token, then set these secrets for the Edge Function. Use a named Supabase secret key in place of the placeholder. Keep secret values out of shell history where practical.

```bash
openssl rand -hex 32
supabase secrets set \
  MRQ_MCP_API_TOKEN=YOUR_RANDOM_TOKEN \
  MRQ_SUPABASE_SECRET_KEY=YOUR_SUPABASE_SECRET_KEY \
  GITHUB_TOKEN=YOUR_FINE_GRAINED_GITHUB_TOKEN \
  GITHUB_REPOSITORY=lava-chen/macro-regime-quant
```

The fine-grained GitHub token is scoped to `lava-chen/macro-regime-quant` and needs **Actions: write** to call `workflow_dispatch`. It stays in Edge Function secrets. The Supabase secret key stays server-side in Edge Function secrets and GitHub Actions secrets; neither key belongs in the Site UI, MCP input, or repository.

Deploy the custom-auth function with gateway JWT verification disabled; the handler validates `MRQ_MCP_API_TOKEN` itself using a constant-time bearer comparison:

```bash
supabase functions deploy mrq-api --no-verify-jwt
```

Do not remove the handler's bearer check. The Edge Function URL is HTTPS, but it is not public access: every data route requires the private Site token.

In GitHub repository settings, add these Actions secrets:

- `MRQ_SUPABASE_URL`: the dedicated project's URL.
- `MRQ_SUPABASE_SECRET_KEY`: the backend secret key used by the temporary worker.

The worker has `contents: read`, checks out the trusted `main` ref, and has a 90-minute job timeout. It receives no GitHub dispatch token. The Edge Function alone can dispatch jobs.

## Connect the installed private Site

Set the Site's server-side environment variables:

- `MRQ_BACKTEST_API_URL=https://YOUR_PROJECT_REF.supabase.co/functions/v1/mrq-api`
- `MRQ_BACKTEST_API_TOKEN=the same random bearer token set as MRQ_MCP_API_TOKEN`

Redeploy the MCP-enabled private Site version after changing runtime variables. Keep the Site private. Never put the Supabase secret key or GitHub token in Site environment variables exposed to browser code.

## Verify the complete path

From the installed Macro Quant Terminal plugin:

1. Call `list_assets`, `get_data_status`, and `list_strategies`; confirm they no longer return `backend_unconfigured`.
2. Save a small strategy, submit a short backtest, and record the returned `run_id`.
3. Poll `get_backtest_status` until the status is `succeeded` or `failed`.
4. On success, call `get_backtest_report` and verify strategy inputs, metrics, the equity curve, data-source metadata, and the input snapshot checksum.
5. Submit a second strategy and call `compare_backtests` with both run IDs.
6. Confirm the report and CSV are in the private Storage bucket and that an unauthenticated API request is rejected.

The API's data-status endpoint distinguishes a configured Yahoo provider from a downloaded price snapshot. It must not be read as a point-in-time vintage claim. Current market price history is still provider-latest data; ALFRED vintage coverage, out-of-sample testing, and cross-source data licensing remain separate research-quality work.

## Local checks

```bash
uv run --locked pytest tests/test_backtest_worker.py
uv run --locked pytest
uv run --locked ruff check packages tests scripts
```

For a backtest that is killed after its worker lease expires, rerun the workflow for that same `run_id`; the claim function prevents duplicate active workers and permits recovery after the two-hour lease. Failed calculations remain failed and should be submitted as a new run after the input or data issue is fixed.
