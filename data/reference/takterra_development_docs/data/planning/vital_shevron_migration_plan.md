# План переноса каркаса для Vital Shevron

Дата: 2026-06-12.

Статус: исправленная версия после уточнения владельца.

## Краткий вывод

Задача не в том, чтобы добавить `Vital Shevron` вторым магазином внутрь
текущего проекта `seller_takterra`.

Правильная задача: подготовить отдельный самостоятельный проект для другого
владельца `Vital Shevron`, чтобы он мог независимо управлять своими магазинами
Ozon и WB, развивать проект параллельно, а в дальнейшем обе стороны могли
обмениваться удачными решениями.

Рекомендуемый подход:

```text
seller_takterra          - рабочий проект TAKTERRA
seller_vital_shevron     - отдельный проект Vital Shevron
shared scaffold/patches  - переносимые улучшения между проектами
```

Не переносить:

- API-ключи;
- cookies;
- storage state;
- `.env`;
- `.sessions`;
- личные кабинеты TAKTERRA;
- операционные run artifacts с чувствительными данными;
- pending/approved пакеты TAKTERRA для write-операций.

Переносить:

- рабочий код task-runner;
- безопасные инструкции;
- архитектурные рекомендации;
- safety-подход;
- шаблоны env/systemd;
- тесты;
- sanitized runbook по восстановлению сессий, API-first, dry-run/review/apply;
- структуру проекта.

## Цель переноса

Создать для Vital Shevron не копию магазина TAKTERRA, а самостоятельный
управляющий проект с тем же уровнем зрелости каркаса:

- CLI/task-runner;
- API adapters Ozon/WB;
- LK/session контур Ozon/WB;
- systemd timers;
- status-preflight;
- раздельные каталоги Ozon/WB на первом этапе;
- mapping Ozon/WB товаров;
- будущий unified master catalog после унификации артикулов продавца;
- ежедневные отчеты;
- отзывы/вопросы;
- акции/скидки/продвижение через safety workflow;
- AGENTS.md, runbook, карты проекта и рекомендации;
- тесты и правила дальнейшей разработки.

## Архитектурный принцип

Код и практики можно переносить между проектами. Секреты, сессии и операционные
данные нельзя.

Проекты должны развиваться независимо:

```text
TAKTERRA -> может брать улучшения из Vital Shevron
Vital Shevron -> может брать улучшения из TAKTERRA
```

Для этого нужен не общий live-проект на два владельца, а понятная схема обмена:

- git/архивы патчей;
- changelog переносимых улучшений;
- список общих модулей;
- список локальных отличий каждого проекта.

## Будущий мультиконтур

Информацию о мультиконтуре не удалять. Это отдельное будущее направление, но
оно не заменяет текущую задачу по созданию самостоятельного проекта Vital
Shevron.

Разделение такое:

```text
текущий шаг:
  seller_takterra -> чистый самостоятельный каркас seller_vital_shevron

будущий шаг:
  единая архитектура мультиконтура для нескольких независимых проектов/магазинов
```

Мультиконтур понадобится, чтобы:

- управлять несколькими магазинами/контурами из одного agent framework;
- не смешивать секреты, сессии, каталоги и отчеты;
- переносить удачные функции между TAKTERRA и Vital Shevron;
- иметь единый формат task-runner, runbook, recommendations, reports и safety;
- в будущем подключить бот, где перед операцией явно выбирается контур.

Базовая идея мультиконтура:

```text
contours:
  takterra:
    owner: TAKTERRA
    project_path: /home/pavel/projects/seller_takterra
    ozon_cdp_port: 9444
    session_root: .sessions/
    data_root: data/

  vital_shevron:
    owner: Vital Shevron
    project_path: /home/pavel/projects/seller_vital_shevron
    ozon_cdp_port: <отдельный порт>
    session_root: .sessions/
    data_root: data/
```

Внутри одного проекта мультиконтур можно будет реализовать через `StoreProfile`
или `ContourProfile`, но это следующий архитектурный этап. Для текущего
переноса Vital Shevron важнее сначала сделать чистый самостоятельный проект,
чтобы другой владелец мог работать независимо.

Минимальные правила будущего мультиконтура:

- каждый контур имеет собственный `.env`, `.sessions`, API credentials,
  browser profiles, storage state, CDP ports, systemd units и `data/runs`;
- write-операции всегда содержат `contour_id`, `owner`, `marketplace`,
  `expected_store/seller`;
