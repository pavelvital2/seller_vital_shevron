"use strict";

const API_BASE = "/vital-shevron/api/v1";
const POLL_INTERVAL_MS = 1800;
const MAX_POLL_ATTEMPTS = 100;
const OPERATIONS_STALE_AFTER_MS = 120000;
const TERMINAL = new Set(["success", "partial_success", "failed", "timeout", "cancelled"]);
const VIEWS = new Set(["state", "analytics", "jobs", "approvals"]);
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
  waiting_confirmation: "ожидает подтверждения",
  pending_review: "ожидает решения",
  applying_unknown: "требует сверки",
};
const SOURCE_LABELS = {
  "marketplace-period-report": "отчёт площадки",
  "parser-data-api": "поисковые данные",
  "ozon-stock-supply-monitor": "монитор остатков Ozon",
  "wb-stock-supply-monitor": "монитор остатков Wildberries",
};
const TASK_LABELS = {
  "store-analytics-overview": "Аналитика магазина",
  "daily-morning-report": "Утренний отчёт",
};
const state = {
  csrfToken: "",
  authenticated: false,
  activeJobs: { analytics: "", daily: "" },
  submissionKeys: { analytics: null, daily: null },
};

const element = (id) => document.getElementById(id);

function createNode(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = String(text);
  return node;
}

function setConnection(kind, label) {
  const target = element("connection-state");
  target.dataset.state = kind;
  target.lastElementChild.textContent = label;
}

function setPanel(prefix, kind, title, copy) {
  const panel = element(`${prefix}-status`);
  panel.dataset.state = kind;
  element(`${prefix}-status-title`).textContent = title;
  element(`${prefix}-status-copy`).textContent = copy;
}

function setAnalyticsStatus(kind, title, copy) {
  setPanel("analytics", kind, title, copy);
}

function setDailyStatus(kind, copy) {
  const target = element("daily-report-status");
  target.dataset.state = kind;
  target.textContent = copy;
}

function setAuthenticatedControls() {
  element("run-overview").disabled = !state.authenticated || Boolean(state.activeJobs.analytics);
  element("run-daily-report").disabled = !state.authenticated || Boolean(state.activeJobs.daily);
}

function selectView(name) {
  if (!VIEWS.has(name)) return;
  document.querySelectorAll(".view").forEach((view) => {
    const active = view.id === `view-${name}`;
    view.hidden = !active;
    view.classList.toggle("active", active);
  });
  document.querySelectorAll(".nav-item").forEach((button) => {
    const active = button.dataset.view === name;
    button.classList.toggle("active", active);
    if (active) {
      button.setAttribute("aria-current", "page");
    } else {
      button.removeAttribute("aria-current");
    }
  });
  if (state.authenticated) refreshView(name);
}

function stateLabel(value) {
  return STATE_LABELS[value] || STATE_LABELS.unknown;
}

function sourceLabel(value) {
  return SOURCE_LABELS[value] || "проверенный источник";
}

function taskLabel(value) {
  return TASK_LABELS[value] || "Задача control plane";
}

function regionLabel(value) {
  for (const regions of Object.values(REGIONS_BY_MARKETPLACE)) {
    const match = regions.find(([regionId]) => regionId === value);
    if (match) return match[1];
  }
  return DATA_UNAVAILABLE;
}

function number(value) {
  if (typeof value !== "number" || !Number.isFinite(value)) return DATA_UNAVAILABLE;
  return new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 2 }).format(value);
}

function count(value) {
  if (!Number.isInteger(value) || value < 0) return DATA_UNAVAILABLE;
  return new Intl.NumberFormat("ru-RU").format(value);
}

function money(value) {
  if (typeof value !== "number" || !Number.isFinite(value)) return DATA_UNAVAILABLE;
  return new Intl.NumberFormat("ru-RU", {
    style: "currency",
    currency: "RUB",
    maximumFractionDigits: 0,
  }).format(value);
}

function change(value, formatter) {
  if (typeof value !== "number" || !Number.isFinite(value)) return DATA_UNAVAILABLE;
  const rendered = formatter(Math.abs(value));
  if (value > 0) return `+${rendered}`;
  if (value < 0) return `−${rendered}`;
  return rendered;
}

function formatTimestamp(value) {
  if (typeof value !== "string" || !value) return DATA_UNAVAILABLE;
  const parsed = Date.parse(value);
  if (!Number.isFinite(parsed)) return DATA_UNAVAILABLE;
  return new Intl.DateTimeFormat("ru-RU", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(parsed));
}

