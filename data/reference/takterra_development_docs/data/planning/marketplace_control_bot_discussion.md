# Обсуждение единого бота управления маркетплейсами TAKTERRA

Дата создания: 2026-06-10.

## Правило ведения

Все важные договоренности, идеи, архитектурные решения, открытые вопросы и
изменения направления по проекту единого бота фиксировать в этом файле.

Перед продолжением обсуждения или проектирования сначала читать этот файл, чтобы
не терять контекст и не повторять уже принятые решения.

Обновлять файл нужно после каждого содержательного обсуждения, если появились:

- новая идея;
- принятое решение;
- уточнение цели;
- ограничение;
- риск;
- открытый вопрос;
- изменение приоритета;
- новая сущность данных;
- новая команда бота;
- новая рекомендация по архитектуре.

Не записывать сюда секреты, токены, cookies, storage state, персональные коды
входа и содержимое закрытых файлов. Допустимо фиксировать только факт наличия
секретной зоны и правила работы с ней.

## Подключение к ЛК Ozon и WB

2026-06-10 следующий шаг определен как подключение к личным кабинетам Ozon и WB.
Старые проекты показывают, что подключение нужно строить через persistent
браузерные профили и отдельные инструкции, ссылки на которые лежат в корневых
`AGENTS.md`:

- Ozon: `SESSION.md` и `LOGIN_INSTRUCTION.md` в проектах `seller_ozon` и
  `seller_ozon_vitalsewing`;
- WB: `00_raw_snapshots/wb-persistent-browser.md`, session-refresh и preflight в
  проекте `seller_wb`.

Для TAKTERRA создана постоянная инструкция:

```text
data/planning/lk_connection_runbook.md
```

Принятое правило: из старых проектов переносится только модель подключения,
скриптовой подход и список проверенных URL. Cookies, токены, storage state и
браузерные профили не переносятся.

Целевой контур TAKTERRA:

```text
.sessions/ozon/chrome-profile/
.sessions/ozon/ozon_seller_storage_state.json
.sessions/wb/browser-profile/
.sessions/wb/wb_storage_state.json
```

Эти файлы должны быть локальными, секретными и исключенными из git.

Рекомендация по следующему техническому шагу: до фактического логина создать
адаптированные session-скрипты и единый `lk_preflight.py`, чтобы вход в ЛК сразу
проверял видимый магазин TAKTERRA, доступность основных URL и отсутствие
утечки секретов в логи.

Уточнение владельца: перед подготовкой подключения агент обязан перечитать
корневой `AGENTS.md`. Сессия должна иметь автоматический вход/refresh по таймеру
и автоматически обновлять cookies/state при каждом успешном входе или импорте
cookies.

Реализация в TAKTERRA:

```text
scripts/sessions/ozon_seller_interactive_login.js
scripts/sessions/ozon_import_cookies_check.js
scripts/sessions/ozon_session_refresh.sh
scripts/sessions/start_ozon_session_watchdog.sh
scripts/sessions/stop_ozon_session_watchdog.sh
```

Ограничение: если Ozon требует новый OTP/SMS/email-код, полностью автоматический
вход невозможен без свежего кода владельца. Автоматизируется все до точки
запроса кода, а после успешного входа или cookie-импорта state обновляется
автоматически.

Первый запуск Ozon TAKTERRA через новый профиль показал защитную страницу
`Похоже, нет соединения` с `__rr=1` до ввода почты. Это штатный cookie-сценарий:
OTP не повторять, ждать свежие cookies владельца, импортировать только через
`tmp/auth/` и после успешного импорта удалить временный cookie-файл.

Первая попытка cookie-импорта сохранена в отчете:

```text
data/runs/2026-06-10/lk_connection_ozon_20260610T165258Z/summary.md
```

Результат: cookies позволили странице распознать TAKTERRA в данных, но Ozon
продолжил отдавать `__rr=1` / `Похоже, нет соединения` на dashboard, analytics,
products и prices. Сессию не считать подключенной. Временный cookie-файл и
нерабочий локальный профиль удалены. Нужны cookies из браузера, где dashboard
Ozon Seller уже открыт без `__rr=1`.

Вторая попытка cookie-импорта сохранена:

```text
data/runs/2026-06-10/lk_connection_ozon_20260610T165908Z/summary.md
```

Импортёр доработан под формат нескольких JSON-документов подряд, но Ozon снова
вернул `__rr=1`. Следующий cookie-файл лучше брать как полный request header
`Cookie` из Network-запроса успешной вкладки dashboard, а не как отдельные
cookie-объекты.

Третья попытка cookie-импорта:

```text
data/runs/2026-06-10/lk_connection_ozon_20260610T170741Z/summary.md
```

Файл был цельным JSON с 19 парами, импортёр дополнительно доработан под raw
`Cookie:` header, но результат не изменился: новый чистый профиль TAKTERRA
получает `__rr=1`. Cookie-only сценарий считать исчерпанным для текущей сессии.
Следующий рабочий вариант - создать полноценный долгоживущий TAKTERRA profile
через ручной вход в этом профиле или получить явное разрешение владельца на
перенос/использование уже живого старого persistent profile.

После сравнения с другим агентом выявлено ключевое отличие: другой агент входит
через уже живой старый CDP `127.0.0.1:9222` проекта `seller_ozon`, где работает
полный persistent profile и watchdog для магазина `Vital Shevron`. Это не
cookie-only импорт в новый профиль TAKTERRA. Новый профиль TAKTERRA получает
`__rr=1`, потому что ему не хватает полного прогретого браузерного состояния.

Решение для TAKTERRA нужно выбрать явно:

```text
1. Создать собственный долгоживущий TAKTERRA keeper/profile и один раз пройти
   полноценный вход в нем.
2. Временно использовать старый живой CDP только как read-only источник опыта,
   не копируя секреты.
3. Переносить профиль/сессию из старого проекта только при отдельном явном
   разрешении владельца, потому что это перенос секретов между проектами.
```

## Цель проекта

Подготовить и реализовать единый бот управления двумя магазинами TAKTERRA:

- Ozon;
- Wildberries.

Продукция единая для обоих маркетплейсов. Внутренние артикулы должны быть
общими и использоваться как основа единого каталога.

## Исходные проекты

Использовать как источники опыта и материалов:

- `/home/pavel/projects/seller_wb` - ранний/рабочий проект по WB, источник
  практики по акциям, скидкам, ценам, API/ЛК и preflight.
- `/home/pavel/projects/seller_ozon` - старый/боевой проект Ozon, источник
  реальных Ozon-процессов: эластичный бустинг, CPC, карточки, отзывы,
  parser/analytics, drift-check и apply/verify.
- `/home/pavel/projects/seller_ozon_vitalsewing` - более зрелый Ozon-проект,
  источник архитектурной модели: `src/`, `tasks/`, `data/12_apply`,
  `data/14_workflows`, safety guard, handoff, структура и концепция dispatcher.

Рабочий отчет по анализу этих проектов:

```text
data/reports/2026-06-10/seller_ozon_vitalsewing_and_seller_wb_analysis.md
```

## Базовая архитектурная идея

Не склеивать старые проекты физически и не копировать их raw-данные в новый
проект.

Правильная модель:

```text
seller_ozon/                  read-only источник Ozon-опыта
seller_ozon_vitalsewing/      источник зрелой структуры и safety-подхода
seller_wb/                    read-only источник WB-опыта
seller_takterra/              новый управляющий проект TAKTERRA
```

`seller_takterra` должен стать центром управления, а старые проекты - источниками
знаний, формул, edge cases и проверенных workflow.

## Центральная сущность

Основой должен быть единый master catalog TAKTERRA.

Минимальная модель:

```text
master_sku
title
product_group
pack_qty
supplier_sku
ozon_offer_id
ozon_product_id
ozon_sku
ozon_barcode
wb_vendor_code
wb_nm_id
wb_barcode
status_ozon
status_wb
```

Все дальнейшие задачи должны по возможности сходиться к `master_sku`, а не жить
только в терминах Ozon `offer_id/product_id/sku` или WB `vendorCode/nmID`.

Уточнение от владельца: артикул продавца должен быть одинаковым на Ozon и
Wildberries.

Целевое правило:

```text
master_sku == Ozon offer_id == WB vendorCode
```

Ручной mapping допустим только как диагностический или переходный инструмент,
если фактические данные API временно не соответствуют этому правилу. Целевую
архитектуру нужно строить вокруг одинакового артикула продавца.

## Бот как диспетчер

Бот не должен быть монолитом с бизнес-логикой внутри.

Целевая модель:

```text
Telegram / chat interface
  -> dispatcher
  -> task registry
  -> approval guard
  -> lock manager
  -> marketplace adapter
  -> task result/report
```

Бизнес-логика Ozon и WB должна жить в адаптерах и task-модулях, а бот должен:

- принимать команду;
- понимать магазин и маркетплейс;
- проверять риск;
- проверять необходимость подтверждения;
- ставить lock на опасный ресурс;
- запускать зарегистрированную задачу;
- отправлять владельцу отчет;
- сохранять task result.