- pending/approved packages нельзя переносить между контурами без ручного
  review;
- общий код можно переносить патчами, секреты и операционные данные - нельзя;
- бот должен требовать выбор контура до выбора операции.

Отдельная рекомендация по мультиконтурной архитектуре зафиксирована в
`data/planning/recommendations_index.md` как `REC-033`.

## Рекомендуемое имя и место нового проекта

Предварительно:

```text
/home/pavel/projects/seller_vital_shevron
```

Если владелец согласует другое имя, использовать его, но не смешивать с:

```text
/home/pavel/projects/seller_takterra
/home/pavel/projects/seller_ozon_vitalsewing
```

Старый проект `seller_ozon_vitalsewing` остается read-only источником опыта,
если владелец отдельно не разрешит иное.

## Что входит в переносимый каркас

### Код

Переносить:

```text
src/
scripts/
tests/
deploy/systemd/user/
pyproject.toml
README.md
.env.example
.gitignore
AGENTS.md
```

После переноса адаптировать названия:

- `TAKTERRA` -> `Vital Shevron` в документах и ожидаемых marker values;
- `takterra-agent` -> согласованное CLI имя, например
  `vital-shevron-agent` или временно оставить `takterra-agent` до
  отдельного rename-этапа;
- `takterra-*` systemd unit names -> `vital-shevron-*`;
- `OZON_EXPECTED_STORE=TAKTERRA` -> `OZON_EXPECTED_STORE=Vital Shevron`;
- `WB_EXPECTED_SELLER=...` -> фактический продавец Vital Shevron после входа.

### Документы

Переносить как шаблон и очищенную базу знаний:

```text
data/planning/*_runbook.md
data/planning/recommendations_index.md
data/planning/project_map.md
data/planning/ozon_cabinet_map.md
data/planning/wb_cabinet_map.md
data/15_architecture_notes/
```

Но перед переносом нужно:

- убрать операционные данные TAKTERRA;
- убрать run_id, которые раскрывают конкретные действия TAKTERRA, если они не
  нужны как учебные примеры;
- заменить магазин/продавца на Vital Shevron или generic placeholders;
- оставить инструкции по процессу: API-first, safety, session restore,
  status-preflight, write approval.

### Данные

Не переносить текущие рабочие данные TAKTERRA:

```text
.env
.sessions/
data/catalog/raw/
data/catalog/processed/
data/runs/
data/pending/
data/approved/
tmp/auth/
*.log
```

Для Vital Shevron создать пустую структуру:

```text
data/catalog/ozon/raw/
data/catalog/ozon/processed/
data/catalog/wb/raw/
data/catalog/wb/processed/
data/catalog/mapping/
data/catalog/unified/
data/runs/
data/pending/
data/approved/
data/reports/
.sessions/ozon/
.sessions/wb/
tmp/auth/
```

И добавить `.gitkeep` или README-файлы там, где нужно сохранить пустые папки.

## Особенность каталогов Vital Shevron

У Vital Shevron артикулы продавца на Ozon и WB сейчас не совпадают. Поэтому
нельзя использовать TAKTERRA-логику как готовое допущение
`master_sku == Ozon offer_id == WB vendorCode`.

Обязательное требование владельца от 2026-06-12: весь функционал Vital Shevron
должен работать даже до унификации артикулов продавца.

Практический вывод: несовпадение seller SKU не является блокером для работы
проекта. До унификации:

- Ozon-сценарии работают по Ozon-native идентификаторам:
  `offer_id`, `product_id`, `sku`;
- WB-сценарии работают по WB-native идентификаторам:
  `vendorCode`, `nmID`, barcode;
- объединенные отчеты используют mapping, если он есть, и явно показывают
  `unmatched`/`needs_owner_review`, если mapping еще не подтвержден;
- marketplace-local сценарии не должны падать только потому, что mapping
  отсутствует или не полон;
- cross-marketplace write-операции, где одно действие затрагивает один и тот же
  товар на Ozon и WB, требуют подтвержденного mapping для этих строк.

Правильная стратегия:

1. Сначала считать Ozon и WB отдельными каталогами.
2. Получить read-only снимок Ozon catalog.
3. Получить read-only снимок WB catalog.
4. Построить таблицу сопоставления товаров между Ozon и WB.
5. Работать с бизнес-сценариями через mapping, а не через равенство артикулов.
6. После проверки mapping подготовить проект унификации артикулов продавца.
7. Только после отдельного review и подтверждения владельца менять артикулы
   продавца на Ozon/WB.

