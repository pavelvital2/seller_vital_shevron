# Карта проекта seller_takterra

Назначение: постоянная карта структуры проекта TAKTERRA. Обновлять при каждом
изменении архитектуры папок/файлов или при появлении нового важного артефакта.

## Корень проекта

```text
/home/pavel/projects/seller_takterra
```

## Основные файлы

- `AGENTS.md` - постоянные правила работы агентов.
- `README.md` - краткое описание проекта.
- `pyproject.toml` - настройки Python-пакета и тестов.
- `.env.example` - пример переменных окружения без секретов.
- `.gitignore` - исключения локальных/секретных/кэш-файлов.

## Код

```text
src/takterra_agent/
```

- `cli.py` - CLI-вход в task-runner.
- `config.py` - загрузка доступов из env/file-env без записи секретов.
- `http.py` - общий JSON HTTP-клиент.
- `catalog/` - схема и сборка master catalog.
- `sessions/` - единый session/status слой для Ozon/WB: snapshot, freshness,
  systemd unit status, session manager и restore Ozon workflow.
- `marketplaces/ozon/adapter.py` - Ozon Seller API adapter.
- `marketplaces/ozon/performance_adapter.py` - Ozon Performance API adapter для
  продвижения; preflight получает только metadata Bearer-токена без сохранения
  токена.
- `marketplaces/wb/adapter.py` - WB Content API adapter.
- `marketplaces/wb/statistics_adapter.py` - WB Statistics API adapter для
  read-only заказов, продаж и временного legacy-снимка остатков.
- `marketplaces/wb/communications_adapter.py` - WB Feedbacks/Questions API
  adapter для счетчиков, списков неотвеченных отзывов/вопросов и apply ответов
  через `POST /api/v1/feedbacks/answer`, `PATCH /api/v1/questions`.
- `tasks/catalog_fetch.py` - read-only сбор Ozon/WB каталога.
- `tasks/daily_morning_report.py` - ежедневный read-only управленческий отчет:
  v1 технический preflight/status отчет и v2 селлерский отчет
  `--seller-v2` с заказами, продажами, остатками, обращениями, последними
  акциями и техническим приложением.
- `tasks/reviews_questions.py` - сценарий отзывов/вопросов Ozon/WB:
  read-only/API-first сбор, Ozon LK/CDP fallback при 403 Review API,
  pending-черновики, approved/apply публичных ответов и отметки пустых
  Ozon-оценок просмотренными.
- `data/planning/search_queries_runbook.md` - инструкция read-only сбора топа
  поисковых запросов из ЛК Ozon/WB; текущий сценарий выполняется браузерным
  скриптом, пока не вынесен в CLI task.
- `tasks/status_preflight.py` - read-only preflight состояния проекта: Ozon API,
  WB API, ЛК-сессии Ozon/WB и master catalog.
- `tasks/ozon_elastic_plan.py` - dry-run план Ozon Elastic Boosting без upload
  изменений в Ozon.
- `tasks/wb_actions_discount_plan.py` - dry-run план WB акций/скидок, включая
  схему `65-50-50`, без upload изменений в WB.
- `tasks/actions_apply.py` - apply/verify согласованного пакета Ozon Elastic и
  WB `65-50-50` после preflight, fresh dry-run и drift-check.
- `tasks/wb_card_create_plan.py` - dry-run план создания WB-карточек.
- `tasks/wb_card_create_apply.py` - apply/verify создания WB-карточек.
- `safety/` - будущий safety-контур: approvals, locks, dry-run.
- `bot/` - будущий Telegram dispatcher.

## Session-скрипты

```text
scripts/sessions/
```

- `open_ozon_seller_agent.sh` - ручной запуск Chrome с CDP для Ozon TAKTERRA.
- `ozon_seller_interactive_login.js` - ручной Ozon-вход с поддержкой нескольких
  последовательных OTP/SMS/email-кодов.
- `ozon_import_cookies_check.js` - импорт свежих Ozon cookies во временном
  cookie-сценарии и проверка dashboard.
