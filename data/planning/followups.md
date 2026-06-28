# Follow-up Tasks

Файл для контрольных задач, которые нужно не потерять между сессиями агентов.
Если задача закрыта, обновить статус, дату закрытия и ссылку на отчет.

## Pending

### FU-2026-06-26-001 - Массовый аудит карточек Ozon/WB

Статус: `source_packages_ready_filtered`

Где остановились:

- задача: подготовка массового карточного аудита Vital Shevron перед
  унификацией названий, описаний, характеристик, фото, группировки и будущего
  мастер-паспорта;
- свежий `seo_query_pack` собран и подключен к `card-content-audit-packages`;
- полный запуск по всем строкам backlog:
  `card_audit_packages_seo_filtered_all_20260626T0838`;
- входных строк: `710`;
- создано audit packages для fresh-аудиторов: `645`;
- исключено из массового потока: `65`;
- причины исключения: `needs_manual_review` - `3`,
  `excluded_non_patch_assortment` - `62`;
- индекс пакетов:
  `data/catalog/content/card_audit_packages/card_audit_packages_seo_filtered_all_20260626T0838/package_index.csv`;
- индекс исключенных:
  `data/catalog/content/card_audit_packages/card_audit_packages_seo_filtered_all_20260626T0838/excluded_package_index.csv`;
- правило: fresh-аудиторам выдавать только карточки со статусами
  `ready` и `ready_broad_only`; `ready_broad_only` помечать как ограниченный
  broad-demand SEO без точной тематической частотности;
- статусы `needs_manual_review` и `excluded_non_patch_assortment` не выдавать
  аудиторам до отдельного решения владельца;
- write-операций с карточками Ozon/WB не было.

Следующий практический шаг:

1. Запускать fresh-аудиторов по `package_index.csv`, начиная с приоритета
   `business_priority=now` / `audit_priority=high`, если владелец не задаст
   другой порядок.
2. Каждый аудит сохранять в слой 2 `data/catalog/card_audits/` и проверять
   отдельным fresh-валидатором.
3. Не формировать Layer 3 `master_passport` и не готовить marketplace write
   dry-run до owner approval по результатам аудита.

### FU-2026-06-23-001 - Продолжить присвоение внутренних артикулов

Статус: `done_current_assortment_non_current_deferred`

Где остановились:

- задача: owner-review внутренних `internal_sku` для товаров, которые есть
  только на Ozon или только на WB;
- рабочий файл результата:
  `data/catalog/unified/internal_sku_assignment_owner_review.csv`;
- записано owner-review строк: `441`;
- утверждено/присвоено владельцем: `376` строк;
- отклонено без внутреннего артикула: `3` строки;
- отложено без внутреннего артикула: `62` строки;
- закрыты все строки со статусом `auto_candidate`: осталось `0`;
- дублей утвержденных `approved_internal_sku` на момент проверки нет;
- тест генератора `tests/test_catalog_internal_sku_plan.py` проходил:
  `7 passed`;
- активного непросмотренного остатка в
  `internal_sku_assignment_plan.csv` после вычитания owner-review CSV:
  `0` строк;
- текущий слой присвоения внутренних артикулов для актуального ассортимента
  закрыт.

Правила, которые уже зафиксированы:

- позывные идут как `pz` и не получают тематический префикс `raz`;
- `ВДВ` / воздушно-десантные войска относятся к `voisk`, даже если в названии
  также есть `СВО`;
- военная прокуратура относится к войсковой тематике `voisk`;
- военная полиция относится к войсковой тематике `voisk`;
- для `kit2` нарукавных комплектов силовых структур использовать
  `chev_kit2_nr_<structure>_pictNNNN`; для смешанных комплектов
  нагрудный + нарукавный место ношения не указывать;
- группа комплектов в `needs_owner_review` закрыта полностью;
- первая пачка одиночных шевронов записана:
  `batch_11_needs_review_chevrons_01`, отчет
  `data/runs/2026-06-23/internal_sku_owner_review_chevrons_batch_01/approved_rows.md`;
- `pict0016` отклонен без `internal_sku`: владелец указал, что товар снят с
  продажи из-за риска нарушения авторских прав;
- `back0016` и `back0017` проверены по второму фото: размер `215x70`, поэтому
  зафиксированы как наспинные `chev_back_fssp_text0001/0002`;
- `form0059` "Миротворческие силы" зафиксирован как нарукавный войсковой
  `chev_nr_voisk_text0008`, несмотря на размер `80x50`;
- по `mvdkit2nr0015` в локальном Ozon snapshot второго фото нет; строка
  зафиксирована как `chev_nr_mvd_pict0002` по первому фото и старому `offer_id`;
