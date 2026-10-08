/* global echarts */
"use strict";

const ASSET_COLORS = {
  SPY: "#e4be63",
  QQQ: "#79bdf0",
  GLD: "#d5a76a",
  TLT: "#a998eb",
  DBC: "#82cda8",
};
const PALETTE = ["#e4be63", "#79bdf0", "#82cda8", "#d69483", "#a998eb", "#6fc6c7", "#de8db7", "#b3c97b"];
const REGIME_COLORS = {
  recovery: "#73c8a0",
  reflation: "#d8b867",
  stagflation: "#e18478",
  disinflation: "#78b9e8",
  "growth-inflation": "#d8b867",
};
const REGIME_LABELS = {
  recovery: "复苏",
  reflation: "再通胀",
  stagflation: "滞胀",
  disinflation: "通胀回落",
  "growth-inflation": "增长与通胀",
};
const COUNTRY_LABELS = { China: "中国", "United States": "美国" };
const DOMAIN_LABELS = { markets: "市场", macro_china: "中国宏观", macro_us: "美国宏观" };
const KIND_LABELS = { asset_price: "资产价格", macro_observation: "宏观指标", macro_factor: "宏观因子" };
const UNIT_LABELS = {
  USD: "美元",
  percent_yoy: "同比（%）",
  percent: "百分比（%）",
  index_points: "指数点",
  billions_usd: "十亿美元",
  "z-score": "标准分",
};
const RANGE_DAYS = { "1M": 31, "3M": 92, "6M": 183, "1Y": 365, "3Y": 1096, "5Y": 1826 };
const state = {
  summary: {},
  series: [],
  regimes: [],
  backtests: {},
  origin: "loading",
  marketRange: "1Y",
  macroRange: "ALL",
  marketMode: "indexed",
  macroMode: "zscore",
  selectedMarket: new Set(["market:SPY", "market:QQQ", "market:GLD", "market:TLT", "market:DBC"]),
  selectedMacro: new Set(),
  country: "China",
  volumeSymbol: "SPY",
  charts: {},
};

const $ = (id) => document.getElementById(id);
const fmtDate = (value) => {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value) : new Intl.DateTimeFormat("zh-CN", { timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit" }).format(date);
};
const fmtTimestamp = (value) => {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value) : new Intl.DateTimeFormat("zh-CN", { timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }).format(date);
};
const number = (value, digits = 2) => Number(value).toLocaleString("zh-CN", { maximumFractionDigits: digits, minimumFractionDigits: digits });
const safeText = (node, value) => { node.textContent = value == null ? "" : String(value); return node; };
const el = (tag, className, text) => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = String(text);
  return node;
};

async function fetchJson(baseUrl, file, version) {
  const remoteUrl = new URL(file, baseUrl);
  remoteUrl.searchParams.set("v", version);
  try {
    const response = await fetch(remoteUrl, { cache: "no-store", mode: "cors" });
    if (!response.ok) throw new Error(`${file}：请求失败（${response.status}）`);
    return { value: await response.json(), origin: "github" };
  } catch (remoteError) {
    try {
      const response = await fetch(`./data/${file}`, { cache: "no-store" });
      if (!response.ok) throw remoteError;
      return { value: await response.json(), origin: "site fallback" };
    } catch (_) {
      return { value: null, origin: "unavailable", error: remoteError };
    }
  }
}

async function loadData(showToast = false) {
  const refresh = $("refresh-button");
  refresh.classList.add("loading");
  try {
    const feedResponse = await fetch("./data-feed.json", { cache: "no-store" });
    if (!feedResponse.ok) throw new Error("无法读取数据源配置。");
    const feed = await feedResponse.json();
    const version = Date.now();
    const results = await Promise.all([
      fetchJson(feed.baseUrl, "summary.json", version),
      fetchJson(feed.baseUrl, "series.json", version),
      fetchJson(feed.baseUrl, "regimes.json", version),
      fetchJson(feed.baseUrl, "backtests.json", version),
    ]);
    state.summary = results[0].value || {};
    state.series = results[1].value?.series || [];
    state.regimes = results[2].value?.countries || [];
    state.backtests = results[3].value || {};
    state.origin = results.some((item) => item.origin === "github") ? "github" : results.some((item) => item.origin === "site fallback") ? "site fallback" : "unavailable";
    initializeSelections();
    renderAll();
    if (showToast) showToastMessage(state.origin === "github" ? "已从公开数据分支重新读取。" : "当前使用站点内上次保存的有效数据。", state.origin === "unavailable");
  } catch (error) {
    setFeedState("failed", "数据读取失败");
    if (showToast) showToastMessage(error.message || "数据刷新失败。", true);
  } finally {
    refresh.classList.remove("loading");
  }
}

