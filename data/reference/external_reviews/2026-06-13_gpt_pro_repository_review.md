Проверил репозиторий через GitHub. Код **не запускал** и локально не клонировал, поэтому это ревью по содержимому репозитория, структуре, README, видимым Python-модулям, тестам и проектным документам.

# Короткий вывод

**Как заготовка операционного агента для Ozon/WB — пойдет. Как фундамент будущего Telegram-бота — пока рано.**

Сейчас `seller_vital_shevron` больше похож на перенесенный и частично адаптированный **CLI/task-runner + агентский рабочий каркас**, а не на систему:

```text
Telegram → TaskRegistry → JobRunner → SafetyGuard → Executor → result/report → Telegram
```

Хорошо, что уже есть реальные процессы: Ozon/WB API-адаптеры, задачи для WB promotion, WB акций/скидок, Ozon Elastic, Ozon CPC, отзывы/вопросы, сессии ЛК, runbook-и и тесты. Но пока нет главного слоя, который нужен именно под бота: **нормального реестра задач, единого manifest/job lifecycle, централизованного safety guard, единого approval lifecycle и полноценного bot dispatcher**.

---

# Что уже хорошо

## 1. Направление правильное: API-first, dry-run, approval, verify

В `AGENTS.md` уже зафиксировано важное правило: опасные операции должны идти по цепочке `read-only -> dry-run -> review -> approved -> apply -> verify -> result`, а write-операции в магазинах нельзя выполнять без явного подтверждения владельца. Это ровно то, что нужно для Telegram-бота с кнопками, особенно для цен, скидок, рекламы, карточек, отзывов, вопросов и поставок. ([GitHub][1])

Отдельно хорошо, что в `AGENTS.md` прописан принцип API-first: если действие можно сделать через официальный API, ЛК используется только когда API недостаточно. Это правильная основа для снижения риска в ЛК-операциях. ([GitHub][1])

---

## 2. Есть хорошая операционная документация

В `README.md` и `data/planning/` уже заведены runbook-и по статус-проверке, ЛК-сессиям, утреннему отчету, catalog mapping, отзывам/вопросам, Telegram-отчетам, Ozon Elastic, Ozon CPC, WB акциям, WB promotion, ценам, поисковым запросам, парсерам, SEO, группировке карточек и поставкам. Это сильная сторона проекта: логика не только в голове агента, а частично вынесена в постоянные документы. ([GitHub][2])

Это особенно полезно для одноразовых агентов: будущий агент сможет получать не “сделай акцию”, а ссылку на конкретный runbook + task metadata + job context.

---

## 3. Разделение Ozon/WB-каталогов заложено правильно

В README описано, что до унификации артикулов Ozon-сценарии работают по `offer_id`, `product_id`, `sku`, WB-сценарии — по `vendorCode`, `nmID`, barcode, а mapping нужен только для объединенных отчетов и cross-marketplace операций. Это правильная модель: нельзя насильно склеивать Ozon и WB, пока нет подтвержденного mapping. ([GitHub][2])

В `telegram_bot_management_transition_plan.md` эта мысль развита: общий каталог продукции нужен, но apply должен продолжать использовать native marketplace IDs, а cross-marketplace write-операции нельзя делать без подтвержденного mapping. ([GitHub][3])

---

## 4. Есть реальные task-модули, а не только идеи

В `src/takterra_agent/tasks/` уже есть модули для:

```text
catalog_fetch
daily_morning_report
status_preflight
reviews_questions
ozon_elastic_plan/apply
ozon_cpc_optimization_plan/apply
wb_actions_discount_plan/apply
wb_card_create_plan/apply
wb_promotion_report
wb_promotion_bid_plan/apply
actions_apply
```

Это уже рабочий контур задач, который можно постепенно превратить в нормальный task-runner. ([GitHub][4])

---

## 5. Apply-команды не совсем “дикие”: есть подтверждение, preflight, drift-check, verify

Например, `wb_promotion_bids_apply.py` требует `confirmed_by_user`, делает preflight, строит свежий WB promotion report, строит fresh plan, проверяет drift между согласованным и свежим планом, формирует payload, применяет ставки, затем проверяет результат. Это правильная логика для опасной операции. ([GitHub][5])