Рекомендуемые файлы:

```text
data/catalog/ozon/processed/ozon_catalog.csv
data/catalog/wb/processed/wb_catalog.csv
data/catalog/mapping/ozon_wb_product_mapping.csv
data/catalog/mapping/ozon_wb_product_mapping_review.md
data/catalog/unified/future_seller_sku_plan.csv
```

Минимальные поля mapping:

```text
internal_product_id
product_name
ozon_offer_id
ozon_product_id
ozon_sku
wb_vendor_code
wb_nm_id
barcode
match_confidence
match_basis
needs_owner_review
target_unified_seller_sku
```

`match_basis` должен объяснять, почему товары признаны одинаковыми:

```text
name
barcode
photo
dimensions
characteristics
manual_owner_confirmed
```

Пока mapping не подтвержден, повышенным риском считаются не все операции, а
только операции, которые пытаются связать один и тот же товар между двумя
маркетплейсами. Отдельные Ozon-only и WB-only сценарии должны продолжать
работать по native ID маркетплейса.

Унификация артикулов продавца - отдельный опасный сценарий:

```text
read-only -> mapping draft -> owner review -> approved mapping ->
dry-run rename plan -> owner approval -> apply -> verify -> result
```

Перед изменением артикулов нужно отдельно проверить по документации/API и ЛК,
какие ограничения есть у Ozon и WB на изменение `offer_id`/`vendorCode` у уже
созданных карточек. Если через API изменение невозможно или ограничено, только
тогда использовать ЛК, по правилу API-first.

## План действий

### Этап 0. Зафиксировать текущее состояние TAKTERRA

Цель: переносить только рабочий каркас.

1. Проверить:

```bash
PYTHONPATH=src pytest -q
PYTHONPATH=src python3 -m takterra_agent.cli sessions status
PYTHONPATH=src python3 -m takterra_agent.cli status-preflight
```

2. Сохранить безопасный отчет состояния без секретов.
3. Отдельно отметить текущий риск: в `seller_takterra` нет `.git`, поэтому
   перед крупным переносом лучше согласовать включение git-контроля кода и
   документов без секретов.

### Этап 1. Составить transfer manifest

Цель: явно разделить `include`, `exclude`, `sanitize`.

Создать:

```text
data/planning/vital_shevron_transfer_manifest.md
```

Разделы:

```text
include:
  - src/
  - scripts/
  - tests/
  - deploy/
  - pyproject.toml
  - README.md
  - AGENTS.md
  - .env.example
  - .gitignore
  - sanitized data/planning/

exclude:
  - .env
  - .sessions/
  - tmp/auth/
  - data/runs/
  - data/pending/
  - data/approved/
  - data/catalog/raw/
  - data/catalog/processed/
  - files with secrets/cookies/storage state/OTP

sanitize:
  - TAKTERRA-specific names
  - ИП Рантусова marker
  - Ozon/WB company identifiers
  - operational run details
  - uploaded task IDs
```

Критерий готовности: есть список, по которому новый агент сможет создать
проект без случайного переноса секретов.

### Этап 2. Создать чистый проект Vital Shevron

Цель: отдельная рабочая папка.

Предварительная команда после согласования пути:

```bash
mkdir -p /home/pavel/projects/seller_vital_shevron
```

Затем скопировать только разрешенные manifest-файлы.

Важно: не использовать `cp -a` всего проекта, потому что он утащит `.sessions`,
run artifacts и потенциально чувствительные данные.

### Этап 2A. Git/GitHub для чистого проекта

Требование владельца от 2026-06-12: после установки чистого проекта сделать для
него репозиторий на GitHub.

Порядок:

1. Сначала проверить, что в проект не попали секреты и операционные артефакты:

```bash
find . -path './.sessions/*' -type f -print
find . -name '.env' -print
find data/runs data/pending data/approved -type f -print
```

2. Проверить `.gitignore`.
3. Инициализировать локальный git:

```bash
git init
git status --short
```

4. Сделать первый commit только после проверки списка файлов.
5. Создать GitHub repository и подключить remote.

Критерий: в GitHub попадает только код, тесты, шаблоны и sanitized документы.
`.env`, `.sessions`, cookies, storage state, API credentials, OTP и operational
run artifacts не коммитятся.

### Этап 3. Очистить и переименовать проект

Цель: чтобы Vital Shevron не наследовал TAKTERRA как активный магазин.

Минимальные правки:

