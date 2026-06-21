# План перехода Vital Shevron к управлению Ozon/WB через Telegram-бота

Дата: 2026-06-13.

## Краткий вывод

Telegram-бот для Vital Shevron нужно строить не как набор быстрых кнопок, а как
тонкий интерфейс поверх устойчивого task-runner:

```text
Telegram chat
  -> bot dispatcher
  -> task registry
  -> safety guard
  -> workflow runner
  -> marketplace adapters / LK bridges
  -> run manifest / reports
  -> Telegram result
```

Сначала нужно стандартизировать запуски, approvals и lifecycle операций. Только
после этого подключать кнопки бота. Иначе бот начнет дублировать CLI-логику и
повысит риск write-операций в Ozon/WB.

## Источники

Актуальные документы Vital Shevron:

- `AGENTS.md`;
- `data/planning/project_map.md`;
- `data/planning/recommendations_index.md`;
- `data/planning/revision_2026-06-13.md`;
- `data/planning/pricing_runbook.md`;
- `data/planning/search_queries_runbook.md`;
- `data/planning/wb_parser_positions_runbook.md`;
- `data/planning/ozon_cabinet_map.md`;
- `data/planning/wb_cabinet_map.md`.

Перенесенные документы TAKTERRA:

- `data/reference/takterra_development_docs/data/15_architecture_notes/questions_and_recommendations.md`;
- `data/reference/takterra_development_docs/data/planning/recommendations_index.md`;
- `data/reference/takterra_development_docs/data/planning/project_structure_optimization_review_2026-06-11.md`;
- `data/reference/takterra_development_docs/data/planning/marketplace_control_bot_discussion.md`;
- `data/reference/takterra_development_docs/data/planning/vital_shevron_migration_plan.md`;
- `data/reference/takterra_development_docs/data/planning/vital_shevron_transfer_manifest.md`.

## Базовые принципы

1. Бот не содержит бизнес-логику. Бизнес-логика остается в task/workflow
   модулях и marketplace adapters.
2. Бот подключает только стабилизированные CLI/workflow-сценарии.
3. Любая write-операция остается в цепочке:

```text
read-only -> dry-run -> review -> approved -> apply -> verify -> result
```

4. Для операций, которые можно выполнить через официальный API, приоритет у API.
   ЛК используется как fallback или для задач, где API недостаточно.
5. В Telegram нельзя выводить секреты, cookies, storage state, auth headers,
   API-ключи, коды входа и закрытые attachment contents.
6. До унификации артикулов Vital Shevron бот должен работать с Ozon/WB через
   native marketplace IDs и учитывать mapping только там, где он действительно
   нужен.
7. Любая новая кнопка бота должна иметь runbook, task registry entry, тесты,
   safety metadata и понятный отчет.
8. Telegram-ответ по отчетным задачам должен включать краткий chat-summary и,
   если команда вернула безопасный `artifacts.report`, прикрепленный файл
   полного отчета через `sendDocument`.
9. Целевой проект должен быть store-agnostic: core-код, package name,
   task-runner, bot dispatcher, общие runbook-и и архитектурные документы не
   должны зависеть от названия конкретного магазина или старого проекта.
   Store-specific значения должны жить в отдельном `StoreProfile`/конфиге,
   `.env`, runtime state и профильных данных магазина.

## Целевая архитектура

```text
src/seller_agent/
  core/
    run_manifest.py
    task_registry.py
    workflow_runner.py
    artifacts.py

  safety/
    approvals.py
    guards.py
    lifecycle.py
    locks.py
    idempotency.py

  workflows/
    status/
    daily_report/
    reviews_questions/
    pricing/
    search_queries/
    wb_parser_positions/
    ozon_cpc/
    wb_promotion/
    wb_actions/
    catalog_mapping/

  marketplaces/
    ozon/
    wb/

  lk/
    ozon/
    wb/

  bot/
    dispatcher.py
    commands.py
    formatters.py
    approvals.py
```

Данные:

```text
data/
  runs/
    index.jsonl
    YYYY-MM-DD/<run_id>/
  pending/
  approved/
  locks/
  reports/
  planning/
  reference/
```

