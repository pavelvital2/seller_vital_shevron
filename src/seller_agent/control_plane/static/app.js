"use strict";

const API_BASE = "/vital-shevron/api/v1";
const POLL_INTERVAL_MS = 1800;
const TERMINAL = new Set(["success", "partial_success", "failed", "timeout", "cancelled"]);
const REGIONS_BY_MARKETPLACE = {
  ozon: [
    ["moscow", "Москва"],
    ["rostov-on-don", "Ростов-на-Дону"],
  ],
  wb: [
    ["moscow", "Москва"],
    ["rostov-on-don", "Ростов-на-Дону"],
    ["novosibirsk", "Новосибирск"],
    ["kazan", "Казань"],
  ],
};
const DATA_UNAVAILABLE = "данные недоступны";
const STATE_LABELS = {
  unknown: "неизвестно",
  fresh: "свежие данные",
  stale: "устаревшие данные",
  warning: "требует внимания",
  ok: "готово",
  ready: "готово",
  success: "готово",
  partial_success: "готово частично",
  failed: "ошибка",
  timeout: "превышено время ожидания",
  cancelled: "отменено",
  created: "создано",
  queued: "в очереди",
  running: "выполняется",
};
const SOURCE_LABELS = {
  "marketplace-period-report": "отчёт площадки",
  "parser-data-api": "поисковые данные",
  "ozon-stock-supply-monitor": "монитор остатков Ozon",
  "wb-stock-supply-monitor": "монитор остатков Wildberries",
};
const state = { csrfToken: "", authenticated: false, activeJobId: "" };

const element = (id) => document.getElementById(id);

function setConnection(kind, label) {
  const target = element("connection-state");
  target.dataset.state = kind;
  target.lastElementChild.textContent = label;
}

function selectView(name) {
  document.querySelectorAll(".view").forEach((view) => {
    const active = view.id === `view-${name}`;
    view.hidden = !active;
    view.classList.toggle("active", active);
  });
  document.querySelectorAll(".nav-item").forEach((button) => {
    button.classList.toggle("active", button.dataset.view === name);
  });
}

function setAnalyticsStatus(kind, title, copy) {
  const panel = element("analytics-status");
  panel.dataset.state = kind;
  element("analytics-status-title").textContent = title;
  element("analytics-status-copy").textContent = copy;
}

function createNode(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = String(text);
  return node;
}

function metricCard(label, value) {
  const item = createNode("div", "metric");
  item.append(createNode("span", "", label), createNode("strong", "", value));
  return item;
}

function stateLabel(value) {
  return STATE_LABELS[value] || STATE_LABELS.unknown;
}

function sourceLabel(value) {
  return SOURCE_LABELS[value] || "проверенный источник";
}

function regionLabel(value) {
  for (const regions of Object.values(REGIONS_BY_MARKETPLACE)) {
    const match = regions.find(([regionId]) => regionId === value);
    if (match) return match[1];
  }
  return DATA_UNAVAILABLE;
}

function number(value) {
  if (typeof value !== "number") return DATA_UNAVAILABLE;
  return new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 2 }).format(value);
}

function money(value) {
  if (typeof value !== "number") return DATA_UNAVAILABLE;
  return new Intl.NumberFormat("ru-RU", {
    style: "currency",
    currency: "RUB",
    maximumFractionDigits: 0,
  }).format(value);
}

function change(value, formatter) {
  if (typeof value !== "number") return DATA_UNAVAILABLE;
  const rendered = formatter(Math.abs(value));
  if (value > 0) return `+${rendered}`;
  if (value < 0) return `−${rendered}`;
  return rendered;
}

function sourceCard(title, source, metrics) {
  const card = createNode("article", "result-card");
  card.append(createNode("h3", "", title));
  if (!source || source.data_available !== true) {
    card.append(createNode("p", "card-note", "Данные недоступны: источник не подтвердил результат."));
    return card;
  }
  const grid = createNode("div", "metric-grid");
  metrics.forEach(([label, value]) => grid.append(metricCard(label, value)));
  card.append(grid);
  if (source.freshness) {
    card.append(createNode("p", "card-note", `Свежесть: ${stateLabel(source.freshness.state)} · ${source.freshness.observed_at || DATA_UNAVAILABLE}`));
  }
  return card;
}

