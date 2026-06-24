# Catalog Content

Рабочая папка для производных read-only файлов единого контентного слоя.

Generated-файлы в этой папке не коммитятся:

- `content_master.csv/json` - текущий снимок unified content master;
- `content_audit.csv/json` - строки, требующие контентного, SEO или
  ассортиментного review.
- `card_content_index.csv/json` - производный индекс snapshot карточек:
  наличие описания, количество фото, количество характеристик, хештеги/теги и
  статус raw snapshot.
- `card_content_audit_backlog.csv/json` - read-only очередь карточного
  аудита: приоритет, причины, фокус проверки и следующий шаг.
- `ozon_card_content.json`, `wb_card_content.json` - generated read-only
  snapshots карточного контента.

Команды:

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m seller_agent.cli fetch-card-content

PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m seller_agent.cli build-content-master

PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m seller_agent.cli card-content-audit-backlog
```

Этот слой не меняет карточки Ozon/WB, фото, цены, остатки или артикулы
продавца. `fetch-card-content` только читает API и считает фото по URL/объектам
в карточке; это не является визуальным фото-аудитом. Перед рекомендациями по
конкретной карточке все фото нужно открыть, посмотреть и описать по
`data/planning/product_card_work_runbook.md`.
