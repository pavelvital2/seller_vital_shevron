# Reviews And Questions Runbook

## Read-only сбор

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m seller_agent.cli reviews-questions --marketplace all
```

Для проверки только WB:

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m seller_agent.cli reviews-questions --marketplace wb
```

Команда выполняет read-only сбор отзывов и вопросов, готовит отчет и не
публикует ответы.

## Apply

Ответы покупателям - опасная операция.

Цепочка:

```text
read-only -> draft answers -> owner review -> approved -> apply -> verify -> result
```

Approved-пакет должен содержать:

- `schema_version: "approval-package/v1"` для новых пакетов;
- верхнеуровневый `status: "approved"`;
- `pending_id` и `source_run_id`;
- `actions_checksum` для применяемых строк;
- список `actions`;
- у каждой применяемой строки `approved: true`;
- у каждой применяемой строки `state: "approved"`.

Штатный способ создать approved-пакет после согласования владельца:

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m seller_agent.cli prepare-reviews-questions-approved \
  --source-pending <pending_id> \
  --mode all
```

Режимы:

- `all` - ответы на отзывы/вопросы и отметка Ozon-отзывов просмотренными;
- `replies-only` - только публичные ответы и WB-вопросы;
- `mark-viewed-only` - только отметка Ozon-отзывов просмотренными.

Команда сохраняет:

```text
data/approved/<approved_id>/approved_apply_plan.json
data/approved/<approved_id>/skipped_actions.json
data/approved/<approved_id>/APPROVED_PACKAGE.md
```

Apply выполнять только после явного согласования владельца и только из
approved-пакета:

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m seller_agent.cli apply-reviews-questions \
  --approved-path data/approved/<approved_id>/approved_apply_plan.json \
  --confirmed-by-user
```

Если запустить apply без `--confirmed-by-user`, guard должен завершить команду
с блокером `Apply requires --confirmed-by-user`. Это штатная защита, после
проверки согласования нужно повторить команду с флагом.

`apply-reviews-questions` проверяет `actions_checksum` до внешних write-
операций. Если approved JSON был изменен после создания, apply должен
завершиться ошибкой checksum mismatch.

Проверить статус pending/approved/apply chain:

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m seller_agent.cli approvals status \
  --id <pending_id>
```

Закрыть устаревший pending-пакет, если владелец решил не применять его:

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m seller_agent.cli approvals close \
  --id <pending_id> \
  --kind pending \
  --reason "superseded"
```

Непримененный approved-пакет закрывать только осознанно: команда требует
`--force`, потому что это скрывает уже согласованную write-операцию из обычного
`approvals status`.

Если `status: "approved"` есть только наверху, но у строк нет
`approved: true`, apply может завершиться техническим `ok`, но ничего не
отправить. Такой результат считать нулевым apply, а не успешной операцией.