function initializeSelections() {
  const macroSeries = state.series.filter((series) => series.domain === (state.country === "China" ? "macro_china" : "macro_us"));
  const validIds = new Set(macroSeries.map((series) => series.id));
  state.selectedMacro = new Set([...state.selectedMacro].filter((id) => validIds.has(id)));
  if (!state.selectedMacro.size) {
    macroSeries.filter((series) => series.kind === "macro_factor").slice(0, 4).forEach((series) => state.selectedMacro.add(series.id));
  }
  const assets = new Set(state.series.filter((series) => series.domain === "markets").map((series) => series.id));
  state.selectedMarket = new Set([...state.selectedMarket].filter((id) => assets.has(id)));
  if (assets.size && !state.selectedMarket.size) state.selectedMarket = assets;
}

function setFeedState(status, label) {
  const indicator = $("feed-indicator");
  indicator.classList.toggle("failed", status === "failed");
  indicator.classList.toggle("partial", status === "partial");
  safeText($("feed-label"), label);
}

function renderAll() {
  renderFeedStatus();
  renderMarketPage();
  renderMacroPage();
  renderDataPage();
  renderResearchPage();
  Object.values(state.charts).forEach((chart) => chart?.resize());
}

function renderFeedStatus() {
  const status = state.summary.status || "unavailable";
  const label = status === "success" ? "数据正常" : status === "partial" ? "部分数据可用" : status === "failed" ? "最近更新失败" : "等待数据";
  setFeedState(status === "failed" ? "failed" : status === "success" ? "success" : "partial", label);
  safeText($("market-asof"), latestMarketDate() ? fmtDate(latestMarketDate()) : "暂无行情");
  safeText($("market-stale-state"), sourceFreshness("market_prices"));
  safeText($("market-provider-state"), sourceRunLabel("market_prices"));
  safeText($("market-last-success"), fmtTimestamp(state.summary.sources?.market_prices?.last_success_at));
}

function sourceFreshness(name) {
  const source = state.summary.sources?.[name];
  if (!source) return "暂无成功更新记录";
  if (source.state === "failed") return "最近更新失败 · 保留此前有效数据";
  if (source.state === "not_run") return "等待首次行情同步";
  return `最近更新于 ${fmtTimestamp(source.last_success_at)}`;
}

function sourceRunLabel(name) {
  const source = state.summary.sources?.[name];
  if (!source) return "等待首次运行";
  if (source.state === "failed") return "更新失败 · 保留此前有效数据";
  if (source.state === "not_run") return "本次未运行";
  return "已连接 · 日线数据";
}

function latestMarketDate() {
  const dates = state.series.filter((series) => series.domain === "markets").flatMap((series) => series.points.map((point) => point.date));
  dates.sort();
  return dates.at(-1) || null;
}

function renderMarketPage() {
  const marketSeries = state.series.filter((series) => series.domain === "markets");
  const cards = $("market-strip");
  cards.replaceChildren();
  const desired = ["SPY", "QQQ", "GLD", "TLT", "DBC"];
  desired.forEach((ticker, index) => {
    const record = marketSeries.find((series) => series.symbol === ticker || series.id === `market:${ticker}`);
    const card = el("article", "market-card");
    const top = el("div", "market-card-top");
    const tickerNode = el("span");
    const swatch = el("i", "asset-swatch");
    swatch.style.background = ASSET_COLORS[ticker] || PALETTE[index];
    tickerNode.append(swatch, document.createTextNode(ticker));
    top.append(tickerNode, el("span", "market-card-label", record ? "日线" : "等待数据"));
    card.append(top);
    const points = record?.points || [];
    if (points.length) {
      const last = points.at(-1);
      const previous = points.length > 1 ? points.at(-2) : null;
      const change = previous?.value ? ((last.value / previous.value) - 1) * 100 : null;
      card.append(el("div", "market-price", `$${number(last.value)}`));
      const move = el("div", `market-change ${change == null ? "" : change >= 0 ? "positive" : "negative"}`, change == null ? `截至 ${last.date}` : `${change >= 0 ? "+" : ""}${number(change)}%  ·  ${last.date}`);
      card.append(move);
    } else {
      card.append(el("div", "market-price market-card-empty", "—"));
      card.append(el("div", "market-change", "等待有效行情数据"));
    }
    cards.append(card);
  });
  renderMarketControls(marketSeries);
  renderMarketChart(marketSeries);
}