## Этапность внедрения

На первом этапе в рабочей папке `seller_takterra` живет агент. Агент сам
исследует проекты, готовит скрипты, запускает их, проверяет результаты и
обновляет рабочие файлы обсуждения/планирования.

На этом этапе бот не должен сразу получать много кнопок. Сначала каждая операция
должна быть отработана как понятный сценарий:

```text
идея операции
  -> скрипт/task-runner в seller_takterra
  -> read-only проверка
  -> dry-run отчет
  -> ручное подтверждение
  -> apply при необходимости
  -> verify
  -> сохраненный результат
```

Только после того как сценарий стабилен, его можно привязывать к боту:

```text
кнопка бота
  -> зарегистрированная задача
  -> подготовка dry-run
  -> показ владельцу
  -> approve/reject
  -> выполнение
  -> отчет в чат
```

Иными словами, бот должен становиться интерфейсом к уже проверенным операциям, а
не местом, где впервые появляется непроверенная бизнес-логика.

Примеры будущих кнопок/операций:

- Ozon: эластичный бустинг;
- Ozon: акции;
- Ozon: ответы на отзывы;
- Ozon: ответы на вопросы;
- Wildberries: акции;
- Wildberries: скидки/цены;
- Wildberries: ответы на отзывы;
- Wildberries: ответы на вопросы.

## Safety-контур

Для опасных операций обязательна цепочка:

```text
read-only -> dry-run -> pending -> approved -> apply -> verify -> result
```

Опасные операции:

- цены;
- скидки;
- акции;
- рекламные ставки и бюджеты;
- карточки товаров;
- фото;
- ответы покупателям;
- поставки;
- остатки;
- любые действия с финансовыми последствиями.

Apply должен быть невозможен без approved-файла или явного подтверждения
владельца.

## Первичный MVP бота

Первые команды для обсуждения:

```text
/status
/status ozon
/status wb

/catalog check
/stocks all

/reviews ozon dry_run
/reviews wb dry_run

/actions ozon dry_run
/actions wb dry_run

/prices ozon dry_run
/prices wb dry_run

/task_status TASK_RUN_ID
/approve TASK_RUN_ID
/reject TASK_RUN_ID
```

Apply-команды не делать короткими и легкими для случайного запуска. Для опасных
действий бот должен показывать краткое резюме:

```text
что меняем
сколько строк
какой риск
где pending/approved файл
какой payload
как проверим результат
```

## Рекомендованный следующий шаг

Правильнее начать не с Telegram-кнопок и не с массового переноса старых скриптов,
а с одного безопасного вертикального сценария в `seller_takterra`.

Первый сценарий: единый каталог и read-only проверка соответствия товаров между
Ozon и Wildberries.

Почему именно это:

- продукция единая для обоих маркетплейсов;
- внутренние артикулы должны стать центром всей системы;
- без надежной связки `master_sku -> Ozon -> WB` опасно автоматизировать цены,
  акции, рекламу, остатки и ответы;
- read-only сценарий не меняет данные на маркетплейсах и безопасен для первого
  запуска;
- результат сразу полезен: покажет дубли, пропуски, несовпадения артикулов,
  баркодов, статусов и карточек.

Практический следующий шаг:

```text
1. Создать в seller_takterra каркас agent/task-runner.
2. Описать схему master catalog.
3. Собрать первичный каталог из доступных источников Ozon/WB.
4. Сделать read-only задачу catalog_check.
5. Сохранить отчет: что совпало, что не найдено, где нужны ручные решения.
6. Только после этого переходить к первой бизнес-операции: акции, цены,
   эластичный бустинг или отзывы.
```

Первый будущий интерфейс к этому сценарию:

```text
/catalog check
```

На агентском этапе это должна быть не кнопка, а запускаемый сценарий, который
создает понятный отчет в рабочей папке.

## Первый API-запуск каталога

2026-06-10 получены API-доступы для проверки Ozon Seller API, Ozon Performance
API и Wildberries API. Сами ключи не записывать в этот файл, код, отчеты или
репозиторий. Использовать только переменные окружения или внешние файлы с
секретами.

В `seller_takterra` создан минимальный каркас agent/task-runner и выполнен первый
read-only сценарий:

```text
PYTHONPATH=src python3 -m takterra_agent.cli fetch-catalog
```

Первый Ozon-запуск был выполнен с Ozon Seller API-ключом от другого магазина и
дал снимок на 146 товаров. После уточнения владельца этот снимок удален из
рабочей папки как нерелевантный для TAKTERRA.

Удалены локальные артефакты старых запусков с Ozon-снимком 146 товаров:

```text
data/catalog/raw/2026-06-10/catalog_fetch_20260610T130510
data/runs/2026-06-10/catalog_fetch_20260610T130510
data/catalog/raw/2026-06-10/catalog_fetch_20260610T130625
data/runs/2026-06-10/catalog_fetch_20260610T130625
data/catalog/raw/2026-06-10/catalog_fetch_20260610T130741
data/runs/2026-06-10/catalog_fetch_20260610T130741
data/catalog/raw/2026-06-10/catalog_fetch_20260610T134012
data/runs/2026-06-10/catalog_fetch_20260610T134012
```

## Корректный API-запуск каталога TAKTERRA

После получения корректного Ozon Seller API-ключа выполнен новый read-only
`catalog_check`.

Последний актуальный запуск:

```text
run_id: catalog_fetch_20260610T135321
report: data/runs/2026-06-10/catalog_fetch_20260610T135321/catalog_check_report.md
summary: data/runs/2026-06-10/catalog_fetch_20260610T135321/summary.json
master_catalog_csv: data/catalog/processed/master_catalog.csv
master_catalog_json: data/catalog/processed/master_catalog.json
```

Результат:

```text
Ozon product list: 203
Ozon product info: 203
WB cards: 198
master catalog rows: 203
matched by exact SKU: 198
Ozon only: 5
WB only: 0
barcode mismatch: 0
Ozon platform barcode rows: 198
API errors: 0
```

Вывод: целевое правило `master_sku == Ozon offer_id == WB vendorCode`
подтверждено для всех 198 WB-карточек. Все товары WB найдены на Ozon по
одинаковому артикулу продавца.

Есть 5 товаров Ozon-only:

```text
chev_raz_pict0015
chev_raz_pict0023
chev_rusob_pict0001
chev_svo_pict0020
Patronash0001
```

Это нужно разобрать отдельно: товары есть на Ozon, но не найдены в WB API-снимке.
Уточнение владельца: эти 5 карточек просто еще не успели добавить на WB. Это не
ошибка сопоставления и не проблема единого артикула. Каталог считается
согласованным по 198 общим товарам, а 5 Ozon-only нужно учитывать как очередь
на добавление в WB.

Владелец подтвердил направление: эти 5 карточек можно создать на WB.

## Правила качества карточек Ozon/WB

Для одного и того же товара параметры должны быть максимально идентичны на Ozon и
Wildberries.

Обязательное правило:

- артикул продавца одинаковый: `master_sku == Ozon offer_id == WB vendorCode`;
- описание переносится с Ozon на WB без смысловых изменений;
- характеристики и параметры товара должны совпадать между маркетплейсами;
- заполняются все характеристики WB, которые применимы к товару;
- если характеристика WB не применима или нет источника значения, это явно
  фиксируется в dry-run и требует решения владельца перед apply;
- габариты и вес переводятся из Ozon в единицы WB без потери смысла;
- название WB берется из Ozon, но при ограничении длины сокращается по аналогии
  с существующими WB-карточками TAKTERRA;
- фотографии берутся с Ozon и загружаются на WB отдельным шагом после создания
  текстовой части карточки и появления `nmID`;
- apply создания/обновления карточки запрещен, если есть неподтвержденные
  расхождения в характеристиках, описании, размерах, цене или фото.

Практическое следствие: задача создания WB-карточек должна быть не просто
генератором минимального payload, а валидатором полноты и идентичности карточки.

Так как создание карточек WB - write-операция, применять ее можно только через
safety-контур:

```text
dry-run -> review -> approved -> generate WB barcodes -> create cards -> check errors -> upload media -> verify
```

Официальные методы WB для будущего apply:

```text
POST /content/v2/cards/upload      создание отдельных карточек
POST /content/v2/cards/upload/add  добавление карточек в существующую группу
POST /content/v2/barcodes          генерация WB skus/barcodes
POST /content/v2/cards/error/list  проверка ошибок создания
POST /content/v3/media/save        загрузка медиа по ссылкам
```

Подготовлен dry-run создания карточек WB:

```text
run_id: wb_card_create_plan_20260610T140152
report: data/runs/2026-06-10/wb_card_create_plan_20260610T140152/wb_card_create_dry_run.md
plan_json: data/runs/2026-06-10/wb_card_create_plan_20260610T140152/wb_card_create_plan.json
upload_payload_draft: data/runs/2026-06-10/wb_card_create_plan_20260610T140152/wb_cards_upload_payload_draft.json
upload_add_payload_draft: data/runs/2026-06-10/wb_card_create_plan_20260610T140152/wb_cards_upload_add_payload_draft.json
queue: data/catalog/wb_onboarding/ozon_only_to_add_to_wb.csv
```

