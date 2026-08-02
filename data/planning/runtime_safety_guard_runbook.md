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

## Канонические marketplace/profile leases

Все операции, которые кратковременно изменяют общий browser profile, используют
точный lease одного фактического профиля:

```text
lk:ozon:profile:chrome-profile
lk:wb:profile:browser-profile
```

Job Worker consumers, Ozon/WB session refresh services и ручные refresh wrappers
получают ключ через `seller_agent.core.resource_keys`. Внешний refresh ожидает
lease до 120 секунд и освобождает его сразу после завершения подпроцесса.
Постоянный Ozon Chrome keeper является только browser host и lease на весь срок
жизни не удерживает; lease берет короткая mutable CDP refresh/monitor операция.

Прямые maintenance-entrypoints подчиняются тому же контракту. Команды
`sessions start/stop/restart` атомарно получают ключи всех выбранных профилей,
а `restore-ozon-session` получает Ozon key до остановки процессов, удаления
`Singleton*` и interactive login. `install-session-systemd --apply --switch`
атомарно получает оба profile key до первого switch-side effect. После
завершения switch manager leases освобождаются, затем обе self-leasing oneshot
refresh-службы запускаются синхронно и их terminal результаты входят в общий
status; ошибка любой службы не может быть представлена как успешный switch.
`sessions status`, dry-run и установка units без `--switch` profile lease не
берут. Все эти leases ограничены TTL операции и снимаются в `finally`.

API write-задачи отдельно используют `api:ozon:vital-shevron:write` или
`api:wb:vital-shevron:write` вместе с предметными locks. Read-only API задачи
эти ключи не получают, поэтому не конфликтуют с write/LK без фактического
общего mutable resource. Если требуемый lease занят, marketplace workflow не
начинается и write-window не открывается.

## Execution graph policy

`tasks policy` проверяет фактический runtime graph каждой включенной write-задачи:

- capability имеет строго `mode=apply`, confirmation и семантически полный
  transport contract: правильный `plan_run_id`/`source_run_id`, непустые
  `approval_id`/`approval_checksum`, `confirmed_by_user const=true`, а в
  результате ограниченный `overall_status` и непустой `run_id`;
- source plan зарегистрирован, включен, имеет строго `dry_run` mode и callable
  `WorkflowRunner` handler;
- verify task зарегистрирован, включен, имеет строго `mode=verify` и реальный
  callable handler;
- apply handler callable, а набор locks содержит ровно canonical API write
  key каждой затрагиваемой площадки и только canonical LK keys;
- disabled write capability не публикуется в Telegram и содержит явную причину.

Для enabled graph source mode обязан быть именно `dry_run`: только успешный
`dry_run` результат участвует в автоматической регистрации runtime approval.
Source/apply/verify handlers обязаны иметь единый вызываемый контракт
`(task, data_dir, credentials, inputs)`. Marketplace-множества source и verify
должны точно совпадать с apply (порядок незначим), поэтому Ozon apply нельзя
связать с WB source/verify. До допуска capability к write window `tasks policy`
проверяет apply `result_schema` как закрытый контракт: обязательные непустые `run_id` и
`overall_status` только из `ok/warning/partial/blocked/error`; произвольные
дополнительные статусы fail-closed считаются policy defect.

Policy не содержит allowlist известных дефектов. Plan completion создает
runtime approval только для единственной включенной apply-задачи и только при
наличии типизированного `seller.plan_approval_candidate.v1`: planner обязан
указать свой фактический `action_count`, canonical source field и тот же
`run_id`. Нулевой, отсутствующий или поврежденный action-set не создает
`pending_review`. Один `run_id` сам по себе не является доказательством наличия
write-действий.

`action_count` считается по фактическому default apply payload, а не по размеру
исходного scope или общему числу рекомендаций. В частности, WB Best Price
использует `to_change_discount` (`changes_only=true`); bid planners исключают
строки, которые apply отфильтрует по action, API source, min bid, unchanged bid
или placement; inbox исключает manual/read-only actions. Поэтому no-op plan и
plan только с неподдержанными для apply строками не создают ложный approval.
Inbox отдельно считает только `_approvable_review_actions` и, для Ozon,
`_approvable_messenger_actions`, после удаления действий с уже сохранённым
durable receipt по canonical `inbox_action_id`. Manual review, пустой reply и
полностью receipted хвост остаются в read-only отчёте, но дают
`apply_actions_count=0` и не создают `pending_review`.

Отсутствующий `state/inbox_action_receipts.json` означает первый запуск и
валидный пустой verified set. Если файл существует, он обязан целиком
соответствовать `inbox-action-receipts/v1`: top-level содержит только
`schema_version` и object `receipts`, каждый canonical 64-hex action ID ведёт
на полный verified receipt с безопасными идентификаторами и timezone-aware
`verified_at`. Truncated/malformed JSON, другая schema, неверный тип receipts
или повреждённая запись дают только постоянный код
`inbox_receipt_state_invalid`. `JobService` блокирует inbox plan/apply до
handler, approval reservation и write-window; прямые plan/apply вызовы также
fail-closed. Повреждённый файл не перезаписывается. Verify остаётся
`warning/manual_verification_required` с reason code `receipt_state_invalid` и
не использует повреждённое состояние как evidence.

Повторная проверка `task.enabled` выполняется в `JobService.run` до SafetyGuard,
resource leases, approval reservation и write-window. Поэтому уже поставленная
в очередь apply-задача, которую отключили до исполнения, завершается
`blocked/task_disabled`: approval остается в исходном состоянии, а события
`job_approval_reserved` и `job_write_window_started` не создаются.

Неподдержанные standalone card create/update/remove, seller SKU recovery,
Ozon PARTIAL_APPROVED recovery, fast approved-card и combined Ozon Messenger
write routes отключены до отдельного полного runtime design. Legacy
`actions-apply` также остается отключенным. `reviews-questions-apply`
отключен отдельно: текущий read-only plan создает pending package, а handler
требует отдельный legacy `approved_path`; объявлять такой graph замкнутым до
безопасного runtime bridge нельзя.

Существующие Ozon/WB inbox apply routes остаются включенными, но их локальные
receipts используются только как защита от повторного apply и диагностический
след. В P0-G нет нового независимого marketplace read-back, поэтому inbox
verify всегда возвращает `warning`, `manual_verification_required=true` и
`verification_confirmed=false`; approval остается открытым для ручной проверки
или будущего отдельного read-back. Отсутствующий/поврежденный reviews pending,
невалидный inbox package и нулевой source action-set также fail-closed дают
только безопасные reason codes. Локальный receipt никогда не называется
marketplace verification и verify не повторяет reply/mark-read write.
