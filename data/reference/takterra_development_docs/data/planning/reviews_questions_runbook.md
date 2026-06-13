# Инструкция по отзывам и вопросам TAKTERRA

Дата создания: 2026-06-11.

Назначение: безопасно собирать отзывы и вопросы Ozon/WB, готовить черновики
ответов и pending-пакет для владельца. Публикация ответов и отметка отзывов
просмотренными относятся к опасным операциям и запрещены без явного
подтверждения.

## Safety-цепочка

```text
read-only -> dry-run -> review -> approved -> apply -> verify -> result
```

Штатный сценарий реализует:

```text
read-only -> dry-run -> review/pending -> approved -> apply -> verify -> result
```

Apply-этап принимает только approved JSON-пакет и требует явный флаг
`--confirmed-by-user`.

## Команда

Read-only/dry-run:

```bash
PYTHONPATH=src python3 -m takterra_agent.cli reviews-questions --limit 100
```

Опции:

```bash
--marketplace all|ozon|wb
--limit 100
--run-id reviews_questions_YYYYMMDDTHHMMSS
```

Apply approved-пакета:

```bash
PYTHONPATH=src python3 -m takterra_agent.cli apply-reviews-questions \
  --approved-path data/approved/.../approved_apply_plan.json \
  --confirmed-by-user
```

Разрешенные action types в approved-пакете:

```text
public_review_reply
mark_review_viewed
question_answer (только WB)
```

`question_answer` разрешен только для WB-вопросов через официальный
`PATCH /api/v1/questions`. Для Ozon-вопросов apply-метод пока не реализован и
такой action должен блокироваться до отдельного исследования.

## Источники данных

### Wildberries

Источник: официальный WB Customer Communication API:

```text
https://dev.wildberries.ru/en/docs/openapi/user-communication
```

Используемые read-only методы:

```text
GET /api/v1/feedbacks/count-unanswered
GET /api/v1/questions/count-unanswered
GET /api/v1/feedbacks?isAnswered=false&take=N&skip=0&order=dateDesc
GET /api/v1/questions?isAnswered=false&take=N&skip=0&order=dateDesc
```

Документация WB также описывает write-методы:

```text
POST /api/v1/feedbacks/answer
PATCH /api/v1/questions
```

Их можно использовать только в будущем apply-этапе после approved-пакета.

Для доступа нужен токен категории `Feedbacks and Questions`. Постоянный локальный
secret file:

```text
.sessions/wb/wb_api_token.txt
```

`.env` должен указывать на этот файл через `TAKTERRA_WB_TOKEN_FILE`. Если
`WB_API_TOKEN` не задан и файл `TAKTERRA_WB_TOKEN_FILE` отсутствует, сценарий
фиксирует блокер `missing_credentials` и не переходит в ЛК.

### Ozon

По правилу API-first сначала выполняется read-only проверка официального Seller
API:

```text
POST /v1/review/count
POST /v1/review/list
```

На текущем ключе TAKTERRA 2026-06-11 оба метода вернули:

```text
HTTP 403: not available with existing subscription
```

Поэтому для Ozon используется fallback через уже подключенную ЛК-сессию/CDP:

```text
http://127.0.0.1:9444
```

Внутренние read-only endpoints ЛК:

```text
POST /api/review/counter
POST /api/v4/review/list
POST /api/v1/get-new-question-counter
POST /api/v1/question-counter
POST /api/v1/question-list
```

Внутренние write endpoints ЛК, используемые только на apply-этапе после
подтверждения владельца:

```text
POST /api/review/comment/create
POST /api/v2/review/change-interaction-status
```

`/api/v2/review/change-interaction-status` используется для пустых отзывов/
оценок без текста, где публичный ответ не нужен. Тело запроса содержит список
`review_status_list` со статусом `VIEWED`.

Перед чтением скрипт проверяет:

- доступность CDP;
- отсутствие экрана входа;
- отсутствие блокировки/incident/captcha;
- маркер магазина `TAKTERRA`.

## Артефакты запуска

Каждый запуск сохраняется в:

```text
data/runs/YYYY-MM-DD/reviews_questions_YYYYMMDDTHHMMSS/
```

Ключевые файлы:

```text
summary.json
reviews_questions_dry_run.md
processed/reviews_questions_items.json
processed/reviews_questions_actions.json
raw/ozon_api/
raw/ozon_lk/
raw/wb_api/
```

Pending-пакет для владельца:

```text
data/pending/reviews_questions_YYYYMMDDTHHMMSS_pending/
  manifest.json
  draft_answers.json
  draft_answers.csv
  APPROVAL_REQUIRED.md
```

## Классификация

Отзывы:

- `needs_owner_review_for_public_reply` - есть текст или WB позволяет ответ на
  необработанный отзыв; готовится черновик ответа;
- `priority_problem_review` - низкая оценка или претензия; черновик только для
  ручной проверки;
- `can_mark_viewed_after_owner_confirmation` - Ozon пустая оценка без текста;
  публичный ответ недоступен, можно отметить просмотренной только после
  подтверждения;
- `needs_manual_check` - нестандартный случай.

Вопросы:

- `needs_stock_check` - вопрос про наличие, количество, партию;
- `needs_product_context_check` - вопрос про размер, цвет, материал,
  совместимость, липучку/велкро;
- `needs_owner_review_for_answer` - можно готовить ответ, но перед отправкой
  владелец должен проверить текст;
- `needs_manual_check` - неполные данные.

## Правила текстов

- Не обещать скидки, подарки, связь вне маркетплейса или ручной возврат денег.
- Не спорить с покупателем.
- Для отрицательных отзывов не использовать короткую шаблонную благодарность.
- По вопросам о наличии/количестве не отвечать без проверки остатков.
- По характеристикам отвечать только фактами из карточки/master catalog или
  после подтверждения владельца.

## Первый запуск TAKTERRA

```text
run_id: reviews_questions_20260611T142015
overall_status: warning
items_count: 58
actions_count: 58
Ozon API: HTTP 403, fallback LK/CDP ok
Ozon reviews: 58
Ozon questions: 0
WB: missing_credentials из-за отсутствующего WB token file
public_review_reply: 2
mark_review_viewed: 56
```

Файлы:

```text
data/runs/2026-06-11/reviews_questions_20260611T142015/reviews_questions_dry_run.md
data/pending/reviews_questions_20260611T142015_pending/APPROVAL_REQUIRED.md
```

Ответы не опубликованы, отзывы не отмечены просмотренными.

## Восстановление WB API-токена 2026-06-11

Полученный от владельца WB API-файл перенесен из временного Telegram attachment
path в постоянную локальную секретную зону:

```text
.sessions/wb/wb_api_token.txt
```

Права файла:

```text
600
```

`.env` обновлен на постоянный путь `TAKTERRA_WB_TOKEN_FILE`. Содержимое токена
не выводилось в чат, отчеты или документы.

Контрольный WB read-only dry-run после восстановления:

```text
run_id: reviews_questions_20260611T143549
overall_status: ok
WB API: ok
WB reviews: 8
WB questions: 0
public_review_reply: 8
```

Файлы:

```text
data/runs/2026-06-11/reviews_questions_20260611T143549/reviews_questions_dry_run.md
data/pending/reviews_questions_20260611T143549_pending/APPROVAL_REQUIRED.md
```

Ответы не опубликованы.

## Apply ответов 2026-06-11

После согласования владельцем вариантов ответов создан approved-пакет:

```text
data/approved/reviews_questions_apply_20260611T145000/approved_apply_plan.json
data/approved/reviews_questions_apply_20260611T145000/approved_apply_plan.md
```

В пакет вошли только публичные ответы:

```text
Ozon public_review_reply: 2
WB public_review_reply: 8
mark_review_viewed: 0
```