- `ozon_seller_persistent_session.js` - проверка/экспорт persistent-сессии Ozon.
- `ozon_session_keepalive_cdp.js` - keepalive Ozon через открытый CDP.
- `ozon_keep_dashboard_open.js` - постоянный headless/CDP keeper dashboard Ozon.
- `start_ozon_keeper.sh` - фоновый запуск Ozon keeper с pid/log.
- `stop_ozon_keeper.sh` - остановка Ozon keeper и его process group.
- `ozon_session_refresh.sh` - единый refresh: сначала `cdp_keepalive`, если
  живой CDP `9444`; иначе cookie-import при наличии `tmp/auth/ozon_user_cookies.json`
  или продление persistent-сессии.
- `start_ozon_session_watchdog.sh` - запуск проектного таймера refresh.
- `stop_ozon_session_watchdog.sh` - остановка проектного таймера refresh.
- `wb_auth_once_file_code.js` - вход WB по схеме старого проекта: один
  persistent profile, SMS-код через временный файл; доработан на ожидание
  email-кода в той же форме, если WB запросит второй код.
- `wb_persistent_session.js` - проверка/экспорт persistent-сессии WB.
- `wb_session_keepalive.js` - keepalive WB-сессии с проверкой активного продавца
  `ИП Рантусова` и экспортом state при успешной проверке.
- `wb_daily_session_refresh.sh` - refresh WB для `seller.wildberries.ru` и
  `cmp.wildberries.ru` с логами в `.sessions/wb/session_refresh_logs/`.
- `start_wb_session_watchdog.sh` - запуск проектного таймера refresh WB.
- `stop_wb_session_watchdog.sh` - остановка проектного таймера refresh WB.

## Общие JS helpers

```text
scripts/lib/
```

- `playwright.js` - общий загрузчик Playwright для JS-скриптов ЛК. Ищет
  доступный Playwright module и Chromium/Google Chrome executable, поддерживает
  server/systemd-запуск через обычный Google Chrome без привязки к временным
  путям конкретного агента.

## Systemd User Units

```text
deploy/systemd/user/
```

- `takterra-ozon-keeper.service` - долгоживущий Ozon CDP keeper на
  `127.0.0.1:9444`.
- `takterra-ozon-session-refresh.service` - Ozon CDP keepalive service.
- `takterra-ozon-session-refresh.timer` - Ozon refresh каждые 30 минут.
- `takterra-wb-session-refresh.service` - WB keepalive service.
- `takterra-wb-session-refresh.timer` - WB refresh каждые 60 минут.

## Action-скрипты

```text
scripts/actions/
```

- `wb_download_active_actions.js` - read-only снимок активных акций WB через
  локальный persistent profile: список промо, Excel-файлы товаров в акциях и
  текущий прайс-снимок. Секреты читаются только внутри браузерного контекста и
  не выводятся.

## Reviews/Questions-скрипты

```text
scripts/reviews/
```

- `ozon_reviews_questions_readonly_cdp.js` - Ozon fallback через живую
  ЛК/CDP-сессию `127.0.0.1:9444`: read-only сбор новых отзывов и вопросов,
  проверка магазина `TAKTERRA`, сохранение raw/processed без публикации ответов.
- `ozon_apply_reviews_questions_cdp.js` - Ozon apply через живую
  ЛК/CDP-сессию `127.0.0.1:9444`: публикация approved-ответов через
  `/api/review/comment/create` и отметка approved пустых отзывов просмотренными
  через `/api/v2/review/change-interaction-status`.

## Тесты

```text
tests/
```

- `test_catalog_loader.py` - проверка сопоставления master catalog.
- `test_wb_card_create_plan.py` - проверка подготовки WB-описаний.
- `test_wb_actions_discount_plan.py` - проверка схемы `65-50-50` и парсинга
  WB Excel с английскими заголовками.
- `test_actions_apply.py` - проверка guarded WB payload и отбора Ozon apply
  строк.
- `test_status_preflight.py` - проверка статусов preflight и summary master
  catalog.
- `test_sessions.py` - проверка freshness-логики, dry-run restore Ozon и
  dry-run systemd install.
