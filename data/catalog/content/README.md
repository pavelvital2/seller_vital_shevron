# Catalog Content

Рабочая папка для производных read-only файлов единого контентного слоя.

Generated-файлы в этой папке не коммитятся:

- `content_master.csv/json` - текущий снимок unified content master;
- `content_audit.csv/json` - строки, требующие контентного, SEO или
  ассортиментного review.

Команда:

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m seller_agent.cli build-content-master
```

Этот слой не меняет карточки Ozon/WB, фото, цены, остатки или артикулы
продавца. Перед рекомендациями по карточке нужен отдельный полный snapshot
описаний, характеристик, хештегов/тегов и всех фото.