function renderFacts(title, items, fallback) {
  const card = createNode("article", "result-card");
  card.append(createNode("h3", "", title));
  const list = createNode("ul", "fact-list");
  if (!Array.isArray(items) || items.length === 0) {
    list.append(createNode("li", "", fallback));
  } else {
    items.forEach((item) => list.append(createNode("li", "", `${item.label || "Подтверждённый сигнал"} · ${sourceLabel(item.source)}`)));
  }
  card.append(list);
  return card;
}

function renderResult(summary) {
  const root = element("analytics-result");
  root.replaceChildren();
  root.hidden = false;
  const sales = summary.sales;
  const current = sales && sales.current ? sales.current : {};
  const changes = sales && sales.changes ? sales.changes : {};
  const margin = sales && sales.margin ? sales.margin : {};
  const marginValue = margin.data_available === true && typeof margin.value === "number"
    ? money(margin.value)
    : DATA_UNAVAILABLE;
  const parser = summary.parser_visibility || {};
  const parserMetrics = parser.metrics || parser.current || {};
  const parserCard = sourceCard("Видимость в поиске", parser, [
    ["Видимые товары", number(parserMetrics.visible_products)],
    ["Запросы", number(parserMetrics.query_count ?? parserMetrics.visible_queries)],
    ["Строк сравнения", number(parserMetrics.total_rows)],
  ]);
  const comparison = parser.comparison || {};
  const comparisonLabel = comparison.available === true
    ? `${comparison.previous_date} → ${comparison.current_date}`
    : "сравнение недоступно";
  parserCard.append(createNode("p", "card-note", `Регион: ${regionLabel(summary.region_id || parser.region_id)} · Снимки поиска: ${comparisonLabel}`));
  root.append(
    sourceCard("Продажи и финансы", sales, [
      ["Заказы", number(current.orders)],
      ["Заказы к прошлому периоду", change(changes.orders, number)],
      ["Выручка", money(current.revenue)],
      ["Выручка к прошлому периоду", change(changes.revenue, money)],
      ["Возвраты", number(current.returns)],
      ["Возвраты к прошлому периоду", change(changes.returns, number)],
      ["Маржа", marginValue],
      ["Поступления до себестоимости", money(current.net_before_cogs)],
    ]),
    sourceCard("Остатки", summary.stocks, [["Доступно единиц", number(summary.stocks && summary.stocks.total_units)]]),
    parserCard,
    renderFacts("Подтверждённые проблемы", summary.problems, "Подтверждённых проблем в доступных источниках нет."),
    renderFacts("Следующие действия", summary.next_actions, "Дополнительных действий сейчас не требуется."),
  );
  const parserState = summary.parser_visibility && summary.parser_visibility.freshness
    ? summary.parser_visibility.freshness.state
    : "unknown";
  element("overview-freshness").textContent = stateLabel(parserState);
  const statusKind = parserState === "stale" ? "stale" : summary.overall_status === "ok" ? "ready" : "warning";
  setAnalyticsStatus(statusKind, "Отчёт готов", `Статус: ${stateLabel(summary.overall_status)}. Свежесть поисковых данных: ${stateLabel(parserState)}.`);
}

async function requestJson(path, options) {
  const response = await fetch(`${API_BASE}${path}`, {
    credentials: "same-origin",
    cache: "no-store",
    ...options,
  });
  const payload = await response.json().catch(() => ({ error: { code: "response_invalid" } }));
  if (!response.ok) {
    const code = payload && payload.error ? payload.error.code : "request_failed";
    throw new Error(code);
  }
  return payload;
}

async function authenticate() {
  const telegram = window.Telegram && window.Telegram.WebApp;
  const rawInitData = telegram ? telegram.initData : "";
  if (!rawInitData) throw new Error("telegram_init_data_missing");
  telegram.ready();
  telegram.expand();
  const result = await requestJson("/auth/telegram", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ init_data: rawInitData }),
  });
  state.csrfToken = result.csrf_token;
  state.authenticated = true;
  element("run-overview").disabled = false;
  setConnection("ready", "Защищено");
}