Аналогично `wb_actions_discount_apply.py` требует `confirmed_by_user`, делает fresh preflight, строит свежий план, проверяет drift, отправляет payload в WB API, опрашивает статус загрузки и пишет артефакты. ([GitHub][6])

Это надо сохранить. Но сейчас эти проверки размазаны по apply-модулям, а должны быть вынесены в общий safety layer.

---

## 6. Есть тесты по ключевым сценариям

В `tests/` видны тесты для apply-операций, каталогов, daily report, Ozon CPC, Ozon Elastic, reviews/questions, sessions, status preflight, WB actions, WB cards, WB promotion. Это лучше, чем типичный “бот на коленке без тестов”. ([GitHub][7])

---

## 7. `.gitignore` в целом правильный

`.gitignore` исключает `.env`, `.env.*`, `.sessions/`, `tmp/`, `*.log`, `data/runs/`, `data/pending/`, `data/approved/`, `data/reports/`, raw/processed catalog snapshots и runtime-папки. Это правильная защита от случайного коммита токенов, сессий, отчетов и рабочих данных. ([GitHub][8])

---

# Что плохо или рискованно

## 1. Репозиторий публичный, а внутри есть бизнес-чувствительные документы

Сейчас GitHub показывает репозиторий как `Public`, хотя в README и `project_map.md` еще написано `visibility: PRIVATE`. Это уже несоответствие документации факту. ([GitHub][2])

Проблема не только в секретах. В репозитории есть операционные документы, бизнес-логика, стратегия развития, приоритеты автоматизации, сведения о каталогах, mapping, рекламных/ценовых процессах и даже коммерчески чувствительные параметры в planning-документах. Например, в плане Telegram-перехода описаны приоритетные бизнес-сценарии по pricing, search, parser positions, ads monitoring, discounts, reviews/questions. ([GitHub][3])

**Рекомендация:** если это реальный рабочий проект магазина, я бы снова сделал репозиторий приватным. Если он должен быть публичным временно для ревью, потом обязательно закрыть.

---

## 2. Старые названия TAKTERRA до сих пор в ядре проекта

`pyproject.toml` все еще называет проект `seller-takterra` и описывает его как `TAKTERRA marketplace agent and task runner`. ([GitHub][9])

Python-package тоже называется `takterra_agent`, а `project_map.md` прямо говорит, что имя оставлено временно и в будущем должно быть переименовано в универсальное `seller_agent`. ([GitHub][10])

Это не косметика. Для агента, разработчика и будущего бота это источник путаницы:

```text
репозиторий: seller_vital_shevron
путь из твоего проекта: /home/pavel/projects/Vital_Shevron_bot
путь в документах: /home/pavel/projects/seller_vital_shevron
package: takterra_agent
pyproject name: seller-takterra
описание: TAKTERRA
```

Нужно привести к единому стандарту. Не обязательно сразу делать большой rename, но хотя бы зафиксировать, что сейчас это transitional package.

---

## 3. Структура уже перегружена документацией раньше, чем появился runtime

Документов много: runbook-и, handoff-и, revision, рекомендации, карты ЛК, планы развития, reference TAKTERRA. Это полезно, но есть риск, что агент будет тонуть в контексте.

Сейчас проект уже содержит большой набор planning-файлов в `data/planning/`, а `AGENTS.md` требует агенту регулярно читать и обновлять множество документов. ([GitHub][11])

Для твоей исходной цели — кнопки в Telegram и одноразовые агенты — важнее сначала стабилизировать:

```text
TaskRegistry
RunManifest
Job lifecycle
SafetyGuard
Approval lifecycle
Telegram report format
```

А не расширять документы еще глубже.

---

## 4. `TaskRegistry` пока почти фикция

В `bot/dispatcher.py` сейчас создается registry и регистрируется только `catalog_fetch`, причем `handler=None`. ([GitHub][12])

Сам `TaskRegistry` тоже минимальный: `RegisteredTask` содержит только `name`, `is_read_only`, `handler`, а registry умеет только `register`, `get`, `names`. Нет metadata, нет риска, нет marketplace, нет режима, нет confirmation, нет runbook, нет telegram label, нет параметров. ([GitHub][13])

Это главный блокер для Telegram-бота. Пока нет нормального TaskRegistry, бот неизбежно начнет дублировать CLI-команды руками.

---

## 5. CLI слишком монолитный

