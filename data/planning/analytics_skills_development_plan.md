# План развития аналитических skills/plugins

Дата: 2026-06-13.

## Краткий вывод

Сейчас проекту нужен легкий repo skill для повторяемой аналитики Ozon/WB, а не
отдельный Codex plugin или multi-agent runtime. Skill фиксирует правила
анализа, источники и формат отчетов без установки новых инструментов.

Plugin, Agents SDK, tracing и отдельные MCP-интеграции имеют смысл только после
стабилизации CLI-задач, `RunManifest`, `TaskRegistry`, source contracts и
форматов отчетов. До этого они добавят слой сложности без гарантированного
выигрыша.

## Что сделано сейчас

- Создан repo skill:

```text
.agents/skills/marketplace-analytics/SKILL.md
```

- Назначение skill: read-only аналитика маркетплейсов, SEO-аудит карточек,
  parser-позиции, поисковые запросы, цены/маржинальность, эффективность
  продвижения, отзывы/вопросы и повторяемые отчеты.
- Skill не выполняет write-операции в магазинах и не хранит секреты.
- Skill ссылается на действующие runbook-и проекта и safety-правила
  `AGENTS.md`.

## Что не делать сейчас

1. Не делать отдельный Codex plugin до стабилизации workflow.
2. Не внедрять Agents SDK только ради SEO-аналитики.
3. Не добавлять tracing до появления реального task-runner lifecycle и
   регулярных запусков.
4. Не ставить новые инструменты агентом. Если понадобится внешний инструмент,
   агент должен описать потребность, а установка выполняется централизованно.
5. Не смешивать skill с секретами, cookies, storage state, токенами и raw
   операционными выгрузками.

## Этап 1. Repo skill и ручная аналитика

Статус: выполнено частично.

Цель: дать fresh-агенту короткую точку входа для аналитики Ozon/WB без
повторного восстановления правил из длинной истории чата.

Что есть:

- `.agents/skills/marketplace-analytics/SKILL.md`;
- `data/planning/seo_audit_runbook.md`;
- `data/planning/search_queries_runbook.md`;
- `data/planning/ozon_parser_positions_runbook.md`;
- `data/planning/wb_parser_positions_runbook.md`;
- `data/planning/pricing_runbook.md`;
- `data/planning/ozon_cpc_efficiency_runbook.md`;
- `data/planning/wb_promotion_runbook.md`;
- `data/planning/reviews_questions_runbook.md`.

Следующие шаги:

1. Использовать skill в следующих SEO/parser/pricing/ads аналитических задачах.
2. После каждого реального анализа дополнять skill только теми правилами,
   которые повторяются и помогают fresh-агенту.
3. В каждом итоговом отчете по задаче с этим skill отдельно отмечать, найдено
   ли полезное улучшение для skill. Если улучшение есть, агент должен
   предложить владельцу конкретную правку.
4. Не превращать skill в архив отчетов: конкретные результаты хранить в
   `data/runs/` или профильных permanent-документах.

Критерий готовности:

- Fresh-агент может открыть `AGENTS.md`, затем skill и профильный runbook, после
  чего понимает, какие источники собрать и какой отчет подготовить.

## Этап 2. CLI-команды для аналитики

Статус: proposed.

Цель: перенести повторяемые части аналитики из ручной работы агента в
проверяемые команды.

Приоритетные команды:

```text
seo-audit --marketplace ozon|wb|all
ozon-parser-positions
wb-parser-positions
search-queries --marketplace ozon|wb|all --period week
pricing-status --marketplace ozon|wb|all
ads-effectiveness --marketplace ozon|wb|all
```

Требования:

- read-only режим по умолчанию;
- единый формат входов и выходов;
- сохранение derived-отчетов в `data/runs/<date>/<run_id>/`;
- отсутствие секретов в логах и отчетах;
- явные source timestamps;
- понятные skipped/limitations секции;
- тесты на парсинг входных файлов, сопоставление ID и формат отчетов.

Критерий готовности:

- Агент не пересобирает аналитику вручную там, где уже есть команда.
- Отчет можно воспроизвести по входным данным и `manifest.json`.

## Этап 3. RunManifest и TaskRegistry

Статус: proposed.

Цель: сделать аналитику частью общего task-runner, пригодного для Telegram-бота.

Связанные документы:

- `data/planning/telegram_bot_management_transition_plan.md`;
- `data/planning/recommendations_index.md`.

Что нужно:

1. `RunManifest` для каждого нового запуска.
2. `data/runs/index.jsonl` для поиска последних запусков.
3. `TaskRegistry` как единый источник CLI и будущего Telegram-бота.
4. Метаданные задач: marketplace, mode, risk, runbook, credentials, mapping,
   confirmation, telegram availability.

Критерий готовности:

- Telegram-бот сможет запускать read-only аналитику через registry, а не через
  отдельную ручную логику.

## Этап 4. Codex plugin

Статус: deferred.

Цель: упаковать устойчивые аналитические правила, шаблоны и helper-скрипты в
переиспользуемый plugin, если появится практическая необходимость.

Когда имеет смысл:

- есть несколько проектов-магазинов с одинаковой архитектурой;
- CLI-команды и отчеты стабилизированы;
- появились общие helper-скрипты, которые не должны жить в одном проекте;
- нужно централизованно обновлять skill, prompts, references и scripts.

Что должен включать будущий plugin:

- skill `marketplace-analytics`;
- reference contracts для Ozon/WB source datasets;
- scripts для валидации отчетов и manifest;
- prompts для типовых аналитических задач;
- безопасные инструкции без секретов и operational data.

Критерий готовности:

- Plugin сокращает дублирование между проектами, а не подменяет недоделанный
  task-runner.

## Этап 5. Agents SDK и tracing

Статус: deferred.

Цель: использовать multi-agent orchestration и трассировку только там, где
реально нужны несколько ролей или проверяемая история решений.

Потенциальные роли:

- data collector: собирает read-only источники;
- analyst: строит выводы и рекомендации;
- verifier: проверяет источники, ID mapping, ограничения и противоречия;
- reporter: формирует отчет для Telegram/файла.

Когда внедрять:

- после базового Telegram bot MVP;
- после появления регулярных задач и approval lifecycle;
- когда ручная проверка причин решений станет узким местом.

Критерий готовности:

- Tracing помогает проверять решения и ошибки, а не просто создает красивые
  логи.

## Риски

- Слишком ранний plugin усложнит проект и закрепит еще нестабильные форматы.
- Бесконтрольное расширение skill превратит его в шумный документ. Добавлять
  нужно только повторяемые правила, проверенные ограничения, source routes,
  recovery-сценарии, шаблоны отчетов и критерии качества.
- Без `RunManifest` аналитические отчеты будут плохо воспроизводимы.
- Без подтвержденного mapping нельзя делать надежные cross-marketplace выводы
  по конкретным товарам.
- Без source timestamps нельзя уверенно сравнивать Ozon и WB за один период.

## Следующий практический шаг

Для текущей SEO/parser аналитики использовать
`.agents/skills/marketplace-analytics/SKILL.md`, дождаться полного свежего WB
parser прохода и затем
собрать общий Ozon/WB SEO-отчет по одинаковому формату.
