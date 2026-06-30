# External Reviews

В этой папке хранятся внешние review-документы по проекту. Они не являются
источником истины вместо `AGENTS.md` и `data/planning/*`, но используются как
справочный слой для проверки архитектурных решений, очередности развития и
рисков.

## Документы

- `2026-06-13_gpt_pro_repository_review.md` - внешний review репозитория
  через GitHub от 2026-06-13. Основная рекомендация: строить Telegram-бота не
  поверх прямых CLI-вызовов, а через `RunManifest`, расширенный
  `TaskRegistry`, `WorkflowRunner`, `SafetyGuard`, approval lifecycle и locks.
- `2026-06-25_gpt_pro_repository_review.md` - внешний review текущего `main`
  от 2026-06-25. Основная рекомендация: основу проекта не переписывать, но
  временно остановить расширение бизнес/write-функций и стабилизировать
  runtime-ядро: `SQLite Job Store`, `TaskRegistry v2`, единый `JobRunner`,
  атомарные approvals/resource locks, Telegram update deduplication и перевод
  Telegram из исполнителя задач в диспетчер job-ов.

## Как использовать

1. Перед планированием нового слоя Telegram/task-runner сверять его с этим
   review как с архитектурным baseline.
2. Если замечание из review уже закрыто, проверять актуальный статус в
   `data/planning/recommendations_index.md`, `data/planning/project_map.md` и
   профильном runbook.
3. Review от 2026-06-25 считать более актуальным для runtime-ядра, чем review
   от 2026-06-13, потому что он уже учитывает `seller_agent`, `RunManifest`,
   расширенный `TaskRegistry`, approval packages, read-only `WorkflowRunner` и
   Telegram MVP.
4. Не переносить выводы из review автоматически в действующие правила:
   действующие правила фиксируются в `AGENTS.md` и профильных runbook.
5. Если новый внешний review прислан повторно и нормализованно совпадает с уже
   сохраненным файлом, не создавать дубль; зафиксировать проверку в рабочем
   отчете или кратком summary.

## Проверка 2026-06-30

Вложение `анализ_gpt_pro_13.06.26.md`, присланное 2026-06-30 из
`telegram-ai-agent/data`, нормализованно совпало с каноническим файлом
`2026-06-13_gpt_pro_repository_review.md`. Отличие было только техническим на
уровне байтового представления, поэтому отдельный дубликат не создан.