Dry-run результат:

```text
5 карточек в плане
3 high-confidence upload_add
2 upload с ручной проверкой subject/category
apply не выполнялся
```

После уточнения правила качества карточек подготовлен новый strict dry-run.
Затем владелец уточнил, что `Patronash0001` - это категория/предмет `Аксессуары
для оружия`, патронташ. Dry-run обновлен с правильным WB subject:

```text
run_id: wb_card_create_plan_20260610T143938
report: data/runs/2026-06-10/wb_card_create_plan_20260610T143938/wb_card_create_dry_run.md
plan_json: data/runs/2026-06-10/wb_card_create_plan_20260610T143938/wb_card_create_plan.json
media_upload_plan: data/runs/2026-06-10/wb_card_create_plan_20260610T143938/wb_media_upload_plan.json
queue: data/catalog/wb_onboarding/ozon_only_to_add_to_wb.csv
```

Strict dry-run результат:

```text
5 карточек в плане
3 upload_add
2 upload
5 media upload plans from Ozon photos
0 shortened WB titles
apply не выполнялся
```

Из-за правила “заполнять все применимые характеристики” все 5 карточек теперь
имеют статус manual review: нужно заполнить или явно признать неприменимыми
оставшиеся WB-характеристики перед apply.

Заполнение в strict dry-run:

```text
chev_raz_pict0015   16 / 25 характеристик, 6 Ozon-фото
chev_raz_pict0023   16 / 25 характеристик, 6 Ozon-фото
chev_rusob_pict0001 16 / 25 характеристик, 6 Ozon-фото
chev_svo_pict0020   16 / 25 характеристик, 6 Ozon-фото
Patronash0001       19 / 26 характеристик, 5 Ozon-фото
```

Предварительная разбивка:

```text
chev_raz_pict0015   -> upload_add, subjectID 2367 Декор для одежды, imtID 1464304998
chev_raz_pict0023   -> upload_add, subjectID 2367 Декор для одежды, imtID 1464304998
chev_svo_pict0020   -> upload_add, subjectID 2367 Декор для одежды, imtID 1492532894
chev_rusob_pict0001 -> upload, subjectID 2367 Декор для одежды, manual review
Patronash0001       -> upload, subjectID 5517 Аксессуары для оружия, manual review
```

Перед apply нужно подтвердить:

- `chev_rusob_pict0001` создавать отдельной карточкой WB в `Декор для одежды`;
- `Patronash0001` создавать в WB-предмете `5517 Аксессуары для оружия`;
- цены в черновике: 1100 для шевронов, 1500 для патронташа;
- использовать Ozon-изображения как источник медиа для WB;
- для незаполненных характеристик WB: заполнить значение или явно отметить, что
  характеристика не применима.

Отдельно: Ozon по совпавшим товарам возвращает баркоды вида `OZN...`, а WB -
свои штрихкоды. Это помечается как `ozon_platform_barcode`, не как ошибка
сопоставления.

Следующий рекомендуемый шаг после корректного API-запуска:

```text
1. Доработать отчет `catalog_check`, чтобы он отдельно показывал matched,
   Ozon-only, WB-only и platform barcode rows.
2. Завести 5 Ozon-only товаров в список будущего добавления на WB.
3. После подтверждения каталога выбрать первую бизнес-операцию для dry-run:
   цены/скидки, акции WB/Ozon, эластичный бустинг Ozon или отзывы/вопросы.
```

## Файловая структура

Файловую структуру нужно готовить перед первым рабочим сценарием
`catalog_check`, то есть на ближайшем практическом шаге.

Важно не начинать с большой финальной структуры под полноценного Telegram-бота.
На первом этапе нужен минимальный каркас, который поддерживает работу агента,
task-runner, отчеты, безопасные pending/approved операции и будущую привязку к
кнопкам.

Предлагаемый стартовый каркас:

```text
seller_takterra/
  pyproject.toml
  README.md
  .env.example

  src/
    takterra_agent/
      __init__.py
      cli.py
      config.py
      logging.py

      catalog/
        schema.py
        loader.py
        checker.py

      marketplaces/
        ozon/
          adapter.py
        wb/
          adapter.py

      tasks/
        registry.py
        catalog_check.py

      safety/
        approvals.py
        locks.py
        dry_run.py

      reports/
        writer.py

      bot/
        dispatcher.py

  data/
    planning/
    reports/
    catalog/
    runs/
    pending/
    approved/
    locks/

  scripts/
    run_catalog_check.py

  tests/
```

Смысл структуры:

- `src/takterra_agent/` - код нового управляющего проекта;
- `catalog/` - единый каталог и связка `master_sku -> Ozon -> WB`;
- `marketplaces/` - адаптеры Ozon и WB без логики Telegram;
- `tasks/` - запускаемые сценарии, которые потом можно привязать к кнопкам;
- `safety/` - dry-run, approvals, locks;
- `reports/` - единый формат отчетов;
- `bot/` - будущий тонкий интерфейс к уже проверенным задачам;
- `data/catalog/` - рабочие файлы каталога без секретов;
- `data/runs/` - результаты запусков задач;
- `data/pending/` и `data/approved/` - файлы подтверждения опасных операций.

Решение по времени: структуру нужно подготовить до реализации `catalog_check`.
После этого первый сценарий должен сразу жить в правильном месте, а не как
разовый скрипт без будущей интеграции.

## Открытые вопросы

- Где хранить финальный master catalog: только в `seller_takterra` или отдельным
  будущим пакетом?
- Какая первая рабочая интеграция важнее: Ozon или WB?
- Бот сразу Telegram или сначала CLI/task-runner с отчетами? Предварительное
  решение: начинать с CLI/task-runner, Telegram-кнопки добавлять после
  стабилизации сценариев.
- Нужно ли создавать новый WB-проект `seller_wb_takterra`, или текущий
  `seller_wb` остается только read-only источником опыта?
- Какие внутренние артикулы TAKTERRA уже считаются каноническими?
- Какую первую бизнес-операцию брать после каталога: цены/скидки, акции,
  эластичный бустинг Ozon или отзывы/вопросы?
- Как оформить следующий слой проекта: task registry, approvals, locks,
  Telegram dispatcher или сначала еще один CLI-сценарий?

## Журнал обсуждений

### 2026-06-10

- Зафиксирована цель: подготовить план создания единого бота для управления
  двумя магазинами TAKTERRA на Ozon и Wildberries.
- Подтверждено, что продукция единая, внутренние артикулы совпадают между Ozon и
  WB и должны быть центром модели данных.
- Согласовано, что старые проекты используются как источники опыта, а не
  физически объединяются.
- Зафиксировано рабочее правило: все дальнейшие идеи и решения по этому проекту
  записывать и постоянно обновлять в этом файле.
- Согласована этапность: сначала агент в `seller_takterra` вручную запускает и
  проверяет сценарии, затем стабильные сценарии постепенно привязываются к
  командам и кнопкам бота.
- Зафиксирован рекомендуемый следующий шаг: начать с каркаса agent/task-runner,
  схемы `master_sku` и read-only сценария `catalog_check`, а не с кнопок бота и
  не с опасных операций.
- Зафиксировано, что файловую структуру нужно подготовить на ближайшем
  практическом шаге, до реализации `catalog_check`. Структура должна быть
  минимальной и ориентированной на agent/task-runner, отчеты, safety-контур и
  будущую привязку задач к боту.
- Получены API-доступы TAKTERRA без записи секретов в проектные документы.
- В `seller_takterra` создан минимальный каркас agent/task-runner и выполнен
  первый read-only сбор каталога через API Ozon/WB.
- Зафиксирован результат первого API-запуска с неверным Ozon-ключом: Ozon 146
  товаров, WB 198 карточек, прямых совпадений нет. После уточнения владельца
  этот снимок признан нерелевантным и удален из рабочей папки.
- Уточнено владельцем: артикул продавца должен быть одинаковым на Ozon и WB.
  Следовательно, результат `0 matched` является аномалией данных/кабинетов, а не
  нормальной архитектурной моделью. Цель: `master_sku == Ozon offer_id ==
  WB vendorCode`; mapping только временный инструмент.
- Уточнено владельцем: переданный Ozon Seller API-ключ оказался ключом от другого
  магазина, не от Ozon-магазина TAKTERRA. Предыдущий вывод о несовпадении
  Ozon/WB артикулов TAKTERRA отменяется. Нужно получить корректный Ozon-ключ и
  повторить `catalog_check`.
- Получен корректный Ozon Seller API-ключ TAKTERRA и выполнен новый
  `catalog_check`: Ozon 203 товара, WB 198 карточек, 198 совпадений по
  `Ozon offer_id == WB vendorCode`, 5 Ozon-only, 0 WB-only, ошибок API нет.
