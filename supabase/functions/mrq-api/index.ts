import { createClient } from "@supabase/supabase-js";

const FUNCTION_PREFIXES = ["/functions/v1/mrq-api", "/mrq-api"];
const STORAGE_BUCKET = "mrq-backtest-artifacts";
const MAX_BODY_BYTES = 1_048_576;
const RUN_ID_RE = /^bt_[a-f0-9]{24}$/;
const STRATEGY_ID_RE = /^[a-z0-9][a-z0-9_-]{0,63}$/;
const SYMBOL_RE = /^[A-Z0-9.^=_-]{1,24}$/;
const REBALANCE = new Set(["buy_and_hold", "monthly", "quarterly", "annual"]);

type JsonObject = Record<string, unknown>;
type Admin = ReturnType<typeof createClient>;

class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
  ) {
    super(message);
  }
}

function json(status: number, value: unknown): Response {
  return new Response(JSON.stringify(value), {
    status,
    headers: {
      "Content-Type": "application/json; charset=utf-8",
      "Cache-Control": "no-store",
    },
  });
}

function adminClient(): Admin {
  const url = Deno.env.get("SUPABASE_URL");
  const key = Deno.env.get("MRQ_SUPABASE_SECRET_KEY");
  if (!url || !key) {
    throw new ApiError(503, "backend_unconfigured", "Supabase backend secrets are not configured");
  }
  return createClient(url, key, {
    auth: { autoRefreshToken: false, persistSession: false },
  });
}

function routePath(request: Request): string {
  let path = new URL(request.url).pathname;
  for (const prefix of FUNCTION_PREFIXES) {
    if (path === prefix) return "/";
    if (path.startsWith(`${prefix}/`)) {
      path = path.slice(prefix.length);
      break;
    }
  }
  return decodeURIComponent(path) || "/";
}

function constantTimeEqual(left: string, right: string): boolean {
  const a = new TextEncoder().encode(left);
  const b = new TextEncoder().encode(right);
  if (!a.length || !b.length) return false;
  let difference = a.length ^ b.length;
  const length = Math.max(a.length, b.length);
  for (let index = 0; index < length; index += 1) {
    difference |= (a[index] ?? 0) ^ (b[index] ?? 0);
  }
  return difference === 0;
}

function isAuthorized(request: Request): boolean {
  const expected = Deno.env.get("MRQ_MCP_API_TOKEN") ?? "";
  const header = request.headers.get("Authorization") ?? "";
  const match = /^Bearer\s+(.+)$/i.exec(header);
  return Boolean(expected && match && constantTimeEqual(match[1], expected));
}

async function readJson(request: Request): Promise<JsonObject> {
  const length = Number(request.headers.get("Content-Length") ?? "0");
  if (Number.isFinite(length) && length > MAX_BODY_BYTES) {
    throw new ApiError(413, "request_too_large", "Request body exceeds 1 MiB");
  }
  const body = await request.text();
  if (new TextEncoder().encode(body).byteLength > MAX_BODY_BYTES) {
    throw new ApiError(413, "request_too_large", "Request body exceeds 1 MiB");
  }
  if (!body) throw new ApiError(422, "invalid_request", "Request body is required");
  let value: unknown;
  try {
    value = JSON.parse(body);
  } catch {
    throw new ApiError(422, "invalid_request", "Request body must be valid JSON");
  }
  if (!isRecord(value)) throw new ApiError(422, "invalid_request", "Request body must be a JSON object");
  return value;
}

