# Приоритетный план автоматизации 2026-07-31

Статус: `implemented_verified`.

## Решение владельца

Реализовать первые десять задач из актуальной очереди рекомендаций в порядке
приоритета. Telegram Mini App в эту пачку не входит.

Работа выполняется автономно по уже согласованным правилам. Marketplace write
без нового явного согласования запрещен. Задачи, связанные с ценами, акциями,
рекламой, карточками или ответами покупателям, в этой пачке могут создавать
только read-only результат, checksummed dry-run или approval package.

## Порядок

1. `VS-REC-144` - ежедневный Job Worker-контроль ликвидационных когорт Ozon/WB.
2. `VS-REC-141` - WB второй price dry-run и полный контроль когорты без apply.
3. `VS-REC-137` - автоматические контрольные проверки Ozon `Звездные товары`.
4. `VS-REC-143` - штатный read-only аудит потерь и компенсаций WB.
5. `VS-REC-126` - общий WB browser-profile lease и безопасный retry до write.
6. `VS-REC-142` - уведомления о смене состояния Ozon LK.
7. `VS-REC-022` - мониторинг срока Ozon min-price и refresh dry-run.
8. `VS-REC-020` - унифицированный approval package.
9. `VS-REC-033` - полный lifecycle и защита от повторного apply.
10. `VS-REC-034` - централизованный SafetyGuard.

## Общие критерии готовности

Для каждой задачи:

- есть штатный task/CLI или общий runtime-компонент, а не одноразовая команда;
- задача зарегистрирована в `TaskRegistry`, если она является операцией;
- длительная или регулярная операция идет через Job Worker;
- результат имеет RunManifest и безопасные artifacts;
- есть focused tests и обновлен профильный runbook;
- ошибки не раскрывают cookies, tokens, storage state или auth headers;
- write не выполняется без точного owner-approved package;
- статус рекомендации обновляется только после проверки реализации.

## Ограничения по отдельным задачам

- `VS-REC-137` и `VS-REC-143` могут остаться `scheduled`, если контрольная
  дата или внешний отчет еще не наступили; при этом автоматический запуск и
  обработка результата должны быть готовы.
- `VS-REC-141`: второй WB price stage только dry-run; upload запрещен.
- `VS-REC-022`: разрешены timer-status и refresh-plan; применение refresh
  запрещено без отдельного согласования.
- `VS-REC-144`: hard-stop формирует список/approval review, но не выключает
  рекламу и не меняет цены автоматически.

## Проверка всей пачки

После десяти задач:

1. focused tests после каждого блока;
2. полный `pytest` с `PYTHONPATH=src:.`;
3. `compileall`, `git diff --check`, TaskRegistry policy и secret scan;
4. read-only smoke для новых задач там, где доступны внешние данные;
5. обновление `recommendations_index.md`, `followups.md`, runbook и Hermes
   summary без секретов.

## Реализация

- `VS-REC-144`: `liquidation-daily-control`, scheduled enqueue, Telegram
  formatter, exact cohorts и checksummed stop-review.
- `VS-REC-141`: `wb-liquidation-stage2-plan`, только fresh dry-run 18 строк.
- `VS-REC-137`: `ozon-stars-control` и one-shot timers 3/7/14 дней.
- `VS-REC-143`: `wb-incident-audit` и контрольный timer 2026-08-03.
- `VS-REC-126`: общий `lk:wb:browser-profile` lease до workflow/write.
- `VS-REC-142`: `ozon-lk-state-monitor`, уведомления только при transition.
- `VS-REC-022`: `ozon-min-price-timer-plan`, refresh только как dry-run.
- `VS-REC-020/033/034`: `seller.approval_package.v1`, lifecycle close и
  общий `SafetyGuard` в `JobService`.

## Проверка 2026-07-31

- полный `pytest`: `506 passed`;
- `compileall`, TaskRegistry policy (`90 tasks`, `0 issues`) и
  `git diff --check`: успешно;
- live WB stage 2 dry-run:
  `wb_liquidation_stage2_plan_20260731T223733` - `17` изменить, `1` уже цель,
  `0` blocked, write не выполнялся;
- live Ozon timer-status:
  `ozon_min_price_timer_plan_20260731T223742` - `597/597`, refresh `0`;
- live liquidation control:
  `liquidation_daily_control_20260731T224044` - точные `138/163`, hard stop
  `0/0`, write не выполнялся;
- Ozon LK scheduled Job Worker smoke: job `success`, state `ok`, неизмененное
  состояние подавлено без Telegram-шума;
- WB keepalive smoke через shared lease: systemd service `SUCCESS`;
- новые timers установлены и enabled; старые текстовые Stars reminders
  остановлены во избежание дублей.
