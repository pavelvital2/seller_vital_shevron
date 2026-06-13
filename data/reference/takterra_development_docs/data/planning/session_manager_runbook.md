# Session Manager Runbook

Дата создания: 2026-06-11.

Назначение: единый порядок управления локальными ЛК-сессиями Ozon/WB TAKTERRA
без ручного запуска отдельных shell-скриптов.

## Команды

Показать состояние обеих сессий:

```bash
PYTHONPATH=src python3 -m takterra_agent.cli sessions status
```

Показать состояние одной сессии:

```bash
PYTHONPATH=src python3 -m takterra_agent.cli sessions status --marketplace ozon
PYTHONPATH=src python3 -m takterra_agent.cli sessions status --marketplace wb
```

Запустить, остановить или перезапустить старый script-managed контур:

```bash
PYTHONPATH=src python3 -m takterra_agent.cli sessions start --marketplace ozon
PYTHONPATH=src python3 -m takterra_agent.cli sessions stop --marketplace ozon
PYTHONPATH=src python3 -m takterra_agent.cli sessions restart --marketplace ozon
```

Для WB аналогично использовать `--marketplace wb`. Для обоих маркетплейсов
использовать `--marketplace all`.

## Как читать статус

`overall_status: ok` означает, что:

- Ozon keeper или systemd service жив;
- Ozon refresh watchdog/timer жив;
- WB refresh watchdog/timer жив;
- последний успешный keepalive не просрочен;
- для Ozon открыт CDP `127.0.0.1:9444`.

В поле `watchdog_source`:

- `pid` - работает старый bash-loop watchdog;
- `systemd` - работает новый `systemd --user` timer.

В поле `refresh` смотреть:

```text
finished_at
age_seconds
interval_seconds
overdue_after_seconds
overdue
state_exported
values_printed
```

`overdue: true` или `status: warning/error` считать блокером для browser-based
write-операций до восстановления сессии.

## Восстановление Ozon-сессии

Команда dry-run:

```bash
PYTHONPATH=src python3 -m takterra_agent.cli restore-ozon-session --dry-run
```

Фактическое восстановление:

```bash
PYTHONPATH=src python3 -m takterra_agent.cli restore-ozon-session --email <email>
```

Сценарий делает:

1. Ставит локальный lock `data/locks/ozon-session-restore.lock`.
2. Если доступен `systemd --user`, останавливает
   `takterra-ozon-session-refresh.timer`,
   `takterra-ozon-session-refresh.service` и
   `takterra-ozon-keeper.service`.
3. Останавливает legacy script-managed watchdog/keeper, если они есть.
4. Проверяет живые процессы Chrome/Node на Ozon profile
   `.sessions/ozon/chrome-profile`.
5. Если процессы остались, завершает только TAKTERRA Ozon-процессы; если
   процессов нет, удаляет stale `SingletonLock`, `SingletonCookie`,
   `SingletonSocket`.
6. Запускает интерактивный вход `ozon_seller_interactive_login.js`; на сервере
   без `$DISPLAY` автоматически оборачивает запуск в `xvfb-run -a`.
7. После успешного или неуспешного входа запускает keeper и refresh timer
   обратно через `systemd --user`, если он доступен; иначе legacy scripts.
8. Сохраняет безопасный summary без cookies, storage state и кодов.

Если Ozon запрашивает код, вводить код только в интерактивный prompt. Коды не
записывать в файлы проекта, отчеты и сообщения.

### Инцидент восстановления 2026-06-12

При восстановлении проекта Ozon ЛК был в состоянии `Login required`, а
интерактивный вход через `restore-ozon-session` сначала не проходил по двум
причинам:

- headed Chrome запускался без `$DISPLAY`;
- после аварийной попытки старый keeper/Chrome держал
  `.sessions/ozon/chrome-profile`, из-за чего Chrome сообщал о
  `ProcessSingleton`.

Исправление внесено в `src/takterra_agent/sessions/manager.py`:

- `restore-ozon-session` использует `xvfb-run -a`, если нет `$DISPLAY`;
- перед логином останавливает `systemd --user` Ozon services/timer;
- очищает только TAKTERRA Ozon profile-процессы и stale `Singleton*` после
  проверки отсутствия живых PID.

Контрольный результат:

```text
restore_ozon_session_20260612T174933: overall_status ok
sessions_status_20260612T175227: overall_status ok
status_preflight_20260612T175227: overall_status ok
```

Ozon refresh interval: `1800` секунд. WB refresh interval: `3600` секунд.

## Systemd User Timers

Unit-файлы проекта:

```text
deploy/systemd/user/takterra-ozon-keeper.service
deploy/systemd/user/takterra-ozon-session-refresh.service
deploy/systemd/user/takterra-ozon-session-refresh.timer
deploy/systemd/user/takterra-wb-session-refresh.service
deploy/systemd/user/takterra-wb-session-refresh.timer
```

Dry-run установки:

```bash
PYTHONPATH=src python3 -m takterra_agent.cli install-session-systemd
```

Установка и переключение с bash-loop на systemd:

```bash
PYTHONPATH=src python3 -m takterra_agent.cli install-session-systemd --apply --switch
```

Команда копирует unit-файлы в:

```text
/home/pavel/.config/systemd/user/
```

Затем выполняет:

- `systemctl --user daemon-reload`;
- останавливает старые bash-loop watchdog/keeper;
- включает и перезапускает `takterra-ozon-keeper.service`;
- включает и перезапускает Ozon/WB timers;
- запускает первый Ozon/WB keepalive через systemd service.

Проверка timers:

```bash
systemctl --user list-timers 'takterra-*' --no-pager
```

Проверка service/timer статуса:

```bash
systemctl --user status takterra-ozon-keeper.service \
  takterra-ozon-session-refresh.timer \
  takterra-wb-session-refresh.timer --no-pager
```

## Текущее состояние

2026-06-11 выполнено переключение на `systemd --user`.

Контроль:

```text
sessions_status_20260611T000502: overall_status ok
status_preflight_20260611T000512: overall_status ok
```

Текущие интервалы:

```text
Ozon timer: 30 минут
WB timer: 60 минут
```

После переключения `status-preflight` видит:

```text
Ozon watchdog source: systemd
WB watchdog source: systemd
Ozon keeper: systemd service + pid file
Ozon CDP: 127.0.0.1:9444 ok
```