function renderMarketControls(records) {
  const container = $("market-series-controls");
  container.replaceChildren();
  const candidates = ["SPY", "QQQ", "GLD", "TLT", "DBC"];
  candidates.forEach((ticker, index) => {
    const record = records.find((item) => (item.symbol || item.id.split(":").at(-1)) === ticker);
    const selected = record && state.selectedMarket.has(record.id);
    const chip = el("button", `series-chip ${selected ? "" : "off"}`);
    chip.type = "button";
    chip.setAttribute("aria-pressed", String(Boolean(selected)));
    const swatch = el("i", "asset-swatch");
    swatch.style.background = ASSET_COLORS[ticker] || PALETTE[index % PALETTE.length];
    chip.append(swatch, document.createTextNode(ticker), el("span", "check", record ? selected ? "●" : "○" : "···"));
    if (!record) {
      chip.disabled = true;
      chip.title = "等待首次成功导出行情数据";
    }
    chip.addEventListener("click", () => {
      if (!record) return;
      if (state.selectedMarket.has(record.id)) state.selectedMarket.delete(record.id);
      else state.selectedMarket.add(record.id);
      renderMarketPage();
    });
    container.append(chip);
  });
  const volume = $("volume-symbol");
  const selectedSymbols = records.map((record) => record.symbol || record.id.split(":").at(-1));
  volume.replaceChildren(...selectedSymbols.map((symbol) => {
    const option = el("option", "", symbol);
    option.value = symbol;
    return option;
  }));
  if (selectedSymbols.length && !selectedSymbols.includes(state.volumeSymbol)) state.volumeSymbol = selectedSymbols[0];
  volume.value = state.volumeSymbol;
}

function cutoffDate(range, latest) {
  if (range === "ALL" || !latest) return null;
  const days = RANGE_DAYS[range];
  if (!days) return null;
  const date = new Date(`${latest}T00:00:00Z`);
  date.setUTCDate(date.getUTCDate() - days);
  return date.toISOString().slice(0, 10);
}

function rangePoints(series, range) {
  const points = series?.points || [];
  const cutoff = cutoffDate(range, points.at(-1)?.date);
  return cutoff ? points.filter((point) => point.date >= cutoff) : points;
}

function indexedValues(points, mode) {
  if (mode !== "indexed") return points.map((point) => [point.date, Number(point.value)]);
  const first = points.find((point) => Number.isFinite(Number(point.value)));
  if (!first || Number(first.value) === 0) return [];
  return points.map((point) => [point.date, (Number(point.value) / Number(first.value)) * 100]);
}

