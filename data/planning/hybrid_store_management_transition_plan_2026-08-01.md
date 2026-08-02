# Переход к гибридному управлению Vital Shevron

Дата: 2026-08-01

Статус: архитектура согласована владельцем; этапы 0-3 завершены. Первый пакет
этапа 4 (`Stage 4A read-only operational control`) реализован в отдельном
worktree и ожидает независимой приёмки архитектором. Commit, merge, deployment
и переход к следующему пакету пока запрещены.

Обновление от 2026-08-02: владелец согласовал отдельного нового Telegram-бота
с Mini App как целевой owner-facing интерфейс. Старый production-бот остается
рабочим до подтвержденного переноса функций. Это изменение затрагивает этапы 3
и 9, но не меняет единые `TaskRegistry`, `JobService`, runtime DB, Job Worker и
marketplace safety lifecycle.

## 1. Цель

Построить систему управления магазином, в которой:

- Telegram-бот является основным интерфейсом владельца;
- формализуемые операции выполняются детерминированным кодом;
- marketplace write проходит только через Job Worker и SafetyGuard;
- агенты используются только для анализа, стратегии, контента, исследования
  новых проблем и разработки;
- любое значимое изменение остается проверяемым от исходных данных до
  фактического результата;
- действующий магазин продолжает работать во время поэтапного перехода.

Целевая модель:

```text
Telegram bot / future Mini App
  -> TaskRegistry
  -> JobService / JobWorker
  -> WorkflowRunner / SafetyGuard
  -> Ozon/WB API or controlled LK bridge
  -> verify / RunManifest / Telegram report
```

Интеллектуальный контур:

```text
Owner or administrator
  -> analyst / card / technical agent
  -> structured package
  -> owner review in Telegram
  -> deterministic Job Worker task
```

## 2. Архитектурное решение

Выбрана модель `code-first + agents-on-demand + owner approval`.

Отвергнуты следующие варианты:

- один универсальный агент - из-за перегруженного контекста и смешивания ролей;
- множество постоянных marketplace-агентов - из-за пересечения API/LK,
  дублирования контекста и сложного восстановления;
- только код без агентов - из-за невозможности надежно решать неоднозначные,
  исследовательские и творческие задачи;
- прямое выполнение marketplace write агентами - из-за отсутствия
  детерминированности и разделения рекомендации, approval и применения.

## 3. Подтвержденная текущая база

Переход не требует переписывания проекта. Уже существуют:

- `TaskRegistry` с `executor`, risk/mode, parameter schema, lock keys,
  plan/apply/verify links и Telegram metadata;
- `JobStore`, `JobService`, `JobWorker` и `task_requests`;
- Telegram update deduplication и callback handling;
- approvals, checksums, resource leases и SafetyGuard;
- `WorkflowRunner` и `RunManifest`;
- read-only, dry-run, apply и verify задачи для части Ozon/WB workflows;
- Telegram-команды, кнопки, parameter flows, jobs, approvals и отчеты;
- Parser Data API, marketplace adapters и контролируемые LK-сценарии;
- тестовый контур для registry, jobs, safety, bot и бизнес-задач.

На дату плана команда `tasks policy` не показывает активных policy gaps.

## 4. Целевые роли

### 4.1. Администратор

- постоянные Telegram-топик и tmux-сессия;
- модель `gpt-5.6-sol`, reasoning `xhigh`;
- принимает неструктурированный запрос владельца;
- выбирает готовую кнопку либо формирует task package профильному агенту;
- управляет приоритетами и нестандартными зависимостями;
- проверяет наличие результата и незакрытого follow-up;
- не меняет код и не выполняет marketplace write.

Постоянными являются топик и tmux, а не бесконечный Codex transcript. Контекст
администратора должен контролируемо заменяться после handoff.

### 4.2. Аналитик

- свежая Codex-сессия под конкретную задачу;
- использует только read-only источники;
- интерпретирует данные, сравнивает сценарии и формирует гипотезы;
- возвращает `RecommendationPackage`;
- не имеет права выполнять apply или редактировать код.

### 4.3. Карточный агент

- свежая Codex-сессия под товар или согласованный пакет;
- готовит тексты, SEO, изображения, инфографику и `CardChangePackage`;
- не публикует изменения напрямую;
- owner-approved пакет применяет зарегистрированная задача Job Worker.

### 4.4. Технический агент