- вторая пачка одиночных шевронов записана:
  `batch_12_needs_review_chevrons_02`, отчет
  `data/runs/2026-06-23/internal_sku_owner_review_chevrons_batch_02/approved_rows.md`;
- для `batch_12_needs_review_chevrons_02` владелец подтвердил все строки как
  `pict`; поправки по тематике: строка 3 - `svo`, строка 6 - `svo`, строка
  8 - `raz`, строка 10 - `svo`;
- третья пачка одиночных шевронов записана:
  `batch_13_needs_review_chevrons_03`, отчет
  `data/runs/2026-06-23/internal_sku_owner_review_chevrons_batch_03/approved_rows.md`;
- для `batch_13_needs_review_chevrons_03` поправки владельца по тематике:
  строка 2 - `prikol`, 5 - `raz`, 8 - `raz`, 9 - `raz`, 10 - `raz`,
  11 - `prikol`, 12 - `prikol`, 13 - `raz`, 14 - `prikol`, 15 - `raz`;
- четвертая пачка одиночных шевронов записана:
  `batch_14_needs_review_chevrons_04`, отчет
  `data/runs/2026-06-23/internal_sku_owner_review_chevrons_batch_04/approved_rows.md`;
- для `batch_14_needs_review_chevrons_04` поправки владельца: строка 3 -
  нарукавный, строка 5 - нарукавный, строка 7 - `svo`, строка 10 - `svo`,
  строка 13 - `raz`, строка 16 - `svo`, строка 17 - `svo`;
- пятая пачка одиночных шевронов записана:
  `batch_15_needs_review_chevrons_05`, отчет
  `data/runs/2026-06-23/internal_sku_owner_review_chevrons_batch_05/approved_rows.md`;
- для `batch_15_needs_review_chevrons_05` поправки владельца: строка 1 -
  `bpla`, строка 3 - `raz`, строка 4 - `raz`, строка 9 - `raz`, строка
  16 - снята с продажи из-за нарушения авторских прав и записана как
  `owner_rejected_internal_sku` без `approved_internal_sku`;
- шестая пачка одиночных шевронов записана:
  `batch_16_needs_review_chevrons_06`, отчет
  `data/runs/2026-06-23/internal_sku_owner_review_chevrons_batch_06/approved_rows.md`;
- для `batch_16_needs_review_chevrons_06` поправки владельца: строка 1 -
  снята с продажи из-за нарушения авторских прав и записана как
  `owner_rejected_internal_sku` без `approved_internal_sku`; строка 3 - `kp`;
  строка 8 - `kp`; строки 14-18 - новая тематика `brig` ("бригады");
- седьмая пачка одиночных шевронов записана:
  `batch_17_needs_review_chevrons_07`, отчет
  `data/runs/2026-06-23/internal_sku_owner_review_chevrons_batch_07/approved_rows.md`;
- для `batch_17_needs_review_chevrons_07` владелец подтвердил остальные
  строки как предложено, а строки 10 и 11 исправил на `kp` ("на кепку");
  они записаны как `chev_kp_prikol_text0014` и
  `chev_kp_prikol_text0015`;
- восьмая пачка одиночных шевронов записана:
  `batch_18_needs_review_chevrons_08`, отчет
  `data/runs/2026-06-23/internal_sku_owner_review_chevrons_batch_08/approved_rows.md`;
- для `batch_18_needs_review_chevrons_08` владелец подтвердил все строки как
  предложено; это WB-позывные, записаны в формате `chev_pz_ng_textNNNN` без
  тематического `raz`;
- девятая пачка одиночных шевронов записана:
  `batch_19_needs_review_chevrons_09`, отчет
  `data/runs/2026-06-23/internal_sku_owner_review_chevrons_batch_09/approved_rows.md`;
- для `batch_19_needs_review_chevrons_09` владелец подтвердил все строки как
  предложено; это WB-позывные, записаны в формате `chev_pz_ng_textNNNN` без
  тематического `raz`;
- десятая пачка одиночных шевронов записана:
  `batch_20_needs_review_chevrons_10`, отчет
  `data/runs/2026-06-23/internal_sku_owner_review_chevrons_batch_10/approved_rows.md`;
- для `batch_20_needs_review_chevrons_10` владелец подтвердил остальные
  строки как предложено, а строки 2, 6 и 9 исправил: строка 2 -
  `kp + prikol` (`chev_kp_prikol_pict0005`), строка 6 - `kp`
  (`chev_kp_prikol_text0016`), строка 9 - `svo`
  (`chev_nr_svo_pict0052`);