`cli.py` импортирует много task-функций и вручную описывает subparser почти для каждой операции: catalog, status, sessions, Ozon Elastic, Ozon CPC, WB actions, WB promotion, WB cards, reviews/questions и apply-команды. ([GitHub][14])

Для текущего CLI это терпимо. Для будущего бота — плохо. Telegram-боту нельзя копировать эту логику. Нужно, чтобы и CLI, и бот брали список задач из одного реестра.

Иначе будет два источника истины:

```text
cli.py знает одно
bot/dispatcher.py знает другое
runbook-и описывают третье
```

---

## 6. Safety layer есть в идеологии, но слабый в реализации

`approvals.py` сейчас фактически только проверяет наличие файла `data/approved/<run_id>.approved.json`. Этого недостаточно для боевых операций. ([GitHub][15])

`dry_run.py` сейчас содержит только простую `DryRunDecision` и `read_only_decision()`. Это не полноценный guard. ([GitHub][16])

`locks.py` делает lock через создание файла `<name>.lock`, но без атомарного создания, TTL, owner PID, marketplace/resource metadata, stale-lock восстановления и audit trail. Для ЛК и write-операций этого мало. ([GitHub][17])

Сейчас безопасность держится на дисциплине отдельных task-модулей. Надо сделать так, чтобы новая write-команда физически не могла обойти общий guard.

---

## 7. Нет единого `RunManifest`

В `telegram_bot_management_transition_plan.md` правильно написано, что нужен `RunManifest` с полями `run_id`, `task`, `mode`, `risk`, `marketplaces`, `status`, `started_at`, `finished_at`, `inputs`, `artifacts`, `source_run_ids`, `pending_id`, `approved_id`, `applied_by_run_id`, `closed`. Там же указано, что нужно добавить `src/takterra_agent/core/run_manifest.py` и `data/runs/index.jsonl`. ([GitHub][3])

Но по видимой структуре `src/takterra_agent/core/` пока нет, а `TaskRegistry` и dispatcher еще старые/минимальные. Значит, проект сам уже понимает проблему, но реализация еще не сделана.

Для Telegram-бота это критично. Без manifest бот не сможет нормально отвечать на вопросы:

```text
что запущено
что завершилось
что упало
что согласовано
что применено
где отчет
можно ли повторить
была ли уже применена операция
```

---

## 8. Нет полноценного Telegram-бота

В `src/takterra_agent/bot/` сейчас виден только `dispatcher.py`, и он пока регистрирует одну заглушечную задачу. ([GitHub][18])

То есть фактически Telegram-слой еще не начат. Это нормально на текущей стадии, но важно не обманываться: текущий проект — это не “почти бот”, а “CLI/task-runner, к которому можно будет подключить бота после нормализации”.

---

## 9. `.env.example` надо привести в порядок

`.env.example` содержит правильную идею: реальные значения не коммитить, ключи хранить в `.env` и во внешних файлах. Но там все еще прописаны абсолютные пути вида `/home/pavel/projects/seller_vital_shevron/.sessions/...`, тогда как ты сейчас обозначал рабочую папку как `/home/pavel/projects/Vital_Shevron_bot`. ([GitHub][19])

Это надо унифицировать. Иначе один агент будет работать в одном пути, второй — в другом, а systemd/сессии/credentials начнут расходиться.

---

## 10. Формат некоторых raw-файлов выглядит странно

В raw-просмотре GitHub несколько файлов отображаются как одна длинная строка: `pyproject.toml`, `.gitignore`, `.env.example`, `config.py`, `http.py`, `registry.py`, некоторые task-файлы. ([GitHub][9])

Я не могу на 100% утверждать, что это не артефакт web-просмотра, но локально это надо проверить. Если файлы реально записаны в одну строку, это плохо:

```bash
python -m compileall src
python - <<'PY'
import tomllib
from pathlib import Path
tomllib.loads(Path("pyproject.toml").read_text())
print("pyproject ok")
PY
```

Особенно подозрительно выглядит `pyproject.toml`: raw показывает `[project] name = "seller-takterra" ...` в одной строке, а TOML обычно требует отдельные строки для секций и полей. ([GitHub][9])

---

# Оценка по направлениям

