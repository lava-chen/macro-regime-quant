-- Bootstrap schema for the private Macro Regime Quant Supabase project.
-- Keep this file as the source SQL until a project is provisioned; create the
-- tracked migration with `supabase migration new mrq_control_plane` before
-- applying it with `supabase db push`.

create table if not exists public.mrq_assets (
    symbol text primary key check (symbol ~ '^[A-Z0-9.^=_-]{1,24}$'),
    kind text not null,
    provider text not null,
    created_at timestamptz not null default now()
);

insert into public.mrq_assets (symbol, kind, provider) values
    ('CASH', 'cash', 'synthetic'),
    ('CNY=X', 'fx', 'yahoo'),
    ('DBC', 'commodity', 'yahoo'),
    ('GLD', 'commodity', 'yahoo'),
    ('QQQ', 'market', 'yahoo'),
    ('SPY', 'market', 'yahoo'),
    ('TLT', 'market', 'yahoo')
on conflict (symbol) do update set
    kind = excluded.kind,
    provider = excluded.provider;

create table if not exists public.mrq_strategies (
    strategy_id text primary key check (strategy_id ~ '^[a-z0-9][a-z0-9_-]{0,63}$'),
    version integer not null check (version > 0),
    strategy_json jsonb not null check (jsonb_typeof(strategy_json) = 'object'),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create table if not exists public.mrq_strategy_versions (
    strategy_id text not null references public.mrq_strategies(strategy_id) on delete restrict,
    version integer not null check (version > 0),
    strategy_json jsonb not null check (jsonb_typeof(strategy_json) = 'object'),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    primary key (strategy_id, version)
);

create table if not exists public.mrq_backtest_runs (
    run_id text primary key check (run_id ~ '^bt_[0-9a-f]{24}$'),
    status text not null default 'queued'
        check (status in ('queued', 'running', 'succeeded', 'failed')),
    request_json jsonb not null check (jsonb_typeof(request_json) = 'object'),
    request_sha256 text not null check (request_sha256 ~ '^[0-9a-f]{64}$'),
    idempotency_key text unique check (idempotency_key is null or length(idempotency_key) between 1 and 128),
    strategy_id text references public.mrq_strategies(strategy_id) on delete set null,
    strategy_version integer,
    submitted_at timestamptz not null default now(),
    started_at timestamptz,
    finished_at timestamptz,
    backtest_id text,
    error text,
    attempts integer not null default 0 check (attempts >= 0),
    dispatch_status text not null default 'pending'
        check (dispatch_status in ('pending', 'dispatching', 'dispatched', 'failed')),
    dispatch_attempts integer not null default 0 check (dispatch_attempts >= 0),
    dispatch_claimed_at timestamptz,
    dispatched_at timestamptz,
    dispatch_error text,
    worker_execution_id text,
    lease_expires_at timestamptz,
    artifacts_prefix text,
    updated_at timestamptz not null default now()
);

create index if not exists mrq_backtest_runs_status_time_idx
    on public.mrq_backtest_runs (status, submitted_at desc);

create table if not exists public.mrq_asset_data_status (
    symbol text primary key references public.mrq_assets(symbol) on delete cascade,
    availability text not null check (availability in ('provider_configured', 'snapshot_available')),
    coverage_start date,
    coverage_end date,
    observations integer check (observations is null or observations >= 0),
    retrieved_at timestamptz,
    source_url text,
    price_basis text,
    content_sha256 text check (content_sha256 is null or content_sha256 ~ '^[0-9a-f]{64}$'),
    quality text not null,
    point_in_time_vintage boolean not null default false,
    last_run_id text references public.mrq_backtest_runs(run_id) on delete set null,
    updated_at timestamptz not null default now()
);

create table if not exists public.mrq_backtest_reports (
    run_id text primary key references public.mrq_backtest_runs(run_id) on delete cascade,
    strategy_json jsonb not null,
    metrics jsonb not null,
    period jsonb not null,
    data jsonb not null,
    report_sha256 text not null check (report_sha256 ~ '^[0-9a-f]{64}$'),
    input_sha256 text,
    artifacts_prefix text not null,
    generated_at timestamptz,
    created_at timestamptz not null default now()
);

alter table public.mrq_assets enable row level security;
alter table public.mrq_strategies enable row level security;
alter table public.mrq_strategy_versions enable row level security;
alter table public.mrq_backtest_runs enable row level security;
alter table public.mrq_asset_data_status enable row level security;
alter table public.mrq_backtest_reports enable row level security;

revoke all on table public.mrq_assets from public, anon, authenticated;
revoke all on table public.mrq_strategies from public, anon, authenticated;
revoke all on table public.mrq_strategy_versions from public, anon, authenticated;
revoke all on table public.mrq_backtest_runs from public, anon, authenticated;
revoke all on table public.mrq_asset_data_status from public, anon, authenticated;
revoke all on table public.mrq_backtest_reports from public, anon, authenticated;

grant select on table public.mrq_assets to service_role;
grant select, insert, update, delete on table public.mrq_strategies to service_role;
grant select, insert, update, delete on table public.mrq_strategy_versions to service_role;
grant select, insert, update, delete on table public.mrq_backtest_runs to service_role;
grant select, insert, update, delete on table public.mrq_asset_data_status to service_role;
grant select, insert, update, delete on table public.mrq_backtest_reports to service_role;

create or replace function public.save_mrq_strategy(
    p_strategy_id text,
    p_strategy_json jsonb,
    p_replace boolean
)
returns setof public.mrq_strategies
language plpgsql
security definer
set search_path = ''
as $$
declare
    v_current public.mrq_strategies%rowtype;
    v_version integer;
    v_now timestamptz := now();
begin
    if jsonb_typeof(p_strategy_json) <> 'object' then
        raise exception 'strategy must be a JSON object' using errcode = '22023';
    end if;

    if p_replace then
        select * into v_current
        from public.mrq_strategies
        where strategy_id = p_strategy_id
        for update;

        if not found then
            raise exception 'Strategy % was not found', p_strategy_id using errcode = 'P0002';
        end if;

        v_version := v_current.version + 1;
        update public.mrq_strategies
        set version = v_version,
            strategy_json = p_strategy_json,
            updated_at = v_now
        where strategy_id = p_strategy_id;
    else
        v_version := 1;
        insert into public.mrq_strategies (
            strategy_id, version, strategy_json, created_at, updated_at
        ) values (
            p_strategy_id, v_version, p_strategy_json, v_now, v_now
        );
    end if;

    insert into public.mrq_strategy_versions (
        strategy_id, version, strategy_json, created_at, updated_at
    ) values (
        p_strategy_id, v_version, p_strategy_json, v_now, v_now
    );

    return query
    select * from public.mrq_strategies where strategy_id = p_strategy_id;
end;
$$;

create or replace function public.submit_mrq_backtest_run(
    p_run_id text,
    p_request_json jsonb,
    p_request_sha256 text,
    p_idempotency_key text,
    p_strategy_id text,
    p_strategy_version integer
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    v_run public.mrq_backtest_runs%rowtype;
begin
    if p_idempotency_key is not null then
        select * into v_run
        from public.mrq_backtest_runs
        where idempotency_key = p_idempotency_key
        for update;

        if found then
            if v_run.request_sha256 <> p_request_sha256 then
                raise exception 'Idempotency-Key was already used for a different request'
                    using errcode = '22023';
            end if;
            return to_jsonb(v_run) || jsonb_build_object('created', false);
        end if;
    end if;

    insert into public.mrq_backtest_runs (
        run_id, request_json, request_sha256, idempotency_key, strategy_id,
        strategy_version, backtest_id
    ) values (
        p_run_id, p_request_json, p_request_sha256, p_idempotency_key,
        p_strategy_id, p_strategy_version, p_run_id
    )
    on conflict (idempotency_key) do nothing
    returning * into v_run;

    if not found then
        select * into v_run
        from public.mrq_backtest_runs
        where idempotency_key = p_idempotency_key
        for update;

        if not found then
            raise exception 'Could not store the backtest request';
        end if;
        if v_run.request_sha256 <> p_request_sha256 then
            raise exception 'Idempotency-Key was already used for a different request'
                using errcode = '22023';
        end if;
        return to_jsonb(v_run) || jsonb_build_object('created', false);
    end if;

    return to_jsonb(v_run) || jsonb_build_object('created', true);
end;
$$;

create or replace function public.claim_mrq_backtest_dispatch(p_run_id text)
returns boolean
language plpgsql
security definer
set search_path = ''
as $$
declare
    v_claimed integer;
begin
    update public.mrq_backtest_runs
    set dispatch_status = 'dispatching',
        dispatch_attempts = dispatch_attempts + 1,
        dispatch_claimed_at = now(),
        dispatch_error = null,
        updated_at = now()
    where run_id = p_run_id
      and status = 'queued'
      and (
          dispatch_status in ('pending', 'failed')
          or (dispatch_status = 'dispatching' and dispatch_claimed_at < now() - interval '2 minutes')
      );

    get diagnostics v_claimed = row_count;
    return v_claimed = 1;
end;
$$;

create or replace function public.claim_mrq_backtest_run(
    p_run_id text,
    p_execution_id text
)
returns setof public.mrq_backtest_runs
language plpgsql
security definer
set search_path = ''
as $$
begin
    return query
    update public.mrq_backtest_runs as run
    set status = 'running',
        started_at = coalesce(run.started_at, now()),
        attempts = run.attempts + 1,
        worker_execution_id = p_execution_id,
        lease_expires_at = now() + interval '2 hours',
        updated_at = now()
    where run.run_id = p_run_id
      and (
          (run.status = 'queued' and run.dispatch_status in ('dispatching', 'dispatched'))
          or (run.status = 'running' and run.lease_expires_at < now())
      )
    returning run.*;
end;
$$;

revoke all on function public.save_mrq_strategy(text, jsonb, boolean) from public, anon, authenticated;
revoke all on function public.submit_mrq_backtest_run(text, jsonb, text, text, text, integer)
    from public, anon, authenticated;
revoke all on function public.claim_mrq_backtest_dispatch(text) from public, anon, authenticated;
revoke all on function public.claim_mrq_backtest_run(text, text) from public, anon, authenticated;

grant execute on function public.save_mrq_strategy(text, jsonb, boolean) to service_role;
grant execute on function public.submit_mrq_backtest_run(text, jsonb, text, text, text, integer)
    to service_role;
grant execute on function public.claim_mrq_backtest_dispatch(text) to service_role;
grant execute on function public.claim_mrq_backtest_run(text, text) to service_role;

insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values (
    'mrq-backtest-artifacts',
    'mrq-backtest-artifacts',
    false,
    26214400,
    array['application/json', 'text/markdown', 'text/csv']
)
on conflict (id) do update set
    public = false,
    file_size_limit = excluded.file_size_limit,
    allowed_mime_types = excluded.allowed_mime_types;
