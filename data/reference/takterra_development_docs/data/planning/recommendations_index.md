# Реестр рекомендаций TAKTERRA

Дата создания: 2026-06-10.

Назначение: единая точка входа для поиска рекомендаций агента. Новый агент
должен читать этот файл после `AGENTS.md`, если владелец просит вернуться к
рекомендациям, улучшениям, оптимизации или автоматизации.

Этот файл не заменяет подробные runbook и архитектурные заметки. Он хранит
короткий индекс: что рекомендовано, статус, где искать детали и какой следующий
шаг.

## Статусы

```text
proposed     - предложено агентом, требует решения владельца;
accepted     - принято как направление, но еще не реализовано полностью;
in_progress  - начато;
implemented  - реализовано;
deferred     - отложено;
rejected     - отклонено владельцем.
```

## Как обновлять

При появлении новой рекомендации агент обязан:

1. Добавить короткую запись в этот файл.
2. Указать ссылку на подробный документ: runbook, архитектурную заметку,
   discussion или карту проекта.
3. Указать следующий практический шаг.
4. Обновить статус после решения владельца или реализации.

Секреты, токены, cookies, коды входа и storage state сюда не записывать.

## Активные рекомендации

| ID | Тема | Статус | Где детали | Следующий шаг |
| --- | --- | --- | --- | --- |
| REC-001 | Единый session manager для Ozon/WB `status/start/stop/restart` вместо набора отдельных shell-скриптов | implemented | `data/planning/session_manager_runbook.md`, `src/takterra_agent/sessions/manager.py` | Использовать `PYTHONPATH=src python3 -m takterra_agent.cli sessions status` как основной вход проверки сессий |
| REC-002 | Перевести keeper/watchdog из ручных `nohup` loop в user-level `systemd` services/timers | implemented | `data/planning/session_manager_runbook.md`, `deploy/systemd/user/` | Контролировать timers через `systemctl --user list-timers 'takterra-*' --no-pager` |
| REC-003 | Добавить в `status-preflight` возраст последнего успешного keepalive и текущий интервал watchdog | implemented | `data/planning/status_preflight_runbook.md`, `src/takterra_agent/tasks/status_preflight.py` | Смотреть `ozon_refresh_state` и `wb_refresh_state` в summary/report перед опасными операциями |
| REC-004 | Добавить lock/режим восстановления входа: останавливать keeper/watchdog перед интерактивным логином и запускать обратно после успеха | implemented | `data/planning/session_manager_runbook.md`, `src/takterra_agent/sessions/manager.py` | Для восстановления Ozon использовать `restore-ozon-session --dry-run`, затем запуск без dry-run при необходимости |
| REC-005 | Добавить retention-очистку старых `storage_state` backups и логов без удаления последнего рабочего состояния | proposed | `data/planning/marketplace_control_bot_discussion.md`, `data/planning/lk_connection_runbook.md` | Согласовать срок хранения логов и backups |
| REC-006 | Для WB promo Excel сделать гибридный парсер `read_only -> normal mode -> diagnostic report` | proposed | `data/planning/wb_actions_discounts_runbook.md` | Заменить постоянное normal mode чтение на fallback-логику и добавить тест |
| REC-007 | Исследовать JSON-эндпоинт WB ЛК для товаров акции и сделать его основным источником вместо Excel, если он стабилен | proposed | `data/planning/wb_actions_discounts_runbook.md` | Снять read-only network/API snapshot и сравнить с Excel-выгрузкой |
| REC-008 | Для Ozon Elastic и WB скидок добавить reviewed payload + drift-check перед apply | implemented | `data/planning/actions_apply_runbook.md`, `data/pending/actions_apply_pending_20260610T232837/`, `data/runs/2026-06-10/actions_apply_20260610T234323/` | Следующий apply выполнять только через pending package + fresh preflight/dry-run/drift-check |
| REC-009 | Подключать к Telegram-боту только стабилизированные task-runner сценарии, а не непроверенную бизнес-логику | accepted | `data/15_architecture_notes/questions_and_recommendations.md`, `data/planning/marketplace_control_bot_discussion.md` | После apply/verify сценария добавить его в будущий task registry |
| REC-010 | Добавить WB apply guard: блокировать строки price/payload, которых нет в master catalog, даже если WB prices их показывает | implemented | `data/planning/actions_apply_runbook.md`, `data/planning/wb_actions_discounts_runbook.md`, `data/runs/2026-06-10/actions_apply_20260610T234323/processed/wb_excluded_rows.csv` | Вынести аналогичный guard в будущие WB price/promo сценарии до upload |
| REC-011 | Сделать ежедневный утренний отчет как read-only task-runner сценарий и будущую первую регулярную команду бота | implemented | `data/planning/daily_morning_report_runbook.md`, `src/takterra_agent/tasks/daily_morning_report.py` | Расширять v2 отдельными read-only задачами: продажи, заказы, остатки, отзывы, реклама |
| REC-012 | Заменить WB legacy stocks `/api/v1/supplier/stocks` в ежедневном отчете v2 на warehouse remains report до отключения 2026-06-23 | accepted | `data/planning/daily_morning_report_runbook.md`, `src/takterra_agent/marketplaces/wb/statistics_adapter.py` | Реализовать async-задачу create/status/download для WB warehouse remains и заменить legacy-блок в v2 |
| REC-013 | Добавить retry и безопасную диагностику для WB keepalive seller-marker перед финальным `Expected seller marker not found` | proposed | `data/planning/wb_actions_discounts_runbook.md`, `scripts/sessions/wb_session_keepalive.js` | Согласовать и реализовать 2-3 повторные проверки `seller/cmp` перед статусом `error` |
| REC-014 | Добавить отдельный сценарий обработки WB Price Quarantine и staged discount changes для резкого перехода `0 -> 50` | accepted | `data/planning/wb_price_quarantine_runbook.md`, `data/runs/2026-06-11/wb_discount_stage_49_apply_then_50_20260611T104150Z/` | Автоматизировать CLI `wb-quarantine status/release`, затем отдельно исследовать безопасную staged-стратегию скидок |
| REC-015 | Убрать зависимость `status-preflight` от временного Telegram attachment path для WB API-токена | implemented | `data/planning/marketplace_control_bot_discussion.md`, `src/takterra_agent/config.py`, `.sessions/wb/wb_api_token.txt` | Контролировать, что `.env` указывает на постоянный локальный secret file; сам токен не записывать в docs/logs |
| REC-016 | В WB Price Quarantine CLI требовать явный intent: `cancel_change` или `apply_new_price`, без дефолтного действия | proposed | `data/planning/wb_price_quarantine_runbook.md`, `data/planning/marketplace_control_bot_discussion.md` | При реализации `wb-quarantine release` сделать обязательный параметр intent и dry-run preview последствий |
| REC-017 | Следующий технический этап: перевести ручной WB staged discounts/quarantine опыт в штатный task-runner и встроить защиту в apply акций | accepted | `data/planning/wb_actions_discounts_runbook.md`, `data/planning/wb_price_quarantine_runbook.md`, `data/planning/actions_apply_runbook.md` | После сценария отзывов/вопросов реализовать `plan/apply-wb-staged-discounts`, `wb-quarantine status/release` и запрет прямого `0 -> 50` в общем apply |
| REC-018 | Для отзывов/вопросов добавить отдельный approved/apply/verify сценарий, который отправляет только согласованные ответы и отмечает только согласованные пустые Ozon-оценки | implemented | `data/planning/reviews_questions_runbook.md`, `data/runs/2026-06-11/reviews_questions_apply_20260611T154941/`, `data/runs/2026-06-11/reviews_questions_apply_20260611T155516/` | Использовать `apply-reviews-questions --approved-path ... --confirmed-by-user` для согласованных пакетов отзывов/вопросов |
| REC-019 | Добавить CLI-команду подготовки approved-пакетов по pending для отзывов/вопросов: `replies-only`, `mark-viewed-only`, `questions-only` | proposed | `data/planning/reviews_questions_runbook.md`, `data/planning/marketplace_control_bot_discussion.md` | Согласовать и реализовать `prepare-reviews-questions-approved`, чтобы исключить ручную сборку JSON |
| REC-020 | Ввести единый `RunManifest` и `data/runs/index.jsonl` для всех task-runner запусков | proposed | `data/planning/project_structure_optimization_review_2026-06-11.md` | Спроектировать схему manifest и начать с новых запусков `reviews_questions`/`apply-reviews-questions` |
| REC-021 | Сделать `TaskRegistry` реальным источником CLI и будущего бота, а не заглушкой | proposed | `data/planning/project_structure_optimization_review_2026-06-11.md`, `src/takterra_agent/tasks/registry.py`, `src/takterra_agent/bot/dispatcher.py` | Зарегистрировать все текущие CLI-команды с mode/risk/runbook/handler и строить help/dispatcher от registry |
| REC-022 | Закрыть lifecycle `pending -> approved -> applied -> verified -> closed` и блокировать повторный apply старых approved-пакетов | proposed | `data/planning/project_structure_optimization_review_2026-06-11.md`, `data/pending/`, `data/approved/` | Добавить статусные поля в approved/pending manifest и проверку idempotency перед apply |
| REC-023 | Разделить крупные task-модули на `collect/plan/apply/report/schemas`, начиная с `reviews_questions.py` и `daily_morning_report.py` | proposed | `data/planning/project_structure_optimization_review_2026-06-11.md`, `src/takterra_agent/tasks/reviews_questions.py`, `src/takterra_agent/tasks/daily_morning_report.py` | Выполнить совместимый перенос без изменения CLI-поведения и покрыть существующими тестами |
| REC-024 | Упаковать CDP/LK subprocess JS-скрипты в Python bridge/wrapper слой `src/takterra_agent/lk/` | proposed | `data/planning/project_structure_optimization_review_2026-06-11.md`, `scripts/reviews/`, `scripts/sessions/` | Создать wrapper-контракты для Ozon/WB LK операций, оставив JS как исполнительный bridge |
| REC-025 | Добавить общий artifact retention/cleanup для `data/runs`, `data/pending`, `data/approved`, caches и больших raw artifacts | proposed | `data/planning/project_structure_optimization_review_2026-06-11.md`, `data/planning/lk_connection_runbook.md` | Согласовать сроки хранения и реализовать `cleanup-artifacts --dry-run` без удаления последних successful/apply runs |
| REC-026 | Синхронизировать README/CLI-документацию с task registry и добавить console entry point `takterra-agent` | proposed | `data/planning/project_structure_optimization_review_2026-06-11.md`, `README.md`, `pyproject.toml` | После REC-021 обновить README и добавить `[project.scripts]` |
| REC-027 | Ввести git-контроль кода и документов после отдельной проверки секретов и `.gitignore` | proposed | `data/planning/project_structure_optimization_review_2026-06-11.md`, `.gitignore` | Согласовать, какие planning/docs коммитить, затем инициализировать git без операционных данных и секретов |
| REC-028 | Автоматизировать регулярные read-only задачи через timers и отправку краткого результата в чат | proposed | `data/planning/project_structure_optimization_review_2026-06-11.md`, `data/planning/daily_morning_report_runbook.md`, `deploy/systemd/user/` | После RunManifest настроить timer для `daily-morning-report --seller-v2`, `status-preflight`, read-only `reviews-questions` |
| REC-029 | Централизовать safety guard для write-операций: risk, approval, lock, idempotency, allowed action types | proposed | `data/planning/project_structure_optimization_review_2026-06-11.md`, `src/takterra_agent/safety/` | Вынести проверки `--confirmed-by-user`, locks и whitelist из отдельных apply-команд в общий слой |
| REC-030 | Перенести Ozon Seller/Performance API credentials из временных Telegram attachment paths в постоянную `.sessions/ozon/` secret-зону и сделать отсутствующие file-env безопасными для preflight | implemented | `data/runs/2026-06-12/project_restore_20260612T175400/project_restore_report.md`, `src/takterra_agent/config.py`, `.env` | Контролировать, что `.env` указывает на постоянные local secret files; сами ключи не записывать в docs/logs |
| REC-031 | Усилить `restore-ozon-session`: запуск интерактивного Ozon login через `xvfb-run` без `$DISPLAY`, остановка `systemd --user` контура и безопасная очистка stale Chrome `Singleton*` после проверки живых PID | implemented | `data/planning/session_manager_runbook.md`, `src/takterra_agent/sessions/manager.py`, `data/runs/2026-06-12/project_restore_20260612T175400/project_restore_report.md` | Использовать только штатную CLI-команду `restore-ozon-session`; не чистить Chrome profile вручную до проверки PID |
| REC-032 | Подготовить самостоятельный проект Vital Shevron на базе рабочего каркаса TAKTERRA без переноса секретов, сессий и операционных данных | implemented | `data/planning/vital_shevron_migration_plan.md`, `data/planning/vital_shevron_transfer_manifest.md`, `data/runs/2026-06-12/vital_shevron_scaffold_transfer_20260612T184552/transfer_report.md` | API подключены, первый `fetch-catalog` выполнен; следующий шаг - owner review mapping draft и уточнение связей Ozon/WB |
| REC-033 | Сохранить и развить идею мультиконтура для будущего управления несколькими независимыми проектами/магазинами через `ContourProfile`/`StoreProfile` | proposed | `data/planning/vital_shevron_migration_plan.md` | Вернуться после создания самостоятельного Vital Shevron каркаса: спроектировать `contour_id`, раздельные ports/sessions/data/systemd и выбор контура в будущем боте |
| REC-034 | Для Vital Shevron не предполагать совпадение артикулов продавца Ozon/WB: весь функционал должен работать до унификации через отдельные Ozon/WB каталоги, native ID и optional mapping | implemented | `data/planning/vital_shevron_migration_plan.md`, `/home/pavel/projects/seller_vital_shevron/data/planning/catalog_mapping_runbook.md` | Первый mapping draft создан: Ozon `548`, WB `431`, exact seller SKU matches `98`, owner review `783` строк |
| REC-035 | После чистой установки Vital Shevron создать GitHub repository, но не коммитить секреты, сессии и operational artifacts | implemented | `data/planning/vital_shevron_migration_plan.md`, `/home/pavel/projects/seller_vital_shevron/data/planning/vital_shevron_bootstrap_plan.md` | GitHub repo создан: `https://github.com/pavelvital2/seller_vital_shevron`, visibility `PRIVATE`, latest pushed commit `ae7b0e5` |
| REC-036 | В `apply-actions` не отправлять пустой WB upload, если по схеме `65-50-50` нет строк к изменению | implemented | `data/planning/actions_apply_runbook.md`, `src/takterra_agent/tasks/actions_apply.py`, `data/pending/actions_apply_pending_20260612T211753/` | Использовать текущий no-op guard: WB при `0` guarded rows фиксируется как skipped без write-запроса |
| REC-037 | Автоматизировать read-only сбор топа поисковых запросов Ozon/WB в CLI-команду `search-queries` вместо ручного браузерного скрипта | proposed | `data/planning/search_queries_runbook.md`, `data/runs/2026-06-12/search_queries_20260612T220200/` | Реализовать task `search_queries.py`: marketplace, query, period, limit, LK/API fallback, markdown/json artifacts |

## Постоянные источники рекомендаций

Если нужно проверить, не появились ли новые рекомендации без записи в этот
реестр, смотреть:

```text
data/15_architecture_notes/questions_and_recommendations.md
data/planning/marketplace_control_bot_discussion.md
data/planning/*_runbook.md
data/planning/project_map.md
```

Быстрый поиск по проекту:

```bash
rg -n "Рекомендация|рекомендац|оптимизац|автоматизац|Следующий шаг|proposed|accepted" data/planning data/15_architecture_notes
```