- `AGENTS.md`: правила Vital Shevron, другой владелец;
- `README.md`: описание проекта Vital Shevron;
- `.env.example`: переменные без реальных значений;
- `deploy/systemd/user/*`: unit names и paths под новый проект;
- `data/planning/project_map.md`: новая карта проекта;
- `data/planning/*_runbook.md`: заменить TAKTERRA-specific markers на
  placeholders Vital Shevron;
- отчеты/рекомендации: оставить как методологию, а не как историю операций
  TAKTERRA.

Решение по Python package:

1. Быстрый и безопасный вариант: временно оставить `takterra_agent` внутри
   кода, но переименовать CLI/docs/systemd. Это снижает риск поломки.
2. Чистый вариант: переименовать package в `vital_shevron_agent` или
   `seller_agent`. Это лучше архитектурно, но требует больше тестов.

Моя рекомендация: сначала быстрый безопасный вариант, после первого рабочего
preflight Vital Shevron сделать отдельный rename-этап.

### Этап 4. Secret bootstrap для Vital Shevron

Цель: подготовить места для секретов без их записи в проект.

Создать пустые/закрытые места:

```text
.sessions/ozon/ozon_seller_api_credentials.txt
.sessions/ozon/ozon_performance_api_credentials.txt
.sessions/wb/wb_api_token.txt
```

Права:

```text
chmod 600 или 660 с согласованной группой
```

`.env` Vital Shevron должен ссылаться на эти файлы, но не хранить сами ключи.

### Этап 5. Первый read-only запуск Vital Shevron

Цель: проверить API без ЛК и без write-операций.

После получения API credentials:

```bash
PYTHONPATH=src python3 -m takterra_agent.cli status-preflight --skip-lk
```

Ожидаемо:

```text
Ozon Seller API: ok
Ozon Performance API: ok или skipped, если ключ не выдан
WB API: ok
master catalog: warning/error до первого fetch-catalog
```

Затем:

```bash
PYTHONPATH=src python3 -m takterra_agent.cli fetch-catalog
```

Для Vital Shevron `fetch-catalog` должен на первом этапе сформировать два
раздельных каталога:

```text
Ozon catalog
WB catalog
```

`master catalog` в смысле единого артикула продавца не считать готовым, пока
не построен и не подтвержден mapping Ozon/WB товаров.

### Этап 5A. Mapping Ozon/WB товаров Vital Shevron

Цель: сопоставить разные артикулы продавца Ozon/WB.

1. Сформировать `ozon_catalog.csv`.
2. Сформировать `wb_catalog.csv`.
3. Построить draft mapping по доступным признакам:

- название;
- штрихкод;
- фото;
- категория;
- размеры;
- характеристики;
- цена как вспомогательный признак;
- ручное подтверждение владельца.

4. Сформировать отчет строк:

```text
matched_high_confidence
matched_need_review
ozon_only
wb_only
possible_duplicates
conflicts
```

5. После review владельца сохранить approved mapping.

Критерий готовности: для каждого товара известно, какой Ozon item соответствует
какой WB card, либо товар явно помечен как `ozon_only`/`wb_only`/`unmatched`.

### Этап 5B. Будущая унификация артикулов продавца

Цель: привести артикулы продавца Ozon/WB к единой системе.

Этап не выполнять автоматически при первом переносе. Сначала только план:

1. Выбрать формат будущего единого артикула продавца.
2. Сформировать `future_seller_sku_plan.csv`.
3. Проверить техническую возможность изменения артикулов на Ozon/WB через API.
4. Подготовить dry-run rename plan.
5. Отдельно согласовать с владельцем.
6. Выполнить apply только по safety workflow.

До завершения этого этапа все сценарии Vital Shevron должны поддерживать
разные Ozon/WB seller SKU через mapping.

### Этап 6. Подключение ЛК Vital Shevron

Цель: отдельные сессии ЛК нового владельца.

Ozon:

- отдельный `.sessions/ozon/chrome-profile`;
- отдельный `ozon_seller_storage_state.json`;
- отдельный CDP port, если проекты работают на одном VPS;
- `OZON_EXPECTED_STORE=Vital Shevron`.

WB:

- отдельный `.sessions/wb/browser-profile`;
- отдельный `wb_storage_state.json`;
- `WB_EXPECTED_SELLER` уточнить после входа.

После входа:

```bash
PYTHONPATH=src python3 -m takterra_agent.cli sessions status
PYTHONPATH=src python3 -m takterra_agent.cli status-preflight
```

