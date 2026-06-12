# Fresh Agent Handoff - Vital Shevron

Дата подготовки: 2026-06-13 00:30 MSK

## Итог

Актуальный проект:

```text
/home/pavel/projects/seller_vital_shevron
```

GitHub:

```text
https://github.com/pavelvital2/seller_vital_shevron
visibility: PRIVATE
branch: main
base commit before 2026-06-13 revision: a72047b Add fresh agent handoff
```

## Обязательный старт fresh-агента

```bash
cd /home/pavel/projects/seller_vital_shevron
sed -n '1,260p' AGENTS.md
sed -n '1,260p' data/planning/fresh_agent_handoff_2026-06-13.md
git status --short --branch
```

Использовать централизованные инструменты:

```text
/home/Codex/agent-tools/python/bin/python
/home/Codex/agent-tools/python/bin/pytest
/home/Codex/agent-tools/node/node_modules
```

Агенты не устанавливают инструменты сами.

## Проверенное состояние

Последняя полная проверка перед ревизионным commit:

```text
pytest: 48 passed
compileall src/tests: ok
git diff --check: ok
CLI help: ok
```

Runtime reports, raw snapshots, pending/approved packages и секреты не должны
коммититься. В tracked runtime-директориях допустимы только README-заглушки.

## Основные реализованные контуры

- `status-preflight` - API/LK/catalog health check.
- `daily-morning-report` - read-only управленческий отчет.
- `reviews-questions` - read-only отзывы и вопросы Ozon/WB.
- `plan-ozon-elastic` / `apply-ozon-elastic` - Ozon Elastic Boosting.
- `plan-ozon-cpc-optimization` / `apply-ozon-cpc-bids` - Ozon CPC ставки.
- `plan-wb-actions-discounts` - WB акции и скидки, схема Vital Shevron
  `70-55-55`.
- `wb-promotion-report` - WB promotion read-only report.
- `plan-wb-promotion-bids` / `apply-wb-promotion-bids` - WB promotion ставки.

## Последние marketplace write-операции

### Ozon

- Ozon Elastic apply выполнен через отдельный контур и verify.
- Ozon CPC bid apply выполнен через Performance API и verify.
- SKU `2468954880` дополнительно установлен на `1.00` руб. точечной операцией.

Подробности: `data/planning/ozon_elastic_runbook.md` и
`data/planning/ozon_cpc_efficiency_runbook.md`.

### WB

WB promotion apply выполнен 2026-06-13:

```text
run_id: wb_promotion_bids_apply_20260613T001504
approved plan: wb_promotion_bid_plan_20260613T000806
applied action: scale_candidate only
applied rows: 22
skipped rows: 14
current_bid_sum_applied: 25.60
target_bid_sum_applied: 27.40
bid_change_sum_applied: +1.80
verify: ok, mismatches 0
```

Снижения и строки `review_card_then_reduce` не применялись. Для них требуется
отдельное явное согласование владельца.

## Safety

Для опасных операций обязательна цепочка:

```text
read-only -> dry-run -> review -> approved -> apply -> verify -> result
```

Нельзя выполнять write-операции в магазинах без явного подтверждения владельца.

API-first: ЛК использовать только если официальный API не покрывает задачу или
не позволяет завершить конкретную внештатную ситуацию.

## Секреты

Не выводить, не коммитить и не записывать в документы:

- API keys;
- tokens;
- cookies;
- storage state;
- SMS/email-коды;
- содержимое `.sessions/` и `tmp/auth/`.

Перед commit:

```bash
git status --short --ignored
git ls-files .env .sessions tmp/auth data/runs data/pending data/approved
```

В индексе не должно быть `.env`, `.sessions`, `tmp/auth`, runtime reports,
raw/processed catalog snapshots или business mapping CSV/MD.

## Следующие рекомендуемые шаги

1. Сделать scheduled monitoring WB promotion после повышения ставок:
   read-only report через 24-48 часов, сравнение ДРР/заказов по 22 товарам.
2. Автоматизировать ежедневный seller report с блоком рекламы Ozon/WB.
3. Перевести LK refresh с legacy watchdog на `systemd --user` timers.
4. Спроектировать approval queue для Telegram-бота: dry-run -> кнопка
   согласования -> apply -> verify.
5. Подготовить отдельный слой inventory/stock checks перед рекламными
   повышениями.
