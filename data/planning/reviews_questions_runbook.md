# Reviews And Questions Runbook

## Read-only сбор

```bash
PYTHONPATH=src python3 -m takterra_agent.cli reviews-questions --dry-run
```

## Apply

Ответы покупателям - опасная операция.

Цепочка:

```text
read-only -> draft answers -> owner review -> approved -> apply -> verify -> result
```

## Правило до унификации SKU

Отзывы/вопросы обрабатываются по native ID маркетплейса. Mapping нужен только
для объединенной аналитики по одному товару между Ozon и WB.

