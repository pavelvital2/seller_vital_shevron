# Development Work Checkpoint

Дата чекпойнта: 2026-07-05.

## Назначение

Этот файл фиксирует состояние работ по развитию проекта перед возвратом к
карточкам. Источник истины по правилам остается `AGENTS.md`, по runtime-плану -
`data/planning/runtime_job_store_plan.md`, по карточкам -
`data/planning/product_card_work_checkpoint.md`.

## Текущая ветка

```text
feature/runtime-job-store
```

Последние зафиксированные изменения в ветке:

- `881c0a6 Route priority action callbacks through jobs`;
- `4bee55b Add workflow handlers for apply jobs`.

## Что уже сделано по развитию проекта

### Runtime / Job Store

- Добавлен SQLite Job Store MVP:
  `src/seller_agent/core/job_store.py`,
  `src/seller_agent/core/job_models.py`.
- Добавлены `JobService`, `JobRunner`, `JobWorker`:
  `src/seller_agent/core/job_service.py`,
  `src/seller_agent/core/job_runner.py`,
  `src/seller_agent/core/job_worker.py`.
- Добавлены CLI-команды `jobs list/show/submit/run/run-next/cancel`.
- `WorkflowRunner` получил apply handler-ы для основных подтверждаемых
  write-контуров Ozon/WB.
- `TaskRegistry` расширен v2 metadata для apply-задач: source plan task,
  verify task, lock keys, timeout/result metadata.

### Telegram bot

- Live `/status` и `/today` могут ставиться в runtime jobs при запуске polling
  с `--runtime-jobs`.
- Callback-и Ozon Elastic и WB actions `70-55-55` переключены на `JobService`:
  бот создает job, а marketplace write выполняется через runner/safety-контур.
- Ozon actions optimizer уже имеет отдельный CLI/Telegram контур, но его
  callback пока не переведен на JobService.

### Проверки

- `PYTHONPATH=src /home/Codex/agent-tools/python/bin/python -m pytest`:
  `296 passed`.
- `tasks policy` на 2026-07-05 показывает только legacy-проблемы старого
  `actions-apply`; новые профильные apply-контуры должны идти через отдельные
  tasks, а не через legacy `actions-apply`.

## Что сознательно не доделываем перед карточками

Следующие runtime-задачи остаются после текущего карточного блока:

1. Полный callback dedup/async worker для всех inline-кнопок.
2. Перевод Ozon actions optimizer, WB promotion и карточного batch apply
   callbacks на JobService по одному с отдельными smoke-тестами.
3. Доведение approvals/resource leases до единого атомарного слоя для всех
   опасных операций.
4. Решение судьбы legacy `actions-apply`: архивировать или закрыть
   недостающими policy-полями.

Эти пункты важны, но сейчас не выше карточной работы по приоритету владельца:
надо продолжать карточки и продажи, не начинать новый большой runtime-слой.

## Точка возврата к карточкам

Перед продолжением карточек читать:

1. `AGENTS.md`;
2. `data/planning/product_card_work_checkpoint.md`;
3. `data/planning/card_ops/quick_access.md`;
4. `data/planning/product_card_work_runbook.md`;
5. `data/planning/product_card_fill_template_runbook.md`;
6. `data/planning/product_card_editor_field_map_runbook.md`;
7. `data/planning/ozon_product_card_content_runbook.md`;
8. `data/planning/wb_card_create_runbook.md`;
9. `data/planning/product_card_designer_tasks.md`.

Текущая карточная очередь:

- `data/catalog/card_audits/seo_priority_20260704/0001...0005` уже применены
  и проверены: `owner_review.status=owner_approved_applied_verified`,
  `marketplace_apply.status=applied_verified`;
- marketplace write по ним выполнен пачкой
  `card_batch_0001_0005_apply_20260704T1907`, восстановление контента -
  `card_batch_0001_0005_content_recovery_apply6_20260704T2045`, финальная
  проверка -
  `card_batch_0001_0005_final_readonly_verify_20260704T2055`;
- следующую карточку на согласование брать из актуального
  `data/catalog/content/card_content_audit_backlog.csv`, а не из старой
  очереди `seo_priority_20260628`, если владелец не задаст другой приоритет;
- согласованные карточки копить в batch, нормальный размер пачки сейчас -
  `5` карточек;
- после явного `применяй` запускать только безопасный batch apply с verify.

## Риски

- Нельзя смешивать новый runtime-рефакторинг и карточный marketplace apply в
  одном непрозрачном изменении.
- Нельзя повторно применять уже owner-approved карточки без свежего
  batch-пакета, drift-check и verify.
- Нельзя отправлять владельцу карточный HTML без встроенных изображений и
  без текущего согласованного шаблона.

## Следующий правильный шаг

Продолжить карточную работу: подготовить следующий owner-review HTML по
актуальному backlog, отправить владельцу на согласование, затем копить
согласованные карточки до batch apply.
