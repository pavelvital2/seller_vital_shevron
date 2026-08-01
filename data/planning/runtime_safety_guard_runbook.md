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
