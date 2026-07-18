# Development Work Checkpoint

Дата чекпойнта: 2026-07-18.

## Назначение

Файл фиксирует актуальное состояние развития проекта после включения Job
Worker, добавления периодических отчетов, остатков/поставок, файлов `В работу`,
доработки карточного контура и сохранения концепции кнопки `Цены и маржа`.

Источники истины:

- общие правила: `AGENTS.md`;
- текущая ревизия: `data/planning/revision_2026-07-18.md`;
- runtime: `data/planning/runtime_job_store_plan.md`;
- карточки: `data/planning/product_card_work_checkpoint.md`;
- цены: `data/planning/pricing_runbook.md`;
- концепция будущей кнопки цен:
  `data/planning/pricing_margin_button_plan.md`.

## Текущая ветка

```text
checkpoint/card-runtime-20260708
```

Ветка используется как общий проверенный checkpoint накопленных изменений
карточек, Telegram, runtime, отчетов, продвижения и supply planning. Новый
отдельный feature-branch для кнопки `Цены и маржа` создавать после возврата к
ее реализации.

## Runtime / Job Worker

- SQLite runtime хранится в игнорируемом `runtime/runtime.db`.
- `JobStore`, `JobService`, `JobRunner`, `JobWorker`, approvals и resource
  leases подключены к TaskRegistry/WorkflowRunner.
- Основной Telegram bot запущен с `--runtime-jobs`.
- `vital-shevron-telegram-job-worker.timer` установлен, enabled и active.
- Bot, worker service/timer и repository templates совпадают.
- Linger включен; user systemd работает через `/run/user/1000/bus`.
- На момент ревизии нет активных jobs, незавершенных approvals или resource
  leases.
- Исторические `failed`/`partial_success` jobs сохранены как audit trail и не
  являются активной очередью; связанные инциденты описаны в профильных
  runbook/followups.

## Telegram и task-runner

- TaskRegistry содержит `80` задач, активных policy-проблем нет.
- CLI, WorkflowRunner и Telegram поддерживают периодический отчет отдельно по
  Ozon/WB: краткий, финансовый и полный.
- В меню обеих площадок подключены read-only `Остатки и поставки`.
- В меню обеих площадок подключены файлы `В работу` с выбором режима,
  количества кластеров и расчетом кластерной потребности.
- WB имеет сохраненную кнопку `70-55-55` и отдельную `Ручную акцию`.
- Долгие/опасные операции должны продолжать переводиться в Job Worker по одной
  с tests и read-only smoke; нельзя возвращать marketplace write внутрь
  обычного Telegram callback без JobService/safety.

## Карточный контур

- Действует трехслойная модель source -> audit -> owner-approved passport.
- После `apply -> verify` статусы синхронизируются в Layer 2, Layer 3,
  `data/catalog/card_status/latest.json` и карточном checkpoint.
- Fresh-аудиторы получили усиленные SEO, hashtag, package dimensions и WB
  departmental media quality gates.
- Для WB-фото ведомственной символики сохраняется обязательная защита
  ретушью/водяным знаком; нейтральные варианты ношения можно переносить.
- Текущую очередь и последние примененные паспорта восстанавливать только из
  `data/planning/product_card_work_checkpoint.md`, не из этого общего
  development checkpoint.

## Отчеты, продвижение и supply planning

- Daily report v3 разделяет WB витринные и финансовые суммы и использует
  актуальные источники остатков/поставок.
- Периодический отчет считает выплаты и физические изделия по `pack_qty`.
- WB parser-анализ не должен обрезать полный магазин одним глобальным
  `limit=500`; для полного сравнения нужен специализированный aggregate или
  покверийный маршрут.
- После WB portfolio bid apply действует follow-up на 3 и 7 полных дней;
  ранний parser-срез не является основанием для повторного повышения ставок.
- WB/Ozon `В работу` рассчитываются по потребности конкретного destination
  cluster и вычитают только локальные остатки/confirmed inbound этого
  кластера.

## Цены и маржа

Обсуждение будущей кнопки сохранено в
`data/planning/pricing_margin_button_plan.md`. Реализация не начата.

Перед реализацией нужно согласовать:

1. основной финансовый период: 30 дней с контролем 15 дней;
2. изменяемый либо фиксированный резерв акции 20%;
3. изменяемую либо фиксированную целевую видимую скидку 50%.

Первый этап должен быть read-only калькулятором с финансовой расшифровкой и
dry-run. Marketplace write добавлять позже отдельно для Ozon и WB.

## Проверка checkpoint

На ревизии 2026-07-18 подтверждено:

```text
pytest: 407 passed
compileall src/tests/scripts: ok
TaskRegistry policy: 0 issues
registered tasks with missing runbooks: 0 / 80
JSON syntax: ok
JavaScript syntax: ok
git diff --check: ok
status-preflight --skip-lk: ok
Ozon Seller API: ok
Ozon Performance API: ok
WB API: ok
Parser Data API /health: ok
Telegram bot: active
Job Worker timer: enabled/active
Ozon/WB session units: enabled/active
```

`ruff` не запускался: модуль отсутствует в централизованном Python toolchain.
Это остаточное ограничение проверки, а не подтвержденная ошибка кода.

## Незакрытые направления

1. Follow-up WB portfolio promotion через 3 и 7 полных дней.
2. Продолжение массового карточного аудита по актуальному backlog.
3. Разбор важных Ozon notification followups из `followups.md`.
4. Дальнейший runtime hardening: единый dedup/idempotency и перевод остальных
   длительных Telegram callback в Job Worker.
5. После текущего checkpoint вернуться к согласованию и реализации кнопки
   `Цены и маржа`.

## Следующий шаг

После commit/push вернуться к
`data/planning/pricing_margin_button_plan.md`, согласовать три открытых решения
и спроектировать read-only калькулятор без marketplace write.