- `test_daily_morning_report.py` - проверка локальной сборки утреннего отчета,
  pending/apply сопоставления, парсинга рекомендаций и v2 бизнес-агрегатов.
- `test_reviews_questions.py` - проверка отсутствующего WB token file,
  нормализации WB отзывов/вопросов, генерации pending-черновиков и
  `mark_review_viewed` для пустых Ozon-оценок.

## Данные и отчеты

```text
data/
```

- `data/planning/` - правила, обсуждение, runbook, карты проекта и ЛК.
- `data/planning/project_structure_optimization_review_2026-06-11.md` - аудит
  структуры проекта после добавления новых функций; рекомендации по
  структуризации, автоматизации, run manifests, registry, lifecycle и cleanup.
- `data/15_architecture_notes/` - архитектурные вопросы, рекомендации и решения,
  влияющие на структуру проекта, master catalog, safety-контур и будущего бота.
- `data/reports/` - исследовательские отчеты.
- `data/runs/` - результаты запусков task-runner.
- `data/pending/` - pending-пакеты для опасных операций, ожидающие явного
  подтверждения владельца перед apply.
- `data/runs/2026-06-10/lk_connection_ozon_20260610T165258Z/summary.md` -
  безопасный отчет по первой попытке подключения ЛК Ozon TAKTERRA через cookies.
- `data/runs/2026-06-10/lk_connection_ozon_20260610T165908Z/summary.md` -
  безопасный отчет по второй попытке cookie-импорта Ozon.
- `data/runs/2026-06-10/lk_connection_ozon_20260610T170741Z/summary.md` -
  безопасный отчет по третьей попытке cookie-импорта Ozon.
- `data/runs/2026-06-10/lk_connection_ozon_transfer_20260610T173809Z/summary.md` -
  безопасный отчет по переносу полной Ozon-сессии, переключению на TAKTERRA и
  запуску keeper/watchdog.
- `data/runs/2026-06-10/lk_connection_wb_20260610T190836Z/summary.md` -
  безопасный отчет по подключению WB, переключению активного продавца на
  `ИП Рантусова` и проверке seller/cmp.
- `data/runs/2026-06-10/ozon_elastic_plan_20260610T230610/` - dry-run Ozon
  Elastic Boosting; upload в Ozon не выполнялся.
- `data/runs/2026-06-10/wb_actions_discount_plan_65-50-50_20260610T231441/` -
  dry-run WB акций/скидок по схеме `65-50-50`; upload в WB не выполнялся.
- `data/runs/2026-06-10/actions_review_20260610T232748/` - единый review-отчет
  по Ozon Elastic и WB `65-50-50`; apply не выполнялся.
- `data/runs/2026-06-10/actions_review_20260610T232748/wb_detailed_review_65-50-50.md` -
  подробный WB-review с active/future promos, распределением скидок и
  master-catalog guard.
- `data/runs/2026-06-10/actions_review_20260610T232748/wb-upload-payload-preview-65-50-50-master-catalog-guard.json` -
  WB guarded preview на 151 строку, исключающий 2 WB-only строки из исходного
  preview на 153 строки.
- `data/pending/actions_apply_pending_20260610T232837/` - pending-пакет apply по
  акциям; применен после явного подтверждения владельца.
- `data/runs/2026-06-10/actions_apply_20260610T234323/` - первый успешный
  apply Ozon Elastic и WB `65-50-50`: Ozon 12 update + 5 deactivate, WB guarded
  upload 151 строка, upload ID `138255979`.
- `data/runs/2026-06-11/status_preflight_20260611T000512/` - контрольный
  preflight после перехода сессий на `systemd --user`, `overall_status: ok`.
- `data/runs/2026-06-11/sessions_status_20260611T000502/` - контрольный
  `sessions status`, источник watchdog для Ozon/WB: `systemd`.
- `data/runs/2026-06-11/daily_morning_report_20260611T070112/` - первый
  утренний отчет MVP v1 после правки формата Markdown, `overall_status: ok`.
