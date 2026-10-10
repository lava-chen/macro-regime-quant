# Project status audit — 2026-10-10

This audit distinguishes code present in `main` at `0af82015b8b48ea8ba240f2bec2e393d8572dbe7` from capabilities that were exercised. A YAML entry, API schema, scheduled workflow, or deployment reference alone is not treated as proof that a service is operating.

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
| Python tests and lint | verified | On the P0 work branch: `pytest -q` reports 220 passed and 2 local HTTP tests skipped; Ruff passes. The two skips are due to sandbox socket restrictions and must run in GitHub CI. |
| China NBS/PBOC watch | implemented | Merged PR #21 added collectors, append-only releases/revisions, availability evidence, quality audit, PIT `as_of` report, and scheduled/manual refresh. `docs/china_macro_data_watch.md` lists missing core CPI evidence and unimplemented GDP, profits, unemployment, property, and China 10-year yield coverage. |
| China factor/regime report | partial | Growth, Inflation, Liquidity and Real Rate have data-availability checks; under-sourced factors return insufficient data. The published report is an evidence-backed research summary, not a complete cross-asset allocation engine. |
| CBOE VIX history and updater | implemented | Merged PR #20 added the CBOE CSV updater, validation, tests, and a scheduled/manual workflow. Its PR records an imported snapshot through 2026-09-22 and says the scheduled refresh had not yet run; do not infer current freshness from the workflow file. Source redistribution rights remain a review item. |
| FRED current-vintage macro access | implemented | The repository has FRED-backed macro sources and ordinary historical research paths. Latest-revised history is not point-in-time historical evidence. |
| ALFRED vintage archive wired into historical decisions | partial | PR #17 is still an open draft, based on `ea0132ad8109975c63f4fd600ceb6a651f4eb007`, and explicitly says `load_monthly_panel`, `build_us_baseline`, and `test-idea` do not read an as-of vintage. It has 339 changed files and is not safe to merge as a unit. |
| Yahoo market-price adapter and local snapshots | implemented | The backtest workbench can load normalized daily adjusted prices from the configured provider or a local snapshot. Provider history is revised and not PIT. A scheduled market-price refresh is not present on `main`; this audit's GLD/QQQ snapshots remain local and are not committed. |
| SEC/EDGAR fundamentals | partial | The repository contains an SEC research-data path and PIT-oriented contract. Coverage and company/tag validation are not broad enough to claim a complete, production-grade fundamentals panel. |
| Ordinary backtest engine before this P0 branch | partial | The original engine and cash-flow runner existed, but hand-computable regression cases exposed same-day return attribution, skipped cash-flow rebalancing, cash pseudo-asset turnover, and strategy-version retention issues. The fixes and regression tests are in this P0 change set; see the PR checks before treating them as merged. |
| Backtest HTTP API | implemented | Merged PR #22 provides a bearer-token-protected synchronous HTTP API and OpenAPI schema. It has no durable asynchronous `run_id` queue or task-status store. It does not place orders. |
| Strategy version history | implemented in this P0 change set | Before the P0 change, the local strategy store kept only the latest spec. The change archives immutable `vN.json` records and makes reports identify the exact strategy version and hash. |
| Three suggested stock-data repositories | partial | Their names and licensing caveats are indexed in `config/external_market_sources.yaml`. No raw data is mirrored because the repositories do not provide a verified redistribution license. |
| GitHub Actions | implemented | Main has CI plus China macro and VIX update workflows. No workflow proves that every scheduled source is currently fresh; scheduled runs can be delayed or fail. |
| Supabase and R2 | planned | `config/storage.yaml` has disabled routing. No credentials or deployed storage integration are configured. |
| Vercel dashboard/backend | planned | A future consumer is named in configuration; no deployed Vercel service was verified. |
| Existing private ChatGPT Site | partial | The owner Site is live and privately restricted. Its current published version does not declare an MCP server, so there is no Site-hosted plugin to call. No Sites project binding is present in this repository. |
| PR #19 dashboard code | partial | PR #19 remains open, is not based on current `main`, and its description states it uses revised US history and has no validated PIT backtest. Its referenced Site publication is older than the current Site version. Do not merge the PR wholesale. |

## PR audit

| PR | Current state | Decision |
| --- | --- | --- |
| #17 | Open draft; base is stale; 339 files | Keep unmerged. Rebase only after isolating the ALFRED archive changes, then implement per-decision-date vintage loading and a no-fallback PIT test. |
| #19 | Open; base is stale; 15 files / 11,123 additions | Keep unmerged. Reconcile the dashboard with current main and current Site version; remove duplicate data workflows and make every data claim match tested behavior. |
| #20–#24 | Merged; #24 produced current main | Audited as history; their merged content is not proof that every scheduled job or external deployment is operating now. |

## P0 audit performed on this branch

The P0 change establishes close-to-close return ownership, next-close execution, post-market transaction fees, cash as a non-traded residual, periodic rebalancing on the contribution path, strict shared-history completeness, and immutable saved strategy versions. Tests use hand-calculable prices and a separate dollar ledger. The GLD/QQQ audit additionally records both source hashes and an aligned-input hash in a local report; these private market files and generated performance reports are excluded from this public repository.

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

This audit deliberately does not claim withdrawals, cross-exchange holiday inference, point-in-time Yahoo prices, or automatic asynchronous backtest execution. Those require additional contracts and validation.