- все строки `needs_owner_review` с `product_prefix=chev` закрыты; оставшиеся
  `11` строк `needs_owner_review` имеют неизвестный тип товара и не относятся
  к текущему ассортименту шевронов/нашивок/петлиц: подшивы, плащ-палатки,
  панамы, бафф/снуд и балаклава;
- оставшиеся `62` позиции non-current ассортимента записаны как
  `owner_deferred_internal_sku` в batch
  `batch_21_deferred_non_current_assortment`, отчет
  `data/runs/2026-06-23/internal_sku_owner_review_deferred_non_current_assortment/deferred_rows.md`;
- решение владельца: присвоение новых артикулов этим товарам отложить; на
  Ozon/WB этот товар пока поставляться не будет. В отложенные вошли
  `51` строка `unsupported_product_type` и `11` строк `needs_owner_review`
  с неизвестным типом товара;
- реальные seller SKU / `vendorCode` / `offer_id` в Ozon/WB пока не меняются,
  фиксируется только внутренний `internal_sku`.

Следующий практический шаг:

1. Не возвращать deferred non-current ассортимент в текущий слой
   `chev/nash/loop` без отдельного решения владельца.
2. Если владелец позже решит поставлять эти товары на Ozon/WB, сначала
   добавить новые типы/префиксы в `seller_sku_rules.md` и пересобрать
   `plan-internal-skus`.
3. Базовый read-only `pricing-status` подключен к unified catalog 2026-06-24.
   Fresh API price snapshots Ozon/WB подключены в ветке
   `feature/pricing-margin-live-snapshots`: `--refresh-api` сохраняет
   `ozon_product_info_prices*.json` и `wb_goods_prices.json` в run artifacts.
   Action-price из Ozon Elastic dry-run и WB actions dry-run также подключены.
   Следующий слой: подключить Ozon Superboosting/STOCK_DISCOUNT как отдельный
   источник и финансовую модель FBO/FBW для настоящих margin/min-price выводов.

### FU-2026-06-19-001 - Контроль Ozon Супербустинг

Статус: `additional_control_done_pending_review`

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

   Статус: `done_partial_day_control_pending_review`.

   Результат 2026-06-21 17:03 MSK:

   - контрольный run:
     `data/runs/2026-06-21/ozon_superboosting_control_20260621T170303/ozon_superboosting_control_report.md`;
   - сводный отчет по акциям:
     `data/runs/2026-06-21/actions_check_20260621T1705/actions_check_report_2026-06-21.md`;
   - активных товаров в супербустинге: `91` из `91`;
   - расхождений по цене: `0`;
   - за 2026-06-20: `32` заказанные штуки;
   - за неполный 2026-06-21 на 17:03 MSK: `25` заказанных штук;
   - всего за контрольный период: `57` заказанных штук, `31859.00` руб.
     выручки;
   - по индивидуальному сравнению с baseline `7d`: `24` товара `keep`,
     `67` товаров `remove_candidate`;
   - `remove_candidate` считать списком для отдельного review/dry-run, а не
     автоматическим решением о снятии: 2026-06-21 на момент проверки был
     неполным днем.
   - после применения Ozon Elastic `ozon_elastic_apply_20260621T170628`
     выполнена отдельная проверка, что `Супербустинг` не затронут:
     `data/runs/2026-06-21/ozon_superboosting_verify_after_elastic_20260621T170803/ozon_superboosting_verify_after_elastic_report.md`;
     результат `ok`, активных товаров `91/91`, расхождений по цене `0`.

3. `2026-06-23` - дополнительный контроль Ozon Супербустинг при проверке
   акций:

   Статус: `done_partial_day_control_pending_review`.

   Результат 2026-06-23 06:35 MSK:

   - контрольный run:
     `data/runs/2026-06-23/ozon_superboosting_check_20260623T0630/ozon_superboosting_check_report.md`;
   - сводный отчет по акциям:
     `data/runs/2026-06-23/actions_check_20260623T0630/actions_check_report_2026-06-23.md`;
   - активных товаров в супербустинге: `91` из `91`;
   - пропавших ожидаемых товаров: `0`;
   - лишних активных товаров: `0`;
   - расхождений по цене: `0`;
   - за полный день 2026-06-22: `34` заказанные штуки,
     `20653.00` руб. выручки;
   - за неполный 2026-06-23 на момент проверки: `3` заказанные штуки,
     `2074.00` руб. выручки;
   - предварительная классификация по товарам: `keep` - `16`, `watch` - `7`,
     `remove_candidate` - `68`;
   - 2026-06-23 на момент проверки был неполным днем, поэтому снятие товаров
     из `Супербустинга` выполнять только через отдельный dry-run/review и
     явное подтверждение владельца.
   - после применения Ozon Elastic `ozon_elastic_apply_actions_check_20260623`
     выполнена отдельная проверка, что `Супербустинг` не затронут:
     `data/runs/2026-06-23/ozon_superboosting_verify_after_elastic_20260623T064724/ozon_superboosting_verify_after_elastic_report.md`;
     результат `ok`, активных товаров `91/91`, расхождений по цене `0`.

