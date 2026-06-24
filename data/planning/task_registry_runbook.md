# TaskRegistry Runbook

Дата актуализации: 2026-06-24

## Итог

`TaskRegistry` - единый машинно-читаемый список операций проекта для CLI,
fresh-агентов и будущего Telegram-бота. Он не заменяет сами CLI-команды, а
описывает их метаданные: режим, риск, маркетплейсы, runbook, требования к
credentials/LK/mapping и необходимость подтверждения владельца.

## Где находится

```text
src/seller_agent/tasks/registry.py
```

Bot dispatcher должен брать список задач из этого же registry:

```text
src/seller_agent/bot/dispatcher.py
```

## CLI

Список задач:

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m seller_agent.cli tasks list
```

Показать одну задачу:

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m seller_agent.cli tasks show --task reviews-questions
```

Фильтры:

```bash
--mode read_only|dry_run|apply|verify|maintenance
--risk none|low|normal|high
--marketplace all|ozon|wb
--telegram-only
```

## Поля задачи

Минимальная схема:

```json
{
  "name": "",
  "command": "",
  "title": "",
  "description": "",
  "mode": "read_only|dry_run|apply|verify|maintenance",
  "risk": "none|low|normal|high",
  "marketplaces": ["ozon", "wb"],
  "runbook_path": "",
  "requires_credentials": false,
  "requires_lk": false,
  "requires_mapping": false,
  "requires_confirmation": false,
  "telegram_enabled": false,
  "telegram_button_label": "",
  "aliases": [],
  "is_read_only": true,
  "is_write": false
}
```

## Safety-правила

- Все `mode=apply` задачи должны иметь `requires_confirmation: true`.
- Write-задачи не должны запускаться будущим Telegram-ботом напрямую: бот
  может показывать статус, risks, rows и создавать approved package, но apply
  остается отдельным подтверждаемым действием.

## Важные read-only задачи

- `build-unified-catalog` - собирает внутренний product-level catalog из
  confirmed mapping и marketplace catalogs.
- `plan-internal-skus` - готовит owner-review план внутренних артикулов для
  `ozon_only`/`wb_only` товаров.
- `pricing-status` - строит read-only статус цен и готовности маржинального
  анализа по unified catalog и локальным или fresh API Ozon/WB price snapshots.
  Опция `--refresh-api` обращается только к read-only price endpoints и
  сохраняет snapshots в `data/runs/<date>/<run_id>/raw/`. Требует mapping как
  слой нормализации, дополнительно читает Ozon/WB action dry-run CSV для
  action-price, но не меняет цены и не обращается к write API.
- `requires_mapping: true` означает, что задача не должна переходить к
  cross-marketplace write без подтвержденного mapping.
- `requires_lk: true` означает, что перед запуском нужно проверить профильную
  LK/session инструкцию и не выводить cookies/storage state в отчеты.
- `runbook_path` обязателен для всех постоянных задач.

## Подключено

Ветка `feature/task-registry` регистрирует все текущие CLI-команды:

- `runs`;
- `tasks`;
- `approvals`;
- `fetch-catalog`;
- `build-unified-catalog`;
- `plan-internal-skus`;
- `status-preflight`;
- `daily-morning-report`;
- `sessions`;
- `restore-ozon-session`;
- `install-session-systemd`;
- `plan-ozon-elastic`;
- `apply-ozon-elastic`;
- `plan-ozon-cpc-optimization`;
- `apply-ozon-cpc-bids`;
- `plan-wb-actions-discounts`;
- `apply-wb-actions-discounts`;
- `wb-promotion-report`;
- `plan-wb-promotion-bids`;
- `apply-wb-promotion-bids`;
- `apply-actions`;
- `plan-wb-card-create`;
- `apply-wb-card-create`;
- `reviews-questions`;
- `apply-reviews-questions`;
- `prepare-reviews-questions-approved`.

`build-unified-catalog`:

- task name: `catalog-build-unified`;
- mode: `read_only`;
- risk: `low`;
- marketplaces: `ozon`, `wb`;
- requires_mapping: `true`;
- credentials/LK/confirmation не требуются;
- Telegram: `/catalog` показывает последний `catalog-build-unified` из
  `RunManifest` и ключевые цифры из `summary` artifact; `/catalog <запрос>`
  ищет товар в unified catalog по internal/Ozon/WB идентификаторам, barcode и
  названию;
- runbook: `data/planning/catalog_mapping_runbook.md`;
- назначение: собрать внутренний `data/catalog/unified/products.csv/json` из
  confirmed mapping и локальных processed каталогов Ozon/WB.

`plan-internal-skus`:

- task name: `catalog-internal-sku-plan`;
- mode: `read_only`;
- risk: `low`;
- marketplaces: `ozon`, `wb`;
- requires_mapping: `true`;
- credentials/LK/confirmation не требуются;
- runbook: `data/planning/catalog_mapping_runbook.md`;
- назначение: построить review-план внутренних `internal_sku` для
  `ozon_only`/`wb_only` товаров без изменения артикулов продавца на Ozon/WB.

## Следующий шаг

1. Подключить генерацию CLI/help или документации из registry без изменения
   внешнего поведения команд.
2. Расширять `WorkflowRunner` только через задачи, уже описанные в registry и
   профильных runbook-ах.
3. Проектировать общий `SafetyGuard`, чтобы
   apply-команды не дублировали проверки подтверждения, preflight, drift-check
   и idempotency.