function timestampIsStale(value) {
  if (typeof value !== "string" || !value) return true;
  const parsed = Date.parse(value);
  if (!Number.isFinite(parsed)) return true;
  const age = Date.now() - parsed;
  return age < 0 || age > OPERATIONS_STALE_AFTER_MS;
}

function metricCard(label, value) {
  const item = createNode("div", "metric");
  item.append(createNode("span", "", label), createNode("strong", "", value));
  return item;
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
    items.forEach((item) => {
      const label = item && typeof item.label === "string" ? item.label : "Подтверждённый сигнал";
      const source = item && typeof item.source === "string" ? item.source : "";
      list.append(createNode("li", "", `${label} · ${sourceLabel(source)}`));
    });
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
  setAuthenticatedControls();
  setConnection("ready", "Защищено");
}

async function restoreSession() {
  const result = await requestJson("/session", { method: "GET" });
  state.csrfToken = result.csrf_token;
  state.authenticated = true;
  setAuthenticatedControls();
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

function submissionKey(slot, payload) {
  const fingerprint = JSON.stringify(payload);
  const current = state.submissionKeys[slot];
  if (current && current.fingerprint === fingerprint) return current.key;
  const key = crypto.randomUUID();
  state.submissionKeys[slot] = { fingerprint, key };
  return key;
}

function finishPolling(slot, clearIdempotency) {
  state.activeJobs[slot] = "";
  if (clearIdempotency) state.submissionKeys[slot] = null;
  setAuthenticatedControls();
}

function handlePollingError(slot, onError) {
  finishPolling(slot, false);
  onError();
}

async function pollJob(publicJobId, slot, onProgress, onTerminal, onError, attempt = 0) {
  if (state.activeJobs[slot] !== publicJobId) return;
  if (attempt >= MAX_POLL_ATTEMPTS) {
    finishPolling(slot, false);
    onError("stale");
    return;
  }
  const job = await requestJson(`/jobs/${encodeURIComponent(publicJobId)}`, { method: "GET" });
  if (!TERMINAL.has(job.status)) {
    onProgress(job);
    window.setTimeout(() => {
      pollJob(publicJobId, slot, onProgress, onTerminal, onError, attempt + 1)
        .catch(() => handlePollingError(slot, onError));
    }, POLL_INTERVAL_MS);
    return;
  }
  finishPolling(slot, true);
  onTerminal(job);
}

function showAnalyticsJobError(kind) {
  const stale = kind === "stale";
  setAnalyticsStatus(
    stale ? "stale" : "error",
    stale ? "Результат не обновился" : "Не удалось получить отчёт",
    stale ? "Ожидание ограничено. Безопасно проверьте задание позже." : "Безопасно повторите запрос позже.",
  );
}

function handleAnalyticsTerminal(job) {
  if (job.status !== "success" && job.status !== "partial_success") {
    setAnalyticsStatus("error", "Отчёт не получен", `Статус: ${stateLabel(job.status)}. Изменения на площадке не выполнялись.`);
    refreshJobs();
    refreshOperations();
    return;
  }
  const summary = job.result && job.result.summary;
  if (!summary || typeof summary !== "object" || Array.isArray(summary)) {
    setAnalyticsStatus("empty", "Результат пуст", "Подтверждённые данные не получены.");
  } else {
    renderResult(summary);
  }
  refreshJobs();
  refreshOperations();
}

async function submitOverview(event) {
  event.preventDefault();
  if (!state.authenticated || state.activeJobs.analytics) return;
  const payload = {
    task_id: "store-analytics-overview",
    params: {
      marketplace: selectedValue("marketplace"),
      period_days: Number(selectedValue("period")),
      region_id: selectedValue("region"),
    },
  };
  element("analytics-result").hidden = true;
  setAnalyticsStatus("loading", "Создаю задачу только для чтения", "Отчёт выполняет единственный штатный обработчик.");
  element("run-overview").disabled = true;
  try {
    const job = await requestJson("/jobs", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRF-Token": state.csrfToken,
        "Idempotency-Key": submissionKey("analytics", payload),
      },
      body: JSON.stringify(payload),
    });
    state.activeJobs.analytics = job.job_id;
    setAuthenticatedControls();
    await pollJob(
      job.job_id,
      "analytics",
      (progress) => setAnalyticsStatus("loading", "Задача в очереди", `Статус: ${stateLabel(progress.status)}. Результат обновится автоматически.`),
      handleAnalyticsTerminal,
      showAnalyticsJobError,
    );
  } catch (error) {
    handlePollingError("analytics", showAnalyticsJobError);
  }
}