function renderMarketChart(records = state.series.filter((series) => series.domain === "markets")) {
  const chart = getChart("market-chart");
  const chosen = records.filter((series) => state.selectedMarket.has(series.id));
  const withPoints = chosen.filter((series) => series.points?.length);
  const empty = $("market-chart-empty");
  empty.classList.toggle("hidden", withPoints.length > 0);
  if (!window.echarts || !withPoints.length) { chart.clear(); return; }
  const latest = withPoints.map((series) => series.points.at(-1).date).sort().at(-1);
  const series = [];
  withPoints.forEach((record, index) => {
    const ticker = record.symbol || record.id.split(":").at(-1);
    const points = rangePoints(record, state.marketRange);
    series.push({
      name: ticker,
      type: "line",
      xAxisIndex: 0,
      yAxisIndex: 0,
      showSymbol: false,
      symbol: "circle",
      data: indexedValues(points, state.marketMode),
      lineStyle: { width: 1.8, color: ASSET_COLORS[ticker] || PALETTE[index % PALETTE.length] },
      itemStyle: { color: ASSET_COLORS[ticker] || PALETTE[index % PALETTE.length] },
      emphasis: { focus: "series", lineStyle: { width: 2.8 } },
      connectNulls: false,
    });
  });
  const volumeRecord = withPoints.find((record) => (record.symbol || record.id.split(":").at(-1)) === state.volumeSymbol) || withPoints[0];
  const volumeTicker = volumeRecord?.symbol || volumeRecord?.id.split(":").at(-1);
  if (volumeRecord) {
    series.push({
      name: `${volumeTicker} 成交量`,
      type: "bar",
      xAxisIndex: 1,
      yAxisIndex: 1,
      data: rangePoints(volumeRecord, state.marketRange).filter((point) => point.volume != null).map((point) => [point.date, Number(point.volume)]),
      itemStyle: { color: `${ASSET_COLORS[volumeTicker] || "#82cda8"}80`, borderRadius: [1, 1, 0, 0] },
      barMaxWidth: 7,
      tooltip: { valueFormatter: (value) => number(value, 0) },
    });
  }
  const modeLabel = state.marketMode === "indexed" ? "区间相对表现 · 起点 = 100" : "复权收盘价 · 美元";
  safeText($("market-chart-title"), modeLabel);
  chart.setOption({
    animation: false,
    color: PALETTE,
    textStyle: { color: "#81909a", fontFamily: "SFMono-Regular, Consolas, monospace", fontSize: 9 },
    grid: [{ left: 59, right: 35, top: 42, height: "55%" }, { left: 59, right: 35, top: "70%", height: "17%" }],
    axisPointer: { link: [{ xAxisIndex: [0, 1] }], lineStyle: { color: "#70828e", type: "dashed" } },
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "cross", label: { backgroundColor: "#263641" } },
      backgroundColor: "#111a21",
      borderColor: "#34454f",
      textStyle: { color: "#e0e7eb", fontSize: 10 },
      formatter: (items) => {
        if (!items?.length) return "";
        const date = fmtDate(items[0].axisValue);
        const rows = items.map((item) => {
          const isVolume = item.seriesName.endsWith(" 成交量");
          return `<div style="display:flex;justify-content:space-between;gap:18px;margin-top:5px"><span>${item.marker}${escapeHtml(item.seriesName)}</span><strong style="font-family:monospace">${isVolume ? number(item.value?.[1] ?? item.value, 0) : number(item.value?.[1] ?? item.value)}${isVolume ? "" : state.marketMode === "indexed" ? "" : " 美元"}</strong></div>`;
        }).join("");
        return `<div style="min-width:180px"><strong>${escapeHtml(date)}</strong>${rows}</div>`;
      },
    },
    legend: { top: 7, left: 55, itemWidth: 11, itemHeight: 2, itemGap: 13, textStyle: { color: "#a2afb7", fontSize: 9 } },
    xAxis: [
      { type: "time", gridIndex: 0, axisLabel: { show: false }, axisLine: { lineStyle: { color: "#26343e" } }, axisTick: { show: false }, splitLine: { show: false }, min: "dataMin", max: "dataMax" },
      { type: "time", gridIndex: 1, axisLabel: { color: "#72818b", fontSize: 8, hideOverlap: true }, axisLine: { lineStyle: { color: "#26343e" } }, axisTick: { show: false }, splitLine: { show: false }, min: "dataMin", max: "dataMax" },
    ],
    yAxis: [
      { type: "value", gridIndex: 0, scale: true, name: state.marketMode === "indexed" ? "指数" : "美元", nameTextStyle: { color: "#687882", fontSize: 8 }, axisLabel: { color: "#71808a", fontSize: 8 }, axisLine: { show: false }, axisTick: { show: false }, splitLine: { lineStyle: { color: "#202c35", type: "dashed" } } },
      { type: "value", gridIndex: 1, scale: true, name: `${volumeTicker || ""} 成交量`, nameTextStyle: { color: "#687882", fontSize: 8 }, axisLabel: { color: "#71808a", fontSize: 8, formatter: compactNumber }, axisLine: { show: false }, axisTick: { show: false }, splitLine: { lineStyle: { color: "#1c2831", type: "dashed" } } },
    ],
    dataZoom: [
      { type: "inside", xAxisIndex: [0, 1], filterMode: "none", zoomOnMouseWheel: true, moveOnMouseMove: true },
      { type: "slider", xAxisIndex: [0, 1], bottom: 7, height: 18, borderColor: "#26343e", backgroundColor: "#0a1117", fillerColor: "#32463d55", handleStyle: { color: "#89b39a" }, textStyle: { color: "#687781", fontSize: 8 }, dataBackground: { lineStyle: { color: "#44545e" }, areaStyle: { color: "#2a3942" } } },
    ],
    series,
  }, true);
}

function compactNumber(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "";
  if (n >= 1e9) return `${(n / 1e9).toFixed(1)}十亿`;
  if (n >= 1e6) return `${(n / 1e6).toFixed(1)}百万`;
  if (n >= 1e3) return `${(n / 1e3).toFixed(0)}千`;
  return String(Math.round(n));
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[character]);
}

function renderMacroPage() {
  const countrySeries = state.series.filter((series) => series.domain === (state.country === "China" ? "macro_china" : "macro_us"));
  renderMacroControls(countrySeries);
  renderMacroChart(countrySeries);
  renderRegimeView();
  const alert = $("macro-pit-alert");
  const us = state.country === "United States";
  alert.classList.toggle("safe", !us);
  safeText(alert, us
    ? "美国历史因子采用 FRED 当前修订后的数据与近似发布日期。ALFRED 历史版本尚未接入历史时点面板，因此不能用于点时有效回测。"
    : "中国曲线来自已归档的国家统计局和中国人民银行发布快照。覆盖不完整且起始时间不一；缺少的实际利率与核心 CPI 不会补填。");
  safeText($("macro-source-label"), us ? "FRED · 当前修订值 · 非点时有效" : "国家统计局 / 中国人民银行 · 官方发布快照");
}