| Направление                | Оценка | Комментарий                                                       |
| -------------------------- | -----: | ----------------------------------------------------------------- |
| Бизнес-логика Ozon/WB      |   7/10 | Уже есть реальные задачи, API-адаптеры, apply/dry-run логика      |
| Документация процессов     |   8/10 | Много runbook-ов, но есть риск перегруза                          |
| Безопасность по правилам   |   8/10 | Правила сформулированы хорошо                                     |
| Безопасность в коде        |   4/10 | Guard/approval/locks пока слабые и размазанные                    |
| Готовность к Telegram-боту |   3/10 | Нет нормального registry, manifest, lifecycle, dispatcher         |
| Структура проекта          |   5/10 | Направление нормальное, но старые имена и runtime/data смешаны    |
| Репозиторная гигиена       |   5/10 | `.gitignore` хороший, но публичность и бизнес-доки рискованные    |
| Тесты                      |   6/10 | Тесты есть, но нужен coverage именно для registry/manifest/safety |

---

# Что я бы оставил

Оставить и развивать:

```text
src/takterra_agent/marketplaces/ozon/
src/takterra_agent/marketplaces/wb/
src/takterra_agent/tasks/*_plan.py
src/takterra_agent/tasks/*_apply.py
src/takterra_agent/tasks/reviews_questions.py
src/takterra_agent/tasks/status_preflight.py
src/takterra_agent/sessions/
data/planning/*_runbook.md
AGENTS.md
.agents/skills/marketplace-analytics/SKILL.md
tests/
```

Особенно ценно:

```text
1. API-first подход.
2. Разделение Ozon/WB native IDs.
3. Dry-run/apply структура.
4. Drift-check перед apply.
5. Verify после apply.
6. Runbook-и.
7. Тесты.
8. Сессии ЛК через отдельный контур.
```

---

# Что я бы переделал

## 1. Не строить Telegram-бот поверх текущего `cli.py` напрямую

Плохо:

```text
Telegram button → subprocess python -m takterra_agent.cli apply-wb-promotion-bids ...
```

Так можно сделать временно, но это быстро станет хаосом.

Правильно:

```text
Telegram button
  ↓
TaskRegistry
  ↓
JobRunner
  ↓
SafetyGuard
  ↓
WorkflowRunner
  ↓
Task handler
  ↓
RunManifest + artifacts
  ↓
Telegram summary
```

---

## 2. Сделать настоящий `TaskRegistry`

Сейчас registry слишком бедный. Нужна структура примерно такая:

```python
@dataclass(frozen=True)
class TaskDefinition:
    id: str
    title: str
    description: str
    marketplace: str | list[str]
    group: str
    mode: str
    risk: str
    executor: str
    handler: Callable
    requires_credentials: bool
    requires_lk: bool
    requires_mapping: bool
    requires_confirmation: bool
    supports_dry_run: bool
    default_params: dict
    runbook_path: str
    telegram_enabled: bool
    telegram_button_label: str
    locks: list[str]
```

Первый набор задач для registry:

```text
status_preflight
fetch_catalog
daily_morning_report
reviews_questions
plan_wb_actions_discounts
apply_wb_actions_discounts
wb_promotion_report
plan_wb_promotion_bids
apply_wb_promotion_bids
plan_ozon_elastic
apply_ozon_elastic
plan_ozon_cpc_optimization
apply_ozon_cpc_bids
sessions_status
sessions_restart
```

---

## 3. Ввести `RunManifest`

Каждый запуск должен писать:

```text
data/runs/YYYY-MM-DD/<run_id>/manifest.json
data/runs/index.jsonl
```

Минимально:

```json
{
  "run_id": "wb_promotion_report_20260613T120000",
  "task_id": "wb_promotion_report",
  "mode": "read_only",
  "risk": "low",
  "marketplaces": ["wb"],
  "status": "ok",
  "started_at": "2026-06-13T12:00:00+03:00",
  "finished_at": "2026-06-13T12:01:20+03:00",
  "inputs": {},
  "artifacts": {
    "summary": "summary.json",
    "report": "report.md"
  },
  "source_run_ids": [],
  "pending_id": null,
  "approved_id": null,
  "applied_by_run_id": null,
  "closed": true
}
```

Без этого бот будет слепой.

---

## 4. Сделать централизованный `SafetyGuard`

Сейчас apply-команды сами проверяют `confirmed_by_user`, fresh plan, drift-check и т.д. Это лучше, чем ничего, но не масштабируется.

Нужен общий guard:

