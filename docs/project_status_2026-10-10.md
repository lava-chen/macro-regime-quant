# Project status audit — 2026-10-10

This audit reflects `main` at `f6a7e15c87bb3365f4e37a1739ddceaf4f7c3756` and Site source version 8 saved on 2026-10-10. A YAML entry, API schema, scheduled workflow, or saved Site version alone is not treated as proof that a service is operating.

## Status definitions

- **verified** — exercised in this audit with a reproducible check.
- **implemented** — code and an operating contract are present, but live operation or important edge cases have not been independently verified here.
- **partial** — a working subset exists; documented coverage or correctness gaps remain.
- **planned** — documented as future work, without an end-to-end implementation.
- **blocked** — cannot be completed until a named external permission, service, credential, or user decision is available.

## Repository and project inventory

| Area | Status | Evidence and boundary |
| --- | --- | --- |
| Five-package dependency direction | verified | `python scripts/check_layering.py` passes for `mrq-core <- mrq-data <- mrq-engines <- mrq-research <- mrq-cli`. |
| Python tests and lint | verified | After PR #26: local `pytest -q` reports 225 passed and 2 local HTTP tests skipped; Ruff check and layering check pass. GitHub CI on PR #26 ran the HTTP integration test and finished green. |
| China NBS/PBOC watch | implemented | Merged PR #21 added collectors, append-only releases/revisions, availability evidence, quality audit, PIT `as_of` report, and scheduled/manual refresh. `docs/china_macro_data_watch.md` lists missing core CPI evidence and unimplemented GDP, profits, unemployment, property, and China 10-year yield coverage. |
| China factor/regime report | partial | Growth, Inflation, Liquidity and Real Rate have data-availability checks; under-sourced factors return insufficient data. The published report is an evidence-backed research summary, not a complete cross-asset allocation engine. |
| CBOE VIX history and updater | implemented | Merged PR #20 added the CBOE CSV updater, validation, tests, and a scheduled/manual workflow. Its PR records an imported snapshot through 2026-09-22 and says the scheduled refresh had not yet run; do not infer current freshness from the workflow file. Source redistribution rights remain a review item. |
| FRED current-vintage macro access | implemented | The repository has FRED-backed macro sources and ordinary historical research paths. Latest-revised history is not point-in-time historical evidence. |
| ALFRED vintage archive wired into historical decisions | partial | PR #17 is still an open draft, based on `ea0132ad8109975c63f4fd600ceb6a651f4eb007`, and explicitly says `load_monthly_panel`, `build_us_baseline`, and `test-idea` do not read an as-of vintage. It has 339 changed files and is not safe to merge as a unit. |
| Yahoo market-price adapter and local snapshots | implemented | The backtest workbench can load normalized daily adjusted prices from the configured provider or a local snapshot. Provider history is revised and not PIT. A scheduled market-price refresh is not present on `main`; this audit's GLD/QQQ snapshots remain local and are not committed. |
| SEC/EDGAR fundamentals | partial | The repository contains an SEC research-data path and PIT-oriented contract. Coverage and company/tag validation are not broad enough to claim a complete, production-grade fundamentals panel. |
| Ordinary backtest engine | verified | PR #25 fixed next-close execution ownership, cash-flow rebalancing, fee/turnover treatment, missing-price handling, and immutable strategy history. Small hand-calculable tests and an independent dollar ledger agree; private GLD/QQQ snapshots and full audit report are retained outside the public repository. Yahoo history is not PIT. |
| Backtest HTTP API | implemented | PR #22 provides bearer-token-protected synchronous endpoints. PR #26 adds an idempotent SQLite `run_id` queue, asynchronous status/report endpoints, and `/data-status`. The queue is one worker in one process and requires persistent storage; it is not a distributed or serverless queue. It does not place orders. |
| Strategy version history | verified | Strategy updates archive immutable `vN.json` records; reports identify the exact strategy version and hash. Regression tests retrieve an older version after an update. |
| Three suggested stock-data repositories | partial | Their names and licensing caveats are indexed in `config/external_market_sources.yaml`. No raw data is mirrored because the repositories do not provide a verified redistribution license. |
| GitHub Actions | implemented | Main has CI plus China macro and VIX update workflows. No workflow proves that every scheduled source is currently fresh; scheduled runs can be delayed or fail. |
| Supabase and R2 | planned | `config/storage.yaml` has disabled routing. No credentials or deployed storage integration are configured. |
| Vercel dashboard/backend | planned | A future consumer is named in configuration; no deployed Vercel service was verified. |
| Existing private ChatGPT Site | partial | Published v7 remains private static content without MCP. Source v8 was saved as a Worker + MCP artifact (source commit `b8b2696cc4dfa6ec4951df51eb46a5aa9c54bfe4`) but not deployed. Mock-backend tests pass; the live API variables are unset, no Site plugin is provisioned, and no real ChatGPT call has been made. |
| PR #19 dashboard code | partial | PR #19 remains open, is not based on current `main`, and its description states it uses revised US history and has no validated PIT backtest. Its referenced Site publication is older than the current Site version. Do not merge the PR wholesale. |

## PR audit

| PR | Current state | Decision |
| --- | --- | --- |
| #17 | Open draft; base is stale; 339 files | Keep unmerged. Rebase only after isolating the ALFRED archive changes, then implement per-decision-date vintage loading and a no-fallback PIT test. |
| #19 | Open; base is stale; 15 files / 11,123 additions | Keep unmerged. Reconcile the dashboard with current main and current Site version; remove duplicate data workflows and make every data claim match tested behavior. |
| #20–#25 | Merged | PR #25 is the verified P0 timing and accounting correction. Scheduled workflows still need live-run checks. |
| #26 | Merged at `f6a7e15c87bb3365f4e37a1739ddceaf4f7c3756` | Adds the single-instance async run queue and data-status endpoint. GitHub CI passes, including the HTTP integration test over fixture snapshots. |

## P0 audit on `main`

PR #25 establishes close-to-close return ownership, next-close execution, post-market transaction fees, cash as a non-traded residual, periodic rebalancing on the contribution path, strict shared-history completeness, and immutable saved strategy versions. Tests use hand-calculable prices and a separate dollar ledger. The GLD/QQQ audit additionally records both source hashes and an aligned-input hash in a local report; these private market files and generated performance reports are excluded from this public repository.

Reproduction commands:

```bash
uv sync --locked --all-packages --group dev
uv run --locked pytest -q
uv run --locked ruff check packages tests scripts
uv run --locked python scripts/check_layering.py
uv run --locked python scripts/backtest_smoke.py
uv run --locked python scripts/backtest_p0_timing_audit.py --project-root . --start-date 2004-11-18 --end-date 2026-10-09 --output-dir /tmp/gld_qqq_timing_audit
uv run --locked python scripts/backtest_mechanism_ablation.py --project-root . --start-date 2004-11-18 --end-date 2026-10-09 --output-dir /tmp/gld_qqq_mechanism_ablation
```

Remaining gaps include withdrawals, cross-exchange holiday inference, point-in-time Yahoo prices, variable cash yields, shared/multi-instance task storage, and a deployed backend. The async API is implemented and fixture-tested; it is not yet a production deployment.