function renderMacroControls(records) {
  const container = $("macro-series-controls");
  container.replaceChildren();
  const sorted = [...records].sort((a, b) => (a.kind === b.kind ? a.label.localeCompare(b.label) : a.kind === "macro_factor" ? -1 : 1));
  sorted.forEach((record, index) => {
    const selected = state.selectedMacro.has(record.id);
    const chip = el("button", `series-chip ${selected ? "" : "off"}`);
    chip.type = "button";
    chip.setAttribute("aria-pressed", String(selected));
    const swatch = el("i", "asset-swatch");
    swatch.style.background = PALETTE[index % PALETTE.length];
    chip.append(swatch, document.createTextNode(record.label.replace(/^(中国|美国)/, "")), el("span", "check", selected ? "●" : "○"));
    chip.addEventListener("click", () => {
      if (state.selectedMacro.has(record.id)) state.selectedMacro.delete(record.id);
      else state.selectedMacro.add(record.id);
      renderMacroPage();
    });
    container.append(chip);
  });
}

function zscore(points) {
  const values = points.map((point) => Number(point.value)).filter(Number.isFinite);
  if (values.length < 2) return points.map((point) => [point.date, null]);
  const mean = values.reduce((a, b) => a + b, 0) / values.length;
  const variance = values.reduce((sum, value) => sum + ((value - mean) ** 2), 0) / values.length;
  const sd = Math.sqrt(variance);
  if (!sd) return points.map((point) => [point.date, 0]);
  return points.map((point) => [point.date, (Number(point.value) - mean) / sd]);
}

function renderMacroChart(records = state.series.filter((series) => series.domain === (state.country === "China" ? "macro_china" : "macro_us"))) {
  const chart = getChart("macro-chart");
  const chosen = records.filter((series) => state.selectedMacro.has(series.id));
  const hasPoints = chosen.filter((series) => series.points?.length);
  $("macro-chart-empty").classList.toggle("hidden", hasPoints.length > 0);
  if (!window.echarts || !hasPoints.length) { chart.clear(); return; }
  const allDates = hasPoints.flatMap((record) => record.points.map((point) => point.date)).sort();
  const cutoff = cutoffDate(state.macroRange, allDates.at(-1));
  const visible = hasPoints.map((record) => ({ record, points: record.points.filter((point) => !cutoff || point.date >= cutoff) })).filter((item) => item.points.length);
  const units = state.macroMode === "zscore" ? ["z-score"] : [...new Set(visible.map((item) => item.record.unit || "value"))].slice(0, 4);
  const unitAxis = new Map(units.map((unit, index) => [unit, index]));
  const series = visible.map(({ record, points }, index) => {
    const color = PALETTE[index % PALETTE.length];
    const useZscore = state.macroMode === "zscore";
    const alreadyZ = record.unit === "z-score";
    const data = useZscore && !alreadyZ ? zscore(points) : points.map((point) => [point.date, Number(point.value)]);
    return {
      name: record.label,
      type: "line",
      xAxisIndex: 0,
      yAxisIndex: useZscore ? 0 : (unitAxis.get(record.unit || "value") || 0),
      data,
      showSymbol: false,
      connectNulls: false,
      lineStyle: { width: 1.7, color },
      itemStyle: { color },
      emphasis: { focus: "series", lineStyle: { width: 2.5 } },
    };
  });
  const yAxis = units.map((unit, index) => ({
    type: "value",
    scale: true,
    name: UNIT_LABELS[unit] || unit,
    nameTextStyle: { color: "#687882", fontSize: 8 },
    position: index === 0 ? "left" : "right",
    offset: index > 1 ? (index - 1) * 44 : 0,
    axisLabel: { color: "#71808a", fontSize: 8 },
    axisLine: { show: false },
    axisTick: { show: false },
    splitLine: index === 0 ? { lineStyle: { color: "#202c35", type: "dashed" } } : { show: false },
  }));
  chart.setOption({
    animation: false,
    textStyle: { color: "#81909a", fontFamily: "SFMono-Regular, Consolas, monospace", fontSize: 9 },
    grid: { left: 60, right: Math.max(35, units.length > 1 ? 50 * (units.length - 1) : 25), top: 48, bottom: 54 },
    axisPointer: { link: [{ xAxisIndex: [0] }], lineStyle: { color: "#70828e", type: "dashed" } },
    tooltip: {
      trigger: "axis",
      backgroundColor: "#111a21",
      borderColor: "#34454f",
      textStyle: { color: "#e0e7eb", fontSize: 10 },
      formatter: (items) => {
        if (!items?.length) return "";
        return `<strong>${escapeHtml(fmtDate(items[0].axisValue))}</strong>${items.map((item) => `<div style="display:flex;justify-content:space-between;gap:16px;margin-top:5px">${item.marker}${escapeHtml(item.seriesName)}<b style="font-family:monospace">${number(item.value?.[1] ?? item.value)}${state.macroMode === "zscore" ? " 个标准差" : ""}</b></div>`).join("")}`;
      },
    },
    legend: { top: 7, left: 55, itemWidth: 11, itemHeight: 2, itemGap: 13, textStyle: { color: "#a2afb7", fontSize: 9 } },
    xAxis: { type: "time", axisLabel: { color: "#72818b", fontSize: 8, hideOverlap: true }, axisLine: { lineStyle: { color: "#26343e" } }, axisTick: { show: false }, splitLine: { show: false } },
    yAxis,
    dataZoom: [
      { type: "inside", filterMode: "none" },
      { type: "slider", bottom: 4, height: 17, borderColor: "#26343e", backgroundColor: "#0a1117", fillerColor: "#32463d55", handleStyle: { color: "#89b39a" }, textStyle: { color: "#687781", fontSize: 8 } },
    ],
    series,
  }, true);
}

