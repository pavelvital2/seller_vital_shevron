# TAKTERRA Development Docs Reference

Дата переноса: 2026-06-13.

## Назначение

Эта папка хранит read-only копию документов TAKTERRA, связанных с развитием
проекта, архитектурой task-runner, safety-контуром, Telegram-ботом,
регулярной автоматизацией и переносом опыта в Vital Shevron.

Источник:

```text
/home/pavel/projects/seller_takterra
```

Документы скопированы в Vital Shevron как справочный слой. Они не заменяют
`AGENTS.md`, текущие runbook-и Vital Shevron и `data/planning/project_map.md`.

## Что скопировано

```text
data/reference/takterra_development_docs/data/15_architecture_notes/
data/reference/takterra_development_docs/data/planning/
```

Скопированы все markdown-документы из:

```text
/home/pavel/projects/seller_takterra/data/15_architecture_notes/
/home/pavel/projects/seller_takterra/data/planning/
```

Ключевые документы для развития Vital Shevron:

- `data/15_architecture_notes/questions_and_recommendations.md` - архитектурные
  решения TAKTERRA: task-runner вместо разовых скриптов, бот как dispatcher,
  safety-контур, вертикальные workflow.
- `data/planning/recommendations_index.md` - реестр рекомендаций TAKTERRA.
- `data/planning/project_structure_optimization_review_2026-06-11.md` - аудит
  структуры TAKTERRA и рекомендации по `RunManifest`, `TaskRegistry`,
  approval lifecycle, cleanup и timers.
- `data/planning/marketplace_control_bot_discussion.md` - журнал развития
  идеи marketplace control bot, операций и внештатных ситуаций.
- `data/planning/vital_shevron_migration_plan.md` - план создания Vital
  Shevron как самостоятельного проекта на базе TAKTERRA.
- `data/planning/vital_shevron_transfer_manifest.md` - логика безопасного
  переноса без секретов и операционных данных.

## Как использовать

При проектировании новой автоматизации Vital Shevron:

1. Сначала читать актуальные документы Vital Shevron:
   `AGENTS.md`, `data/planning/project_map.md`,
   `data/planning/recommendations_index.md`.
2. Затем использовать эту папку как источник опыта TAKTERRA.
3. Проверять, применимо ли TAKTERRA-допущение к Vital Shevron.
4. Если решение переносится, фиксировать его в текущих документах Vital
   Shevron, а не ссылаться только на TAKTERRA-копию.

## Важное отличие Vital Shevron

В TAKTERRA ключевая модель строилась вокруг совпадения артикулов:

```text
master_sku == Ozon offer_id == WB vendorCode
```

Для Vital Shevron это не является рабочим допущением. До унификации артикулов:

- Ozon-сценарии работают через `offer_id`, `product_id`, `sku`;
- WB-сценарии работают через `vendorCode`, `nmID`, barcode;
- mapping Ozon/WB является дополнительным слоем;
- cross-marketplace write-операции требуют подтвержденного mapping.

## Безопасность

В эту папку перенесены только markdown-документы. Секреты, `.env`, cookies,
storage state, API-ключи, attachment files, `data/runs`, `data/pending`,
`data/approved` и рабочие каталоги TAKTERRA не переносились.