## Этап 0. Зафиксировать источники опыта

Статус: выполнено частично этим документом.

Что сделать:

1. Хранить TAKTERRA-документы в
   `data/reference/takterra_development_docs/`.
2. Не считать их действующими правилами Vital Shevron без адаптации.
3. В каждом новом архитектурном решении явно указывать, переносится ли оно из
   TAKTERRA без изменений или адаптируется.

Критерий готовности:

- TAKTERRA-документы скопированы в reference-зону;
- `project_map.md` ссылается на reference-зону;
- этот план добавлен в постоянные документы Vital Shevron.

## Этап 1. Единый `RunManifest`

Статус: `in_progress`.

MVP начат 2026-06-18 в ветке `feature/run-manifest-stage-1`: добавлен
`src/seller_agent/core/run_manifest.py`, runtime-индекс
`data/runs/index.jsonl`, CLI `runs list/latest/show` и подключение к
`status-preflight`, `daily-morning-report`, `reviews-questions`.

Ветка `feature/run-manifest-coverage` расширяет Этап 1: добавляет
`lifecycle_status`, автоматическое извлечение `source_run_ids`, связи
`pending_id/approved_id/applied_by_run_id` и подключает manifest к основным
read-only/dry-run/apply задачам Ozon/WB.

Цель: любой запуск task-runner должен иметь общий машинно-читаемый паспорт.

Минимальная схема:

```json
{
  "run_id": "",
  "task": "",
  "mode": "read_only|dry_run|apply|verify|maintenance",
  "risk": "none|low|normal|high",
  "marketplaces": ["ozon", "wb"],
  "status": "ok|warning|blocked|error",
  "lifecycle_status": "created|pending_review|approved|applied|verified|failed|closed",
  "started_at": "",
  "finished_at": "",
  "inputs": {},
  "artifacts": {},
  "source_run_ids": [],
  "pending_id": "",
  "approved_id": "",
  "applied_by_run_id": "",
  "closed": false
}
```

Что сделать:

1. Добавить `src/seller_agent/core/run_manifest.py`.
2. Добавить запись строк в `data/runs/index.jsonl`.
3. Подключить manifest к новым запускам, затем постепенно к существующим.
4. Добавить lifecycle-связи `pending_id -> approved_id -> applied_by_run_id`.
5. Добавить команды:

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python -m seller_agent.cli runs list
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python -m seller_agent.cli runs latest --task status-preflight
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python -m seller_agent.cli runs show --run-id <run_id>
```

Критерий готовности:

- новые runs пишут `manifest.json`;
- `data/runs/index.jsonl` пополняется;
- можно найти последний успешный run нужной задачи без ручного поиска по
  папкам;
- dry-run запускам назначается `pending_review`;
- apply-запуски связываются с approved/fresh/preflight run и получают
  `applied` или `verified`.

## Этап 2. Реальный `TaskRegistry`

Статус: `in_progress`.

Ветка `feature/task-registry` добавляет первый рабочий слой:
`src/seller_agent/tasks/registry.py`, CLI `tasks list/show` и
`bot/dispatcher.py` как thin layer поверх registry.

Цель: CLI, fresh-агенты и будущий бот должны брать список задач из одного
источника.

Task metadata:

```text
name
title
description
mode
risk
marketplaces
handler
requires_credentials
requires_lk
requires_mapping
requires_confirmation
runbook_path
telegram_enabled
telegram_button_label
```

Что сделать:

1. Расширить `src/seller_agent/tasks/registry.py` - выполнено первым
   проходом.
2. Зарегистрировать все текущие CLI-команды - выполнено первым проходом.
3. Добавить CLI `tasks list/show` - выполнено первым проходом.
4. Подключить CLI help/docs к registry без изменения внешнего поведения.
5. Сделать `bot/dispatcher.py` thin layer поверх registry - выполнено первым
   проходом.

Критерий готовности:

- все текущие команды видны в registry;
- для каждой команды известен риск и режим;
- будущий бот не дублирует список команд руками.
- `tasks list --telegram-only` показывает команды, которые можно подключать к
  read-only Telegram MVP.

## Этап 2A. Сопоставление Ozon/WB и общий каталог продукции

Цель: создать подтвержденный внутренний слой продукции Vital Shevron, который
связывает разные Ozon/WB артикулы без изменения артикулов продавца на
маркетплейсах.

Этот этап должен идти сразу после базовых `RunManifest` и `TaskRegistry`, но до
массовой бизнес-автоматизации, `pricing-status`, рекламных циклов и write-
кнопок Telegram-бота.

Почему так:

- для цен и маржинальности нужно понимать, где один и тот же шеврон или
  комплект на Ozon и WB;
- для рекламы и поисковых запросов нужен единый product-level взгляд;
- cross-marketplace write-операции нельзя делать без подтвержденного mapping;
- бот должен показывать владельцу понятный общий товар, но apply должен
  продолжать использовать native marketplace IDs.

Что сделать:

1. Обновить раздельные каталоги:

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python -m seller_agent.cli fetch-catalog
```