После apply обязательна проверка:

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m seller_agent.cli reviews-questions --marketplace all
```

Успешной операцией считать только результат, где:

- отчет apply показывает фактически отправленные ответы/отметки, а не `0 из 0`;
- контрольный read-only подтверждает, что согласованные обращения исчезли из
  неотвеченных/непросмотренных;
- новые обращения, появившиеся после approved-пакета, не применяются задним
  числом и требуют отдельного review/approval.

Если после apply API вернул `ok`, но контрольный read-only снова показывает то
же обращение, считать это внештатной ситуацией. Нужно проверить payload,
повторить точечную verify-команду и не писать владельцу, что операция закрыта,
пока контроль не подтвердит исчезновение обращения.

Если официальный Ozon Seller Review API при контрольной проверке возвращает
`HTTP 403: not available with existing subscription`, но fallback через Ozon
ЛК/CDP успешно отработал и контрольный отчет показывает `0` отзывов, `0`
вопросов и `0` действий к применению, операцию считать подтвержденной с
предупреждением по источнику. В итоговом отчете нужно явно указать, что
официальный API недоступен по подписке, а проверка выполнена через ЛК/CDP
fallback.

## Правила черновиков

- Telegram-вывод строить по общему стандарту
  `data/planning/chat_report_templates.md` и профильным правилам ниже.
- После каждой переделки review-отчета агент обязан прислать в чат
  обновленную версию отчета, а не только ссылку на файл. Это фиксирует форму
  согласования.
- Формат отчета в чат:
  1. краткий итог: сколько всего обращений, сколько отзывов/вопросов, сколько
     действий;
  2. оценки: сводка по рейтингам;
  3. что написали покупатели и чем недовольны;
  4. что предлагается ответить;
  5. что предлагается отметить просмотренным без ответа;
  6. ограничения/источники;
  7. конкретная фраза, что нужно согласовать для apply.
- Отчет должен быть понятным и развернутым, но не огромным: в чат выводить все
  обращения, где есть текст или ответ, а однотипные отзывы без текста
  группировать с сохранением оценок и количества. Полный построчный список
  хранить в `APPROVAL_REQUIRED.md`.
- Review-отчет должен показывать оценки: сводку по рейтингам и оценку в каждой
  строке, включая отзывы без текста, которые предлагается только отметить
  просмотренными.
- Нельзя на каждый отзыв отвечать одинаковой однотипной фразой. Черновики
  должны отличаться по формулировке и учитывать текст покупателя, тип товара,
  оценку и наличие медиа. Шаблон допустим только как основа, но итоговые ответы
  в одном отчете не должны выглядеть как массовая копипаста.
- Отзыв без текста, но с прикрепленным фото или видео, нельзя просто отмечать
  просмотренным. Такой отзыв требует публичного ответа. Если ответ невозможен
  технически, это нужно вынести отдельным блокером на согласование владельца.
- Если к отзыву прикреплены фото или видео, агент должен по возможности
  просмотреть медиа перед рекомендацией ответа. Если источник отдает только
  количество медиа без ссылок/превью, в отчете нужно явно написать ограничение:
  `медиа есть, но содержимое не просмотрено`, и не выдавать выводы о содержимом
  медиа как подтвержденные.
- В списке отзывов с вариантами ответов оценка покупателя обязательна в каждой
  строке. Для медиа-отзывов также указывать количество фото и видео.
- Все фото из отзывов, по которым готовится ответ, нужно отправлять владельцу в
  Telegram для согласования ответа. Если фото недоступны для скачивания или
  отправки, в chat-отчете нужно явно написать, какие фото не удалось приложить
  и почему.
- Если в отзыве есть жалоба на качество, пошив, брак или формулировки вроде
  `так себе`, `криво`, `кривовато`, отвечать нейтрально: поблагодарить за
  обратную связь, признать замечание, написать про проверку позиции/партии и
  не использовать позитивный шаблон `рады, что понравилось`.
- Вопросы про конкретный позывной не закрывать общим шаблоном про липучку.
  Сначала проверить ассортимент/карточку. Если позывной не найден, писать
  только проверяемый факт: текущая карточка содержит другой позывной, наличие
  запрошенного позывного не подтверждено.
- Для ответа на WB-вопрос через `PATCH /api/v1/questions` использовать payload
  `{"id": "...", "answer": {"text": "..."}, "state": "wbRu"}`. Форма
  `{"id": "...", "text": "...", "state": "wbRu"}` может вернуть
  `error: false`, но оставить вопрос в неотвеченных.
- Если Ozon Seller Review API возвращает `HTTP 403: not available with existing
  subscription`, использовать fallback через Ozon ЛК/CDP, но явно писать это в
  отчете как ограничение источника.
- Для Ozon-отзывов с `photos_count > 0` список `/api/v4/review/list` может
  отдавать только счетчик фото без ссылок. Чтобы получить вложения перед
  ответом, через активную Ozon LK/CDP-сессию открыть read-only detail:
  `POST /api/v2/review/detail` с `company_id`, `company_type: "seller"` и
  `review_uuid`. В ответе проверять поля `photos` и `videos`; фото покупателя
  нужно отличать от `product.cover_image`, потому что `cover_image` - это
  изображение карточки товара.
- Готовый helper для новых агентов:

```bash
NODE_PATH=/home/Codex/agent-tools/node/node_modules \
  node scripts/reviews/ozon_review_media_detail_cdp.js \
  --run-dir data/runs/<date>/<reviews_questions_run_id>
