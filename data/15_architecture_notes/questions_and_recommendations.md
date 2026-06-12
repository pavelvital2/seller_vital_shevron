# Vital Shevron Architecture Notes

## Решения 2026-06-12

- Проект Vital Shevron самостоятельный, отдельный от TAKTERRA.
- Секреты, cookies, storage state, `.env`, `.sessions`, runtime artifacts и
  pending/apply packages TAKTERRA не переносятся.
- До унификации seller SKU все функции должны работать по отдельным каталогам
  Ozon/WB и native marketplace IDs.
- Mapping Ozon/WB является optional слоем для объединенных отчетов и
  обязательным только для cross-marketplace write-операций по одному товару.
- После чистой установки нужен GitHub repository.