function isRecord(value: unknown): value is JsonObject {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function canonicalStrategy(input: unknown): JsonObject {
  if (!isRecord(input)) throw new ApiError(422, "invalid_strategy", "strategy must be an object");
  const name = typeof input.name === "string" ? input.name.trim() : "";
  if (!name || name.length > 100) {
    throw new ApiError(422, "invalid_strategy", "Strategy name must contain 1 to 100 characters");
  }
  if (!isRecord(input.weights) || Object.keys(input.weights).length === 0) {
    throw new ApiError(422, "invalid_strategy", "At least one asset weight is required");
  }
  const weights: Record<string, number> = {};
  for (const [rawSymbol, rawWeight] of Object.entries(input.weights)) {
    const symbol = rawSymbol.trim().toUpperCase();
    const weight = Number(rawWeight);
    if (!SYMBOL_RE.test(symbol) || !Number.isFinite(weight) || weight < 0) {
      throw new ApiError(422, "invalid_strategy", `Invalid weight or symbol: ${rawSymbol}`);
    }
    if (Object.hasOwn(weights, symbol)) {
      throw new ApiError(422, "invalid_strategy", `Duplicate asset symbol after normalization: ${symbol}`);
    }
    weights[symbol] = weight;
  }
  const total = Object.values(weights).reduce((sum, weight) => sum + weight, 0);
  if (Math.abs(total - 1) > 1e-8) {
    throw new ApiError(422, "invalid_strategy", "Asset weights must sum to exactly 1.0");
  }

  const rebalance = input.rebalance_frequency ?? "monthly";
  if (typeof rebalance !== "string" || !REBALANCE.has(rebalance)) {
    throw new ApiError(422, "invalid_strategy", "Unsupported rebalance_frequency");
  }
  const initialCapital = Number(input.initial_capital ?? 10_000);
  const transactionCost = Number(input.transaction_cost_bps ?? 5);
  if (!Number.isFinite(initialCapital) || initialCapital <= 0) {
    throw new ApiError(422, "invalid_strategy", "initial_capital must be finite and greater than zero");
  }
  if (!Number.isFinite(transactionCost) || transactionCost < 0) {
    throw new ApiError(422, "invalid_strategy", "transaction_cost_bps must be finite and non-negative");
  }
  const startDate = optionalDate(input.start_date, "start_date");
  const endDate = optionalDate(input.end_date, "end_date");
  if (startDate && endDate && startDate > endDate) {
    throw new ApiError(422, "invalid_strategy", "start_date must be on or before end_date");
  }

  return {
    name,
    weights,
    rebalance_frequency: rebalance,
    start_date: startDate,
    end_date: endDate,
    initial_capital: initialCapital,
    transaction_cost_bps: transactionCost,
    ...(input.cash_flow !== undefined ? { cash_flow: input.cash_flow } : {}),
  };
}

function optionalDate(value: unknown, field: string): string | null {
  if (value === undefined || value === null || value === "") return null;
  if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value)) {
    throw new ApiError(422, "invalid_strategy", `${field} must use YYYY-MM-DD`);
  }
  const parsed = new Date(`${value}T00:00:00.000Z`);
  if (!Number.isFinite(parsed.getTime()) || parsed.toISOString().slice(0, 10) !== value) {
    throw new ApiError(422, "invalid_strategy", `${field} is not a valid date`);
  }
  return value;
}

function stableValue(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(stableValue);
  if (!isRecord(value)) return value;
  return Object.fromEntries(
    Object.keys(value).sort().map((key) => [key, stableValue(value[key])]),
  );
}

async function sha256(value: string | Uint8Array): Promise<string> {
  const bytes = typeof value === "string" ? new TextEncoder().encode(value) : value;
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

function slug(name: string): string {
  const value = name.normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 64)
    .replace(/[-_]+$/g, "");
  return value || "strategy";
}

function apiErrorFromDatabase(error: { code?: string; message?: string }): ApiError {
  const code = error.code ?? "database_error";
  const status = code === "23505" ? 409 : code === "P0002" ? 404 : code === "22023" ? 422 : 500;
  return new ApiError(status, code, error.message ?? "Database request failed");
}

function recordFromStrategy(row: JsonObject): JsonObject {
  return {
    schema_version: 1,
    strategy_id: row.strategy_id,
    version: row.version,
    created_at: row.created_at,
    updated_at: row.updated_at,
    strategy: row.strategy_json,
  };
}

