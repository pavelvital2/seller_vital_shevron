# Follow-up Tasks

Файл для контрольных задач, которые нужно не потерять между сессиями агентов.
Если задача закрыта, обновить статус, дату закрытия и ссылку на отчет.

## Pending

### FU-2026-06-19-001 - Контроль Ozon Супербустинг

Статус: `first_control_done_pending_second_control`

Связанная операция:

- marketplace: `Ozon`
- акция: `Супербустинг`
- action_id: `3876484`
- тип API-акции: `STOCK_DISCOUNT`
- apply_run_id: `ozon_superboosting_apply_20260619T203448`
- approved_id: `ozon_superboosting_20260619T2033_owner_approved_91`
- включено товаров: `91`
- baseline_run_id: `ozon_superboosting_baseline_20260619T203748`
- основной baseline: `2026-06-12..2026-06-18`, `184` заказанные штуки,
  среднесуточно `26.2857` шт. по всему списку `91` товаров
- дополнительный baseline: `2026-06-05..2026-06-18`, `364` заказанные штуки,
  среднесуточно `26` шт. по всему списку `91` товаров
- исходный read-only анализ:
  `data/runs/2026-06-19/ozon_superboosting_probe_20260619T2020/ozon_superboosting_analysis.md`
- результат apply:
  `data/runs/2026-06-19/ozon_superboosting_apply_20260619T203448/ozon_superboosting_apply_result.md`
- baseline для сверки:
  `data/runs/2026-06-19/ozon_superboosting_baseline_20260619T203748/ozon_superboosting_baseline_report.md`
- индивидуальный baseline по товарам:
  `data/runs/2026-06-19/ozon_superboosting_baseline_20260619T203748/processed/ozon_superboosting_baseline_rows.csv`
- дневной baseline по каждому товару:
  `data/runs/2026-06-19/ozon_superboosting_baseline_20260619T203748/processed/ozon_superboosting_baseline_daily_by_product.csv`
- Excel baseline:
  `data/runs/2026-06-19/ozon_superboosting_baseline_20260619T203748/ozon_superboosting_baseline_91_products.xlsx`
- таблица кандидатов:
  `data/runs/2026-06-19/ozon_superboosting_probe_20260619T2020/processed/ozon_superboosting_candidates_analysis.csv`

Напоминания:

1. `2026-06-20` - первый контроль Ozon Супербустинг:
   - проверить, какие товары реально участвуют в акции;
   - собрать продажи/заказы по включенным `product_id`;
   - сравнить со среднесуточными заказанными штуками из baseline `7d`;
   - сравнение делать по каждому товару индивидуально, а не только общей
     суммой по списку;
   - для низкооборачиваемых товаров смотреть baseline `14d` как справку, но
     не подменять им основной критерий;
   - отметить товары, где продажи выросли, не изменились или упали.

   Статус: `done_partial_day_control`.

   Результат 2026-06-20 07:41 MSK:

   - контрольный run:
     `data/runs/2026-06-20/ozon_superboosting_control_20260620T074113/ozon_superboosting_control_report.md`;
   - сводный отчет по акциям:
     `data/runs/2026-06-20/actions_check_20260620T0742/actions_check_report_2026-06-20.md`;
   - активных товаров в супербустинге: `91` из `91`;
   - расхождений по цене: `0`;
   - ранний неполный день 2026-06-20: `7` заказанных штук,
     `4253.00` руб. выручки по Ozon Analytics;
   - решения на отключение по этому контролю не принимать: акция включена
     2026-06-19 в 20:34 MSK, проверка сделана утром 2026-06-20 и не является
     полным днем.
2. `2026-06-21` - второй контроль Ozon Супербустинг:
   - повторить проверку продаж/заказов;
   - если по товару среднесуточные продажи не выросли за два дня, не держать
     его дешевле без отдельной причины;
   - подготовить dry-run на исключение неэффективных товаров из
     `Супербустинг` или возврат к Elastic/обычной акции.

Критерий решения:

- `keep`: продажи выросли и маржа остается приемлемой;
- `watch`: данных мало, но есть заказы или положительная динамика;
- `remove_candidate`: продаж нет или среднесуточные продажи не выросли за два
  дня при сниженной цене;
- `blocked`: нет достоверных данных по продажам, участию в акции или цене.

Отчет владельцу:

- короткая Telegram-сводка;
- список `keep/watch/remove_candidate`;
- файл полного отчета с `product_id`, `sku`, `offer_id`, названием, ценой
  Elastic, ценой Superboosting, разницей цены, остатком, baseline `7d`,
  baseline `14d`, продажами после включения, сравнением по каждому товару и
  решением.