Команда:

```bash
PYTHONPATH=src python3 -m takterra_agent.cli apply-reviews-questions \
  --approved-path data/approved/reviews_questions_apply_20260611T145000/approved_apply_plan.json \
  --confirmed-by-user
```

Результат:

```text
run_id: reviews_questions_apply_20260611T154941
overall_status: ok
WB sent: 8/8
Ozon sent: 2/2
Ozon NOT_VIEWED: 58 -> 56
Ozon PROCESSED: 65 -> 67
WB unanswered after verify: 0
```

Файл результата:

```text
data/runs/2026-06-11/reviews_questions_apply_20260611T154941/reviews_questions_apply_result.md
```

## Apply отметки пустых Ozon-отзывов 2026-06-11

По отдельному подтверждению владельца создан approved-пакет только для пустых
Ozon-оценок, на которые не нужен публичный ответ:

```text
data/approved/reviews_questions_mark_viewed_20260611T155509/approved_apply_plan.json
data/approved/reviews_questions_mark_viewed_20260611T155509/approved_apply_plan.md
```

Команда:

```bash
PYTHONPATH=src python3 -m takterra_agent.cli apply-reviews-questions \
  --approved-path data/approved/reviews_questions_mark_viewed_20260611T155509/approved_apply_plan.json \
  --confirmed-by-user
```

Результат:

```text
run_id: reviews_questions_apply_20260611T155516
overall_status: ok
Ozon marked viewed: 56/56
Ozon NOT_VIEWED: 56 -> 0
Ozon PROCESSED: 67 -> 67
Ozon VIEWED: 176 -> 232
```

Файл результата:

```text
data/runs/2026-06-11/reviews_questions_apply_20260611T155516/reviews_questions_apply_result.md
```

## Рекомендация по автоматизации

Следующий технический шаг: сделать отдельную команду подготовки approved-пакета
из pending-файлов, например:

```text
prepare-reviews-questions-approved --source-pending ... --mode replies-only|mark-viewed-only
```

Это уберет ручную сборку JSON и снизит риск включить в пакет лишний action type.

## Apply ответов 2026-06-12

Новый read-only/dry-run:

```text
run_id: reviews_questions_20260612T204913
overall_status: warning
items_count: 13
actions_count: 13
Ozon API: HTTP 403, fallback LK/CDP ok
Ozon reviews: 12
Ozon questions: 0
WB reviews: 1
WB questions: 0
public_review_reply: 2
mark_review_viewed: 11
```

После вывода нормального отчета в чат владелец подтвердил применение:

```text
согласовано
```

Создан approved-пакет:

```text
data/approved/reviews_questions_apply_20260612T205300/approved_apply_plan.json
data/approved/reviews_questions_apply_20260612T205300/approved_apply_plan.md
```

Команда:

```bash
PYTHONPATH=src python3 -m takterra_agent.cli apply-reviews-questions \
  --approved-path data/approved/reviews_questions_apply_20260612T205300/approved_apply_plan.json \
  --confirmed-by-user
```

Результат:

```text
run_id: reviews_questions_apply_20260612T210755
overall_status: ok
WB sent: 1/1
Ozon sent: 1/1
Ozon marked viewed: 11/11
Ozon NOT_VIEWED: 12 -> 0
Ozon PROCESSED: 67 -> 68
Ozon VIEWED: 232 -> 243
```

Контрольный read-only после apply:

```text
run_id: reviews_questions_20260612T210818
Ozon LK reviews: 0
Ozon LK questions: 0
WB reviews: 0
WB questions: 0
items_count: 0
actions_count: 0
```

Примечание: контрольный запуск вернул технический `overall_status: blocked`,
потому что новых действий для pending-пакета нет. При `reviews_count: 0`,
`questions_count: 0`, `feedbacks_count: 0` это означает, что необработанных
отзывов/вопросов после apply не осталось.