- создается под конкретное ТЗ или неизвестный технический сбой;
- единственный профильный агент с правом изменения кода;
- работает в отдельной ветке и Git worktree;
- использует mocks, fixtures и безопасные smokes;
- не принимает бизнес-решения и не выполняет marketplace write.

## 5. Неподвижные правила

1. Бот не содержит бизнес-логику.
2. Агент не является marketplace executor.
3. Единственный штатный write-путь:

```text
plan -> owner review -> approval -> apply -> verify -> close
```

4. Секреты остаются у сервисов и marketplace adapters, а не у агентов.
5. Для API используется Job Worker. Для LK используется контролируемый bridge
   с отдельным profile lease.
6. Telegram callback не вызывает write-логику напрямую, а создает job.
7. Повторная доставка Telegram update не должна повторять операцию.
8. Любая кнопка имеет TaskRegistry entry, runbook, тесты, result schema и
   понятный rollback/recovery.
9. Не заменять работающий workflow, пока новый маршрут не прошел shadow и
   production verification.
10. Пользователь остается источником approval для значимых write-операций.

## 6. Пошаговый план перехода

### Этап 0. Безопасная исходная точка

Исполнитель: администратор как контролер и временный технический агент.

Фактический baseline:

`data/planning/hybrid_transition_stage0_baseline_2026-08-01.md`

Действия:

1. Дождаться завершения активных jobs и marketplace apply.
2. Проверить pending/approved/applying approvals и resource leases.
3. Зафиксировать Git HEAD, ветку и все незакоммиченные изменения.
4. Сделать резервную копию `runtime/runtime.db` и проверить чтение копии.
5. Зафиксировать состояния systemd, Telegram polling, worker, timers, tmux и
   topic bindings.
6. Запустить текущие policy checks и тестовый baseline.
7. Запретить одновременные изменения действующего проекта двумя агентами.

Критерий готовности:

- нет неизвестных write-операций;
- все незавершенные approvals классифицированы;
- база и runtime-state восстанавливаются из резервной копии;
- baseline сохранен без изменения поведения магазина.

Откат: изменений production-логики на этапе нет.

### Этап 1. Инвентаризация функций и кнопок

Исполнитель: временный read-only аудитор по ТЗ архитектора.

Фактический принятый отчет:

`data/planning/hybrid_transition_stage1_capability_audit_2026-08-01.md`

Для каждой операции зафиксировать:

- бизнес-цель;
- marketplace и store scope;
- текущую команду/task ID;
- режим `read_only`, `dry_run`, `apply`, `verify`;
- `executor=script|agent|hybrid`;
- API/LK зависимость;
- параметры и result schema;
- наличие Telegram-кнопки;
- частоту использования;
- риск и обратимость;
- source plan, approval, verify и lock keys;
- известные пробелы и ручные действия.

Операции разделить на четыре группы:

```text
A. Уже полностью детерминированы и доступны по кнопке.
B. Детерминированы в CLI, но еще не имеют полного Telegram workflow.
C. Повторяются, но пока зависят от ручной работы агента.
D. Реально требуют анализа, творчества или исследования.
```

Артефакт: машинно-читаемая capability matrix и owner-facing Markdown-отчет.

Критерий готовности: для каждой действующей функции определен единственный
целевой исполнитель и отсутствуют бесхозные write-маршруты.

### Этап 2. Жесткие границы доступа

Исполнитель: технический агент в отдельном worktree.

Исполнимое P0-ТЗ этапа:

`data/planning/hybrid_transition_stage2_p0_execution_safety_tz_2026-08-01.md`

Действия:

1. Зафиксировать role/capability policy в коде и документации.
2. Оставить marketplace write credentials только сервисному runtime.
3. Аналитику дать только read-only gateways и безопасные exports.
4. Карточному агенту дать read-only данные и локальную область artifacts.
5. Техническому агенту не выдавать production marketplace credentials.
6. Все обращения к LK защищать marketplace/profile lease.
7. Добавить policy tests, запрещающие агентным task types прямой write.
8. Зафиксировать правило: найденная агентом повторяемая операция сначала
   становится ТЗ на автоматизацию, а не постоянной ручной обязанностью агента.

Критерий готовности:

- ни один agent task не может вызвать marketplace apply;
- каждый production write проходит через JobService/SafetyGuard;
- одновременный доступ к одному LK profile блокируется lease.

Откат: feature flag возвращает предыдущий dispatcher, credentials не
переносятся и не раскрываются.