- `data/runs/2026-06-11/daily_morning_report_v2_20260611T072124/` - первый
  seller-first утренний отчет v2; бизнес-метрики Ozon/WB, обращения WB,
  остатки и техническое приложение, `overall_status: warning` из-за WB sales
  HTTP 429 и временного legacy WB stocks endpoint.
- `data/runs/2026-06-11/actions_check_20260611T082143/` - read-only проверка
  акций после первого apply: Ozon Elastic без новых изменений, WB `65-50-50`
  показывает 22 строки dry-run и требует master-catalog guard перед любым
  будущим apply.
- `data/runs/2026-06-11/wb_actions_discount_apply_65-50-50_20260611T092528/` -
  apply WB `65-50-50` на все 22 строки схемы; WB применил 2 строки `54 -> 53`,
  20 строк `0 -> 50` отклонил ограничением резкого снижения цены.
- `data/runs/2026-06-11/wb_actions_discount_apply_recovery_65-50-50_20260611T092644/` -
  попытка recovery для 20 строк через `0 -> 49`; WB отклонил шаг и перевел
  товары в Price Quarantine.
- `data/runs/2026-06-11/wb_price_quarantine_recovery_20260611T130806/` -
  API-first попытка вывода 20 WB-товаров из Price Quarantine через официальный
  Prices and Discounts API; API upload отклонен как уже установленные
  цена/скидка, товары остались в карантине.
- `data/runs/2026-06-11/wb_price_quarantine_lk_release_20260611T101601Z/` -
  успешный вывод 20 WB-товаров из Price Quarantine через ЛК `Keep Current
  Price`; операция отменила quarantined-скидку и позже признана неверной для
  цели "оставить скидку".
- `data/runs/2026-06-11/wb_discount_stage_49_apply_then_50_20260611T104150Z/` -
  корректирующая операция WB: staged-переход `0 -> 49 -> Apply New Price -> 50`,
  финальная скидка 20 товаров `50%`, карантин `0`.
- `data/runs/2026-06-11/reviews_questions_20260611T142015/` - первый read-only
  dry-run отзывов/вопросов: Ozon Review API вернул 403 по подписке, Ozon
  LK/CDP fallback нашел 58 отзывов и 0 вопросов, WB заблокирован отсутствующим
  API token file, публикация не выполнялась.
- `data/pending/reviews_questions_20260611T142015_pending/` - pending-пакет
  черновиков: 2 публичных ответа Ozon и 56 действий `mark_review_viewed` для
  пустых Ozon-оценок, требует решения владельца.
- `data/runs/2026-06-11/reviews_questions_20260611T143549/` - WB read-only
  dry-run отзывов/вопросов после восстановления постоянного WB API token file:
  8 WB отзывов, 0 вопросов, статус `ok`.
- `data/pending/reviews_questions_20260611T143549_pending/` - pending-пакет
  8 WB-черновиков ответов, требует решения владельца перед публикацией.
- `data/approved/reviews_questions_apply_20260611T145000/` - approved-пакет
  10 публичных ответов на отзывы: 2 Ozon и 8 WB; пустые Ozon-оценки не
  включались.
- `data/runs/2026-06-11/reviews_questions_apply_20260611T154941/` - успешный
  apply ответов: WB `8/8`, Ozon `2/2`, Ozon `NOT_VIEWED 58 -> 56`, WB
  unanswered after verify `0`.
- `data/approved/reviews_questions_mark_viewed_20260611T155509/` -
  approved-пакет 56 Ozon `mark_review_viewed` для пустых оценок без текста.
- `data/runs/2026-06-11/reviews_questions_apply_20260611T155516/` - успешная
  отметка пустых Ozon-оценок просмотренными: `56/56`,
  `NOT_VIEWED 56 -> 0`, `VIEWED 176 -> 232`.
- `data/runs/2026-06-12/restore_ozon_session_20260612T174933/` - успешное
  восстановление Ozon LK: интерактивный вход через `xvfb-run`, магазин
  `TAKTERRA` подтвержден, `stateExported true`, systemd keeper/timer подняты.
- `data/runs/2026-06-12/sessions_status_20260612T175227/` - контрольный
  `sessions status` после восстановления: Ozon/WB `overall_status: ok`,
  watchdog source для обоих маркетплейсов `systemd`.
