# Аудит структуры и автоматизации проекта TAKTERRA

Дата: 2026-06-11.

Назначение: проверить структуру проекта после добавления каталогов, сессий,
акций, карточек, утреннего отчета, отзывов/вопросов и apply-сценариев; дать
предложения по оптимизации, структуризации и автоматизации.

Секреты, cookies, токены и storage state не просматривались и не записывались.

## Краткий вывод

Проект уже перешел из стадии разовых скриптов в рабочий task-runner. Базовая
структура правильная: есть `src/`, `scripts/`, `data/runs`, `data/pending`,
`data/approved`, runbook, карты и safety-правила.

Главный риск следующего этапа: новые функции начинают расти как крупные
монолитные task-файлы и ручные JSON-пакеты. Если сейчас не стандартизировать
workflow, run artifacts, approval lifecycle и task registry, подключение кнопок
бота будет сложным и рискованным.

Рекомендуемый следующий технический шаг: не новый бизнес-сценарий, а
структурный слой `workflow/task registry + run manifest + approved package
builder`. После него бизнес-сценарии можно безопаснее подключать к боту.

## Проверенные источники

Локальные документы:

```text
AGENTS.md
README.md
pyproject.toml
data/planning/project_map.md
data/planning/recommendations_index.md
```

Команды проверки:

```bash
find . -maxdepth 3 -type d
find src -maxdepth 4 -type f
find scripts deploy tests data/planning data/15_architecture_notes -maxdepth 3 -type f
find src/takterra_agent scripts tests -type f \( -name '*.py' -o -name '*.js' -o -name '*.sh' \) -print0 | xargs -0 wc -l
PYTHONPATH=src python3 -m takterra_agent.cli --help
python3 -m compileall -q src scripts
```

Результат технической проверки:

```text
compileall: ok
git repo: нет .git
CLI commands: 13
source/scripts/tests LOC: 10754
data/runs dirs total: 76
data/runs/2026-06-11 dirs: 35
data/runs/2026-06-11 summary.json: 32
data/runs/2026-06-11 reports/md: 16
data size: 32M
.sessions size: 547M
tmp size: 2.8M
```

## Что устроено правильно

1. Старые проекты отделены как read-only источники, рабочая разработка идет в
   `seller_takterra`.
2. Есть разделение на код, браузерные скрипты, данные запусков, pending,
   approved, planning/runbook.
3. Опасные операции уже идут через цепочку `dry-run -> pending/approved ->
   apply -> verify -> result`.
4. Ozon/WB session contour вынесен отдельно и частично переведен на
   `systemd --user`.
5. Для новых операций создаются runbook и артефакты.
6. По отзывам/вопросам появился reusable apply-контур, а не разовый ручной
   вход в ЛК.

## Узкие места

### 1. Task-модули быстро растут

Самые крупные файлы:

```text
src/takterra_agent/tasks/daily_morning_report.py  976 строк
src/takterra_agent/tasks/reviews_questions.py     969 строк
src/takterra_agent/tasks/wb_card_create_apply.py  524 строки
src/takterra_agent/tasks/wb_card_create_plan.py   519 строк
src/takterra_agent/tasks/ozon_elastic_plan.py     492 строки
src/takterra_agent/cli.py                         461 строка
```

Это пока работоспособно, но дальше будет мешать:

- тестировать отдельные части;
- переиспользовать workflow в боте;
- делать автоматические approvals;
- быстро понимать, где read-only, где write, где report.

### 2. `TaskRegistry` и `bot/dispatcher.py` почти не используются

Фактически CLI напрямую импортирует и вызывает функции задач, а registry
содержит только заглушку `catalog_fetch` с `handler=None`.

Это противоречит целевой архитектуре:

```text
bot -> dispatcher -> task registry -> safety guard -> task result
```

Если не исправить, Telegram-бот начнет дублировать CLI-логику.

### 3. Нет единого run manifest

Сейчас у разных сценариев есть `summary.json`, но формат не полностью общий.
Нет единого индекса, который отвечает на вопросы:

- какой run является dry-run;
- какой run стал approved;
- какой approved-пакет был применен;
- можно ли повторно запускать apply;
- какой run закрывает какой pending;
- какая операция write/read-only;
- какой риск и какой marketplace затронут.

### 4. Pending/approved lifecycle не закрыт полностью

Папки `data/pending/` и `data/approved/` есть, но после apply нет единого
автоматического статуса вида:

```text
pending -> approved -> applied -> verified -> closed
```