- Уточнено по баркодам: Ozon возвращает платформенные баркоды `OZN...`; это
  фиксируется как `ozon_platform_barcode`, а не как ошибка сопоставления.
- Уточнено владельцем: 5 Ozon-only карточек просто еще не успели добавить на WB.
  Это не ошибка каталога. Каталог считается согласованным по 198 общим товарам;
  5 товаров нужно вести как очередь добавления на WB.
- Подготовлен dry-run создания 5 WB-карточек. Apply не выполнялся. 3 карточки
  имеют high-confidence план `upload_add`, 2 требуют ручного подтверждения
  subject/category перед применением.
- Зафиксировано правило качества карточек: параметры, характеристики, описание,
  размеры и фото одного товара должны быть идентичны на Ozon и WB; все применимые
  WB-характеристики должны быть заполнены; название WB сокращается только при
  необходимости из-за ограничения длины.
- Подготовлен новый strict dry-run создания 5 WB-карточек с контролем полноты
  характеристик и отдельным планом загрузки Ozon-фото на WB. Apply не выполнялся.
- Уточнено владельцем: `Patronash0001` - это патронташ, категория/предмет
  `Аксессуары для оружия`. WB subjectID обновлен на `5517 Аксессуары для
  оружия`; предыдущий вариант `64 Пояса` больше не использовать.
- Зафиксировано рабочее правило приоритета: основная работа - создание проекта
  управления магазинами. Операции с самими магазинами выполняются как
  сопутствующие сценарии, на которых строится и проверяется проект, а не как
  одноразовые ручные действия.
- Выполнен apply создания/доведения 5 WB-карточек из очереди Ozon-only.
  Итоговый сводный отчет:
  `data/runs/2026-06-10/wb_card_create_completion_report.md`.
- Финальный контрольный `catalog_check` после создания карточек:
  `catalog_fetch_20260610T151249`. Результат: Ozon 203 товара, WB 203 карточки,
  203 совпадения по `master_sku == Ozon offer_id == WB vendorCode`, Ozon-only 0,
  WB-only 0, barcode mismatch 0.
- В ходе WB apply выявлены и зафиксированы правила для будущего task-runner:
  упаковочный вес `88952` нельзя передавать в `characteristics`; вес должен
  идти через `dimensions.weightBrutto` в килограммах. Упаковочные габариты и вес
  считаются заполненными через `dimensions`.
- WB отклоняет emoji в описаниях карточек. Для WB-текста запрещенные emoji нужно
  удалять, сохраняя смысл и переносы строк. Это допустимое отличие от Ozon,
  вызванное ограничением WB.
- WB может частично создать карточки даже при HTTP 400 по одному запросу.
  Поэтому apply-задача должна быть идемпотентной: перед повтором обязательно
  проверять точный `vendorCode`, список ошибок и корзину.
- Для полного снимка WB-каталога нужна стабильная пагинация
  `sort.ascending=true`; без нее общий экспорт может пропускать карточки, хотя
  точечный поиск по `vendorCode` их находит.
- Две карточки из очереди были найдены в корзине WB и восстановлены через
  `/content/v2/cards/recover`: `chev_raz_pict0015`, `chev_rusob_pict0001`.
  Это оформлено как часть сценария доведения каталога до состояния 203/203.
- Подготовлена инструкция/handoff для нового агента:
  `data/planning/new_agent_handoff_2026-06-10.md`. Ее нужно читать сразу после
  этого planning-файла перед продолжением работы.
- Подготовлена постоянная инструкция по загрузке карточек WB:
  `data/planning/wb_card_upload_runbook.md`. Для любых будущих операций создания
  или доведения WB-карточек использовать именно этот runbook.
- В корне проекта создан постоянный файл правил агентов `AGENTS.md`. В него
  вынесен начальный свод правил владельца, ссылки на постоянные инструкции,
  карту проекта и карты ЛК Ozon/WB.
- Созданы постоянные карты:
  `data/planning/project_map.md`,
  `data/planning/ozon_cabinet_map.md`,
  `data/planning/wb_cabinet_map.md`.
  При изменении структуры проекта или обнаружении новых ссылок/разделов ЛК эти
  карты нужно обновлять в том же рабочем цикле.
- По ЛК Ozon проверены три cookie-only попытки подключения TAKTERRA. Все они
  не стали рабочей сессией, поэтому как основной путь выбран полный persistent
  profile + CDP keeper.
- По явному разрешению владельца перенесен полный Ozon-профиль из read-only
  проекта `seller_ozon` в локальную секретную зону `seller_takterra`; старый
  проект не изменялся. После переноса выбран магазин TAKTERRA, session state
  обновлен.
- Зафиксирован нюанс Ozon `__rr=1`: сам query-параметр больше не считается
  блокировкой. Он логируется как `rrMode`, а блокировкой считается только
  сочетание с отсутствием нормальной оболочки кабинета или текстами ошибок.
- Создан и запущен Ozon keeper на CDP `127.0.0.1:9444`; создан и запущен
  watchdog refresh с интервалом 2700 секунд. Первый автоматический refresh
  подтвердил TAKTERRA на dashboard, analytics, products и prices.
- WB подключен через новый локальный persistent profile `seller_takterra` без
  переноса старой сессии. Вход выполнен по схеме старого проекта:
  `wb_auth_once_file_code.js`, SMS в той же форме, затем экспорт state.
- После входа WB сначала активировал продавца `ИП Витальская И. П.`; по команде
  владельца открыт переключатель продавца и выбран `ИП Рантусова`. После
  переключения state экспортирован, refresh по `seller.wildberries.ru` и
  `cmp.wildberries.ru` прошел успешно, активный продавец подтвержден как
  `ИП Рантусова`.
- По модели зрелого проекта
  `/home/pavel/projects/seller_ozon_vitalsewing/data/15_architecture_notes`
  создан постоянный раздел архитектурных заметок TAKTERRA:
  `data/15_architecture_notes/`. В него вынесены решения по product-first
  архитектуре, единому боту-диспетчеру, session-контуру ЛК и safety-цепочке.
- Зафиксирована дорожная карта движения к цели: сначала укрепить фундамент
  проекта и единый `status/preflight`, затем по одному переводить операции в
  task-runner через read-only/dry-run/approved/apply/verify, после стабилизации
  каждой операции подключать ее к Telegram-боту как кнопку или команду.
- Начата реализация ближайшего шага: добавлен сценарий task-runner
  `status-preflight`, который должен стать основой будущей команды `/status`.
  Сценарий проверяет Ozon API, WB API, master catalog и ЛК-сессии Ozon/WB без
  write-операций в маркетплейсах.
- Уточнение владельца: у Ozon отдельный API для продвижения. Значит
  `status-preflight` должен проверять не только Ozon Seller API, но и Ozon
  Performance API через `POST /api/client/token`, без сохранения и вывода
  Bearer-токена.
- Первый полный запуск `status-preflight`:
  `status_preflight_20260610T223753`. Результат: Ozon API ok, WB API ok,
  master catalog ok 203/203, Ozon keeper/watchdog/CDP ok, WB LK ok и активный
  продавец `ИП Рантусова`, но Ozon LK вернул `Login required`. До повторного
  входа в Ozon запрещены браузерные Ozon write-операции; API-only read-only
  Ozon-задачи допустимы.
- Создана постоянная инструкция:
  `data/planning/status_preflight_runbook.md`.
- После замечания владельца добавлена отдельная проверка Ozon Performance API.
  Новый полный запуск `status_preflight_20260610T224117`: Ozon Seller API ok,
  Ozon Performance API ok, WB API ok, master catalog ok 203/203, WB LK ok,
  Ozon keeper/watchdog/CDP ok, но Ozon LK still `Login required`.
- По вопросу владельца о таймере обновления сессий проверено фактическое
  состояние: в `seller_takterra` уже работали Ozon keeper и Ozon watchdog, но
  WB watchdog отсутствовал. Добавлены `start_wb_session_watchdog.sh` и
  `stop_wb_session_watchdog.sh`; `status-preflight` теперь проверяет
  `wb_watchdog_pid`.
- Интервалы таймеров: Ozon watchdog изменен на `1800` секунд / 30 минут, WB watchdog
  `3600` секунд / 60 минут. WB watchdog запущен, первый refresh прошел, полный
  preflight `status_preflight_20260610T224513` подтвердил `wb_watchdog_pid: ok`.
- По команде владельца восстановлена Ozon-сессия через вход по почте. Перед
  входом остановлены Ozon keeper/watchdog, после успешного `LOGIN_SUCCESS`
  TAKTERRA подтверждена и storage state экспортирован. Затем Ozon keeper и Ozon
  watchdog запущены обратно; watchdog работает с интервалом 1800 секунд.
- Контрольный `status-preflight` после восстановления:
  `status_preflight_20260610T225037`, `overall_status: ok`. Ozon Seller API,
  Ozon Performance API, WB API, master catalog, Ozon LK и WB LK проверены
  успешно.