- `data/runs/2026-06-12/status_preflight_20260612T175227/` - полный
  preflight после восстановления: Ozon Seller API, Ozon Performance API, WB API,
  master catalog и LK keepalive `ok`.
- `data/runs/2026-06-12/project_restore_20260612T175400/` - безопасный отчет
  по восстановлению работоспособности проекта 2026-06-12 без секретов/кодов.
- `data/runs/2026-06-12/vital_shevron_scaffold_transfer_20260612T184552/` -
  безопасный отчет по созданию самостоятельного каркаса Vital Shevron в
  `/home/pavel/projects/seller_vital_shevron`.
- `data/catalog/raw/` - raw API-снимки по датам/run_id.
- `data/catalog/processed/` - master catalog CSV/JSON.
- `data/catalog/wb_onboarding/` - очередь и статус доведения товаров на WB.
- `data/catalog/manual/` - ручные mapping/README, только как переходный слой.
- `data/15_architecture_notes/README.md` - назначение раздела архитектурных
  заметок.
- `data/15_architecture_notes/questions_and_recommendations.md` - журнал
  вопросов владельца и архитектурных рекомендаций.

## Актуальные постоянные инструкции

- `data/planning/marketplace_control_bot_discussion.md`
- `data/planning/recommendations_index.md` - единый реестр рекомендаций агента:
  статус, ссылка на детали и следующий шаг.
- `data/planning/vital_shevron_migration_plan.md` - план переноса рабочего
  каркаса TAKTERRA в самостоятельный проект Vital Shevron.
- `data/planning/vital_shevron_transfer_manifest.md` - include/exclude/sanitize
  manifest переноса Vital Shevron.
- `data/15_architecture_notes/questions_and_recommendations.md` - архитектурные
  решения и рекомендации, влияющие на будущую структуру проекта.
- `data/planning/new_agent_handoff_2026-06-10.md`
- `data/planning/lk_connection_runbook.md` - порядок подключения к ЛК Ozon/WB,
  правила persistent-сессий и preflight.
- `data/planning/session_manager_runbook.md` - единый session manager,
  восстановление Ozon-сессии и `systemd --user` timers.
- `data/planning/status_preflight_runbook.md` - инструкция по запуску и чтению
  результата `status-preflight`.
- `data/planning/actions_apply_runbook.md` - apply/verify согласованного пакета
  Ozon Elastic и WB `65-50-50`.
- `data/planning/daily_morning_report_runbook.md` - ежедневный утренний
  read-only отчет и план расширения v2.
- `data/planning/reviews_questions_runbook.md` - read-only сбор отзывов и
  вопросов Ozon/WB, черновики ответов, pending-пакет и ограничения apply.
- `data/planning/ozon_elastic_runbook.md` - инструкция по dry-run Ozon Elastic
  Boosting.
- `data/planning/wb_actions_discounts_runbook.md` - инструкция по dry-run WB
  акций/скидок.
- `data/planning/wb_price_quarantine_runbook.md` - инструкция по диагностике и
  выводу товаров WB из Price Quarantine по правилу API-first.
- `data/planning/wb_card_upload_runbook.md`

## Планируемые локальные сессии

Сессионные файлы ЛК должны храниться только в локальной секретной зоне проекта и
быть исключены из git:

```text
.sessions/ozon/chrome-profile/
.sessions/ozon/ozon_seller_storage_state.json
.sessions/ozon/session_refresh_logs/
.sessions/wb/browser-profile/
.sessions/wb/wb_storage_state.json
.sessions/wb/wb_api_token.txt
.sessions/wb/session_refresh_logs/
tmp/auth/
```

Содержимое этих файлов нельзя включать в отчеты, инструкции или сообщения в чат.

## Актуальные карты

- `data/planning/project_map.md`
- `data/planning/ozon_cabinet_map.md`
- `data/planning/wb_cabinet_map.md`

## Правило обновления

Если агент добавляет, переносит или переименовывает важные папки/файлы, он
обязан обновить эту карту в том же рабочем цикле.