4. `2026-06-24` - дополнительный контроль Ozon Супербустинг при проверке
   акций:

   Статус: `done_full_day_control_pending_review`.

   Результат 2026-06-24 08:24 MSK:

   - контрольный run:
     `data/runs/2026-06-24/ozon_superboosting_check_20260624T0820/ozon_superboosting_check_report.md`;
   - сводный отчет по акциям:
     `data/runs/2026-06-24/actions_check_20260624T0825/actions_check_report_2026-06-24.md`;
   - активных товаров в супербустинге: `91` из `91`;
   - пропавших ожидаемых товаров: `0`;
   - лишних активных товаров: `0`;
   - расхождений по цене: `0`;
   - за полный день 2026-06-23: `23` заказанные штуки,
     `14987.00` руб. выручки;
   - предварительная классификация по товарам: `keep` - `16`, `watch` - `3`,
     `remove_candidate` - `72`;
   - снятие товаров из `Супербустинга` выполнять только через отдельный
     dry-run/review и явное подтверждение владельца.
   - после применения Ozon Elastic `ozon_elastic_apply_actions_check_20260624`
     выполнена отдельная проверка, что `Супербустинг` не затронут:
     `data/runs/2026-06-24/ozon_superboosting_verify_after_elastic_20260624T0829/ozon_superboosting_verify_after_elastic_report.md`;
     результат `ok`, активных товаров `91/91`, расхождений по цене `0`.
   - после отдельного review и подтверждения владельца применен drift-пакет
     Ozon Elastic `ozon_elastic_apply_drift_review_20260624`: применено `4`
     строки activate/update, rejected `0`, verify `ok`; `2` новые fresh-строки
     `pzmh0081` и `bplapict0024` не применялись, потому что не входили в
     согласованный пакет.
   - после второго apply выполнена отдельная проверка, что `Супербустинг` не
     затронут:
     `data/runs/2026-06-24/ozon_superboosting_verify_after_elastic_drift_20260624T0840/ozon_superboosting_verify_after_elastic_report.md`;
     результат `ok`, активных товаров `91/91`, расхождений по цене `0`.

5. `2026-06-25` - дополнительный контроль Ozon Супербустинг при проверке
   акций:

   Статус: `done_full_day_control_pending_review`.

   Результат 2026-06-25:

   - контрольный run:
     `data/runs/2026-06-25/ozon_superboosting_check_20260625T0746/ozon_superboosting_check_report.md`;
   - сводный отчет по акциям:
     `data/runs/2026-06-25/actions_check_20260625T0758/actions_check_report_2026-06-25.md`;
   - активных товаров в супербустинге: `91` из `91`;
   - пропавших ожидаемых товаров: `0`;
   - лишних активных товаров: `0`;
   - расхождений по цене: `0`;
   - за полный день 2026-06-24: `19` заказанных штук,
     `10911.00` руб. выручки;
   - за неполный день 2026-06-25 на момент проверки: `3` заказанные штуки,
     `1377.00` руб. выручки;
   - предварительная классификация по товарам: `keep` - `15`, `watch` - `2`,
     `remove_candidate` - `74`;
   - снятие товаров из `Супербустинга` выполнять только через отдельный
     dry-run/review и явное подтверждение владельца.

6. `2026-06-26` - дополнительный контроль Ozon Супербустинг при проверке
   акций:

   Статус: `done_full_day_control_pending_review`.

   Результат 2026-06-26:

   - контрольный run:
     `data/runs/2026-06-26/ozon_superboosting_check_20260626T1015/ozon_superboosting_check_report.md`;
   - сводный отчет по акциям:
     `data/runs/2026-06-26/actions_check_20260626T1015/actions_check_report_2026-06-26.md`;
   - активных товаров в супербустинге: `91` из `91`;
   - пропавших ожидаемых товаров: `0`;
   - лишних активных товаров: `0`;
   - расхождений по цене: `0`;
   - за полный день 2026-06-25: `16` заказанных штук,
     `8923.00` руб. выручки;
   - за неполный день 2026-06-26 на момент проверки: `3` заказанные штуки,
     `1286.00` руб. выручки;
   - предварительная классификация по товарам: `keep` - `10`,
     `remove_candidate` - `81`;
   - снятие товаров из `Супербустинга` выполнять только через отдельный
     dry-run/review и явное подтверждение владельца.
   - после применения Ozon Elastic `ozon_elastic_apply_actions_check_20260626`
     выполнена отдельная проверка, что `Супербустинг` не затронут:
     `data/runs/2026-06-26/ozon_superboosting_verify_after_elastic_20260626T1025/ozon_superboosting_verify_after_elastic_report.md`;
     результат `ok`, активных товаров `91/91`, расхождений по цене `0`.