- Следующий контроль согласован через 30 минут после восстановления Ozon: нужно
  повторить `status-preflight` и убедиться, что Ozon watchdog с интервалом
  1800 секунд реально удерживает сессию.
- Рекомендации по оптимизации после настройки сессий:
  1. Сделать единый session manager для команд `status/start/stop/restart`
     по Ozon/WB вместо набора отдельных скриптов.
  2. После проверки 30-минутного интервала перевести watchdog из ручных
     `nohup` bash-loop процессов в user-level `systemd` timers/services, чтобы
     они переживали перезапуск VPS и имели штатный статус.
  3. Добавить в `status-preflight` возраст последнего успешного keepalive и
     текущий интервал watchdog, чтобы сразу видеть просрочку refresh.
  4. Добавить lock/режим восстановления входа, который сам останавливает
     keeper/watchdog перед интерактивным логином и запускает их обратно после
     `LOGIN_SUCCESS`.
  5. Добавить автоматическую очистку старых `storage_state` backups и логов по
     retention-правилу, без удаления последнего рабочего состояния.
- План следующего рабочего шага после подтверждения стабильности сессий:
  укрепить session/status слой, затем выбрать первый прикладной read-only
  сценарий для будущей кнопки бота: отзывы/вопросы, акции/промо или цены.

## Первый прикладной сценарий: Ozon Elastic и WB акции

По команде владельца начат пункт 5 дорожной карты: прикладные сценарии
акции/промо и скидки через read-only/dry-run.

Выбраны два сценария:

- Ozon: `Эластичный бустинг. Без ограничения срока действия`;
- WB: активные акции и скидки по схеме `65-50-50`.

Реализованы задачи task-runner:

```text
PYTHONPATH=src python3 -m takterra_agent.cli plan-ozon-elastic
PYTHONPATH=src python3 -m takterra_agent.cli plan-wb-actions-discounts --scheme 65-50-50
```

Созданы постоянные инструкции:

```text
data/planning/ozon_elastic_runbook.md
data/planning/wb_actions_discounts_runbook.md
```

Ozon dry-run:

```text
run_id: ozon_elastic_plan_20260610T230610
action_id: 1977747
action_name: Эластичный бустинг. Без ограничения срока действия
active_rows: 203
candidate_rows: 0
update_action_price: 198
update_action_price_with_changed_price: 12
deactivate_from_action: 5
blocked: 0
apply_performed: false
```

WB dry-run:

```text
run_id: wb_actions_discount_plan_65-50-50_20260610T231441
scheme: 65-50-50
total_goods: 205
in_promos: 136
outside_promos: 69
multiple_promos: 136
to_change: 153
increase: 20
decrease: 133
no_change: 52
active_promos: 4
future_promos: 2
apply_performed: false
```

Внештатная ситуация WB: исходные Excel-файлы активных акций не были пустыми, но
выгрузка WB пришла с английскими заголовками и некорректной размерностью листа
для `openpyxl` в `read_only`-режиме. Фактически в 4 Excel-файлах было 49, 137,
137 и 137 непустых строк. Парсер дополнен поддержкой английских заголовков,
нормализацией `NBSP` и обычным чтением Excel вместо `read_only=True`.

Итоговые report Excel проверены:

```text
Ozon Elastic XLSX: 204 непустые строки с заголовком
WB 65-50-50 XLSX: 206 непустых строк с заголовком
```

Apply в Ozon и WB не выполнялся. Следующий шаг перед любым применением:
просмотреть dry-run отчеты и payload preview, затем явно подтвердить apply или
изменить правила расчета.

## Реестр рекомендаций

По вопросу владельца о том, как новый агент завтра найдет все рекомендации,
создан единый индекс:

```text
data/planning/recommendations_index.md
```

Принято правило: подробные рекомендации остаются в профильных документах
(`runbook`, архитектурные заметки, discussion), но каждая новая рекомендация по
оптимизации, автоматизации, архитектуре или работе с ЛК/маркетплейсами должна
добавляться в `recommendations_index.md` с коротким описанием, статусом, ссылкой
на детали и следующим шагом.

Для нового агента порядок такой:

```text
1. Прочитать AGENTS.md.
2. Если работа касается рекомендаций, открыть data/planning/recommendations_index.md.
3. По выбранной рекомендации перейти в профильный документ.
4. После решения владельца или реализации обновить статус в реестре.
```

## Завершение review по акциям

По команде владельца “Давай закончим с акциями” выполнен финальный review
dry-run артефактов Ozon Elastic и WB `65-50-50`.

Свежий preflight:

```text
run_id: status_preflight_20260610T232526
overall_status: ok
```

Создан единый review:

```text
data/runs/2026-06-10/actions_review_20260610T232748/actions_review.md
data/runs/2026-06-10/actions_review_20260610T232748/summary.json
```

Ключевые числа:

```text
Ozon Elastic:
- add/update rows in preview payload: 198
- rows with changed action price: 12
- deactivate no-stock rows: 5

WB 65-50-50:
- total goods: 205
- payload rows: 153
- increase discount rows: 20
- decrease discount rows: 133
- no change rows: 52
```

Владелец ответил “Согласен”. Это зафиксировано как согласие с review/pending
подходом, но не как автоматический запуск write-операций. Создан pending-пакет:

```text
data/pending/actions_apply_pending_20260610T232837/
```

Статус: `pending_explicit_apply_approval`. Перед apply обязательно выполнить
свежий `status-preflight`, свежий dry-run и drift-check против pending-пакета.
Apply в Ozon/WB остается запрещен до явной команды владельца с указанием, что
именно применять.

Дополнительная детализация WB:

```text
data/runs/2026-06-10/actions_review_20260610T232748/wb_detailed_review_65-50-50.md
```

При разборе WB обнаружено, что price snapshot содержит 205 уникальных
`vendorCode`, а свежий `fetch-catalog` `catalog_fetch_20260610T233201` снова
возвращает 203 WB-карточки. Две строки (`chev_kit2_mvd_pict0004`,
`nash_kit2_mvd_pict0004`) точечным WB Content API находятся как активные, но в
полном обходе WB-каталога и на Ozon не находятся. Для безопасного WB apply
создан guarded preview без этих двух строк:

```text
data/runs/2026-06-10/actions_review_20260610T232748/wb-upload-payload-preview-65-50-50-master-catalog-guard.json
```

Guarded payload: 151 строка вместо исходных 153. Правило для будущего apply:
строки WB price, которых нет в master catalog, по умолчанию исключать или
выносить на отдельное явное подтверждение владельца.

## Применение акций Ozon/WB

2026-06-10 владелец явно подтвердил применение акций Ozon и WB.

Запущен task-runner:

```bash
PYTHONPATH=src python3 -m takterra_agent.cli apply-actions \
  --pending-id actions_apply_pending_20260610T232837 \
  --confirmed-by-user
```

Результат:

```text
run_id: actions_apply_20260610T234323
overall_status: ok
```

Перед write выполнены:

```text
status_preflight_20260610T234323: ok
ozon_elastic_plan_20260610T234407: fresh dry-run
wb_actions_discount_plan_65-50-50_20260610T234410: fresh dry-run
drift-check against actions_apply_pending_20260610T232837: ok
```

Ozon Elastic:

```text
activate/update rows applied: 12
deactivate rows applied: 5
verify status: ok
rejected: none
```

WB `65-50-50`:

```text
guarded payload rows applied: 151
excluded rows: 2
upload_id: 138255979
http_status: 200
history status: 5
overAllGoodsNumber: 151
successGoodsNumber: 131
```

Постоянная инструкция создана:

```text
data/planning/actions_apply_runbook.md
```

## Укрепление session/status слоя

2026-06-11 после успешного apply выполнены пункты 1-4 технического плана по
сессиям:

1. Добавлен единый session manager:

```bash
PYTHONPATH=src python3 -m takterra_agent.cli sessions status
PYTHONPATH=src python3 -m takterra_agent.cli sessions start --marketplace ozon
PYTHONPATH=src python3 -m takterra_agent.cli sessions stop --marketplace wb
PYTHONPATH=src python3 -m takterra_agent.cli sessions restart --marketplace all
```

2. `status-preflight` теперь показывает возраст последнего keepalive:

```text
ozon_refresh_state.finished_at
ozon_refresh_state.age_seconds
ozon_refresh_state.interval_seconds
ozon_refresh_state.overdue
wb_refresh_state.finished_at
wb_refresh_state.age_seconds
wb_refresh_state.interval_seconds
wb_refresh_state.overdue
```

3. Добавлен сценарий восстановления Ozon:

```bash
PYTHONPATH=src python3 -m takterra_agent.cli restore-ozon-session --dry-run
PYTHONPATH=src python3 -m takterra_agent.cli restore-ozon-session --email <email>
```

Сценарий ставит lock, останавливает keeper/watchdog, запускает интерактивный
логин и после завершения поднимает контур обратно.

4. Сессии переведены с bash-loop watchdog на `systemd --user`:

```text
takterra-ozon-keeper.service
takterra-ozon-session-refresh.timer
takterra-wb-session-refresh.timer
```