async function getStrategyRow(admin: Admin, strategyId: string, version?: number): Promise<JsonObject> {
  if (!STRATEGY_ID_RE.test(strategyId)) throw new ApiError(404, "not_found", "Strategy was not found");
  const query = version === undefined
    ? admin.from("mrq_strategies").select("*").eq("strategy_id", strategyId)
    : admin.from("mrq_strategy_versions").select("*").eq("strategy_id", strategyId).eq("version", version);
  const { data, error } = await query.maybeSingle();
  if (error) throw apiErrorFromDatabase(error);
  if (!data) throw new ApiError(404, "not_found", "Strategy was not found");
  return data as JsonObject;
}

async function prepareBacktest(admin: Admin, body: JsonObject): Promise<JsonObject> {
  const strategyId = typeof body.strategy_id === "string" ? body.strategy_id : "";
  const hasInline = isRecord(body.strategy);
  if (Boolean(strategyId) === hasInline) {
    throw new ApiError(422, "invalid_request", "Provide exactly one of strategy_id or strategy");
  }
  if (!strategyId) {
    return { strategy: canonicalStrategy(body.strategy), strategy_version: null };
  }

  let version: number | undefined;
  if (body.version !== undefined && body.version !== null) {
    version = Number(body.version);
    if (!Number.isInteger(version) || version < 1) {
      throw new ApiError(422, "invalid_request", "version must be a positive integer");
    }
  }
  const row = await getStrategyRow(admin, strategyId, version);
  const strategy = canonicalStrategy(row.strategy_json);
  const canonical = JSON.stringify(stableValue(strategy));
  return {
    strategy,
    strategy_version: {
      strategy_id: strategyId,
      version: row.version,
      spec_sha256: await sha256(canonical),
    },
  };
}

async function saveStrategy(admin: Admin, strategy: JsonObject, strategyId: string, replace: boolean) {
  const { data, error } = await admin.rpc("save_mrq_strategy", {
    p_strategy_id: strategyId,
    p_strategy_json: strategy,
    p_replace: replace,
  }).single();
  if (error) throw apiErrorFromDatabase(error);
  return recordFromStrategy(data as JsonObject);
}

async function dispatchWorker(runId: string): Promise<void> {
  const token = Deno.env.get("GITHUB_TOKEN");
  const repository = Deno.env.get("GITHUB_REPOSITORY") ?? "lava-chen/macro-regime-quant";
  if (!token) throw new ApiError(503, "backend_unconfigured", "GitHub dispatch token is not configured");
  if (!/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(repository)) {
    throw new ApiError(500, "configuration_error", "GITHUB_REPOSITORY is invalid");
  }
  const response = await fetch(
    `https://api.github.com/repos/${repository}/actions/workflows/run-backtest.yml/dispatches`,
    {
      method: "POST",
      headers: {
        Accept: "application/vnd.github+json",
        Authorization: `Bearer ${token}`,
        "X-GitHub-Api-Version": "2022-11-28",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ ref: "main", inputs: { run_id: runId } }),
    },
  );
  if (!response.ok) {
    const detail = (await response.text()).slice(0, 300);
    throw new ApiError(502, "dispatch_failed", `GitHub Actions dispatch failed (${response.status}): ${detail}`);
  }
}