function renderRegimeView() {
  const record = state.regimes.find((item) => item.country === state.country);
  const history = record?.history || [];
  safeText($("regime-title"), `${COUNTRY_LABELS[state.country] || state.country} · 增长 × 通胀`);
  safeText($("regime-coverage"), history.length ? `${history.length} 个月` : "暂无历史记录");
  const chart = getChart("regime-chart");
  if (!window.echarts || !history.length) {
    chart.clear();
    renderRegimeDistribution([]);
    $("regime-legend").replaceChildren();
    return;
  }
  const states = [...new Set(history.map((item) => item.regime))];
  const displayStates = states.map(regimeLabel);
  const grouped = states.map((name) => ({
    name: regimeLabel(name),
    type: "scatter",
    symbolSize: 8,
    data: history.filter((item) => item.regime === name).map((item) => [item.date, regimeLabel(name)]),
    itemStyle: { color: regimeColor(name) },
  }));
  chart.setOption({
    animation: false,
    textStyle: { color: "#81909a", fontFamily: "SFMono-Regular, Consolas, monospace", fontSize: 8 },
    grid: { left: 106, right: 20, top: 17, bottom: 31 },
    tooltip: { trigger: "item", backgroundColor: "#111a21", borderColor: "#34454f", textStyle: { color: "#e0e7eb", fontSize: 9 }, formatter: (item) => `${escapeHtml(fmtDate(item.value[0]))}<br/>${escapeHtml(item.value[1])}` },
    xAxis: { type: "time", axisLabel: { color: "#72818b", fontSize: 8, hideOverlap: true }, axisLine: { lineStyle: { color: "#26343e" } }, axisTick: { show: false }, splitLine: { show: false } },
    yAxis: { type: "category", data: displayStates, axisLabel: { color: "#87949d", fontSize: 8 }, axisLine: { show: false }, axisTick: { show: false }, splitLine: { show: true, lineStyle: { color: "#1d2932", type: "dashed" } } },
    series: grouped,
  }, true);
  const legend = $("regime-legend");
  legend.replaceChildren();
  states.forEach((name) => {
    const item = el("span");
    const swatch = el("i");
    swatch.style.background = regimeColor(name);
    item.append(swatch, document.createTextNode(regimeLabel(name)));
    legend.append(item);
  });
  renderRegimeDistribution(history);
}

function regimeColor(name) {
  const normalized = String(name).toLowerCase();
  return REGIME_COLORS[normalized] || (normalized.includes("stag") ? REGIME_COLORS.stagflation : normalized.includes("infl") ? REGIME_COLORS.reflation : normalized.includes("dis") ? REGIME_COLORS.disinflation : REGIME_COLORS.recovery);
}

function regimeLabel(name) {
  return REGIME_LABELS[String(name).toLowerCase()] || String(name);
}

function renderRegimeDistribution(history) {
  const container = $("regime-distribution");
  container.replaceChildren();
  if (!history.length) { container.append(el("div", "micro-note", "尚无已导出的状态历史。")); return; }
  const counts = new Map();
  history.forEach((item) => counts.set(item.regime, (counts.get(item.regime) || 0) + 1));
  [...counts.entries()].sort((a, b) => b[1] - a[1]).forEach(([name, count]) => {
    const row = el("div", "distribution-row");
    const swatch = el("i");
    swatch.style.background = regimeColor(name);
    row.append(swatch, el("span", "", regimeLabel(name)), el("b", "", `${Math.round(count / history.length * 100)}%`));
    container.append(row);
  });
}

