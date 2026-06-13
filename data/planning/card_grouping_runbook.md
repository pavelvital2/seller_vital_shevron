# Card Grouping Runbook

Дата создания: 2026-06-13

## Назначение

Инструкция описывает read-only анализ и будущий безопасный dry-run группировки
карточек Ozon/WB.

Группировка карточек является write-опасной операцией, потому что влияет на
карточный контент, SEO, отзывы, рейтинг, поведение покупателей и рекламу.

## Идентификаторы

Ozon:

- текущую фактическую группу читать по `model_info.model_id`;
- размер группы читать по `model_info.count`;
- рабочие ID товара: `offer_id`, `product_id/id`, `sku`;
- правило Ozon по справке: одинаковое значение поля `Название модели` или
  `Объединить на одной карточке` объединяет товары в одну карточку.

WB:

- текущую фактическую группу читать по `imtID`;
- рабочие ID товара: `vendorCode`, `nmID`, barcode;
- для создания карточек с объединением использовать `POST /content/v2/cards/upload/add`;
- для объединения/разъединения существующих карточек использовать
  `POST /content/v2/cards/moveNm`;
- WB объединяет карточки с одинаковым `imtID`; за раз можно объединять до
  `30` карточек.

## Read-only аудит

1. Прочитать `AGENTS.md`, `catalog_mapping_runbook.md`,
   `seo_audit_runbook.md` и этот runbook.
2. Выполнить свежий catalog fetch:

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python -m takterra_agent.cli fetch-catalog
```

3. Для Ozon дополнительно получить attributes через Seller API
   `/v4/product/info/attributes`, если нужно проверить карточные характеристики.
4. Для WB использовать Content API `/content/v2/get/cards/list`.
5. Сохранить производные таблицы в:

```text
data/runs/<date>/card_grouping_review_<timestamp>/
```

Минимальные файлы:

- `summary.json`;
- `card_grouping_review_report.md`;
- `processed/ozon_grouping_summary.csv`;
- `processed/wb_grouping_summary.csv`;
- `processed/grouping_review_priority.csv`.

Raw snapshots остаются runtime-данными и не коммитятся.

## Критерии review

Группу нужно вынести на review, если:

- размер группы больше `30`;
- смешаны разные смысловые кластеры;
- смешаны разные назначения: нагрудный, на спину, нарукавный, петлица,
  комплект;
- смешаны разные категории WB;
- смешаны производимые шевроны и не производимые сейчас товары;
- в группе есть карточки без подтвержденного mapping, а решение нужно
  синхронизировать между Ozon и WB;
- группа создана только потому, что товары похожи тематически, но покупатель не
  воспринимает их как варианты одного товара.

## Правило целевой группировки

Целевая группа должна описываться ключом:

```text
product_family / semantic_cluster / form_factor / pack_qty / attachment / color_family / size_family
```

Примеры:

- `callsign / single / chest / mох`;
- `callsign / kit2 / chest+cap / mох`;
- `fsb / chest / single / field`;
- `fsb / back / single / black-grey`;
- `bpla / fpv_operator / single / mох`;
- `rosguard / sleeve / kit2 / black`;

## Что не смешивать

- разные ведомства;
- нагрудные и спинные;
- петлицы и шевроны;
- одиночные товары и комплекты, если это разные цены и сценарии покупки;
- БПЛА, СВО, приколы, позывные, форменные шевроны в одной группе;
- шевроны и не производимые сейчас товары.

## Предварительный dry-run

Dry-run должен сформировать таблицу:

```text
marketplace
current_group_id
target_group_key
target_group_name
action
native_id
seller_sku
title
reason
risk
requires_owner_review
```

`action`:

- `keep`;
- `split`;
- `merge`;
- `manual_review`;
- `exclude_not_currently_manufactured`.

## Apply

Apply запрещен без явного согласования владельца.

Обязательная цепочка:

```text
read-only -> dry-run -> review -> approved -> apply -> verify -> result
```

Перед apply:

- проверить свежий catalog fetch;
- проверить mapping, если решение затрагивает обе площадки;
- проверить остатки и активную рекламу;
- проверить отзывы/рейтинг у группы, которую планируется делить;
- сохранить approved package с checksum списка строк.

После apply:

- Ozon: проверить `model_info.model_id/count`, статус модерации и видимость;
- WB: проверить `imtID`, ошибки Content API и выборочно витрину;
- через 24-48 часов сравнить SEO/заказы/рекламу.

## Первый фокус Vital Shevron

По аудиту 2026-06-13 сначала готовить dry-run для:

- Ozon `model_id=669023296` - позывные, группа `78`;
- Ozon `model_id=650751128` - БПЛА, группа `37`;
- Ozon `model_id=504942288`, `564967937`, `504942292` - МВД/ФСИН/ФСБ с
  разными назначениями внутри группы;
- WB `imtID=614683800` - БПЛА, группа `29`;
- WB `imtID=613225720`, `613275725` - МВД/ФСБ с разными назначениями.