async function restoreSession() {
  const result = await requestJson("/session", { method: "GET" });
  state.csrfToken = result.csrf_token;
  state.authenticated = true;
  element("run-overview").disabled = false;
  setConnection("ready", "Защищено");
}

function selectedValue(name) {
  const select = document.querySelector(`select[name="${name}"]`);
  if (select) return select.value;
  const selected = document.querySelector(`input[name="${name}"]:checked`);
  return selected ? selected.value : "";
}

function updateRegionOptions() {
  const marketplace = selectedValue("marketplace");
  const select = element("region-select");
  const previous = select.value;
  const regions = REGIONS_BY_MARKETPLACE[marketplace] || [];
  const options = regions.map(([value, label]) => {
    const option = createNode("option", "", label);
    option.value = value;
    return option;
  });
  select.replaceChildren(...options);
  if (regions.some(([value]) => value === previous)) select.value = previous;
}

async function pollJob(publicJobId) {
  const job = await requestJson(`/jobs/${encodeURIComponent(publicJobId)}`, { method: "GET" });
  if (!TERMINAL.has(job.status)) {
    setAnalyticsStatus("loading", "Задача в очереди", `Статус: ${stateLabel(job.status)}. Результат обновится автоматически.`);
    window.setTimeout(() => pollJob(publicJobId).catch(showJobError), POLL_INTERVAL_MS);
    return;
  }
  element("run-overview").disabled = false;
  if (job.status !== "success" && job.status !== "partial_success") {
    setAnalyticsStatus("error", "Отчёт не получен", `Статус: ${stateLabel(job.status)}. Изменения на площадке не выполнялись.`);
    return;
  }
  const summary = job.result && job.result.summary;
  if (!summary || typeof summary !== "object") {
    setAnalyticsStatus("empty", "Результат пуст", "Подтверждённые данные не получены.");
    return;
  }
  renderResult(summary);
}

function showJobError(error) {
  element("run-overview").disabled = !state.authenticated;
  setAnalyticsStatus("error", "Не удалось получить отчёт", "Безопасно повторите запрос позже.");
}

async function submitOverview(event) {
  event.preventDefault();
  if (!state.authenticated) return;
  element("run-overview").disabled = true;
  element("analytics-result").hidden = true;
  setAnalyticsStatus("loading", "Создаю задачу только для чтения", "Отчёт выполняет единственный штатный обработчик.");
  try {
    const job = await requestJson("/jobs", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRF-Token": state.csrfToken,
        "Idempotency-Key": crypto.randomUUID(),
      },
      body: JSON.stringify({
        task_id: "store-analytics-overview",
        params: {
          marketplace: selectedValue("marketplace"),
          period_days: Number(selectedValue("period")),
          region_id: selectedValue("region"),
        },
      }),
    });
    state.activeJobId = job.job_id;
    await pollJob(job.job_id);
  } catch (error) {
    showJobError(error);
  }
}

function bindNavigation() {
  document.querySelectorAll("[data-view]").forEach((button) => {
    button.addEventListener("click", () => selectView(button.dataset.view));
  });
  document.querySelectorAll("[data-open-view]").forEach((button) => {
    button.addEventListener("click", () => selectView(button.dataset.openView));
  });
  document.querySelectorAll('input[name="marketplace"]').forEach((input) => {
    input.addEventListener("change", updateRegionOptions);
  });
  updateRegionOptions();
}

async function boot() {
  bindNavigation();
  element("analytics-form").addEventListener("submit", submitOverview);
  try {
    try {
      await restoreSession();
    } catch (sessionError) {
      await authenticate();
    }
  } catch (error) {
    setConnection("error", "Недоступно");
    setAnalyticsStatus("error", "Откройте приложение из Telegram", "Не удалось подтвердить защищённый сеанс.");
  }
}

document.addEventListener("DOMContentLoaded", boot);