function renderDataPage() {
  const sources = state.summary.sources || {};
  const registry = [
    ["market_prices", "市场行情", "雅虎财经", "ETF 日线价格与普通成交量"],
    ["china_macro", "中国宏观", "国家统计局 / 中国人民银行", "官方发布快照已归档；历史覆盖不均"],
    ["china_snapshot_refresh", "中国数据归档", "官方数据归档扫描", "每月或手动扫描；工作日运行沿用既有快照"],
    ["us_macro", "美国宏观", "FRED 最新修订值", "发布日期为近似值；历史修订仍会影响数据"],
  ];
  const cards = $("data-status-cards");
  cards.replaceChildren();
  registry.forEach(([key, label, provider, description]) => {
    const status = sources[key] || {};
    const failed = status.state === "failed";
    const ready = status.state === "success";
    const card = el("article", "status-card");
    const top = el("div", "status-card-top");
    top.append(el("span", "", label));
    const statusTag = el("span", `status-state ${failed ? "fail" : ready ? "" : "warn"}`);
    statusTag.append(el("i"), document.createTextNode(failed ? "失败" : ready ? "可用" : "未运行"));
    top.append(statusTag);
    card.append(top, el("h3", "", provider));
    const sourceLine = failed ? `${description}。已保留此前有效数据。` : description;
    card.append(el("p", "", sourceLine));
    const lastSuccess = status.last_success_at
      ? `最近成功更新：${fmtTimestamp(status.last_success_at)}`
      : "尚无成功更新记录";
    card.append(el("small", "status-update", lastSuccess));
    cards.append(card);
  });
  const allSeries = state.series;
  safeText($("schema-version"), `v${state.summary.schema_version || "—"}`);
  safeText($("series-count"), `${allSeries.length} 条序列`);
  const body = $("series-table-body");
  body.replaceChildren();
  [...allSeries].sort((a, b) => a.domain.localeCompare(b.domain) || a.label.localeCompare(b.label)).forEach((record) => {
    const row = document.createElement("tr");
    const titleCell = document.createElement("td");
    titleCell.append(document.createTextNode(record.label));
    titleCell.append(el("span", "table-sub", `${KIND_LABELS[record.kind] || "数据序列"} · ${UNIT_LABELS[record.unit] || record.unit || "未注明单位"}`));
    const countryCell = el("td", "", `${COUNTRY_LABELS[record.country] || record.country || "—"} / ${DOMAIN_LABELS[record.domain] || record.domain}`);
    const coverageCell = el("td", "", `${record.coverage?.from || "—"} → ${record.coverage?.through || "—"} · ${record.coverage?.observations || 0} 条`);
    const availableCell = el("td", "", fmtDate(record.coverage?.latest_available_date));
    const sourceCell = el("td", "", record.source || "—");
    const pitCell = document.createElement("td");
    const pit = el("span", `pit-pill ${record.pit_status?.includes("official_release") ? "good" : ""}`, shortPitStatus(record.pit_status));
    pit.title = record.pit_status || "unknown";
    pitCell.append(pit);
    row.append(titleCell, countryCell, coverageCell, availableCell, sourceCell, pitCell);
    body.append(row);
  });
  renderWarnings();
}

function shortPitStatus(value) {
  const status = String(value || "unknown");
  if (status.includes("not_pit_safe") || status.includes("not_pit")) return "非点时安全";
  if (status.includes("official_release")) return "官方快照 · 覆盖不完整";
  if (status.includes("adjusted_history")) return "复权历史 · 无版本归档";
  if (status.includes("unknown")) return "状态未知";
  return "需进一步验证";
}

function renderWarnings() {
  const container = $("data-warnings");
  container.replaceChildren();
  const gates = state.summary.pit_gate || {};
  [["中国数据覆盖", gates.china], ["美国宏观历史", gates.united_states], ["历史时点研究检查", gates.alfred], ["回测准入检查", gates.backtests]].forEach(([title, message]) => {
    if (!message) return;
    const card = el("article", "warning-card");
    card.append(el("strong", "", title), el("span", "", message));
    container.append(card);
  });
  (state.summary.errors || []).forEach((message) => {
    const card = el("article", "warning-card");
    card.append(el("strong", "", "最近一次更新错误"), el("span", "", message));
    container.append(card);
  });
  if (state.summary.china_harvest_state === "not_run") {
    const card = el("article", "warning-card");
    card.append(el("strong", "", "中国数据归档频率"), el("span", "", "国家统计局和中国人民银行官方数据每月扫描一次，也可手动触发；日常更新会沿用此前归档的官方快照。"));
    container.append(card);
  }
  const missingInputs = [
    ...(sources.china_macro?.missing_configured_inputs || []),
    ...(sources.us_macro?.missing_configured_inputs || []),
  ];
  if (missingInputs.length) {
    const card = el("article", "warning-card");
    card.append(el("strong", "", "尚未接入的指标"), el("span", "", `当前配置但没有可用快照：${missingInputs.join("、")}`));
    container.append(card);
  }
}