### Этап 3. Новый Telegram control plane с Mini App

Исполнитель: Seller-технический агент в отдельном worktree проекта
`seller_vital_shevron`.

На этом этапе создается отдельный новый business bot и Mini App. Старый
production-бот не переписывается и продолжает работать до завершения
поэтапного rollout. Общий `/home/pavel/projects/telegram-ai-agent` для
Codex-топиков не изменяется.

Новый bot/Mini App является только дополнительным owner-facing интерфейсом над
существующим backend. Запрещено создавать для него отдельную бизнес-логику,
runtime DB, marketplace worker или копии marketplace credentials.

Целевые разделы:

```text
Главная и состояние
Продажи и аналитика
Цены и акции
Продвижение
Карточки
Остатки и поставки
Отзывы и вопросы
Задания и согласования
Состояние системы
Спросить администратора
```

Действия:

1. Развернуть отдельный новый bot token и systemd-сервис, не меняя старый bot
   token, polling state и service.
2. Создать mobile-first Mini App, открываемый из menu button нового бота.
3. Проверять `Telegram.WebApp.initData` на backend, применять owner allowlist и
   freshness/replay ограничения; не доверять `initDataUnsafe`.
4. Подключить Mini App к существующим `TaskRegistry`, `JobService` и runtime DB.
5. Любой запуск создавать как job и показывать его `job_id`.
6. Первым включить read-only экран `Аналитика магазина`.
7. Для write-сценариев показывать diff, риски, источник данных и срок
   актуальности плана.
8. Разделить действия `Подготовить`, `Согласовать`, `Применить`, `Проверить`.
9. Добавить экраны активных jobs, approvals, ошибок и результатов.
10. Переносить функции по одной; после production verification конкретного
    write-сценария отключать только соответствующий write-вход старого бота.
11. Использовать сообщения нового бота для уведомлений, отчетов и запросов
    owner approval.

Критерий готовности:

- кнопки не дублируют бизнес-логику CLI;
- Mini App и bot callback создают job, а не выполняют write напрямую;
- duplicate update не порождает повторный job/apply;
- владелец видит текущий статус и следующий разрешенный шаг.
- старый production-бот продолжает работать во время rollout;
- новый бот не имеет отдельного marketplace execution контура.

Откат: остановить только сервис нового бота/Mini App и продолжить работу через
старый production-бот; runtime DB и marketplace state не мигрируются назад.

Фактический Stage 3 завершён и развернут отдельным system-level service. Первый
production slice остаётся read-only и запускает только
`store-analytics-overview` через общий Job Worker. Технический и operator
контракт зафиксирован в:

`data/planning/stage3_control_plane_runbook.md`.

### Этап 4. Перенос повторяемых операций в код

Исполнитель: технический агент; приёмка - архитектор и владелец.

Первый ограниченный пакет этапа — `Stage 4A read-only operational control`.
Он не добавляет marketplace write и не начинает перенос dry-run/apply/verify:

- server-side allowlist содержит только `store-analytics-overview` и
  `daily-morning-report`;
- `Состояние`, `Задания` и `Согласования` получают только безопасные SQLite
  projections под действующей owner session;
- `daily-morning-report` создаётся как queued job и выполняется единственным
  существующим Worker;
- approval UI остаётся read-only без approve/reject/apply/verify controls.

ТЗ и runbook пакета:

- `data/planning/hybrid_transition_stage4a_readonly_control_tz_2026-08-02.md`;
- `data/planning/stage4a_readonly_control_runbook.md`.

Статус на 2026-08-02: реализация завершена в
`feat/stage4a-readonly-control-20260802`, offline gates выполняются; независимая
приёмка, commit, merge и deployment ещё не выполнялись.

Порядок приоритета:

1. Частые read-only отчеты и проверки.
2. Dry-run расчеты цен, маржи, акций, продвижения и поставок.
3. Уже существующие plan/apply/verify workflows без полного button flow.
4. Карточные approved packages.
5. Reviews/questions drafts и подтвержденная отправка.
6. Редкие ручные операции только после стабилизации частых сценариев.

Каждая новая автоматизация проходит:

```text
characterization of current behavior
-> deterministic contract
-> tests and negative tests
-> read-only/dry-run
-> Telegram preview
-> owner approval
-> bounded apply
-> independent verify
```

Критерий готовности отдельной операции:

- один и тот же ввод дает воспроизводимый план;
- план имеет checksum и TTL/freshness contract;
- drift перед apply блокирует применение;
- повторный callback не повторяет write;
- verify подтверждает внешнее состояние;
- результат сохраняется в RunManifest и приходит в Telegram.

### Этап 5. Структурированные пакеты агентов

Исполнитель: Seller-технический агент.

Добавить контракты:

```text
AdminTaskPackage
RecommendationPackage
CardChangePackage
TechnicalChangeRequest
```

Обязательные поля:

- `task_id`, роль и store/marketplace scope;
- цель и ограничения;
- источники и freshness;
- факты, допущения и неподтвержденные места;
- варианты и рекомендация;
- затрагиваемые сущности;
- ожидаемые метрики и дата проверки;
- требуемый зарегистрированный task ID;
- запрет или необходимость apply;
- критерии готовности.

Пакет агента не является approval и не может выполняться как shell-команда.
Сначала он проходит schema validation и преобразуется в параметры уже
зарегистрированной задачи.

Критерий готовности: свободный текст агента не может попасть в marketplace
adapter без registry validation, approval и SafetyGuard.

### Этап 6. Реестр решений, экспериментов и follow-ups

Исполнитель: отдельный Seller-исполнитель в worktree.

Действия:

1. Добавить структурированные business decisions/experiments в runtime DB.
2. Связать решение с package, source data, job, approval, RunManifest и verify.
3. Ввести однозначные статусы и уникальные follow-up IDs.
4. Выполнить сначала dry-run миграции `followups.md`.
5. Сохранить Markdown как owner-facing представление.
6. Добавить Telegram-экран: активные решения, даты контроля и результаты.

Критерий готовности: на вопрос «почему изменили, кто подтвердил и что
получилось» отвечает структурированный контур без поиска по transcript.

Откат: новая схема additive; старая Markdown-история остается неизменной до
подтвержденной миграции.

### Этап 7. Жизненный цикл агентных сессий

Исполнитель: отдельный Bot-исполнитель. Не объединять с миграцией runtime DB.

Действия:

1. Сохранить постоянный topic/tmux администратора.
2. Создавать свежие Codex-сессии аналитика, карточного и технического агента
   под конкретный `task_id`.
3. Передавать только минимальный `task context pack`.
4. После задачи сохранять artifacts и одно Hermes summary.
5. Добавить контролируемую ротацию администратора с handoff.
6. Проверить восстановление topic/tmux/model/reasoning после restart и reboot.
7. Изменять bot config только при остановленном боте, затем запускать и
   проверять все bindings.

Критерий готовности:

- новый агент продолжает задачу по package и registry без старого transcript;
- topic и tmux сохраняются;
- частичное восстановление не удаляет state bindings;
- агент не принимает себя за другую роль.

### Этап 8. Расписания и ограниченный автопилот

Исполнитель: технический агент после стабилизации ручных кнопок.

Сначала автоматизируются только read-only jobs:

- утренний отчет;
- состояние API/LK;
- остатки и поставки;
- позиции и видимость;
- контроль ранее примененных решений;
- просроченные approvals/follow-ups.

Write-autopilot по умолчанию выключен. Его можно рассматривать только для
конкретной операции после подтвержденной истории успешных ручных запусков и
отдельного согласования владельца. Обязательны лимиты, allowlist сущностей,
kill switch, rollback/reconciliation и уведомление.

Критерий готовности: сбой timer не выполняет write, а создает безопасное
уведомление или repair task.

### Этап 9. Расширение Mini App после MVP

Базовая Mini App теперь создается на этапе 3. На этом этапе добавляются только
расширенные сценарии, которые не нужны для первого production rollout:

- большие таблицы и фильтры;
- сравнение before/after;
- выбор множества товаров;
- редактирование пакета перед approval;
- графики и история экспериментов.

Mini App продолжает использовать тот же Task API и не получает отдельную
бизнес-логику.

### Этап 10. Пилот и штатный ввод

Порядок rollout:

1. Один read-only сценарий через новое меню.
2. Несколько read-only сценариев разных разделов.
3. Один уже доказанный low-blast-radius plan/apply/verify workflow.
4. Наблюдение за duplicate updates, leases, approvals и recovery.
5. Расширение на остальные стабилизированные операции.
6. Только после этого отключение старого маршрута.

Для первого write-пилота задача выбирается после capability audit. Нельзя
назначать пилотом новую или нестабильную marketplace операцию.