```text
SafetyGuard.check_before_run(task, mode, params)
SafetyGuard.check_before_apply(task, approved_package, current_state)
SafetyGuard.check_after_apply(task, result)
```

Он должен проверять:

```text
task risk
mode
requires_confirmation
approved package
checksum
idempotency
fresh preflight
fresh data age
marketplace lock
LK lock
mapping requirement
allowed action types
expected store/seller
```

---

## 5. Переделать approvals

Текущий `approvals.py` проверяет только существование файла. ([GitHub][15])

Нужен approval package:

```json
{
  "approved_id": "approved_wb_promotion_bids_20260613T120000",
  "source_run_id": "wb_promotion_bid_plan_20260613T115000",
  "task_id": "apply_wb_promotion_bids",
  "approved_by": "pavel",
  "approved_at": "2026-06-13T12:00:00+03:00",
  "mode": "apply",
  "risk": "high",
  "marketplace": "wb",
  "rows_count": 12,
  "actions_checksum": "sha256:...",
  "status": "approved",
  "applied_by_run_id": null,
  "closed": false
}
```

После apply:

```json
{
  "status": "applied",
  "applied_by_run_id": "...",
  "verified": true,
  "closed": true
}
```

Главное правило: **один approved package нельзя применить два раза**.

---

## 6. Усилить locks

Текущий file lock слишком простой. ([GitHub][17])

Нужен lock-файл такого вида:

```json
{
  "lock_name": "wb_ads_write",
  "owner_run_id": "apply_wb_promotion_bids_20260613T120000",
  "pid": 12345,
  "created_at": "2026-06-13T12:00:00+03:00",
  "expires_at": "2026-06-13T12:30:00+03:00",
  "resource": "wb:promotion:bids"
}
```

И операции:

```text
acquire
release
is_stale
recover_stale
list_locks
```

Для ЛК:

```text
lock: ozon_lk
lock: wb_lk
```

Для write API:

```text
lock: ozon_prices_write
lock: ozon_cards_write
lock: wb_prices_write
lock: wb_ads_write
lock: wb_cards_write
lock: reviews_questions_apply
```

---

# Что делать с публичностью репозитория

Я бы сделал так:

1. Вернуть репозиторий в private.
2. Оставить публичным только отдельный sanitized-template, если нужно показывать архитектуру.
3. Прогнать локально проверку:

```bash
git status --short
git ls-files | grep -Ei 'env|session|cookie|token|secret|credential|storage|runs|approved|pending|report|xlsx|csv|json'
```

4. Проверить, нет ли бизнес-чувствительных документов, которые не стоит держать публично:

```bash
grep -RniE 'себестоимость|маржа|прибыль|токен|ключ|cookie|storage|client_id|api_key|пароль|логин|credential|secret' .
```

5. Если репозиторий был публичным хотя бы недолго, считать, что все случайно попавшие секреты скомпрометированы. В просмотренных файлах реальные токены я не увидел, но полный secret scan я не выполнял.

---

# План дальнейших шагов

## Этап 0. Санитарная фиксация проекта

Цель: убрать путаницу и риск.

Сделать:

```text
1. Решить финальный путь проекта:
   /home/pavel/projects/Vital_Shevron_bot
   или
   /home/pavel/projects/seller_vital_shevron

2. Обновить README, AGENTS.md, project_map.md, .env.example под один путь.

3. Обновить README: visibility сейчас public/private по факту.

4. Проверить pyproject.toml локально.

5. Проверить, что все Python-файлы компилируются.

6. Вернуть repo в private, если это рабочий коммерческий проект.
```

Команды:

```bash
cd /home/pavel/projects/Vital_Shevron_bot

PYTHONPATH=src /home/Codex/agent-tools/python/bin/python -m compileall src
PYTHONPATH=src /home/Codex/agent-tools/python/bin/pytest -q

/home/Codex/agent-tools/python/bin/python - <<'PY'
import tomllib
from pathlib import Path
tomllib.loads(Path("pyproject.toml").read_text())
print("pyproject ok")
PY
```

---

## Этап 1. `RunManifest` MVP

Добавить:

```text
src/takterra_agent/core/run_manifest.py
src/takterra_agent/core/run_index.py
```

Сделать команды:

```bash
python -m takterra_agent.cli runs list
python -m takterra_agent.cli runs latest --task status_preflight
python -m takterra_agent.cli runs show --run-id ...
```