Это повышает риск повторно применить старый approved JSON или потерять связь
между pending и результатом.

### 5. Approved-пакеты частично собираются вручную

Последний пакет для отметки пустых Ozon-оценок был сформирован корректно, но
сам факт ручной сборки JSON показывает необходимость команды:

```text
prepare-reviews-questions-approved
```

Такая команда должна отбирать только разрешенные action types и сразу писать
читаемый `.md` для владельца.

### 6. Browser/CDP JS-скрипты лежат отдельно от Python workflow

`scripts/reviews/` и `scripts/sessions/` сейчас работают, но Python-задачи
вызывают их как subprocess. Это допустимо, но нужен единый wrapper-слой:

```text
src/takterra_agent/lk/ozon/
src/takterra_agent/lk/wb/
```

Там должны быть Python-функции с понятными контрактами, а JS остается
исполнительным bridge к браузеру.

### 7. Safety-пакет пока больше задел, чем рабочий слой

Есть:

```text
src/takterra_agent/safety/approvals.py
src/takterra_agent/safety/dry_run.py
src/takterra_agent/safety/locks.py
```

Но apply-команды в основном сами проверяют `--confirmed-by-user` и сами решают,
что разрешено. Нужно централизовать:

- risk category;
- required approval;
- lock name;
- idempotency guard;
- allowed marketplaces/action types.

### 8. `data/runs` быстро растет

За два дня уже `76` run-директорий, за 2026-06-11 - `35`. Это хорошо для
проверяемости, но без retention/index скоро станет трудно искать актуальный
результат.

Нужны:

- `data/runs/index.jsonl`;
- команда `runs list`;
- команда `runs latest --task reviews_questions --status ok`;
- политика хранения больших raw/browser artifacts.

### 9. README отстает от фактических CLI-команд

CLI показывает 13 команд, но README не покрывает все новые сценарии:

```text
reviews-questions
apply-reviews-questions
plan-wb-card-create
apply-wb-card-create
plan-ozon-elastic
plan-wb-actions-discounts
```

README лучше генерировать частично из task registry или хотя бы обновлять
командой проверки.

### 10. Проект не под git

Локальная проверка показала, что `.git` отсутствует. Для проекта с write-
операциями это риск: сложнее видеть изменения в коде, runbook и правилах.

`.gitignore` уже исключает секретные и тяжелые зоны:

```text
.env
data/runs/
data/pending/
data/approved/
.sessions/
tmp/
```

Перед инициализацией git нужно отдельно проверить, какие planning/docs должны
попадать под контроль версий, а какие операционные артефакты должны оставаться
локальными.

## Рекомендуемая целевая структура

Без немедленного массового переезда файлов:

```text
src/takterra_agent/
  cli.py
  config.py
  http.py

  core/
    run_manifest.py
    artifacts.py
    approvals.py
    locks.py
    task_registry.py

  workflows/
    catalog/
      fetch.py
      report.py
    actions/
      ozon_elastic_plan.py
      wb_discount_plan.py
      apply.py
      schemas.py
    reviews_questions/
      collect.py
      drafts.py
      approve.py
      apply.py
      report.py
      schemas.py
    daily_report/
      collect.py
      seller_v2.py
      report.py
    wb_cards/
      plan.py
      apply.py
      schemas.py

  marketplaces/
    ozon/
      seller_api.py
      performance_api.py
      lk_reviews.py
    wb/
      content_api.py
      communications_api.py
      statistics_api.py
      prices_api.py

  lk/
    ozon/
      cdp_bridge.py
    wb/
      browser_bridge.py

  bot/
    dispatcher.py
    commands.py
```

Данные:

```text
data/
  planning/
    runbooks/
    maps/
    audits/
  runs/
    index.jsonl
    YYYY-MM-DD/<run_id>/
  pending/
  approved/
  catalog/
  reports/
  locks/
```

Важное ограничение: текущие пути не надо резко ломать. Сначала добавить новые
слои и совместимые wrappers, потом постепенно переносить крупные task-файлы.

## Приоритетный план оптимизации

### Шаг 1. Единый `RunManifest`

Создать общий формат:

```json
{
  "run_id": "...",
  "task": "...",
  "mode": "read_only|dry_run|apply|verify",
  "risk": "none|low|normal|high",
  "marketplaces": ["ozon", "wb"],
  "status": "ok|warning|blocked|error",
  "started_at": "...",
  "inputs": {},
  "artifacts": {},
  "source_run_ids": [],
  "pending_id": "",
  "approved_id": "",
  "applied_by_run_id": "",
  "closed": false
}
```