function handleDailyTerminal(job) {
  if (job.status === "success" || job.status === "partial_success") {
    setDailyStatus("ready", `Утренний отчёт завершён: ${stateLabel(job.status)}.`);
  } else {
    setDailyStatus("error", `Утренний отчёт не получен: ${stateLabel(job.status)}.`);
  }
  refreshOperations();
  refreshJobs();
}

function showDailyJobError(kind) {
  setDailyStatus(
    kind === "stale" ? "stale" : "error",
    kind === "stale"
      ? "Ожидание ограничено. Проверьте историю заданий позже."
      : "Не удалось подтвердить состояние отчёта. Безопасно повторите позже.",
  );
}

async function submitDailyReport() {
  if (!state.authenticated || state.activeJobs.daily) return;
  const payload = { task_id: "daily-morning-report", params: {} };
  element("run-daily-report").disabled = true;
  setDailyStatus("loading", "Создаю read-only job в общей runtime-очереди.");
  try {
    const job = await requestJson("/jobs", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRF-Token": state.csrfToken,
        "Idempotency-Key": submissionKey("daily", payload),
      },
      body: JSON.stringify(payload),
    });
    state.activeJobs.daily = job.job_id;
    setAuthenticatedControls();
    await pollJob(
      job.job_id,
      "daily",
      (progress) => setDailyStatus("loading", `Статус: ${stateLabel(progress.status)}.`),
      handleDailyTerminal,
      showDailyJobError,
    );
  } catch (error) {
    handlePollingError("daily", showDailyJobError);
  }
}

async function refreshOperations() {
  setPanel("operations", "loading", "Проверяю runtime-сводку", "Показывается только подтверждённое состояние общей очереди.");
  try {
    const payload = await requestJson("/operations/summary", { method: "GET" });
    if (!payload || payload.data_available !== true) {
      throw new Error("data_unavailable");
    }
    const jobs = payload.jobs && typeof payload.jobs === "object" ? payload.jobs : {};
    const approvals = payload.approvals && typeof payload.approvals === "object" ? payload.approvals : {};
    element("active-jobs-count").textContent = count(jobs.active);
    element("terminal-jobs-count").textContent = count(jobs.terminal);
    element("pending-approvals-count").textContent = count(approvals.pending_review);
    element("unknown-approvals-count").textContent = count(approvals.applying_unknown);
    element("operations-updated").textContent = formatTimestamp(payload.observed_at);
    const latest = payload.last_control_job;
    if (latest && typeof latest === "object" && !Array.isArray(latest)) {
      element("last-control-job-title").textContent = taskLabel(latest.task_id);
      element("last-control-job-copy").textContent = `${stateLabel(latest.status)} · ${formatTimestamp(latest.updated_at)}`;
    } else {
      element("last-control-job-title").textContent = "Запусков пока нет";
      element("last-control-job-copy").textContent = "После первого запуска появятся безопасный статус и время.";
    }
    const stale = timestampIsStale(payload.observed_at);
    setPanel(
      "operations",
      stale ? "stale" : "ready",
      stale ? "Сводка устарела" : "Runtime-сводка обновлена",
      stale ? "Данные были подтверждены ранее; обновите раздел позже." : "Это состояние SQLite, а не проверка API, ЛК или площадок.",
    );
  } catch (error) {
    for (const id of (
      ["active-jobs-count", "terminal-jobs-count", "pending-approvals-count", "unknown-approvals-count"]
    )) {
      element(id).textContent = DATA_UNAVAILABLE;
    }
    element("operations-updated").textContent = DATA_UNAVAILABLE;
    setPanel("operations", "error", "Runtime-сводка недоступна", "Я не могу подтвердить состояние очереди по SQLite.");
  }
}

function renderJob(job) {
  const item = createNode("article", "record-item");
  const heading = createNode("div", "record-heading");
  heading.append(
    createNode("h3", "", taskLabel(job.task_id)),
    createNode("span", "record-state", stateLabel(job.status)),
  );
  heading.lastElementChild.dataset.state = job.status;
  const meta = createNode("div", "record-meta");
  meta.append(
    createNode("p", "", `Создано: ${formatTimestamp(job.created_at)}`),
    createNode("p", "", `Обновлено: ${formatTimestamp(job.updated_at)}`),
    createNode("p", "", `Публичный ID: ${typeof job.job_id === "string" ? job.job_id : DATA_UNAVAILABLE}`),
  );
  if (job.finished_at) meta.append(createNode("p", "", `Завершено: ${formatTimestamp(job.finished_at)}`));
  item.append(heading, meta);
  return item;
}

