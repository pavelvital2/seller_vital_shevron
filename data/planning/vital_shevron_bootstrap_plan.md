# Vital Shevron Bootstrap Plan

## Итог

Цель первого этапа: чистый самостоятельный проект, который запускает тесты,
не содержит секретов TAKTERRA, готов к подключению API/LK Vital Shevron и может
быть вынесен в GitHub repository.

## Шаги

1. Проверить чистоту переноса:

```bash
find . -path './.sessions/*' -type f -print
find . -name '.env' -print
find data/runs data/pending data/approved -type f -print
```

2. Проверить код:

```bash
PYTHONPATH=src pytest -q
PYTHONPATH=src python3 -m takterra_agent.cli --help
```

3. Подготовить секреты Vital Shevron во внешних файлах:

```text
.sessions/ozon/ozon_seller_api_credentials.txt
.sessions/ozon/ozon_performance_api_credentials.txt
.sessions/wb/wb_api_token.txt
```

4. Скопировать `.env.example` в `.env` и заполнить только пути/настройки.
Ключи в `.env` не хранить.

5. Выполнить read-only API preflight:

```bash
PYTHONPATH=src python3 -m takterra_agent.cli status-preflight --skip-lk
```

6. Получить первые каталоги:

```bash
PYTHONPATH=src python3 -m takterra_agent.cli fetch-catalog
```

7. Построить mapping draft Ozon/WB.

8. Подключить ЛК Ozon/WB через отдельные сессии и systemd timers.

9. После чистой валидации создать git/GitHub.

## GitHub Stage

Статус на 2026-06-12:

```text
local git: created
main commit: b79434a Initial Vital Shevron scaffold
GitHub remote: created
remote: https://github.com/pavelvital2/seller_vital_shevron
visibility: PRIVATE
default branch: main
```

```bash
git init
git status --short
```

Перед commit убедиться, что в статусе нет:

```text
.env
.sessions/
tmp/auth/
data/runs/*
data/pending/*
data/approved/*
data/catalog/*/raw/*
data/catalog/*/processed/*
```

Remote GitHub создан и подключен как `origin`.

## Ограничения

- До получения API credentials `status-preflight` может показывать ошибки
  credentials. Это ожидаемо и не означает поломку каркаса.
- До подключения ЛК browser-based сценарии будут недоступны.
- До подтвержденного mapping нельзя выполнять cross-marketplace write-операции
  по одному товару.