2. Сохранить/обновить marketplace-local каталоги:

```text
data/catalog/ozon/processed/ozon_catalog.csv
data/catalog/wb/processed/wb_catalog.csv
```

3. Построить draft mapping:

```text
data/catalog/mapping/ozon_wb_product_mapping.csv
data/catalog/mapping/ozon_wb_product_mapping_review.md
```

4. Для каждой строки mapping фиксировать:

```text
internal_product_id
product_name
pack_qty
cost_per_unit
ozon_offer_id
ozon_product_id
ozon_sku
wb_vendor_code
wb_nm_id
barcode
match_confidence
match_basis
needs_owner_review
owner_decision
```

5. Провести owner review:

```text
confirmed
rejected
needs_more_data
ozon_only
wb_only
duplicate_or_variant
```

6. После review сформировать общий каталог продукции:

```text
data/catalog/unified/products.csv
data/catalog/unified/products.json
```

Штатная read-only команда:

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m seller_agent.cli build-unified-catalog
```

Команда добавлена 2026-06-21 как отдельный слой `catalog-build-unified` в
`TaskRegistry`. Она берет confirmed mapping и обработанные Ozon/WB каталоги,
строит внутренний общий каталог, пишет `RunManifest` и issues-report, но не
переименовывает seller SKU на площадках.

Минимальные поля общего каталога:

```text
internal_product_id
product_name
product_group
pack_qty
cost_total
cost_per_unit
ozon_offer_id
ozon_product_id
ozon_sku
wb_vendor_code
wb_nm_id
mapping_status
active_ozon
active_wb
notes
```

7. Подключить общий каталог как read-only слой для:

- `pricing-status`;
- `daily-morning-report --seller-v2`;
- `search-queries`;
- `wb-parser-positions`;
- Ozon CPC и WB promotion отчетов;
- будущей Telegram-команды `/catalog`.

Важное ограничение:

`products.csv/json` не означает унификацию артикулов продавца. Это внутренний
общий каталог проекта. Изменение `offer_id` на Ozon или `vendorCode` на WB -
отдельная опасная операция:

```text
approved mapping -> dry-run rename plan -> owner approval -> apply -> verify
```

Критерий готовности:

- для каждого товара известно, есть ли связь Ozon/WB;
- спорные строки явно помечены и не используются для cross-marketplace write;
- `pricing-status` может считать маржу по общему товару и комплектности;
- бот может показывать `/catalog` как общий product-level отчет без write-
  операций.

## Этап 3. Approval package и lifecycle

Статус: `in_progress`.

Цель: закрыть безопасный цикл dangerous operations.

Ветка `feature/run-manifest-coverage` добавляет первый технический слой:
stable checksum helpers, runtime marker
`data/approved/applied/<sha256-approved-id>.applied.json` и idempotency guard
для основных apply-команд. Guard блокирует повторный apply до внешних
write-запросов, если approved/pending пакет уже отмечен marker или уже есть в
`data/runs/index.jsonl` как примененный.

Ветка `feature/approval-package-builder` начинает следующий слой: добавляет
builder approved package для `reviews-questions`, checksum action rows и
проверку checksum в `apply-reviews-questions` перед write-операциями.

Ветка `feature/approval-status-close` добавляет CLI-основу для будущего
Telegram `/approvals`: read-only `approvals status`, maintenance
`approvals close`, close-marker `data/approved/closed/*.closed.json` и
RunManifest task `approvals-close`.

Lifecycle:

```text
pending -> approved -> applied -> verified -> closed
```

Что сделать:

1. Описать единый JSON-формат pending/approved package.
   Первый формат `approval-package/v1` добавлен для `reviews-questions`.
2. Добавить checksum action rows.
   Для `reviews-questions` checksum считается и проверяется перед apply.
3. Добавить idempotency guard: старый approved нельзя применить повторно.
   Первый общий guard уже добавлен в `src/seller_agent/safety/approvals.py`.
4. После успешного apply обновлять status package.
   Первый runtime marker уже пишется в `data/approved/applied/`.
5. Добавить обзор и закрытие lifecycle.
   Первый CLI-слой `approvals status/close` добавлен.
6. Начать с отзывов/вопросов, потому что там уже есть pending/approved
   практика.

Минимальные команды:

```bash
prepare-reviews-questions-approved --source-pending <id> --mode replies-only
prepare-reviews-questions-approved --source-pending <id> --mode mark-viewed-only
approvals status
approvals close --id <id> --kind pending|approved
```

Критерий готовности:

- apply-команда проверяет, что approved-пакет еще не применялся;
- после apply visible связь: source pending, approved package, apply run,
  verify status;
- ручная сборка approved JSON больше не нужна для типовых сценариев.

Оставшийся gap этапа: расширить единый approved package builder на остальные
write-контуры и подключить `approvals status/close` к Telegram. Сейчас guard
уже защищает apply-команды, создание approved-пакетов реализовано только для
`reviews-questions`, CLI-обзор lifecycle уже добавлен, а TaskRegistry содержит
метаданные `approvals`.

## Этап 4. Централизованный safety guard

Цель: убрать размазанную по apply-командам проверку риска.

Safety guard должен проверять:

- task risk;
- marketplace;
- mode;
- наличие fresh preflight;
- наличие approved package;
- lock на ресурс;
- whitelist action types;
- mapping requirement;
- expected store/seller marker;
- idempotency.

Что сделать:

1. Добавить `src/seller_agent/safety/guards.py`.
2. Перенести общие проверки из apply-команд в safety layer.
3. Оставить task-specific проверки внутри workflow.

Критерий готовности:

- новая write-команда не может обойти общий guard;
- без approval write-команды завершаются ошибкой до внешнего API/LK вызова;
- в отчете видна причина блокировки.

## Этап 5. Нормализовать workflow-структуру

Цель: крупные task-файлы постепенно разделить на слои.

Приоритет:

1. `reviews_questions`;
2. `daily_morning_report`;
3. `pricing`;
4. `search_queries`;
5. `promotion/ads`.

Целевая структура workflow:

```text
collect.py
plan.py
approve.py
apply.py
verify.py
report.py
schemas.py
```

Критерий готовности:

- CLI-поведение не ломается;
- тесты продолжают проходить;
- bot dispatcher может вызвать workflow без знания внутренних деталей.

## Этап 6. Read-only bot MVP

Статус: `in_progress`.

Ветка `feature/read-only-telegram-mvp` добавила первый command layer:
`src/seller_agent/bot/commands.py`, dispatcher `dispatch_message()` и CLI
preview `bot preview --message /status`.

Ветка `feature/telegram-runner-adapter` добавляет real Telegram adapter
`src/seller_agent/bot/telegram_runner.py`: отправка read-only preview в
Telegram, одноразовый `getUpdates` polling и controlled `poll-loop` с
allowlist и lock. Production service включается только после подтверждения
token-file, личного chat id и runtime allowlist.

В следующем слое `/today` и `/status` получают live read-only режим через
`WorkflowRunner`: бот строит свежий `daily-morning-report --seller-v3` или
`status-preflight`, отправляет краткий Telegram-summary и сохраняет полный
runtime-отчет. Write-операций нет.

Цель: первый бот должен только показывать состояние и отчеты, без write.

Команды MVP:

```text
/status
/today
/catalog
/reviews
/approvals
/runs
/help
```

Соответствие task-runner:

| Команда бота | Task |
| --- | --- |
| `/status` | `status-preflight` |
| `/today` | `daily-morning-report --seller-v2` |
| `/catalog` | `fetch-catalog` summary/latest |
| `/reviews` | `reviews-questions --marketplace all` |
| `/approvals` | `approvals status` |
| `/runs` | latest RunManifest по Telegram-задачам |
| `/help` | `TaskRegistry` Telegram tasks |

Критерий готовности:

- бот не делает write-операции;
- Telegram bot token не хранится в проекте, документах, memory или git;
- постоянный polling работает только с allowlist chat_id и lock-file;
- production runner пишет `RunManifest` при live read-only `/today` и
  `/status`; остальные MVP-команды пока читают текущий runtime;
- бот отправляет краткий отчет, безопасно прикрепляет файл `artifacts.report`
  и оставляет ссылки на артефакты;
- ошибки показываются безопасно, без секретов.

Ограничение текущего прохода: live API-задачи разрешены только для read-only
`/today` и `/status`; остальные команды показывают последние runtime-данные и
статусы. Adapter может отправить готовый read-only ответ в Telegram. После
attachment policy, `WorkflowRunner` MVP, live `/status` и rename-only
`seller_agent` следующий отдельный слой - каталог/mapping и аккуратное
расширение на следующие read-only задачи.
Команды `/prices`, `/ads`, `/search`, `/positions` подключать после появления
соответствующих read-only task-runner команд и стандартных отчетов.

## Этап 7. Approval bot

Цель: бот становится интерфейсом review/approval, но не обходит safety.

Workflow:

```text
bot показывает dry-run summary
  -> владелец нажимает approve/reject
  -> создается approved package
  -> apply остается отдельным подтверждаемым действием
  -> verify result отправляется в чат
```

Кнопки:

```text
Approve
Reject
Show rows
Show risks
Create approved package
Run apply after confirmation
```

Критерий готовности:

- кнопка approve создает approved package с checksum;
- apply требует явного подтверждения;
- повторный apply заблокирован;
- verify-результат отправляется в чат.

## Этап 8. Бизнес-автоматизация поверх bot/task-runner

Приоритетные сценарии Vital Shevron:

1. `pricing-status`:
   цены Ozon/WB, минимальные цены, себестоимость 85 ₽ за шеврон,
   маржинальность, FBO/FBW.
2. `search-queries`:
   топ запросов Ozon/WB, сравнение площадок, SEO-рекомендации.
3. `wb-parser-positions`:
   позиции наших `nmID`, конкурентный срез, не использовать `VitalEmb` как
   признак владения.
4. `ads-monitoring`:
   Ozon CPC + WB promotion, delayed monitoring 24/48 часов после apply.
5. `stock/preflight filter`:
   перед повышением рекламных ставок проверять наличие и доступность товара.
6. `actions/discounts`:
   WB акции по схеме Vital Shevron `70-55-55`, Ozon Elastic и будущие акции.
7. `reviews/questions`:
   сбор, draft answers, approved apply, verify.

Критерий готовности:

- каждый сценарий имеет read-only или dry-run режим;
- write-сценарии подключены к approval lifecycle;
- бот показывает не только результат, но и следующий практический шаг.

## Этап 9. Регулярная автоматизация

Цель: бот получает не только ручные команды, но и регулярные отчеты.

Read-only timers:

```text
status-preflight
daily-morning-report --seller-v2
reviews-questions
pricing-status
ads-monitoring
search-queries weekly
wb-parser-positions weekly
```

Правила:

- timers не выполняют write-операции;
- write только по owner approval;
- ошибки timers отправляются в чат кратко и безопасно;
- каждый timer пишет `RunManifest`.

Критерий готовности:

- ежедневный отчет приходит в Telegram;
- критические риски подсвечиваются;
- weekly SEO/positions отчеты доступны по команде и по расписанию.

## Этап 10. Расширение storage и памяти

Стартовый слой:

```text
Git docs -> source of truth
data/runs/index.jsonl -> operational index MVP
Hermes memory -> auxiliary agent context
```

После стабилизации можно рассмотреть PostgreSQL:

```text
runs
run_artifacts
approvals
workflow_events
marketplace_snapshots_index
```

Zep/Graphiti или другой graph-memory слой рассматривать только после появления
устойчивой операционной истории. Он не должен заменять Git-документы и
approval records.

Критерий готовности:

- на вопрос "что запускали, что согласовано, что применено и где отчет" можно
  ответить из `data/runs/index.jsonl` или будущей БД;
- память агента не является единственным источником операционного факта.

## Что не делать

- Не строить Telegram-бота раньше `TaskRegistry` и `RunManifest`.
- Не давать боту прямой доступ к write API без safety guard.
- Не переносить TAKTERRA product-first модель без учета разных Ozon/WB
  артикулов Vital Shevron.
- Не смешивать package rename с реализацией новой логики. Rename-only этап
  выполнен 2026-06-18: рабочий package `src/seller_agent/`, CLI
  `seller-agent`, systemd entrypoint `python -m seller_agent.cli`.
- Не оставлять названия конкретных магазинов и старых проектов в целевом
  generic core. Все такие значения должны быть вынесены в store profile,
  миграционные/reference-документы или удалены после переноса смысла в
  нейтральные формулировки.
- Не хранить secrets, cookies, storage state, auth headers и коды входа в
  Telegram-сообщениях, документах или `data/runs`.
- Не смешивать сессии Vital Shevron и TAKTERRA.

## Этап 11. Store-agnostic sanitize

Цель: привести проект к универсальной системе управления любым магазином на
Ozon/WB.

Что сделать:

1. Переименовать package в универсальное имя - выполнено 2026-06-18:

```text
src/seller_agent/
```

2. Ввести `StoreProfile`:

```text
store_id
store_display_name
owner
marketplaces
expected_ozon_store
expected_wb_seller
seller_sku_rules_profile
pricing_profile
session_profile
data_root
```

3. Перенести store-specific значения из core-документов и кода в профиль:

```text
data/stores/<store_id>/profile.json
data/stores/<store_id>/rules/
data/stores/<store_id>/planning/
```

4. Убрать из generic core:

- названия конкретных магазинов;
- названия старых проектов;
- store-specific схемы скидок;
- store-specific правила seller SKU;
- store-specific себестоимость и маржинальные настройки.

5. Оставить в generic core только шаблоны и contracts:

```text
seller_sku_rules template
pricing_profile schema
catalog_mapping schema
approval package schema
task registry schema
bot command schema
```

6. Store-specific документы оставить только в профиле магазина или в
исторической reference-зоне до завершения sanitize.

Критерий готовности:

- `rg` по generic core не находит названия конкретных магазинов и старых
  проектов;
- все store-specific значения доступны через `StoreProfile`;
- один и тот же `seller_agent` можно подключить к другому магазину без
  переименования package и переписывания core-документов;
- текущий рабочий магазин продолжает работать через свой профиль.

## Первый реализационный спринт

Рекомендуемый состав:

1. `RunManifest` MVP.
2. `data/runs/index.jsonl`.
3. `prepare-reviews-questions-approved` как первый approved package builder.
4. `approvals status/close` и lifecycle schema.
5. Расширенный `TaskRegistry` для всех текущих CLI-команд.
6. Обновление README/CLI docs из registry или по registry.
7. Read-only Telegram MVP на registry без write-кнопок.
8. Реальный Telegram runner и attachment policy.

Критерий завершения спринта:

```text
pytest проходит
CLI help проходит
status-preflight пишет manifest
reviews/questions pending -> approved -> apply -> verify видны как связанная цепочка
fresh-агент понимает список задач из TaskRegistry
бот можно подключать к read-only командам без дублирования CLI
```