Проверка после переключения:

```text
sessions_status_20260611T000502: overall_status ok, watchdog_source systemd
status_preflight_20260611T000512: overall_status ok
```

Текущие интервалы:

```text
Ozon: 30 минут
WB: 60 минут
```

Постоянная инструкция:

```text
data/planning/session_manager_runbook.md
```

## Ежедневный утренний отчет

2026-06-11 владелец предложил начать формирование ежедневного утреннего отчета.

Цель отчета: каждое утро давать короткую управленческую картину по двум
магазинам TAKTERRA на Ozon и WB, отделяя:

- состояние проекта и сессий;
- состояние каталогов;
- продажи/заказы;
- остатки и риски;
- акции/скидки/реклама;
- отзывы/вопросы;
- выполненные задачи агента;
- рекомендации и действия на сегодня.

Предлагаемый MVP v1 должен быть полностью read-only и строиться поверх уже
созданного task-runner/status слоя:

```text
1. Executive summary: ok/warning/error и 3-7 главных пунктов.
2. Project health: status-preflight, sessions status, systemd timers, возраст
   последнего refresh.
3. Catalog health: количество Ozon/WB/master catalog, matched/missing, новые
   расхождения SKU.
4. Marketplace operations: последние task-runner запуски, apply/dry-run,
   pending packages, ошибки.
5. Promo/actions: состояние последнего Ozon Elastic/WB actions workflow,
   активные риски и исключенные строки.
6. Today's action list: что требует решения владельца.
7. Artifact links: ссылки на JSON/Markdown/XLSX отчеты.
```

Расширение v2 после реализации новых read-only задач:

```text
- продажи за вчера/сегодня по Ozon/WB;
- новые/отмененные/проблемные заказы;
- остатки и out-of-stock/low-stock;
- товары с резким изменением цены/скидки;
- отзывы и вопросы без ответа;
- рекламные кампании, бюджеты, расход, статус Ozon Performance/WB promotion;
- финансовые показатели, если будет добавлена себестоимость.
```

Архитектурное решение: утренний отчет должен быть не ручным markdown, а
отдельной task-runner командой, например:

```bash
PYTHONPATH=src python3 -m takterra_agent.cli daily-morning-report
```

Она должна сохранять артефакты в:

```text
data/runs/YYYY-MM-DD/daily_morning_report_YYYYMMDDTHHMMSS/
```

И в дальнейшем стать первой регулярной командой Telegram-бота.

Реализация:

```text
src/takterra_agent/tasks/daily_morning_report.py
tests/test_daily_morning_report.py
data/planning/daily_morning_report_runbook.md
```

CLI:

```bash
PYTHONPATH=src python3 -m takterra_agent.cli daily-morning-report
PYTHONPATH=src python3 -m takterra_agent.cli daily-morning-report --skip-preflight-refresh
```

Сценарий read-only. Обычный запуск делает свежий `status-preflight`; быстрый
режим берет последний сохраненный preflight.

Следующее расширение: добавлять блоки продаж, заказов, остатков, отзывов и
рекламы отдельными read-only задачами.

## Ежедневный утренний отчет v2

2026-06-11 владелец уточнил, что v1 получился техническим отчетом: он нужен, но
не всегда интересен селлеру. Начат v2 как seller-first отчет.

Принятое решение:

```text
v1 = технический отчет проекта;
v2 = отчет для селлера, где сверху бизнес-показатели и действия,
     а технический preflight находится в приложении.
```

Реализация v2:

```text
PYTHONPATH=src python3 -m takterra_agent.cli daily-morning-report --seller-v2
```

Read-only источники v2:

- Ozon Seller API `POST /v1/analytics/data` - заказы/выручка за вчера и
  сегодня;
- Ozon Seller API `POST /v4/product/info/stocks` - остатки, нули и низкие
  остатки;
- WB Statistics API `GET /api/v1/supplier/orders` - заказы за вчера и сегодня;
- WB Statistics API `GET /api/v1/supplier/sales` - предварительные продажи и
  возвраты;
- WB Feedbacks API - счетчики неотвеченных отзывов и вопросов;
- локальные `actions_apply_*` - последнее применение акций;
- технический `status-preflight` - как приложение.

Ограничение и рекомендация: WB stocks сейчас подключен через legacy
`GET /api/v1/supplier/stocks`. Этот метод Wildberries отключает 2026-06-23,
поэтому его нужно заменить на warehouse remains report. Рекомендация внесена в
реестр как `REC-012`.

Полные строки заказов/продаж в артефакты v2 не сохраняются. Сохраняются только
агрегаты: суммы, количества, топы SKU и статусы источников.

Первый живой запуск:

```text
run_id: daily_morning_report_v2_20260611T072124
preflight: status_preflight_20260611T072124 ok
overall_status: warning
report: data/runs/2026-06-11/daily_morning_report_v2_20260611T072124/daily_morning_report_v2.md
```

Причины `warning`: WB sales вернул HTTP 429 rate limit, а WB stocks пока
использует legacy endpoint, который нужно заменить до 2026-06-23.

## Проверка акций 2026-06-11

По запросу владельца выполнена read-only проверка акций без apply:

```text
report: data/runs/2026-06-11/actions_check_20260611T082143/actions_check_report.md
ozon: ozon_elastic_plan_20260611T081950
wb: wb_actions_discount_plan_65-50-50_20260611T082143
```

Ozon Elastic:

```text
active_rows: 198
candidate_rows: 5
update_action_price_with_changed_price: 0
deactivate_from_action: 0
blocked: 0
skip_candidate: 5 no_stock
```

Вывод: Ozon Elastic стабилен, apply не нужен.

WB `65-50-50`:

```text
total_goods: 205
in_promos: 136
outside_promos: 69
to_change: 22
increase: 20
decrease: 2
no_change: 183
active_promos: 4
future_promos: 2
```

Интерпретация: схема `65-50-50` дала 22 строки к изменению. 20 товаров вне
активных акций имеют скидку `0` и по схеме должны получить `50`; 2 строки
`nash_kit2_mvd_pict0004` и `chev_kit2_mvd_pict0004` остаются WB-only
конфликтом. В review/pending нужно показывать все 22 строки как результат схемы,
а две конфликтные строки помечать `blocked_by_master_catalog_guard` и применять
только после отдельного решения владельца или исправления master catalog.

Во время проверки был edge case: первый WB keepalive не увидел маркер продавца
`ИП Рантусова`, повторный keepalive подтвердил продавца и экспортировал state.
Это зафиксировано в WB runbook, рекомендация по retry добавлена как `REC-013`.

## Apply WB 65-50-50 2026-06-11

Владелец уточнил правило: если строка есть в актуальных файлах акций/ценах WB
ЛК, она релевантна для WB-схемы. Master catalog в этой операции не должен
блокировать применение схемы; расхождение каталога разбирается после apply.

Выполнено:

```text
preflight: status_preflight_20260611T091655 ok
fresh dry-run: wb_actions_discount_plan_65-50-50_20260611T092255
apply: wb_actions_discount_apply_65-50-50_20260611T092528
```

Upload отправлен на все 22 строки схемы:

```text
upload_id: 138350059
overAllGoodsNumber: 22
successGoodsNumber: 2
```

Успешно применились 2 строки `54 -> 53`:

- `nash_kit2_mvd_pict0004`;
- `chev_kit2_mvd_pict0004`.

20 строк `0 -> 50` WB отклонил ограничением резкого снижения цены:

```text
Changes weren't saved: New prices are more than twice lower than the current ones.
Please lower them gradually
```

Попытка recovery `0 -> 49`:

```text
run_id: wb_actions_discount_apply_recovery_65-50-50_20260611T092644
upload_id: 138350340
overAllGoodsNumber: 20
successGoodsNumber: 0
```

WB отклонил шаг `49` и перевел товары в Price Quarantine:

```text
New price is several times lower than the current price. Item has been moved to Price Quarantine
```

Дальнейшие upload по этим 20 строкам остановлены до разбора Price Quarantine.

## WB Price Quarantine recovery 2026-06-11

Владелец потребовал вернуть 20 WB-товаров из карантина и добавил постоянное
правило: если операцию можно выполнить и через API, и через ЛК, приоритет у API;
ЛК использовать только если API не дает нужного метода или не позволяет
завершить конкретную внештатную ситуацию. Правило внесено в `AGENTS.md`.

API-first проверка:

```text
официальная документация: https://dev.wildberries.ru/en/docs/openapi/work-with-products
GET /api/v2/quarantine/goods - есть, возвращает товары в карантине
POST /api/v2/upload/task - есть, меняет цену/скидку
direct release API - не найден
```

Попытка через официальный API восстановить старую цену и скидку `0`:

```text
run_id: wb_price_quarantine_recovery_20260611T130806
result: HTTP 400
error: Specified prices and discounts are already set
remaining target rows: 20
```

После этого выполнен переход в ЛК WB, так как официальный API не вывел товары из
карантина. В ЛК найден раздел:

