# Vital Shevron Recommendations Index

Дата старта: 2026-06-12

| ID | Рекомендация | Статус | Детали | Следующий шаг |
| --- | --- | --- | --- | --- |
| VS-REC-001 | Сохранить самостоятельный проект Vital Shevron отдельно от TAKTERRA, без общих секретов, сессий и operational data | implemented | `AGENTS.md`, `README.md`, `project_map.md` | После валидации сделать локальный git и GitHub repository |
| VS-REC-002 | До унификации seller SKU весь функционал должен работать через отдельные Ozon/WB каталоги и native marketplace IDs | implemented | `catalog_mapping_runbook.md` | API подключены, `fetch-catalog` выполнен: Ozon `548`, WB `431`, exact seller SKU matches `98`; следующий шаг - owner review mapping draft |
| VS-REC-003 | Mapping Ozon/WB не является глобальным стоп-фактором: он обязателен только для cross-marketplace product operations | implemented | `AGENTS.md`, `catalog_mapping_runbook.md` | В каждом новом сценарии явно указать, нужен ли mapping |
| VS-REC-004 | Унификация артикулов продавца Ozon/WB - отдельная опасная операция | proposed | `catalog_mapping_runbook.md` | После owner review `783` unmatched/mapping строк проверить API/ЛК ограничения Ozon/WB и подготовить dry-run rename plan |
| VS-REC-005 | Создать GitHub repository после чистой установки и проверки `.gitignore`/секретов | implemented | `vital_shevron_bootstrap_plan.md` | GitHub repo создан: `https://github.com/pavelvital2/seller_vital_shevron`, visibility `PRIVATE`, branch `main` |
| VS-REC-006 | Переименовать Python package `takterra_agent` в нейтральный `seller_agent` или `vital_shevron_agent` отдельным этапом | proposed | `vital_shevron_bootstrap_plan.md` | Вернуться после первого успешного API preflight Vital Shevron |
| VS-REC-007 | Сохранить будущий мультиконтур как отдельное направление обмена функциями между TAKTERRA и Vital Shevron | proposed | `vital_shevron_bootstrap_plan.md` | После стабилизации двух проектов спроектировать `ContourProfile` и переносимые патчи |
| VS-REC-008 | Перевести LK refresh с legacy watchdog-процессов на `systemd --user` timers после подключения WB | proposed | `session_manager_runbook.md` | Сначала подключить WB LK, затем выполнить `install-session-systemd --apply --switch` и проверить автозапуск |
| VS-REC-009 | Сделать единый безопасный loader `.env` для shell-скриптов, чтобы значения с пробелами не ломали refresh | proposed | `session_manager_runbook.md` | Вынести parser в общий shell helper и заменить ad-hoc чтение env в session scripts |