async function enqueue(admin: Admin, request: Request, body: JsonObject): Promise<Response> {
  const prepared = await prepareBacktest(admin, body);
  const idempotencyKey = request.headers.get("Idempotency-Key") ??
    (typeof body.idempotency_key === "string" ? body.idempotency_key : null);
  if (idempotencyKey !== null && (idempotencyKey.length < 1 || idempotencyKey.length > 128)) {
    throw new ApiError(422, "invalid_request", "Idempotency-Key must contain 1 to 128 characters");
  }
  const requestHash = await sha256(JSON.stringify(stableValue(prepared)));
  const strategyVersion = isRecord(prepared.strategy_version) ? prepared.strategy_version : null;
  const runId = `bt_${crypto.randomUUID().replaceAll("-", "").slice(0, 24)}`;
  const { data, error } = await admin.rpc("submit_mrq_backtest_run", {
    p_run_id: runId,
    p_request_json: prepared,
    p_request_sha256: requestHash,
    p_idempotency_key: idempotencyKey,
    p_strategy_id: strategyVersion?.strategy_id ?? null,
    p_strategy_version: strategyVersion?.version ?? null,
  });
  if (error) throw apiErrorFromDatabase(error);
  if (!isRecord(data) || typeof data.run_id !== "string") {
    throw new ApiError(500, "database_error", "Supabase did not return a backtest run");
  }

  const dispatchId = data.run_id;
  const { data: claimed, error: claimError } = await admin.rpc("claim_mrq_backtest_dispatch", {
    p_run_id: dispatchId,
  });
  if (claimError) throw apiErrorFromDatabase(claimError);
  if (claimed === true) {
    try {
      await dispatchWorker(dispatchId);
      const { error: updateError } = await admin.from("mrq_backtest_runs").update({
        dispatch_status: "dispatched",
        dispatched_at: new Date().toISOString(),
        dispatch_error: null,
        updated_at: new Date().toISOString(),
      }).eq("run_id", dispatchId);
      if (updateError) throw apiErrorFromDatabase(updateError);
    } catch (error) {
      const message = error instanceof Error ? error.message.slice(0, 500) : "GitHub dispatch failed";
      await admin.from("mrq_backtest_runs").update({
        dispatch_status: "failed",
        dispatch_error: message,
        updated_at: new Date().toISOString(),
      }).eq("run_id", dispatchId);
      throw error;
    }
  }

  const { data: run, error: readError } = await admin.from("mrq_backtest_runs")
    .select("run_id,status,submitted_at,dispatch_status")
    .eq("run_id", dispatchId)
    .single();
  if (readError) throw apiErrorFromDatabase(readError);
  return json(202, run);
}

async function getStatus(admin: Admin, runId: string): Promise<Response> {
  if (!RUN_ID_RE.test(runId)) throw new ApiError(404, "not_found", "Backtest run was not found");
  const { data, error } = await admin.from("mrq_backtest_runs")
    .select("run_id,status,submitted_at,started_at,finished_at,backtest_id,error,attempts,dispatch_status,dispatch_error")
    .eq("run_id", runId)
    .maybeSingle();
  if (error) throw apiErrorFromDatabase(error);
  if (!data) throw new ApiError(404, "not_found", "Backtest run was not found");
  return json(200, data);
}

async function getReport(admin: Admin, runId: string): Promise<Response> {
  if (!RUN_ID_RE.test(runId)) throw new ApiError(404, "not_found", "Backtest report was not found");
  const { data: record, error: queryError } = await admin.from("mrq_backtest_reports")
    .select("artifacts_prefix")
    .eq("run_id", runId)
    .maybeSingle();
  if (queryError) throw apiErrorFromDatabase(queryError);
  if (!record) throw new ApiError(404, "not_found", "Backtest report was not found");
  const prefix = String((record as JsonObject).artifacts_prefix);
  const { data, error } = await admin.storage.from(STORAGE_BUCKET).download(`${prefix}/report.json`);
  if (error || !data) throw new ApiError(404, "not_found", "Backtest report artifact was not found");
  try {
    return json(200, JSON.parse(await data.text()));
  } catch {
    throw new ApiError(500, "invalid_report", "Stored report is not valid JSON");
  }
}

async function assets(admin: Admin): Promise<Response> {
  const { data, error } = await admin.from("mrq_assets").select("symbol,kind,provider").order("symbol");
  if (error) throw apiErrorFromDatabase(error);
  return json(200, { assets: data ?? [] });
}