Критерий готовности:

```text
overall_status: ok
stateExported: true
expected Ozon store found
expected WB seller found
```

### Этап 7. Первый набор функций Vital Shevron

Запускать в таком порядке:

1. `status-preflight`
2. `fetch-catalog`
3. `daily-morning-report --seller-v2`
4. `reviews-questions --limit 100`
5. `plan-ozon-elastic`
6. `plan-wb-actions-discounts --scheme ...`

Write-операции включать только после отдельного review:

```text
read-only -> dry-run -> review -> approved -> apply -> verify -> result
```

### Этап 8. Механизм обмена улучшениями между проектами

Чтобы проекты могли заимствовать новое друг у друга, нужен процесс:

1. В каждом проекте вести `CHANGELOG.md` или `data/planning/changes.md`.
2. Рекомендации вести в одинаковом формате
   `data/planning/recommendations_index.md`.
3. Все переносимые улучшения помечать:

```text
portable: yes/no
source_project: seller_takterra или seller_vital_shevron
risk: low/medium/high
requires_secrets: no
requires_lk: yes/no
```

4. Перед переносом улучшения делать:

```text
diff/review -> apply to target -> tests -> status-preflight -> update docs
```

5. Не переносить автоматически:

- API credentials;
- sessions;
- store-specific catalog;
- pending/approved packages;
- reports with business data.

## Что нужно согласовать

1. Название и путь нового проекта:

```text
/home/pavel/projects/seller_vital_shevron
```

2. Что именно отдавать Vital Shevron:

- только код и sanitized docs;
- или еще примеры отчетов без чувствительных данных.

3. Нужно ли сразу включать git для обоих проектов.

4. Переименовываем ли Python package сразу или оставляем временно
   `takterra_agent` ради быстрого запуска.

5. Кто будет владельцем секретов Vital Shevron и как они попадут на VPS.

## Моя рекомендация

Идти так:

1. Сначала сделать transfer manifest.
2. Потом создать чистый проект `/home/pavel/projects/seller_vital_shevron`.
3. Скопировать только каркас и sanitized docs.
4. Запустить тесты.
5. Подключить API Vital Shevron.
6. Собрать catalog.
7. Подключить ЛК.
8. После первого `status-preflight: ok` считать каркас переданным.

Это даст Vital Shevron самостоятельный старт и не нарушит рабочий контур
TAKTERRA.

## Статус реализации 2026-06-12

Выполнено:

- создан transfer manifest:
  `data/planning/vital_shevron_transfer_manifest.md`;
- создан самостоятельный проект:
  `/home/pavel/projects/seller_vital_shevron`;
- перенесены код, скрипты, тесты, deploy/systemd templates и sanitized docs;
- не переносились `.env`, `.sessions`, cookies, storage state, API-ключи,
  runtime catalogs, `data/runs`, `data/pending`, `data/approved`;
- добавлены Vital Shevron `AGENTS.md`, `README.md`, `.env.example`,
  `data/planning/*`;
- добавлен catalog rule: все функции работают до унификации seller SKU через
  отдельные Ozon/WB каталоги, native IDs и optional mapping;
- Ozon CDP port нового проекта установлен в `9544`;
- systemd units переименованы в `vital-shevron-*`;
- локальный git создан, первый commit:
  `b79434a Initial Vital Shevron scaffold`;
- GitHub repository создан и подключен как `origin`;
- repository: `https://github.com/pavelvital2/seller_vital_shevron`;
- visibility: `PRIVATE`;
- latest pushed commit: `6394b1e Keep seller_vital_shevron repository name`.

API/catalog bootstrap выполнен 2026-06-12:

```text
Ozon Seller API: ok
Ozon Performance API: ok
WB API: ok
Ozon catalog rows: 548
WB catalog rows: 431
exact seller SKU matches: 98
mapping rows requiring owner review: 783
latest pushed commit: ae7b0e5 Record Vital API catalog bootstrap
```

Проверки:

```text
PYTHONPATH=src pytest -q -> 34 passed
PYTHONPATH=src python3 -m takterra_agent.cli --help -> ok
status-preflight --skip-lk без credentials -> ожидаемый error по credentials/catalog, без crash
```

GitHub:

- repository: `https://github.com/pavelvital2/seller_vital_shevron`;
- visibility: `PRIVATE`;
- branch: `main`;
- remote: `origin`;
- latest pushed commit: `6394b1e Keep seller_vital_shevron repository name`.
