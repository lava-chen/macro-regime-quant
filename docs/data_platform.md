# Data platform routing

The active path stays inside the five-package workspace:

```text
official sources → mrq-data → append-only PIT ledger → mrq-engines → JSON / Markdown report
```

| Data or job | Current destination | Planned destination |
|---|---|---|
| China macro raw snapshots and release evidence | `data/raw/china/china_macro_snapshots.csv` | Partitioned Parquet in R2 if volume justifies it |
| Refresh checkpoint and audit | Git-tracked JSON | Supabase refresh-run metadata index |
| Local factor and research work | Existing five packages | DuckDB local cache at `data/cache/mrq.duckdb` |
| Large US/A-share prices, financials, and vintages | Not imported by this change | Cloudflare R2 Parquet objects |
| Strategies and backtest run indexes | Existing research package files | Supabase Postgres tables |
| Dashboard feed | `reports/china_macro/latest.json` | Vercel terminal reads the stable JSON contract |

`config/storage.yaml` records this routing. R2 and Supabase are disabled until account configuration and secrets are present. GitHub Actions currently commits only the small macro ledger, checkpoint, and latest reports. The existing VIX refresh workflow continues to own its VIX CSV output.

The three user-suggested equity repositories are indexed in `config/external_market_sources.yaml`. Their repository metadata currently has no license declaration. Because the main repository is public, this change does not copy their 116 MB–701 MB snapshots into Git, R2, or a public database. Before enabling those imports, verify redistribution rights, date coverage, adjustment rules, survivorship, and raw units. Their daily files belong in R2 once licensed; DuckDB can then read selected partitions without adding them to Git history.

For Supabase, use small catalog, refresh-run, strategy, and backtest-index rows only. Keep raw prices and full vintage history in R2. Never put credentials in YAML or workflow source; configure the `MRQ_R2_*` and `SUPABASE_DB_URL` secrets in the repository environment when provisioning is authorized.