async function dataStatus(admin: Admin): Promise<Response> {
  const { data, error } = await admin.from("mrq_assets").select("symbol,kind,provider").order("symbol");
  if (error) throw apiErrorFromDatabase(error);
  const configured = (data ?? []).map((asset) => {
    const row = asset as JsonObject;
    const synthetic = row.provider === "synthetic";
    return {
      ...row,
      availability: synthetic ? "synthetic_zero_yield" : "provider_configured",
      coverage_start: null,
      coverage_end: null,
      observations: null,
      retrieved_at: null,
      source_url: null,
      price_basis: null,
      content_sha256: null,
      quality: synthetic ? "modeled_assumption" : "not_fetched",
      note: synthetic
        ? "CASH is modeled at 0% annual yield; it is not an interest-bearing cash proxy."
        : "Provider configuration is not a successful fetch; actual coverage is unknown until a worker requests it.",
    };
  });
  return json(200, { assets: configured });
}

async function route(request: Request): Promise<Response> {
  const path = routePath(request);
  if (request.method === "GET" && path === "/health") {
    return json(200, { status: "ok", service: "macro-regime-quant-supabase" });
  }
  if (!isAuthorized(request)) return json(401, { error: "unauthorized", message: "Invalid bearer token" });
  const admin = adminClient();

  if (request.method === "GET" && path === "/assets") return await assets(admin);
  if (request.method === "GET" && path === "/data-status") return await dataStatus(admin);
  if (request.method === "GET" && path === "/strategies") {
    const { data, error } = await admin.from("mrq_strategies").select("*").order("updated_at", { ascending: false });
    if (error) throw apiErrorFromDatabase(error);
    return json(200, { strategies: (data ?? []).map((row) => recordFromStrategy(row as JsonObject)) });
  }
  if (request.method === "POST" && path === "/strategies") {
    const body = await readJson(request);
    const strategy = canonicalStrategy(body.strategy ?? body);
    const record = await saveStrategy(admin, strategy, slug(String(strategy.name)), false);
    return json(201, record);
  }
  if (request.method === "POST" && (path === "/backtest-runs" || path === "/backtests")) {
    return await enqueue(admin, request, await readJson(request));
  }

  const strategyPath = /^\/strategies\/([^/]+)(?:\/versions(?:\/([^/]+))?)?$/.exec(path);
  if (strategyPath && request.method === "PUT" && !strategyPath[2] && path.endsWith(strategyPath[1])) {
    const strategyId = strategyPath[1];
    const body = await readJson(request);
    const strategy = canonicalStrategy(body.strategy ?? body);
    return json(200, await saveStrategy(admin, strategy, strategyId, true));
  }
  if (strategyPath && request.method === "GET" && strategyPath[2] === undefined && path.includes("/versions")) {
    const strategyId = strategyPath[1];
    if (!STRATEGY_ID_RE.test(strategyId)) throw new ApiError(404, "not_found", "Strategy was not found");
    const { data, error } = await admin.from("mrq_strategy_versions").select("*")
      .eq("strategy_id", strategyId).order("version", { ascending: true });
    if (error) throw apiErrorFromDatabase(error);
    return json(200, { versions: (data ?? []).map((row) => recordFromStrategy(row as JsonObject)) });
  }
  if (strategyPath && request.method === "GET" && strategyPath[2] !== undefined) {
    const version = Number(strategyPath[2]);
    if (!Number.isInteger(version) || version < 1) throw new ApiError(422, "invalid_request", "version must be positive");
    return json(200, recordFromStrategy(await getStrategyRow(admin, strategyPath[1], version)));
  }

  const statusPath = /^\/backtest-runs\/([^/]+)$/.exec(path);
  if (request.method === "GET" && statusPath) return await getStatus(admin, statusPath[1]);
  const reportPath = /^\/backtests\/([^/]+)$/.exec(path);
  if (request.method === "GET" && reportPath) return await getReport(admin, reportPath[1]);
  throw new ApiError(404, "not_found", "Unknown endpoint");
}

Deno.serve(async (request) => {
  try {
    return await route(request);
  } catch (error) {
    if (error instanceof ApiError) {
      return json(error.status, { error: error.code, message: error.message });
    }
    console.error("[mrq-api] request failed", error instanceof Error ? error.name : "unknown");
    return json(500, { error: "internal_error", message: "Request failed" });
  }
});