Каждый сценарий должен писать такой manifest и добавлять строку в
`data/runs/index.jsonl`.

### Шаг 2. Реальный task registry

Registry должен хранить:

```text
task name
description
mode
risk
marketplaces
handler
requires_confirmation
default_button_label
runbook path
```

CLI и будущий бот должны строиться из одного registry.

### Шаг 3. Approved package builder

Сделать команды:

```bash
PYTHONPATH=src python3 -m takterra_agent.cli prepare-reviews-questions-approved \
  --source-pending reviews_questions_..._pending \
  --mode replies-only

PYTHONPATH=src python3 -m takterra_agent.cli prepare-reviews-questions-approved \
  --source-pending reviews_questions_..._pending \
  --mode mark-viewed-only
```

Позже такую же модель применить к акциям, карточкам, ценам.

### Шаг 4. Защита от повторного apply

Перед apply проверять:

- approved-пакет еще не применялся;
- source pending не закрыт;
- fresh preflight актуален для нужного marketplace;
- нет активного lock на тот же ресурс;
- action types входят в whitelist конкретного workflow.

После успешного apply писать:

```text
approved.status = applied
approved.applied_by_run_id = ...
pending.status = closed
```

### Шаг 5. Разделить два самых крупных task-модуля

Начать с `reviews_questions.py`, потому что он уже содержит read-only сбор,
drafts, approvals, WB API apply, Ozon CDP apply и report.

Минимальное разделение:

```text
workflows/reviews_questions/collect.py
workflows/reviews_questions/drafts.py
workflows/reviews_questions/approved.py
workflows/reviews_questions/apply.py
workflows/reviews_questions/report.py
```

Потом аналогично разделить `daily_morning_report.py`.

### Шаг 6. Run/artifact cleanup

Добавить команду:

```bash
PYTHONPATH=src python3 -m takterra_agent.cli cleanup-artifacts --dry-run
```

Правила:

- не удалять последний успешный run каждого task;
- не удалять applied/verified write-операции;
- raw browser/cdp artifacts старше согласованного срока переносить в archive
  или удалять;
- `.sessions` backups/logs чистить отдельно и не трогать текущий state.

### Шаг 7. Автоматизировать регулярные задачи

После стабилизации run manifest:

```text
daily-morning-report --seller-v2
status-preflight
reviews-questions --marketplace all --limit 100
```

можно запускать по `systemd --user timer` и отправлять краткий результат в чат.
Write-операции должны оставаться только через подтверждение владельца.

### Шаг 8. Обновить README и добавить entry point

В `pyproject.toml` добавить console script:

```toml
[project.scripts]
takterra-agent = "takterra_agent.cli:main"
```

README должен содержать актуальные команды или генерироваться из registry.

### Шаг 9. Ввести git после проверки секретов

Если владелец согласует, инициализировать git в `seller_takterra` и зафиксировать
код, runbook, карты, инструкции и аудит. Операционные данные и секреты оставить
в `.gitignore`.

## Предлагаемые новые рекомендации

Эти рекомендации внесены в `data/planning/recommendations_index.md`:

```text
REC-020 - Единый RunManifest и data/runs/index.jsonl.
REC-021 - Реальный task registry как общий источник CLI и будущего бота.
REC-022 - Закрытый lifecycle pending/approved/applied/verified/closed.
REC-023 - Разделить крупные task-модули на collect/plan/apply/report/schemas.
REC-024 - Перенести CDP/LK subprocess-вызовы за Python bridge/wrapper.
REC-025 - Общая artifact retention/cleanup для runs, pending, approved, caches.
REC-026 - README/CLI docs из task registry и console entry point.
REC-027 - Git-контроль кода и документов после проверки секретов.
REC-028 - Регулярные read-only задачи через timers с отправкой краткого итога в чат.
REC-029 - Централизованный safety guard для write-операций.
```

## Практический следующий шаг

Моя рекомендация: начать с `REC-020`, `REC-021`, `REC-022` и `REC-019`.

Причина: эти четыре пункта создают основу, без которой любая новая кнопка бота
будет привязываться к частным деталям конкретного сценария.

Минимальный рабочий результат следующего этапа:

```text
1. data/runs/index.jsonl появляется и пополняется каждым новым run.
2. task registry содержит все текущие CLI-команды.
3. apply-reviews-questions проверяет статус approved-пакета и закрывает его.
4. prepare-reviews-questions-approved собирает approved JSON без ручной правки.
5. README показывает актуальный список команд.
```