Критерий готовности:

```text
каждый новый запуск пишет manifest.json
data/runs/index.jsonl пополняется
можно найти последний успешный запуск задачи
```

---

## Этап 2. Реальный `TaskRegistry`

Расширить:

```text
src/takterra_agent/tasks/registry.py
```

Зарегистрировать все текущие CLI-команды с metadata:

```text
id
title
group
marketplace
mode
risk
handler
requires_credentials
requires_lk
requires_mapping
requires_confirmation
telegram_enabled
runbook_path
locks
```

Критерий готовности:

```text
CLI берет список задач из registry
bot dispatcher берет список задач из registry
нет второго ручного списка команд
```

---

## Этап 3. `WorkflowRunner`

Добавить:

```text
src/takterra_agent/core/workflow_runner.py
```

Он должен:

```text
1. Получить task_id.
2. Получить task definition.
3. Создать run_id.
4. Создать run_dir.
5. Записать manifest started.
6. Вызвать SafetyGuard.
7. Запустить handler.
8. Проверить result.
9. Записать manifest finished.
10. Вернуть Telegram-friendly summary.
```

---

## Этап 4. Централизованный `SafetyGuard`

Добавить:

```text
src/takterra_agent/safety/guards.py
src/takterra_agent/safety/idempotency.py
src/takterra_agent/safety/approval_packages.py
```

Сначала подключить к одной опасной задаче:

```text
apply-reviews-questions
```

Почему с нее: отзывы/вопросы среднерисковые, но проще, чем цены и ставки.

---

## Этап 5. Approval lifecycle

Сделать команды:

```bash
python -m takterra_agent.cli approvals list
python -m takterra_agent.cli approvals show --approved-id ...
python -m takterra_agent.cli approvals create --source-run-id ...
python -m takterra_agent.cli approvals close --approved-id ...
```

И только потом подключать кнопки Telegram:

```text
Показать dry-run
Создать approval
Применить после подтверждения
Проверить результат
```

---

## Этап 6. Read-only Telegram MVP

Не начинать с write-кнопок.

Первый бот должен уметь:

```text
/status
/today
/catalog
/reviews
/ads
/prices
/positions
/approvals
/help
```

Но на первом этапе:

```text
/status → status_preflight
/today → daily_morning_report
/catalog → latest catalog summary
/reviews → reviews_questions prepare only
/ads → WB/Ozon read-only reports
/approvals → list only
```

Без:

```text
изменения ставок
изменения цен
публикации ответов
изменения карточек
создания поставок
```

---

## Этап 7. Approval bot

После read-only MVP:

```text
1. Бот показывает dry-run summary.
2. Пользователь нажимает “Показать строки”.
3. Пользователь нажимает “Согласовать”.
4. Создается approved package.
5. Бот показывает второе подтверждение apply.
6. WorkflowRunner запускает apply.
7. SafetyGuard проверяет approval, checksum, lock, idempotency.
8. Бот возвращает verify-result.
```

---

## Этап 8. Одноразовые агенты Codex

Это делать только после TaskRegistry/RunManifest/SafetyGuard.

Модель:

```text
Telegram button
  ↓
TaskRegistry task executor=agent/hybrid
  ↓
JobRunner создает run_dir
  ↓
AgentExecutor собирает prompt.final.md
  ↓
запускает одноразового Codex в ограниченном контексте
  ↓
агент пишет result.json/report.md
  ↓
validator проверяет результат
  ↓
агент завершается
```

Для agent-задач обязательно:

```text
prompt.final.md
allowed_actions
forbidden_actions
output_contract
timeout
screenshots/
downloads/
agent.log
result.json
report.md
```

---

# Приоритет на ближайший спринт

Я бы не начинал сейчас с UI кнопок. Ближайший спринт:

```text
1. Repo hygiene:
   - private repo
   - единый путь
   - убрать TAKTERRA из pyproject/README там, где не нужно
   - проверить compileall/pytest/pyproject

2. RunManifest MVP:
   - manifest.json
   - data/runs/index.jsonl
   - runs list/latest/show

3. TaskRegistry v1:
   - metadata для всех текущих CLI-команд
   - bot_enabled flag
   - risk/mode/confirmation/locks

4. WorkflowRunner:
   - запуск read-only задач через единый runner
   - status_preflight и reviews_questions через runner

5. SafetyGuard v1:
   - блокировка любых apply без approval/confirmation
   - единый reason в отчете

6. Read-only bot skeleton:
   - /status
   - /today
   - /reviews
   - /help
```

