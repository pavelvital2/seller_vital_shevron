# TaskRegistry Runbook

Дата актуализации: 2026-06-18

## Итог

`TaskRegistry` - единый машинно-читаемый список операций проекта для CLI,
fresh-агентов и будущего Telegram-бота. Он не заменяет сами CLI-команды, а
описывает их метаданные: режим, риск, маркетплейсы, runbook, требования к
credentials/LK/mapping и необходимость подтверждения владельца.

## Где находится

```text
src/takterra_agent/tasks/registry.py
```

Bot dispatcher должен брать список задач из этого же registry:

```text
src/takterra_agent/bot/dispatcher.py
```

## CLI

Список задач:

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m takterra_agent.cli tasks list
```

Показать одну задачу:

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m takterra_agent.cli tasks show --task reviews-questions
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

## Следующий шаг

1. Подключить генерацию CLI/help или документации из registry без изменения
   внешнего поведения команд.
2. Расширять `WorkflowRunner` только через задачи, уже описанные в registry и
   профильных runbook-ах.
3. Проектировать общий `SafetyGuard`, чтобы
   apply-команды не дублировали проверки подтверждения, preflight, drift-check
   и idempotency.
