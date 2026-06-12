# Reviews And Questions Runbook

## Read-only сбор

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m takterra_agent.cli reviews-questions --marketplace all
```

Для проверки только WB:

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m takterra_agent.cli reviews-questions --marketplace wb
```

Команда выполняет read-only сбор отзывов и вопросов, готовит отчет и не
публикует ответы.

## Apply

Ответы покупателям - опасная операция.

Цепочка:

```text
read-only -> draft answers -> owner review -> approved -> apply -> verify -> result
```

## Правило до унификации SKU

Отзывы/вопросы обрабатываются по native ID маркетплейса. Mapping нужен только
для объединенной аналитики по одному товару между Ozon и WB.
