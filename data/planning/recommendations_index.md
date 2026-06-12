# Vital Shevron Recommendations Index

Дата старта: 2026-06-12

| ID | Рекомендация | Статус | Детали | Следующий шаг |
| --- | --- | --- | --- | --- |
| VS-REC-001 | Сохранить самостоятельный проект Vital Shevron отдельно от TAKTERRA, без общих секретов, сессий и operational data | implemented | `AGENTS.md`, `README.md`, `project_map.md` | После валидации сделать локальный git и GitHub repository |
| VS-REC-002 | До унификации seller SKU весь функционал должен работать через отдельные Ozon/WB каталоги и native marketplace IDs | implemented | `catalog_mapping_runbook.md` | После API credentials выполнить `fetch-catalog` и построить mapping draft |
| VS-REC-003 | Mapping Ozon/WB не является глобальным стоп-фактором: он обязателен только для cross-marketplace product operations | implemented | `AGENTS.md`, `catalog_mapping_runbook.md` | В каждом новом сценарии явно указать, нужен ли mapping |
| VS-REC-004 | Унификация артикулов продавца Ozon/WB - отдельная опасная операция | proposed | `catalog_mapping_runbook.md` | После подтвержденного mapping проверить API/ЛК ограничения Ozon/WB и подготовить dry-run rename plan |
| VS-REC-005 | Создать GitHub repository после чистой установки и проверки `.gitignore`/секретов | proposed | `vital_shevron_bootstrap_plan.md` | После тестов выполнить `git init`, проверить `git status --short`, согласовать имя remote |
| VS-REC-006 | Переименовать Python package `takterra_agent` в нейтральный `seller_agent` или `vital_shevron_agent` отдельным этапом | proposed | `vital_shevron_bootstrap_plan.md` | Вернуться после первого успешного API preflight Vital Shevron |
| VS-REC-007 | Сохранить будущий мультиконтур как отдельное направление обмена функциями между TAKTERRA и Vital Shevron | proposed | `vital_shevron_bootstrap_plan.md` | После стабилизации двух проектов спроектировать `ContourProfile` и переносимые патчи |