7. `2026-06-27` - дополнительный контроль Ozon Супербустинг при проверке
   акций:

   Статус: `done_full_day_control_pending_review`.

   Результат 2026-06-27:

   - контрольный run:
     `data/runs/2026-06-27/ozon_superboosting_check_20260627T1620/ozon_superboosting_check_report.md`;
   - сводный отчет по акциям:
     `data/runs/2026-06-27/actions_check_20260627T1625/actions_check_report.md`;
   - активных товаров в супербустинге: `91` из `91`;
   - пропавших ожидаемых товаров: `0`;
   - лишних активных товаров: `0`;
   - расхождений по цене: `0`;
   - за полный день 2026-06-26: `12` заказанных штук,
     `6546.00` руб. выручки;
   - за неполный день 2026-06-27 на момент проверки: `20` заказанных штук,
     `12307.00` руб. выручки;
   - предварительная классификация по товарам: `keep` - `20`,
     `remove_candidate` - `71`;
   - снятие товаров из `Супербустинга` выполнять только через отдельный
     dry-run/review и явное подтверждение владельца;
   - после применения Ozon Elastic `ozon_elastic_apply_20260627T162711`
     выполнена отдельная проверка, что `Супербустинг` не затронут:
     `data/runs/2026-06-27/ozon_superboosting_verify_after_elastic_20260627T1633/ozon_superboosting_verify_after_elastic_report.md`;
     результат `ok`, активных товаров `91/91`, расхождений по цене `0`.

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

### FU-2026-06-21-001 - WB снять с продажи товар с риском авторских прав

Статус: `pending_safety_review`

Источник:

- операция: WB-only catalog mapping review;
- run:
  `data/runs/2026-06-21/wb_only_mapping_review_20260621T1950/`;
- файл кандидатов:
  `data/runs/2026-06-21/wb_only_mapping_review_20260621T1950/wb_remove_from_sale_candidates.csv`.

Товар:

- marketplace: `WB`;
- global_no: `59`;
- vendorCode: `pzngol0026_222179`;
- nmID: `612405540`;
- barcode: `2047245690359`;
- название: `Шеврон на липучке позывной Сталкер олива`;
- причина владельца: `нарушение авторских прав`;
- текущий статус в mapping review: `нет на Ozon`, кандидат на снятие с продажи.

Порядок выполнения:

1. Не снимать товар с продажи в рамках catalog mapping.
2. Отдельно выполнить safety-проход по WB write-операции:
   `read-only -> dry-run -> review -> approved -> apply -> verify -> result`.
3. Перед apply проверить карточку WB, остатки/участие в акциях/рекламе и
   доступный API/LK-способ снятия с продажи.
4. После успешного снятия обновить профильную инструкцию и этот follow-up.

### FU-2026-06-21-002 - Будущая работа с ассортиментными пробелами Ozon/WB

Статус: `future_unscheduled`

Источник:

- правило владельца: товары, которые есть только на одной площадке, в будущем
  нужно рассматривать как ассортиментный backlog и кандидатов на перенос при
  наличии продаж/потенциала. Это не означает, что задача является следующим
  этапом по порядку.

Задача:

1. Когда владелец поставит эту задачу в очередь, разделить ассортиментные
   пробелы на `Ozon-only` и `WB-only`.
2. Для каждого товара собрать продажи, выкупы, остатки, маржу, SEO-видимость,
   фото и риски.
3. Найти товары, которые хорошо продаются на одной площадке и отсутствуют на
   другой.
4. Подготовить кандидатов на перенос между Ozon и WB.
5. Переносить только через карточный `transfer dry-run` с SEO-адаптацией и
   одинаковым по сути заполнением названия, характеристик, описания и фото.

Ограничения:

- не создавать новые карточки и не менять существующие без отдельного
  approval;
- не переносить товары с правовыми/контентными рисками без отдельного решения
  владельца;
- если поля Ozon/WB отличаются по формату, фиксировать различия в dry-run
  отчете.
