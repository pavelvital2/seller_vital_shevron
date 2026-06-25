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
- `parameter_inventory/*.csv` - generated read-only инвентаризация текущих и
  доступных Ozon/WB параметров карточек.
- `product_passport/passport_fields.csv`,
  `product_passport/passport_attribute_mapping.csv`,
  `product_passport/master_product_passport.schema.json` - generated
  read-only контракт данных для будущих паспортов товаров и карточных audit
  packages.
- `card_audit_packages/<run_id>/` - generated read-only пакеты исходных
  данных для карточного аудита: `package_index.csv/json`, по каждой карточке
  `audit_package.json`, `audit_report.md`, `photos.html`. Это не финальный
  аудит и не рекомендации; фото должен смотреть агент.

Команды:

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m seller_agent.cli fetch-card-content

PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m seller_agent.cli build-content-master

PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m seller_agent.cli card-content-audit-backlog

PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m seller_agent.cli design-product-passport

PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m seller_agent.cli card-content-audit-packages
```

Этот слой не меняет карточки Ozon/WB, фото, цены, остатки или артикулы
продавца. `fetch-card-content` только читает API и считает фото по URL/объектам
в карточке; `card-content-audit-packages` только собирает пакеты с
`visual_audit_status=pending_agent_review`. Перед рекомендациями по конкретной
карточке все фото нужно открыть, посмотреть и описать по
`data/planning/product_card_work_runbook.md`.