async function refreshJobs() {
  const root = element("jobs-list");
  root.replaceChildren();
  setPanel("jobs", "loading", "Загружаю задания", "Доступны только jobs, созданные текущим владельцем через Mini App.");
  try {
    const payload = await requestJson("/jobs?limit=20", { method: "GET" });
    if (!payload || payload.data_available !== true) throw new Error("data_unavailable");
    if (!Array.isArray(payload.jobs) || payload.jobs.length === 0) {
      setPanel("jobs", "empty", "Заданий пока нет", "Первый read-only запуск появится здесь автоматически.");
      return;
    }
    payload.jobs.forEach((job) => {
      if (job && typeof job === "object" && !Array.isArray(job)) root.append(renderJob(job));
    });
    if (!root.childElementCount) {
      setPanel("jobs", "empty", "Подтверждённых заданий нет", "Backend не вернул безопасные строки для показа.");
      return;
    }
    setPanel("jobs", "ready", "История обновлена", `Показано заданий: ${root.childElementCount}.`);
  } catch (error) {
    setPanel("jobs", "error", "История недоступна", "Не удалось получить безопасную owner-scoped проекцию.");
  }
}

function renderApproval(approval) {
  const item = createNode("article", "record-item");
  const heading = createNode("div", "record-heading");
  const label = typeof approval.label === "string" && approval.label ? approval.label : "Согласование";
  heading.append(
    createNode("h3", "", label),
    createNode("span", "record-state", stateLabel(approval.status)),
  );
  heading.lastElementChild.dataset.state = approval.status;
  const meta = createNode("div", "record-meta");
  meta.append(
    createNode("p", "", `Создано: ${formatTimestamp(approval.created_at)}`),
    createNode("p", "", `Обновлено: ${formatTimestamp(approval.updated_at)}`),
  );
  item.append(heading, meta);
  if (approval.requires_reconciliation === true) {
    item.append(createNode("p", "record-warning", "Требуется reconciliation. Повторное применение из Mini App заблокировано."));
  }
  return item;
}

async function refreshApprovals() {
  const root = element("approvals-list");
  root.replaceChildren();
  setPanel("approvals", "loading", "Загружаю безопасную сводку", "Payload, checksum и параметры применения не передаются.");
  try {
    const payload = await requestJson("/approvals", { method: "GET" });
    if (!payload || payload.data_available !== true) throw new Error("data_unavailable");
    const counts = payload.counts && typeof payload.counts === "object" ? payload.counts : {};
    element("approvals-pending-count").textContent = count(counts.pending_review);
    element("approvals-unknown-count").textContent = count(counts.applying_unknown);
    if (!Array.isArray(payload.approvals) || payload.approvals.length === 0) {
      setPanel("approvals", "empty", "Неразрешённых согласований нет", "Изменение marketplace state из этого раздела невозможно.");
      return;
    }
    payload.approvals.forEach((approval) => {
      if (approval && typeof approval === "object" && !Array.isArray(approval)) root.append(renderApproval(approval));
    });
    setPanel("approvals", "ready", "Сводка обновлена", `Показано строк: ${root.childElementCount}. Действия недоступны.`);
  } catch (error) {
    element("approvals-pending-count").textContent = DATA_UNAVAILABLE;
    element("approvals-unknown-count").textContent = DATA_UNAVAILABLE;
    setPanel("approvals", "error", "Согласования недоступны", "Я не могу подтвердить unresolved-состояния.");
  }
}

function refreshView(name) {
  if (name === "state") refreshOperations();
  if (name === "jobs") refreshJobs();
  if (name === "approvals") refreshApprovals();
}

function bindNavigation() {
  document.querySelectorAll("[data-view]").forEach((button) => {
    button.addEventListener("click", () => selectView(button.dataset.view || ""));
  });
  document.querySelectorAll("[data-open-view]").forEach((button) => {
    button.addEventListener("click", () => selectView(button.dataset.openView || ""));
  });
  document.querySelectorAll('input[name="marketplace"]').forEach((input) => {
    input.addEventListener("change", updateRegionOptions);
  });
  updateRegionOptions();
}

async function boot() {
  bindNavigation();
  element("analytics-form").addEventListener("submit", submitOverview);
  element("run-daily-report").addEventListener("click", submitDailyReport);
  try {
    try {
      await restoreSession();
    } catch (sessionError) {
      await authenticate();
    }
    refreshOperations();
  } catch (error) {
    setConnection("error", "Недоступно");
    setAnalyticsStatus("error", "Откройте приложение из Telegram", "Не удалось подтвердить защищённый сеанс.");
    setPanel("operations", "error", "Сеанс не подтверждён", "Runtime-сводка без owner session недоступна.");
    setDailyStatus("error", "Запуск заблокирован до подтверждения owner session.");
  }
}

document.addEventListener("DOMContentLoaded", boot);
