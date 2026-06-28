# Master Passport

Слой 3 трехслойной карточной архитектуры Vital Shevron.

Эта папка предназначена для owner-approved мастер-паспортов товаров. Слой 3
заполняется только после того, как владелец согласовал результат аудита из
`data/catalog/card_audits/`.

Мастер-паспорт является целевым источником для будущих:

- Ozon content dry-run;
- WB content dry-run;
- фото dry-run;
- группировки;
- отчетов и Telegram-бота.

Здесь не должны храниться сырые marketplace snapshots и несогласованные
рекомендации агента. Для этого есть:

- слой 1: `data/catalog/content/`;
- слой 2: `data/catalog/card_audits/`.

Рабочие passport-файлы в этой папке не коммитятся. В git хранятся только этот
README и schema.

См. подробное правило:

```text
data/planning/product_card_data_layers_runbook.md
```
