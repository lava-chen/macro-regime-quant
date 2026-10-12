create index if not exists mrq_asset_data_status_last_run_id_idx
    on public.mrq_asset_data_status (last_run_id);

create index if not exists mrq_backtest_runs_strategy_id_idx
    on public.mrq_backtest_runs (strategy_id);