```text
https://seller.wildberries.ru/discount-and-prices/quarantine
```

Фронт ЛК показывает две операции:

```text
Keep Current Price - отменить quarantined-изменение и оставить текущую цену
Apply New Price - применить новую quarantined-цену/скидку
```

Для возврата товаров выбран `Keep Current Price`, который соответствует
внутреннему LK endpoint:

```text
POST /ns/dp-api/discounts-prices/suppliers/api/v1/quarantine/goods/delete
```

Результат:

```text
run_id: wb_price_quarantine_lk_release_20260611T101601Z
before_count: 20
payload internal ids: 20
release_http_status: 200
release_error: false
after_count: 0
visual reload: No items in Quarantine
overall_status: ok
```

Постоянная инструкция создана:

```text
data/planning/wb_price_quarantine_runbook.md
```

Во время работы обнаружен технический риск: `status-preflight` упал из-за
отсутствующего файла WB API-токена по временному пути Telegram attachment.
Сессия WB ЛК при этом жива, keepalive подтвердил `ИП Рантусова`. Рекомендация
`REC-015`: перенести путь WB API-токена в постоянную локальную секретную зону и
обновить env/file-env без сохранения секрета в проект.

## Исправление WB Price Quarantine 2026-06-11

Владелец указал, что предыдущий вывод из карантина сделан неверно: нужно было
оставить скидку по схеме `50%`, а `Keep Current Price` отменил quarantined-
изменение и вернул скидку к `0%`.

Прямой corrective upload `50%`:

```text
run_id: wb_quarantine_apply_new_price_50_20260611T103532Z
upload_id: 138406114
final_unique_discounts: 0
final_quarantine_rows: 0
overall_status: warning
```

WB принял upload task, но по деталям строки остались в статусе `Error`, скидка
не изменилась и карантин не появился.

Рабочая корректирующая последовательность:

```text
run_id: wb_discount_stage_49_apply_then_50_20260611T104150Z
initial_unique_discounts: 0
upload49_id: 138407420
wait49_state: quarantine
Apply New Price 49: HTTP 200
wait49_applied_state: applied
upload50_id: 138407499
wait50_state: applied
final_unique_discounts: 50
final_quarantine_count: 0
overall_status: ok
```

Правило уточнено во всех профильных инструкциях:

- `Keep Current Price` отменяет новое изменение;
- `Apply New Price` сохраняет новую цену/скидку;
- для схемы `65-50-50` и требования оставить скидку использовать `Apply New
  Price`;
- для автоматизации карантина нужен явный параметр намерения:
  `cancel_change` или `apply_new_price`.

Документация WB Partners по карантину цен:

```text
https://seller.wildberries.ru/instructions/ru/ru/material/price-quarantine
обновлено: 18.05.2026
```

Из документации: стандартный порог карантина - снижение цены на `33,3%`
(`1,5` раза); для категорий можно выбрать пороги до `47,5%`; снижение на `50%`
и больше нужно делать поэтапно. Поэтому для товаров с текущей скидкой `0%` и
целевой скидкой `50%` в схеме `65-50-50` зафиксировано правило двух этапов:

```text
0 -> 49 -> Apply New Price при карантине -> 50
```

## Отзывы и вопросы 2026-06-11

По команде владельца следующий технический этап по WB staged discounts
зафиксирован в `data/planning/recommendations_index.md` как `REC-017`, после
чего начат сценарий ответов на отзывы и вопросы.

Создан штатный read-only task-runner:

```text
PYTHONPATH=src python3 -m takterra_agent.cli reviews-questions --limit 100
```

Реализовано:

- `src/takterra_agent/tasks/reviews_questions.py` - единый read-only сценарий;
- `scripts/reviews/ozon_reviews_questions_readonly_cdp.js` - Ozon LK/CDP
  fallback;
- `src/takterra_agent/marketplaces/wb/communications_adapter.py` - добавлены
  списки WB отзывов/вопросов, не только счетчики;
- `src/takterra_agent/config.py` - отсутствующий optional `TAKTERRA_WB_TOKEN_FILE`
  больше не роняет весь `load_credentials()`, а возвращает `None` для WB;
- `tests/test_reviews_questions.py` - тесты нормализации и config guard;
- `data/planning/reviews_questions_runbook.md` - постоянная инструкция.

Источники:

```text
WB official docs:
https://dev.wildberries.ru/en/docs/openapi/user-communication

Ozon Seller API review methods checked by live read-only API requests:
POST /v1/review/count
POST /v1/review/list
```

Текущий ключ Ozon TAKTERRA вернул по обоим Review API методам:

```text
HTTP 403: not available with existing subscription
```

По правилу API-first это зафиксировано как причина перехода к ЛК/CDP fallback
для Ozon. Для WB fallback в ЛК не выполнялся, потому что официальный API
поддерживает нужные методы, но отсутствует текущий token file.

Первый запуск:

```text
run_id: reviews_questions_20260611T142015
overall_status: warning
items_count: 58
actions_count: 58
Ozon LK/CDP: ok
Ozon reviews: 58
Ozon questions: 0
WB API: missing_credentials
```

Состав pending-пакета:

```text
public_review_reply: 2
mark_review_viewed: 56
```

Файлы:

```text
data/runs/2026-06-11/reviews_questions_20260611T142015/reviews_questions_dry_run.md
data/pending/reviews_questions_20260611T142015_pending/APPROVAL_REQUIRED.md
data/pending/reviews_questions_20260611T142015_pending/draft_answers.json
data/pending/reviews_questions_20260611T142015_pending/draft_answers.csv
```

Ответы не опубликованы, отзывы не отмечены просмотренными.

Открытые решения владельца:

1. Публиковать ли 2 подготовленных Ozon-ответа.
2. Отмечать ли 56 пустых Ozon-оценок просмотренными.
3. Восстановить постоянный WB API token file для обработки WB отзывов/вопросов.
4. После согласования реализовать отдельный apply-сценарий, который принимает
   только approved-пакет и делает verify-снимок после отправки.

## Восстановление WB API-токена для отзывов/вопросов 2026-06-11

Владелец повторно передал файл WB API. Содержимое токена не выводилось.

Выполнено:

```text
source attachment: /home/pavel/projects/telegram-ai-agent/data/1781177597_AgADI6AAAh8AAUlJ_api_takterra_wb.txt
permanent secret file: .sessions/wb/wb_api_token.txt
permissions: 600
.env: TAKTERRA_WB_TOKEN_FILE -> permanent secret file
```

`REC-015` переведена в статус `implemented`: зависимость от временного Telegram
attachment path снята.

После восстановления выполнен WB read-only dry-run через официальный Feedbacks
API:

```text
run_id: reviews_questions_20260611T143549
overall_status: ok
WB API methods: feedbacks_count/questions_count/feedbacks_list/questions_list ok
WB reviews: 8
WB questions: 0
public_review_reply: 8
```

Файлы:

```text
data/runs/2026-06-11/reviews_questions_20260611T143549/reviews_questions_dry_run.md
data/pending/reviews_questions_20260611T143549_pending/APPROVAL_REQUIRED.md
```

Перед финальным pending-пакетом исправлен генератор черновиков: для `петлица` и
`нашивка` теперь используется женский род (`понравилась`, `подошла`). Добавлен
тест в `tests/test_reviews_questions.py`.

Ответы не опубликованы.

## Apply ответов на отзывы 2026-06-11

После согласования владельцем отправлены публичные ответы на отзывы.

Approved-пакет:

```text
data/approved/reviews_questions_apply_20260611T145000/approved_apply_plan.json
```

Команда:

```bash
PYTHONPATH=src python3 -m takterra_agent.cli apply-reviews-questions \
  --approved-path data/approved/reviews_questions_apply_20260611T145000/approved_apply_plan.json \
  --confirmed-by-user
```

Результат:

```text
run_id: reviews_questions_apply_20260611T154941
overall_status: ok
WB public replies: 8/8 через официальный WB Feedbacks API
Ozon public replies: 2/2 через LK/CDP fallback
Ozon NOT_VIEWED: 58 -> 56
Ozon PROCESSED: 65 -> 67
WB unanswered after verify: 0
```

Пустые Ozon-оценки в этот пакет не включались.

Файл результата:

```text
data/runs/2026-06-11/reviews_questions_apply_20260611T154941/reviews_questions_apply_result.md
```

## Отметка пустых Ozon-отзывов прочитанными 2026-06-11

По отдельной команде владельца "Отзывы на которые не нужны ответы отметь
прочитанными" создан и применен approved-пакет только для Ozon
`mark_review_viewed`.

Источник метода: старая инструкция `seller_ozon/OZON_REVIEWS_AGENT_GUIDE.md`
фиксирует LK endpoint:

```text
POST /api/v2/review/change-interaction-status
interaction_status: VIEWED
```

По правилу API-first официальный Ozon Review API для TAKTERRA уже был проверен
read-only запросами `/v1/review/count` и `/v1/review/list` и вернул `HTTP 403:
not available with existing subscription`; поэтому операция выполнена через
Ozon LK/CDP fallback.

