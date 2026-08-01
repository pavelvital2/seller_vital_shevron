# Runtime approval и SafetyGuard

Статус: `implemented`, 2026-07-31.

## Единый approval package

Все apply-задачи, отправленные через `JobService`, получают пакет
`seller.approval_package.v1`. В нем фиксируются:

- `approval_id` и `approval_checksum`;
- точная apply-задача, ее source plan и verify task;
- источник согласования и canonical apply params;
- площадки и действия, если они представлены построчно;
- UTC-время создания.

Изменение полей после согласования ломает checksum и блокирует запуск.
Служебное поле `confirmed_by_user` в checksum бизнес-пакета не входит: оно
проверяется SafetyGuard отдельно как обязательное явное подтверждение. Это
позволяет legacy apply-кнопке переиспользовать ровно тот approval, который был
создан dry-run задачей, без изменения его `task/source/apply_params`.

## SafetyGuard

До запуска workflow и до возможного marketplace write общий guard проверяет:

- apply mode, риск и явное `confirmed_by_user=true`;
- наличие source plan, verify task и resource locks;
- approval status и checksum;
- task/source/verify соответствие единого пакета;
- свежесть runtime approval package не более 24 часов;
- наличие mapping evidence для задач, которые требуют mapping.

После этого JobService захватывает leases и атомарно резервирует approval.
Событие `job_write_window_started` создается только после прохождения всех
предварительных gate.

## Recovery checksum legacy-кнопки 2026-08-01

Симптом: Ozon Elastic callback создал apply job с
`approval_status_invalid:pending_review` и
`approval_record_checksum_mismatch`. Marketplace workflow и write-window не
начались.

Причина: dry-run approval содержал canonical `apply_params={plan_run_id}`,
тогда как legacy callback при повторной сборке пакета добавлял в checksummed
payload служебное `confirmed_by_user=true`. Один source plan получил два
разных checksum.

Исправление:

- `confirmed_by_user` исключён из `_approval_payload`, но остается обязательным
  параметром job и отдельной проверкой SafetyGuard;
- добавлен regression-тест `pending_review plan approval -> legacy Apply ->
  same checksum -> approved`;
- если ошибка уже произошла, сначала доказать по job events отсутствие
  `job_write_window_started`, точный owner callback и неизменный source plan;
- затем одобрить исходную approval record и отправить apply через
  `submit_approval_apply`, после чего всё равно выполнить fresh preflight,
  drift-check и verify.

Подтверждённый recovery:
`ozon_elastic_apply_20260801T152300`, drift `0`, Ozon принял `13/13`, verify
`ok`, runtime approval закрыт.

## Lifecycle

Нормальный цикл:

```text
pending_review -> approved -> applying -> applied -> verified -> closed
```

Повторное резервирование approval после `applied/verified/closed` запрещено.
Если apply завершился неопределенно, approval получает `applying_unknown` и
может перейти дальше только через безопасную verify-задачу. Успешный recovery
verify закрывает approval; warning/error его не закрывает.

## WB browser profile

Общий lease `lk:wb:browser-profile` используется session keepalive и WB
action plan/apply. Внешний keeper ожидает lease до 120 секунд. Если ресурс
занят, marketplace workflow не начинается и write-window не открывается.