---

# Итог

**Что пойдет:** текущие API-адаптеры, task-модули, runbook-и, подход dry-run/apply/verify, tests, сессии ЛК, `.gitignore`, правила AGENTS.

**Что надо чинить:** публичность, старые TAKTERRA-имена, слабый TaskRegistry, отсутствие RunManifest, отсутствие WorkflowRunner, слабый централизованный safety layer, примитивные approvals/locks, отсутствие настоящего Telegram dispatcher.

**Главная рекомендация:** не писать кнопки прямо поверх `cli.py`. Сначала сделать `RunManifest + TaskRegistry + WorkflowRunner + SafetyGuard`. После этого Telegram-бот станет тонким интерфейсом, а не еще одним хаотичным слоем логики.

[1]: https://raw.githubusercontent.com/pavelvital2/seller_vital_shevron/main/AGENTS.md "raw.githubusercontent.com"
[2]: https://github.com/pavelvital2/seller_vital_shevron "GitHub - pavelvital2/seller_vital_shevron · GitHub"
[3]: https://raw.githubusercontent.com/pavelvital2/seller_vital_shevron/main/data/planning/telegram_bot_management_transition_plan.md "raw.githubusercontent.com"
[4]: https://github.com/pavelvital2/seller_vital_shevron/tree/main/src/takterra_agent/tasks "seller_vital_shevron/src/takterra_agent/tasks at main · pavelvital2/seller_vital_shevron · GitHub"
[5]: https://raw.githubusercontent.com/pavelvital2/seller_vital_shevron/main/src/takterra_agent/tasks/wb_promotion_bids_apply.py "raw.githubusercontent.com"
[6]: https://raw.githubusercontent.com/pavelvital2/seller_vital_shevron/main/src/takterra_agent/tasks/wb_actions_discount_apply.py "raw.githubusercontent.com"
[7]: https://github.com/pavelvital2/seller_vital_shevron/tree/main/tests "seller_vital_shevron/tests at main · pavelvital2/seller_vital_shevron · GitHub"
[8]: https://raw.githubusercontent.com/pavelvital2/seller_vital_shevron/main/.gitignore "raw.githubusercontent.com"
[9]: https://raw.githubusercontent.com/pavelvital2/seller_vital_shevron/main/pyproject.toml "raw.githubusercontent.com"
[10]: https://raw.githubusercontent.com/pavelvital2/seller_vital_shevron/main/data/planning/project_map.md "raw.githubusercontent.com"
[11]: https://github.com/pavelvital2/seller_vital_shevron/tree/main/data/planning "seller_vital_shevron/data/planning at main · pavelvital2/seller_vital_shevron · GitHub"
[12]: https://raw.githubusercontent.com/pavelvital2/seller_vital_shevron/main/src/takterra_agent/bot/dispatcher.py "raw.githubusercontent.com"
[13]: https://raw.githubusercontent.com/pavelvital2/seller_vital_shevron/main/src/takterra_agent/tasks/registry.py "raw.githubusercontent.com"
[14]: https://raw.githubusercontent.com/pavelvital2/seller_vital_shevron/main/src/takterra_agent/cli.py "raw.githubusercontent.com"
[15]: https://raw.githubusercontent.com/pavelvital2/seller_vital_shevron/main/src/takterra_agent/safety/approvals.py "raw.githubusercontent.com"
[16]: https://raw.githubusercontent.com/pavelvital2/seller_vital_shevron/main/src/takterra_agent/safety/dry_run.py "raw.githubusercontent.com"
[17]: https://raw.githubusercontent.com/pavelvital2/seller_vital_shevron/main/src/takterra_agent/safety/locks.py "raw.githubusercontent.com"
[18]: https://github.com/pavelvital2/seller_vital_shevron/tree/main/src/takterra_agent/bot "seller_vital_shevron/src/takterra_agent/bot at main · pavelvital2/seller_vital_shevron · GitHub"
[19]: https://raw.githubusercontent.com/pavelvital2/seller_vital_shevron/main/.env.example "raw.githubusercontent.com"