Approved-пакет:

```text
data/approved/reviews_questions_mark_viewed_20260611T155509/approved_apply_plan.json
```

Результат:

```text
run_id: reviews_questions_apply_20260611T155516
overall_status: ok
Ozon marked viewed: 56/56
Ozon NOT_VIEWED: 56 -> 0
Ozon VIEWED: 176 -> 232
```

Файл результата:

```text
data/runs/2026-06-11/reviews_questions_apply_20260611T155516/reviews_questions_apply_result.md
```

Рекомендация: добавить CLI-команду подготовки approved-пакетов по pending:
`replies-only`, `mark-viewed-only`, `questions-only`, чтобы не собирать такие
JSON-пакеты вручную.

## Аудит структуры и автоматизации 2026-06-11

После добавления сессий, акций, карточек, утреннего отчета и отзывов/вопросов
выполнен аудит структуры проекта:

```text
data/planning/project_structure_optimization_review_2026-06-11.md
```

Фактические показатели:

```text
CLI commands: 13
source/scripts/tests LOC: 10754
largest task modules: daily_morning_report.py 976 строк, reviews_questions.py 969 строк
data/runs dirs total: 76
data/runs/2026-06-11 dirs: 35
.sessions size: 547M
git repo: нет .git
```

Главный вывод: проектная структура выбрана правильно, но следующий рост нужно
вести через стандартизацию workflow, task registry, run manifests,
pending/approved lifecycle и cleanup, а не через добавление новых крупных
монолитных task-файлов.

Новые рекомендации добавлены в `data/planning/recommendations_index.md` как
`REC-020` - `REC-029`. Приоритет на следующий технический этап:

```text
REC-020 RunManifest + data/runs/index.jsonl
REC-021 реальный task registry для CLI/бота
REC-022 lifecycle pending/approved/applied/verified/closed
REC-019 approved package builder для отзывов/вопросов
```

## Восстановление работоспособности проекта 2026-06-12

Выполнено восстановление проекта `/home/pavel/projects/seller_takterra`.

Итог:

```text
sessions_status_20260612T175227: overall_status ok
status_preflight_20260612T175227: overall_status ok
tests: 33 passed
```

Что восстановлено:

- Ozon Seller API и Ozon Performance API переведены с временных attachment
  paths на постоянные secret files в `.sessions/ozon/`; содержимое секретов в
  документы не записывалось.
- `.env` указывает на постоянные Ozon secret files.
- `src/takterra_agent/config.py` больше не роняет preflight, если Ozon file-env
  указан, но файл отсутствует: возвращается отсутствие credentials, а не
  исключение `FileNotFoundError`.
- JS-скрипты ЛК переведены на общий Playwright helper
  `scripts/lib/playwright.js`, который умеет находить доступный Google
  Chrome/Chromium executable.
- `restore-ozon-session` усилен: останавливает `systemd --user` Ozon-контур,
  запускает интерактивный вход через `xvfb-run -a` на сервере без `$DISPLAY`,
  безопасно очищает stale Chrome `Singleton*` только после проверки живых PID.
- Ozon LK восстановлен по почте: магазин `TAKTERRA` подтвержден,
  `stateExported true`, keeper/timer снова работают через `systemd --user`.
- WB LK и WB API подтверждены рабочими.

Контрольные факты:

```text
Ozon timer: 30 минут
WB timer: 60 минут
Ozon CDP: 127.0.0.1:9444 ok
master catalog: 203 rows, 203 matched rows, 0 ozon_only, 0 wb_only
```

Остаточный риск: в проекте нет `.git`, поэтому нет нормального контроля версий
кода и документов. Рекомендация REC-027 остается актуальной: перед включением
git нужно отдельно проверить `.gitignore` и исключение секретов/операционных
данных.

## План подготовки проекта к Vital Shevron 2026-06-12

Владелец поставил задачу подготовить проект к переносу и работе с другим
магазином `Vital Shevron` на Ozon и WB.

Уточнение владельца: у Vital Shevron другой владелец. Он будет самостоятельно
управлять своими магазинами Ozon/WB. Значит текущая задача - не добавить Vital
Shevron вторым магазином в live-контур TAKTERRA, а перенести рабочий каркас в
отдельный проект, очищенный от секретов, сессий и операционных данных.

Целевая схема:

```text
seller_takterra          - рабочий проект TAKTERRA
seller_vital_shevron     - отдельный проект Vital Shevron
shared scaffold/patches  - переносимые улучшения между проектами
```

План сохранен:

```text
data/planning/vital_shevron_migration_plan.md
```

Новая рекомендация:

```text
REC-032: самостоятельный проект Vital Shevron на базе рабочего каркаса TAKTERRA
REC-033: будущий мультиконтур / ContourProfile
```

Рекомендуемый первый этап:

1. Составить `vital_shevron_transfer_manifest.md`:
   `include/exclude/sanitize`.
2. Создать отдельную папку проекта, предварительно
   `/home/pavel/projects/seller_vital_shevron`.
3. Перенести только код, тесты, systemd/env-шаблоны и sanitized runbook/docs.
4. Не переносить `.env`, `.sessions`, `data/runs`, `data/catalog`,
   `data/pending`, `data/approved`, cookies, storage state, API-ключи.
5. Адаптировать `AGENTS.md`, `README.md`, `.env.example`, systemd unit names,
   expected Ozon/WB markers и карты проекта под Vital Shevron.
6. Запустить тесты и первый read-only `status-preflight --skip-lk`.

Информацию о мультиконтуре не удалять. Мультиконтур понадобится позже, но как
отдельный архитектурный слой после создания самостоятельного проекта Vital
Shevron. Будущий мультиконтур должен управлять несколькими контурами через
раздельные `contour_id`, `.env`, `.sessions`, ports, data roots, systemd units,
pending/approved packages и явный выбор контура в боте.

### Уточнение по каталогам Vital Shevron

Владелец уточнил: у Vital Shevron артикулы продавца на WB и Ozon сейчас не
совпадают.

Значит, в отличие от TAKTERRA, нельзя строить первый master catalog на
допущении:

```text
master_sku == Ozon offer_id == WB vendorCode
```

Для Vital Shevron стартовая схема должна быть другой:

1. Получить Ozon catalog отдельно.
2. Получить WB catalog отдельно.
3. Работать с ними как с двумя независимыми каталогами.
4. Параллельно построить `ozon_wb_product_mapping.csv`.
5. После review владельца использовать mapping в отчетах, акциях, ценах,
   отзывах/вопросах и других сценариях.
6. Отдельным будущим проектом подготовить унификацию артикулов продавца.
7. Менять артикулы продавца на Ozon/WB только через safety workflow:
   `read-only -> mapping draft -> review -> approved -> dry-run rename plan ->
   approval -> apply -> verify`.

Новая рекомендация:

```text
REC-034: Vital Shevron separate catalogs + mapping + future seller SKU unification
```

### Реализация переноса Vital Shevron

2026-06-12 создан самостоятельный каркас:

```text
/home/pavel/projects/seller_vital_shevron
```

Сделано:

- перенесены код, скрипты, тесты, systemd/env-шаблоны и sanitized документы;
- не переносились `.env`, `.sessions`, cookies, storage state, API-ключи,
  pending/approved packages и рабочие снимки каталогов TAKTERRA;
- `AGENTS.md`, `README.md`, `.env.example`, systemd unit names и session
  defaults адаптированы под Vital Shevron;
- Ozon CDP port для Vital Shevron: `9544`;
- `fetch-catalog` в проекте Vital пишет отдельные Ozon/WB каталоги;
- `status-preflight` в `SELLER_SKU_MODE=separate` не считает Ozon-only/WB-only
  строки ошибкой только из-за несовпадения seller SKU;
- локальный git-репозиторий создан, первый commit:
  `b79434a Initial Vital Shevron scaffold`;
- GitHub repository создан и подключен как `origin`;
- repository: `https://github.com/pavelvital2/seller_vital_shevron`;
- visibility: `PRIVATE`;
- latest pushed commit: `6394b1e Keep seller_vital_shevron repository name`.

### Vital Shevron API и первый каталог

2026-06-12 подключены API-файлы Vital Shevron через secret files в
`/home/pavel/projects/seller_vital_shevron/.sessions/`. Содержимое ключей в
документы и git не записывалось.

Read-only preflight:

```text
Ozon Seller API: ok
Ozon Performance API: ok
WB API: ok
```

Первый `fetch-catalog`:

```text
Ozon catalog rows: 548
WB catalog rows: 431
master catalog rows: 881
exact seller SKU matches: 98
Ozon-only rows: 450
WB-only rows: 333
barcode mismatch rows: 0
```

Mapping draft:

```text
data/catalog/mapping/ozon_wb_product_mapping.csv
rows: 881
needs owner review: 783
```

В Vital `.gitignore` добавлена защита от коммита mapping/unified business data.
Latest pushed commit Vital Shevron:

```text
ae7b0e5 Record Vital API catalog bootstrap
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