Критерий полного перехода:

- все частые формализуемые операции доступны через код/кнопки;
- агенты не выполняют routine marketplace work;
- все write идут через единый lifecycle;
- Telegram показывает активные jobs, approvals и результаты;
- role boundaries и session recovery проверены;
- старый маршрут отключен только после доказанной эквивалентности.

## 7. Порядок реализации по репозиториям

Не смешивать изменения двух контуров в одном PR.

### Зафиксированное распределение исполнителей

Архитектор и независимый контролер:

- текущий основной Codex-оркестратор;
- готовит отдельное ТЗ на каждый этап;
- не подменяет назначенного исполнителя;
- проверяет diff, тесты, миграции, recovery и результат.

Read-only аудитор этапов 0-1:

- отдельный свежий агент без права записи;
- фиксирует baseline, capability matrix и automation backlog;
- не меняет проект, runtime, API/LK и Telegram-конфигурацию.

Seller-технический исполнитель:

- роль `seller_vital_shevron_dev`;
- модель `gpt-5.6-sol`, reasoning `xhigh` для архитектурных этапов;
- отдельный topic/tmux технической роли и свежая Codex-сессия на этап;
- отдельные Git branch/worktree;
- изменяет только `/home/pavel/projects/seller_vital_shevron`;
- не выполняет marketplace business operations.

Codex Telegram runtime исполнитель:

- роль `telegram_ai_agent_dev`;
- отдельная ветка/worktree;
- изменяет только `/home/pavel/projects/telegram-ai-agent`;
- отвечает за role routing, свежие Codex-сессии, tmux/topic recovery;
- не изменяет бизнес-бот и marketplace workflows.

Основной `seller_vital_shevron`:

- после перехода становится администратором системы;
- не реализует переход и не меняет код;
- принимает готовый функционал на реальных сценариях;
- сохраняет topic/tmux, но получает контролируемо обновляемый контекст.

Владелец:

- согласовывает переход между этапами, merge/deployment и первый write-пилот;
- остается единственным источником approval для значимых marketplace write.

Обязательный порядок каждого этапа:

```text
TZ -> isolated worktree -> implementation -> focused tests
-> full tests -> independent audit -> owner approval
-> deployment -> production verification
```

Seller project `/home/pavel/projects/seller_vital_shevron`:

```text
capability matrix
role policies
TaskRegistry contracts
deterministic workflows
agent package schemas
decision/experiment registry
business tests
business Telegram menu and dispatcher
```

Codex Telegram runtime `/home/pavel/projects/telegram-ai-agent`:

```text
agent task routing
fresh session lifecycle
tmux/topic recovery
restart/reboot verification
```

Каждый этап выполняет временный профильный агент по отдельному ТЗ. Архитектор
проводит независимый audit; основной Seller-агент принимает готовый функционал
как пользователь системы, но не реализует переход самостоятельно.

## 8. Общие проверки каждого этапа

- `git diff --check`;
- focused tests измененного контура;
- полный project test suite перед merge;
- `tasks policy` без новых gaps;
- отсутствие secrets в staged files и отчетах;
- проверка migration/rollback на копии БД;
- отсутствие активного marketplace write во время deployment;
- Telegram duplicate callback test;
- Job Worker restart/recovery test;
- для bot runtime - проверка tmux, topic bindings и recovery после restart;
- Hermes summary без секретов после завершенного этапа.

## 9. Что не входит в текущий переход

- Graphiti, Mem0 и отдельная универсальная vector memory;
- немедленный переход SQLite на PostgreSQL;
- автоматический marketplace write без owner approval;
- создание постоянных Ozon/WB executor-агентов;
- переписывание работающих marketplace workflows ради единого стиля;
- одновременное внедрение Mini App и нового safety/runtime слоя.

PostgreSQL оценивается отдельно, когда появится фактическая потребность в
нескольких одновременно пишущих service instances или отдельном control plane.

## 10. Ближайший следующий шаг

1. Провести независимую архитектурную и security-приёмку Stage 4A по diff,
   тестам, mock Playwright artifacts и runbook.
2. Только после успешной приёмки и отдельного разрешения владельца выполнить
   commit/merge и контролируемый deployment с owner smoke.
3. Не начинать следующий пакет этапа 4 и не добавлять write/dry-run/apply/
   approve/verify controls до отдельного ТЗ.