function renderResearchPage() {
  const status = state.backtests.status || "not_exported";
  const hasResults = status === "verified" && Array.isArray(state.backtests.results) && state.backtests.results.length > 0;
  safeText($("research-state"), hasResults ? "已有通过验证的研究结果" : "研究结果尚未接入");
  safeText($("research-message"), hasResults
    ? "当前通过研究数据契约导出的结果。具体 PIT 与样本外验证状态见下方。"
    : state.backtests.message || "当前报告目录没有已生成结果。终端不会用模拟收益代替实际回测。");
  const container = $("engine-grid");
  container.replaceChildren();
  const engines = [
    ["宏观", "因子基线 · 部分可用", "中国官方快照覆盖不全；美国数据为最新修订值。"],
    ["研究", "历史时点检查 · 未完成", "按历史时点重建数据的流程尚未通过验证。"],
    ["估值", "已有 Python 代码", "已有 SEC / FCFF 实现；尚无估值运行结果导出到终端。"],
    ["资金流", "基础契约与估算工具", "尚无生产级资金流数据集或终端运行结果。"],
  ];
  engines.forEach(([name, title, detail]) => {
    const cell = el("div", "engine-cell");
    cell.append(el("span", "", name), el("strong", "", title), el("small", "", detail));
    container.append(cell);
  });
}

function getChart(id) {
  if (!window.echarts) return { setOption() {}, clear() {}, resize() {}, dispatchAction() {} };
  if (!state.charts[id]) state.charts[id] = echarts.init($(id), null, { renderer: "canvas" });
  return state.charts[id];
}

function showPage(page) {
  document.querySelectorAll("[data-page-view]").forEach((item) => item.classList.toggle("active", item.dataset.pageView === page));
  document.querySelectorAll(".nav-item, .mobile-nav-item").forEach((item) => {
    const active = item.dataset.page === page;
    item.classList.toggle("active", active);
    if (item.classList.contains("nav-item")) {
      if (active) item.setAttribute("aria-current", "page");
      else item.removeAttribute("aria-current");
    }
  });
  safeText($("breadcrumb-current"), ({ markets: "行情市场", macro: "宏观因子", data: "数据状态", research: "研究结果" })[page] || "研究系统");
  window.location.hash = page;
  window.setTimeout(() => Object.values(state.charts).forEach((chart) => chart?.resize()), 30);
}

function bindControls() {
  document.querySelectorAll("[data-page]").forEach((button) => button.addEventListener("click", () => showPage(button.dataset.page)));
  $("refresh-button").addEventListener("click", () => loadData(true));
  $("market-ranges").addEventListener("click", (event) => {
    const button = event.target.closest("button[data-range]");
    if (!button) return;
    state.marketRange = button.dataset.range;
    selectButton($("market-ranges"), button);
    renderMarketChart();
  });
  $("macro-ranges").addEventListener("click", (event) => {
    const button = event.target.closest("button[data-range]");
    if (!button) return;
    state.macroRange = button.dataset.range;
    selectButton($("macro-ranges"), button);
    renderMacroChart();
  });
  $("market-mode").addEventListener("click", (event) => {
    const button = event.target.closest("button[data-mode]");
    if (!button) return;
    state.marketMode = button.dataset.mode;
    selectButton($("market-mode"), button);
    renderMarketChart();
  });
  $("macro-mode").addEventListener("click", (event) => {
    const button = event.target.closest("button[data-mode]");
    if (!button) return;
    state.macroMode = button.dataset.mode;
    selectButton($("macro-mode"), button);
    renderMacroChart();
  });
  $("country-switch").addEventListener("click", (event) => {
    const button = event.target.closest("button[data-country]");
    if (!button) return;
    state.country = button.dataset.country;
    state.selectedMacro.clear();
    selectButton($("country-switch"), button);
    renderMacroPage();
  });
  $("volume-symbol").addEventListener("change", (event) => {
    state.volumeSymbol = event.target.value;
    renderMarketChart();
  });
  $("market-restore").addEventListener("click", () => state.charts["market-chart"]?.dispatchAction({ type: "dataZoom", start: 0, end: 100 }));
  window.addEventListener("resize", () => Object.values(state.charts).forEach((chart) => chart?.resize()));
  window.addEventListener("hashchange", () => {
    const page = window.location.hash.slice(1);
    if (["markets", "macro", "data", "research"].includes(page)) showPage(page);
  });
}

function selectButton(container, selected) {
  container.querySelectorAll("button").forEach((button) => button.classList.toggle("selected", button === selected));
}

let toastTimer;
function showToastMessage(message, failed = false) {
  const toast = $("toast");
  safeText(toast, message);
  toast.style.borderColor = failed ? "#69423c" : "#365042";
  toast.style.color = failed ? "#dfa094" : "#b8d5c4";
  toast.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toast.classList.remove("show"), 3200);
}

bindControls();
const initialPage = window.location.hash.slice(1);
if (["markets", "macro", "data", "research"].includes(initialPage)) showPage(initialPage);
loadData();