```

  Helper автоматически читает
  `processed/reviews_questions_items.json`, выбирает Ozon-отзывы с
  `photos_count > 0` или `videos_count > 0`, делает read-only запрос
  `/api/v2/review/detail`, сохраняет только redacted detail без автора,
  `order_number`, `chat_url`, user id и других приватных полей, скачивает
  вложения из `photos`/`videos` в
  `processed/ozon_review_media_detail/` и пишет `summary.json` +
  `media_manifest.json`.

  Для точечной проверки одного отзыва:

```bash
NODE_PATH=/home/Codex/agent-tools/node/node_modules \
  node scripts/reviews/ozon_review_media_detail_cdp.js \
  --run-dir data/runs/<date>/<reviews_questions_run_id> \
  --review-uuid <review_uuid>
```

  Нельзя сохранять полный raw detail в постоянные документы или git: в нем
  могут быть имя покупателя, номер заказа, chat URL и user id. В Telegram
  отправлять только сами фото/видео, нужные для согласования ответа, и краткое
  описание без закрытых данных покупателя.
- Если после apply появился новый Ozon-отзыв без текста, которого не было в
  approved-пакете, его можно только вынести в новый отчет на согласование.
  Нельзя автоматически отмечать его просмотренным в рамках старого approval.

## Проверенные операции

### Штатный apply 2026-06-23

- Pending-пакет:
  `data/pending/reviews_questions_20260623T211638_pending`.
- Approved-пакет:
  `data/approved/reviews_questions_20260623T211638_pending_approved/approved_apply_plan.json`.
- Apply: `reviews_questions_apply_20260623T212430`.
- Результат apply: WB ответы `0/0`, WB вопросы `0/0`, Ozon публичные ответы
  `7/7`, Ozon отметки просмотренным `38/38`.
- Ozon счетчик перед/после: `NOT_VIEWED 42 -> 0`, `PROCESSED 2623 -> 2630`,
  `VIEWED 4055 -> 4090`.
- Медиа: перед согласованием проверены `3` Ozon-отзыва с медиа; скачано `6`
  фото через `scripts/reviews/ozon_review_media_detail_cdp.js`, собран
  contact sheet
  `data/runs/2026-06-23/reviews_questions_20260623T211638/processed/ozon_review_media_detail/review_media_contact_sheet.jpg`
  и отправлен владельцу в Telegram.
- Контрольный read-only: `reviews_questions_20260623T212458`.
- Verify: `items_count=0`, `actions_count=0`; WB Feedbacks API вернул `0`
  отзывов/вопросов к обработке, Ozon LK/CDP fallback вернул `0` отзывов и `0`
  вопросов к обработке.
- Нулевой pending контрольного verify
  `reviews_questions_20260623T212458_pending` закрыт как `no_actions_verify`,
  чтобы он не висел в статусах на согласование.
- Ограничение источника: официальный Ozon Review API `/v1/review/count` и
  `/v1/review/list` вернул `HTTP 403: not available with existing
  subscription`, поэтому Ozon проверен через LK/CDP fallback.

### Штатный apply 2026-06-22

- Pending-пакет:
  `data/pending/reviews_questions_20260622T_reviews_check_pending`.
- Approved-пакет:
  `data/approved/reviews_questions_20260622T_reviews_check_pending_approved/approved_apply_plan.json`.
- Перед согласованием владелец заметил, что Telegram-вывод отчета пришел
  одной строкой. Правило перенесено в
  `data/planning/chat_report_templates.md`: owner-facing Telegram-сводки
  отправлять реальным многострочным текстом, без литеральных `\n`.
- Apply: `reviews_questions_apply_20260622T_owner_approved`.
- Результат apply: WB ответы `1/1`, WB вопросы `0/0`, Ozon публичные ответы
  `5/5`, Ozon отметки просмотренным `49/49`.
- Ozon счетчик перед/после: `NOT_VIEWED 53 -> 0`, `PROCESSED 2618 -> 2623`,
  `VIEWED 4004 -> 4052`.
- Медиа: в согласованном пакете был `1` Ozon-отзыв с фото без текста
  (`pict0043`); фото скачано через
  `scripts/reviews/ozon_review_media_detail_cdp.js`, просмотрено и отправлено
  владельцу в Telegram перед apply.
- Контрольный read-only: `reviews_questions_verify_20260622T_after_apply`.
- Verify: `items_count=0`, `actions_count=0`; WB Feedbacks API вернул `0`
  отзывов/вопросов к обработке, Ozon LK/CDP fallback вернул `0` отзывов и `0`
  вопросов к обработке.
- Нулевой pending контрольного verify
  `reviews_questions_verify_20260622T_after_apply_pending` закрыт как
  `no_actions_verify`, чтобы он не висел в статусах на согласование.
- Ограничение источника: официальный Ozon Review API `/v1/review/count` и
  `/v1/review/list` вернул `HTTP 403: not available with existing
  subscription`, поэтому Ozon проверен через LK/CDP fallback.

### Штатный apply 2026-06-21

- Pending-пакет:
  `data/pending/reviews_questions_20260621T165153_pending`.
- Approved-пакет:
  `data/approved/reviews_questions_20260621T165153_pending_approved/approved_apply_plan.json`.
- Apply: `reviews_questions_apply_20260621T165647`.
- Перед apply Ozon-сессия была восстановлена через штатный
  `restore-ozon-session`; CDP guard подтвердил порт `9544` и профиль Vital
  Shevron.
- Результат apply: WB ответы `0/0`, WB вопросы `0/0`, Ozon публичные ответы
  `5/5`, Ozon отметки просмотренным `47/47`.
- Счетчик Ozon перед/после: `NOT_VIEWED 52 -> 0`, `PROCESSED 2613 -> 2618`,
  `VIEWED 3956 -> 4003`.
- Контрольный read-only: `reviews_questions_20260621T165709`.
- Verify: `items_count=0`, `actions_count=0`; WB Feedbacks API вернул `0`
  отзывов/вопросов к обработке, Ozon LK/CDP fallback вернул `0` отзывов и `0`
  вопросов к обработке.
- Ограничение источника: официальный Ozon Review API `/v1/review/count` и
  `/v1/review/list` вернул `HTTP 403: not available with existing
  subscription`, поэтому Ozon проверен через LK/CDP fallback.
- Медиа: в согласованном пакете не было фото или видео.
- Нулевой pending контрольного verify
  `reviews_questions_20260621T165709_pending` закрыт как `no_actions_verify`,
  чтобы он не висел в статусах на согласование.

### Штатный apply 2026-06-20

- Pending-пакет: `data/pending/reviews_questions_20260620T_owner_request_pending`.
- Approved-пакет:
  `data/approved/reviews_questions_20260620T_owner_request_pending_approved/approved_apply_plan.json`.
- Apply: `reviews_questions_apply_20260620T_owner_request`.
- Результат apply: WB ответы `0/0`, WB вопросы `0/0`, Ozon публичные ответы
  `1/1`, Ozon отметки просмотренным `12/12`.
- Контрольный read-only: `reviews_questions_verify_20260620T_owner_request`.
- Verify: `items_count=0`, `actions_count=0`; WB Feedbacks API вернул `0`
  отзывов/вопросов к обработке, Ozon LK/CDP fallback вернул `0` отзывов и `0`
  вопросов к обработке.
- Ограничение источника: официальный Ozon Review API `/v1/review/count` и
  `/v1/review/list` вернул `HTTP 403: not available with existing
  subscription`, поэтому Ozon проверен через LK/CDP fallback.
- Медиа: в согласованном пакете не было фото или видео.
- Нулевой pending контрольного verify
  `reviews_questions_verify_20260620T_owner_request_pending` закрыт как
  `no_actions_verify`, чтобы он не висел в статусах на согласование.

### Штатный apply 2026-06-19

- Pending-пакет: `data/pending/reviews_questions_20260619T065240_pending`.
- Approved-пакет:
  `data/approved/reviews_questions_20260619T065240_pending_approved/approved_apply_plan.json`.
- Apply: `reviews_questions_apply_20260619T0708`.
- Результат apply: WB ответы `2/2`, WB вопросы `0/0`, Ozon публичные ответы
  `3/3`, Ozon отметки просмотренным `31/31`.
- Контрольный read-only: `reviews_questions_verify_20260619T0709`.
- Verify: `items_count=0`, `actions_count=0`; WB Feedbacks API вернул `0`
  отзывов/вопросов к обработке, Ozon LK/CDP fallback вернул `0` отзывов и `0`
  вопросов к обработке.
- Ограничение источника: официальный Ozon Review API `/v1/review/count` и
  `/v1/review/list` вернул `HTTP 403: not available with existing
  subscription`, поэтому Ozon проверен через LK/CDP fallback.
- Медиа: перед согласованием Ozon-отзыв `bplapict0025` был проверен через
  `scripts/reviews/ozon_review_media_detail_cdp.js`; фото покупателя
  отправлено владельцу в Telegram, после согласования опубликован публичный
  ответ.
- Нулевой pending контрольного verify
  `reviews_questions_verify_20260619T0709_pending` закрыт как
  `no_actions_verify`, чтобы он не висел в статусах на согласование.

### Штатный apply 2026-06-18

- Pending-пакет: `data/pending/reviews_questions_20260618T0700_pending`.
- Approved-пакет:
  `data/approved/reviews_questions_20260618T0700_approved/approved_apply_plan.json`.
- Apply: `reviews_questions_apply_20260618T0710`.
- Результат apply: WB ответы `1/1`, WB вопросы `0/0`, Ozon публичные ответы
  `4/4`, Ozon отметки просмотренным `66/66`.
- Контрольный read-only: `reviews_questions_verify_20260618T0711`.
- Verify: `items_count=0`, `actions_count=0`; WB Feedbacks API вернул `0`
  отзывов/вопросов к обработке, Ozon LK/CDP fallback вернул `0` отзывов и `0`
  вопросов к обработке.
- Ограничение источника: официальный Ozon Review API `/v1/review/count` и
  `/v1/review/list` вернул `HTTP 403: not available with existing
  subscription`, поэтому Ozon проверен через LK/CDP fallback.
- Медиа: перед согласованием был проверен Ozon detail
  `/api/v2/review/detail` по отзыву `pict0049`, фото покупателя отправлены в
  Telegram для согласования, после согласования опубликован публичный ответ.
- Техническая доработка после apply: apply summary дополнен `pending_id`,
  `source_run_id` и `applied_counts`, pending manifest помечен как `applied`,
  чтобы ежедневный отчет не показывал уже примененный pending как открытый.

### Штатный apply 2026-06-16

- Approved-пакет: `data/approved/reviews_questions_20260616T085704_approved/approved_apply_plan.json`.
- Apply: `reviews_questions_apply_20260616T0910`.
- Результат apply: WB ответы `2/2`, WB вопросы `0/0`, Ozon публичные ответы
  `2/2`, Ozon отметки просмотренным `41/41`.
- Контрольный read-only: `reviews_questions_verify_20260616T0911`.
- Verify: `items_count=0`, `actions_count=0`; WB Feedbacks API вернул `0`
  отзывов/вопросов к обработке, Ozon LK/CDP fallback вернул `0` отзывов и `0`
  вопросов к обработке.
- Ограничение источника: официальный Ozon Review API `/v1/review/count` и
  `/v1/review/list` вернул `HTTP 403: not available with existing
  subscription`, поэтому Ozon проверен через LK/CDP fallback.

## Правило до унификации SKU

Отзывы/вопросы обрабатываются по native ID маркетплейса. Mapping нужен только
для объединенной аналитики по одному товару между Ozon и WB.
