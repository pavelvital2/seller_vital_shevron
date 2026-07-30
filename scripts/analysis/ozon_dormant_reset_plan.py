#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
from datetime import datetime
from decimal import Decimal, InvalidOperation
import hashlib
import html
import json
from pathlib import Path
import re
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from seller_agent.core.run_manifest import write_summary_run_manifest


VARIABLE_COST_RATE = Decimal("0.5653")
RETAINED_SHARE = Decimal("0.4347")
LOGISTICS_PER_PRODUCT = Decimal("94")
UNIT_COST = Decimal("85")
GROWTH_BID = Decimal("3.00")
CAMPAIGN_ID = "20233460"
OLD_GROUPS = {"never_sold_180d_plus", "dormant_180d_plus", "dormant_90_179d"}


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build an owner-review dry-run for dormant Ozon inventory.")
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--dormant-csv", required=True)
    parser.add_argument("--actions-run-dir", required=True)
    parser.add_argument("--boost-probe-run-dir", required=True)
    parser.add_argument("--cpc-run-dir", required=True)
    parser.add_argument("--wb-signals-csv", required=True)
    parser.add_argument("--unified-csv", default="data/catalog/unified/products.csv")
    parser.add_argument("--run-id")
    return parser.parse_args()


def _decimal(value: Any) -> Decimal:
    if value in (None, ""):
        return Decimal("0")
    try:
        return Decimal(str(value).replace(" ", "").replace(",", "."))
    except (InvalidOperation, ValueError):
        return Decimal("0")


def _money(value: Any) -> str:
    return f"{_decimal(value).quantize(Decimal('0.01'))}"


def _preserve_min_price(current_min_price: Any) -> str:
    return _money(current_min_price)


def _read_csv(path: Path, *, delimiter: str = ",") -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle, delimiter=delimiter))


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def _result_products(payload: dict[str, Any]) -> list[dict[str, Any]]:
    result = payload.get("result") or {}
    rows = result.get("products") or result.get("items") or []
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _elastic_options(actions_run_dir: Path) -> dict[str, list[dict[str, Any]]]:
    options: dict[str, list[dict[str, Any]]] = {}
    raw_dir = actions_run_dir / "raw"
    for group in ("active", "candidate"):
        for path in sorted(raw_dir.glob(f"ozon_action_1977747_{group}_page_*.json")):
            for row in _result_products(json.loads(path.read_text(encoding="utf-8"))):
                product_id = str(row.get("id") or row.get("product_id") or "")
                if not product_id:
                    continue
                pairs = (
                    ("elastic_max", row.get("price_max_elastic"), row.get("max_boost")),
                    ("elastic_min", row.get("price_min_elastic"), row.get("min_boost")),
                    ("elastic_current", row.get("action_price"), row.get("current_boost")),
                )
                for pair_name, price, boost in pairs:
                    if _decimal(price) <= 0 or _decimal(boost) <= 0:
                        continue
                    options.setdefault(product_id, []).append(
                        {
                            "action_id": "1977747",
                            "action_name": "Эластичный бустинг. Без ограничения срока действия",
                            "action_type": "ELASTIC_BOOSTING",
                            "source_group": group,
                            "pair_name": pair_name,
                            "target_action_price": _money(price),
                            "boost_score": _money(boost),
                            "boost_source": pair_name,
                            "current_action_price": _money(row.get("action_price")),
                        }
                    )
    return options


def _boost_sources(probe_run_dir: Path) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for path in sorted((probe_run_dir / "raw").glob("highlight_*_*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        url = str((payload.get("meta") or {}).get("url") or "")
        match = re.search(r"/seller-actions/(\d+)(?:[/?]|$)", url)
        body = payload.get("body")
        if not match or not isinstance(body, dict):
            continue
        action = body.get("action")
        if not isinstance(action, dict):
            continue
        action_id = match.group(1)
        description = re.sub(r"<[^>]+>", " ", str(action.get("description") or ""))
        boost_context = re.findall(r"(?:бустинг|буст)[^%]{0,160}?(\d+(?:[,.]\d+)?)\s*%", description, flags=re.I)
        boost_values = [_decimal(value) for value in boost_context]
        numeric_boost = max(boost_values) if boost_values else Decimal("0")
        result[action_id] = {
            "action_id": action_id,
            "title": str(action.get("title") or ""),
            "action_type": str(action.get("actionType") or ""),
            "date_start": str(action.get("dateStart") or action.get("startDate") or ""),
            "date_end": str(action.get("dateEnd") or action.get("endDate") or ""),
            "boost_percent": _money(numeric_boost) if numeric_boost > 0 else "",
            "boost_source": "lk_seller_actions_description" if numeric_boost > 0 else "not_numeric_in_description",
            "description_excerpt": " ".join(description.split())[:800],
        }
    return result


def _stock_action_options(
    actions_run_dir: Path,
    boost_sources: dict[str, dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    rows = _read_csv(actions_run_dir / "ozon_actions_optimizer_offers.csv", delimiter=";")
    options: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        if row.get("action_type") == "ELASTIC_BOOSTING":
            continue
        product_id = str(row.get("product_id") or "")
        target = _decimal(row.get("target_action_price"))
        source = boost_sources.get(str(row.get("action_id") or ""), {})
        boost = _decimal(source.get("boost_percent") or row.get("boost_score"))
        if not product_id or target <= 0 or boost <= 0:
            continue
        options.setdefault(product_id, []).append(
            {
                "action_id": row.get("action_id") or "",
                "action_name": row.get("action_name") or source.get("title") or "",
                "action_type": row.get("action_type") or source.get("action_type") or "",
                "source_group": row.get("source_group") or "",
                "pair_name": "stock_action",
                "target_action_price": _money(target),
                "boost_score": _money(boost),
                "boost_source": source.get("boost_source") or row.get("boost_source") or "",
                "current_action_price": _money(row.get("current_action_price")),
            }
        )
    return options


def _price_index(actions_run_dir: Path) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    prices = json.loads(
        (actions_run_dir / "processed" / "prices_safe_snapshot.json").read_text(encoding="utf-8")
    )
    by_product = {str(key): value for key, value in prices.items()}
    offer_to_product = {
        str(row.get("offer_id") or ""): product_id
        for product_id, row in by_product.items()
        if row.get("offer_id")
    }
    return by_product, offer_to_product


def _group(
    row: dict[str, str],
    *,
    wb_nm_id: str,
    wb_sales_90d: Decimal,
) -> tuple[str, str]:
    old = row.get("group") in OLD_GROUPS
    weak = _decimal(row.get("buyouts_total")) <= 4
    if old and weak and wb_nm_id and wb_sales_90d == 0:
        return "1", "90+ дней без выкупа, не более 4 выкупов за жизнь, на WB продаж за 90 дней нет"
    if _decimal(row.get("buyouts_total")) >= 5:
        return "2", "на Ozon есть подтвержденный исторический спрос"
    if old and weak and wb_sales_90d > 0:
        return "2", "Ozon слабый, но свежие продажи WB подтверждают спрос"
    return "3", "слабая или неполная история: один финальный 14-дневный запуск"


def _choose_action(
    options: list[dict[str, Any]],
    *,
    group: str,
    min_price: Decimal,
) -> dict[str, Any] | None:
    candidates = options
    if group == "3":
        candidates = [row for row in candidates if _decimal(row.get("target_action_price")) >= min_price]
    if not candidates:
        return None
    return sorted(
        candidates,
        key=lambda row: (_decimal(row.get("boost_score")), _decimal(row.get("target_action_price"))),
        reverse=True,
    )[0]


def _cpc_plan(
    *,
    current_bid: Decimal,
    in_campaign: bool,
    orders: Decimal,
    cpa: Decimal,
    drr: Decimal,
    pack_qty: int,
) -> tuple[str, Decimal, str]:
    efficient = orders > 0 and cpa <= Decimal(50 * pack_qty) and drr <= Decimal("12")
    if not in_campaign:
        return "add_to_campaign_review", GROWTH_BID, "Добавить в действующую CPC-кампанию со стартовой ставкой 3 ₽."
    if current_bid > GROWTH_BID and efficient:
        return "keep_effective_bid", current_bid, "Ставка выше 3 ₽ уже дает заказы в допустимой экономике."
    if current_bid == GROWTH_BID:
        return "keep_bid", current_bid, "Ставка уже соответствует единому уровню запуска 3 ₽."
    if current_bid < GROWTH_BID:
        return "raise_bid", GROWTH_BID, "Повысить до 3 ₽: это выше 75-го процентиля эффективных ставок магазина."
    return "reduce_to_controlled_bid", GROWTH_BID, "Снизить до 3 ₽: повышенная ставка не подтверждена заказами."


def _sha(rows: list[dict[str, Any]]) -> str:
    payload = json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _xlsx(path: Path, rows: list[dict[str, Any]], summary_rows: list[list[Any]]) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "План 138 SKU"
    headers = list(rows[0]) if rows else ["empty"]
    sheet.append(headers)
    for row in rows:
        sheet.append([row.get(key, "") for key in headers])
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    for cell in sheet[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="263238")
        cell.alignment = Alignment(wrap_text=True)
    for column in sheet.columns:
        width = min(max(max(len(str(cell.value or "")) for cell in column) + 2, 11), 55)
        sheet.column_dimensions[get_column_letter(column[0].column)].width = width

    summary = workbook.create_sheet("Сводка")
    for row in summary_rows:
        summary.append(row)
    for cell in summary[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="263238")
    summary.column_dimensions["A"].width = 42
    summary.column_dimensions["B"].width = 24

    method = workbook.create_sheet("Методика")
    method_rows = [
        ["Параметр", "Правило"],
        ["Группа 1", "90+ дней без выкупа, <=4 исторических выкупа, есть WB-пара, продаж WB за 90 дней нет."],
        ["Группа 2", ">=5 исторических выкупов Ozon либо свежие продажи подтвержденной WB-пары."],
        ["Группа 3", "Оставшиеся карточки; финальный прибыльный запуск на 14 дней."],
        ["Акции групп 1-2", "Абсолютный максимальный подтвержденный бустинг; действующий min_price сохраняется как отдельный нижний порог."],
        ["Акции группы 3", "Максимальный подтвержденный бустинг при цене не ниже min_price."],
        ["CPC", "Стартовая/контрольная ставка 3 ₽; hard stop 50 ₽ на физическое изделие без завершенного выкупа."],
        ["Экономика", "Консервативно: цена * 43,47% - 94 ₽ логистика - 85 ₽ * pack_qty; компенсации Ozon не учтены."],
    ]
    for row in method_rows:
        method.append(row)
    for cell in method[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="263238")
    method.column_dimensions["A"].width = 28
    method.column_dimensions["B"].width = 110
    workbook.save(path)


def _html(path: Path, *, rows: list[dict[str, Any]], summary: dict[str, Any], run_id: str) -> None:
    cards = []
    for row in rows:
        action = html.escape(str(row["target_action_name"] or "Нет подтвержденной акции"))
        risk = "loss" if _decimal(row["estimated_margin_before_cpc"]) < 0 else "ok"
        cards.append(
            f"""<tr data-group="{row['decision_group']}" data-text="{html.escape((str(row['offer_id'])+' '+str(row['title'])).lower())}">
<td><b>{row['decision_group']}</b><small>{html.escape(str(row['group_reason']))}</small></td>
<td><b>{html.escape(str(row['offer_id']))}</b><small>product {row['product_id']} · SKU {row['ozon_sku']}</small></td>
<td>{html.escape(str(row['title']))}<small>остаток {row['stock_products']} товаров / {row['stock_physical_pieces']} изделий</small></td>
<td><b>{action}</b><small>{row['target_boost_percent']}% · {row['target_action_price']} ₽ · {html.escape(str(row['action_operation']))}</small></td>
<td><b>{row['current_min_price']} → {row['target_min_price']} ₽</b><small>учёт min_price: {html.escape(str(row['min_price_policy']))}</small></td>
<td><b>{row['current_cpc_bid']} → {row['target_cpc_bid']} ₽</b><small>{html.escape(str(row['cpc_action']))}; лимит {row['cpc_stop_spend']} ₽</small></td>
<td class="{risk}"><b>{row['estimated_margin_before_cpc']} ₽</b><small>консервативно до CPC; WB 90д: {row['wb_sales_90d']}</small></td>
</tr>"""
        )
    group_metrics = summary["groups"]
    metrics = "".join(
        f"<div class='metric'><strong>{key}</strong><span>{value['sku']} SKU · {value['stock_products']} товаров · "
        f"{value['projected_margin_full_stock_before_cpc']} ₽ до CPC</span></div>"
        for key, value in group_metrics.items()
    )
    page = f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Ozon: перезапуск залежавшихся остатков</title>
<style>
:root{{--bg:#f4f6f7;--paper:#fff;--ink:#172025;--muted:#5d6b72;--line:#d9e0e3;--red:#a52a2a;--green:#176c43;--blue:#1565c0}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 Arial,sans-serif;letter-spacing:0}}
header,main{{max-width:1500px;margin:auto;padding:18px}}header{{background:#263238;color:#fff;max-width:none}}header>div{{max-width:1500px;margin:auto}}
h1{{font-size:26px;margin:0 0 8px}}h2{{font-size:19px;margin:0 0 12px}}p{{margin:6px 0}}
.notice{{border-left:5px solid #ef6c00;background:#fff3e0;padding:12px;margin:16px 0}}.grid{{display:grid;grid-template-columns:repeat(4,minmax(170px,1fr));gap:10px;margin:14px 0}}
.metric{{background:var(--paper);border:1px solid var(--line);padding:12px;border-radius:6px}}.metric strong{{display:block;font-size:24px}}.metric span,small{{display:block;color:var(--muted);margin-top:3px}}
.tools{{display:flex;gap:8px;flex-wrap:wrap;margin:14px 0}}input,select{{height:38px;border:1px solid #aeb9be;border-radius:4px;background:#fff;padding:0 10px;font:inherit}}input{{min-width:300px}}
.table{{overflow:auto;background:#fff;border:1px solid var(--line)}}table{{border-collapse:collapse;width:100%;min-width:1350px}}th,td{{padding:9px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}}th{{position:sticky;top:0;background:#37474f;color:#fff}}tr:hover{{background:#f2f7f9}}td.loss{{color:var(--red)}}td.ok{{color:var(--green)}}
.section{{background:#fff;border:1px solid var(--line);padding:16px;margin:14px 0}}code{{background:#e8eef1;padding:2px 5px;border-radius:3px}}
@media(max-width:760px){{header,main{{padding:12px}}h1{{font-size:21px}}.grid{{grid-template-columns:1fr 1fr}}input{{min-width:0;width:100%}}}}
</style></head><body>
<header><div><h1>Ozon: перезапуск 138 залежавшихся SKU</h1><p>Run ID: <code>{html.escape(run_id)}</code> · режим dry-run · изменений в Ozon нет</p></div></header>
<main>
<div class="notice"><b>Решение владельца.</b> Для групп 1–2 выбирается абсолютный максимальный подтверждённый бустинг, а действующая минимальная цена сохраняется отдельным нижним порогом. Для группы 3 сохраняется прибыльный ограничитель.</div>
<div class="grid">{metrics}<div class="metric"><strong>{summary['target_boost_distribution'].get('75.00',0)}</strong><span>SKU с целевым бустингом 75%</span></div></div>
<section class="section"><h2>Что будет применяться только после отдельного согласования</h2>
<p>Группы 1–2: сохранить действующую минимальную цену без изменений, подключить или обновить Elastic только при фактическом расхождении, привести CPC к 3 ₽ и запустить контроль расхода.</p>
<p>Группа 3: финальный 14-дневный запуск при цене не ниже min_price. Hard stop CPC: 50 ₽ на физическое изделие без завершённого выкупа.</p></section>
<div class="tools"><input id="q" placeholder="Поиск по артикулу или названию"><select id="g"><option value="">Все группы</option><option>1</option><option>2</option><option>3</option></select></div>
<div class="table"><table><thead><tr><th>Группа</th><th>Идентификаторы</th><th>Товар</th><th>Акция</th><th>Цена</th><th>CPC</th><th>Экономика</th></tr></thead><tbody>{''.join(cards)}</tbody></table></div>
<section class="section"><h2>Ограничения</h2><p>Расчёт маржи консервативный и не учитывает возможную компенсацию скидки Ozon. Официальный POST-маршрут добавления SKU в CPC реализован, но marketplace apply остаётся заблокирован до согласования точного пакета. Результатом продажи считается завершённый выкуп, а не созданный заказ.</p></section>
</main><script>
const q=document.getElementById('q'),g=document.getElementById('g'),rows=[...document.querySelectorAll('tbody tr')];
function filter(){{const s=q.value.toLowerCase();rows.forEach(r=>r.hidden=(g.value&&r.dataset.group!==g.value)||(s&&!r.dataset.text.includes(s)));}}
q.addEventListener('input',filter);g.addEventListener('change',filter);
</script></body></html>"""
    path.write_text(page, encoding="utf-8")


def main() -> int:
    args = _args()
    started = datetime.now()
    data_dir = Path(args.data_dir)
    run_id = args.run_id or f"ozon_dormant_reset_plan_{started:%Y%m%dT%H%M%S}"
    run_dir = data_dir / "runs" / started.date().isoformat() / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    dormant = _read_csv(Path(args.dormant_csv))
    actions_run_dir = Path(args.actions_run_dir)
    probe_run_dir = Path(args.boost_probe_run_dir)
    cpc_run_dir = Path(args.cpc_run_dir)
    unified = {
        row.get("ozon_offer_id") or "": row
        for row in _read_csv(Path(args.unified_csv))
        if row.get("ozon_offer_id")
    }
    wb_sales = {
        row.get("wb_nm_id") or "": _decimal(row.get("sales_units_30d"))
        for row in _read_csv(Path(args.wb_signals_csv))
        if row.get("wb_nm_id")
    }
    cpc_metrics = {
        row.get("sku") or "": row
        for row in _read_csv(cpc_run_dir / "processed" / "by_sku_30d.csv")
        if row.get("sku")
    }
    campaign_products = json.loads(
        (cpc_run_dir / "raw" / "current_campaign_products.json").read_text(encoding="utf-8")
    )
    current_bids = {
        str(row.get("sku") or ""): _decimal(row.get("bid")) / Decimal("1000000")
        for row in campaign_products
        if row.get("sku")
    }
    prices, offer_to_product = _price_index(actions_run_dir)
    boosts = _boost_sources(probe_run_dir)
    elastic = _elastic_options(actions_run_dir)
    stock_actions = _stock_action_options(actions_run_dir, boosts)

    rows: list[dict[str, Any]] = []
    for source in dormant:
        offer_id = source.get("offer_id") or ""
        product_id = offer_to_product.get(offer_id, "")
        price_row = prices.get(product_id, {})
        price_obj = price_row.get("price") if isinstance(price_row.get("price"), dict) else {}
        current_min = _decimal(price_obj.get("min_price"))
        unified_row = unified.get(offer_id, {})
        wb_nm_id = str(unified_row.get("wb_nm_id") or "")
        sales_90d = wb_sales.get(wb_nm_id, Decimal("0"))
        decision_group, group_reason = _group(source, wb_nm_id=wb_nm_id, wb_sales_90d=sales_90d)
        action = _choose_action(
            [*(elastic.get(product_id) or []), *(stock_actions.get(product_id) or [])],
            group=decision_group,
            min_price=current_min,
        )
        pack_qty = max(1, int(_decimal(source.get("pack_qty")) or 1))
        source_action_price = _decimal((action or {}).get("target_action_price"))
        legal_min_price = _decimal(price_obj.get("price")) / Decimal("2")
        target_action_price = (
            max(source_action_price, legal_min_price)
            if decision_group in {"1", "2"} and source_action_price > 0
            else source_action_price
        )
        boost_is_exact = target_action_price == source_action_price
        estimated_margin = (
            target_action_price * RETAINED_SHARE - LOGISTICS_PER_PRODUCT - UNIT_COST * pack_qty
            if target_action_price > 0
            else Decimal("0")
        )
        ozon_sku = str(source.get("sku") or "")
        metric = cpc_metrics.get(ozon_sku, {})
        current_bid = current_bids.get(ozon_sku, Decimal("0"))
        cpc_action, target_bid, cpc_reason = _cpc_plan(
            current_bid=current_bid,
            in_campaign=ozon_sku in current_bids,
            orders=_decimal(metric.get("orders")),
            cpa=_decimal(metric.get("cpa")),
            drr=_decimal(metric.get("drr_percent")),
            pack_qty=pack_qty,
        )
        action_operation = "blocked_no_confirmed_action"
        if action:
            action_operation = (
                "update_action_price"
                if action.get("source_group") == "active"
                else "add_to_action"
            )
        rows.append(
            {
                "decision_group": decision_group,
                "group_reason": group_reason,
                "offer_id": offer_id,
                "product_id": product_id,
                "ozon_sku": ozon_sku,
                "title": source.get("title") or "",
                "pack_qty": pack_qty,
                "stock_products": int(_decimal(source.get("stock_products"))),
                "stock_physical_pieces": int(_decimal(source.get("stock_physical_pieces"))),
                "buyouts_total": int(_decimal(source.get("buyouts_total"))),
                "days_since_last_buyout": source.get("days_since_last_buyout") or "",
                "wb_nm_id": wb_nm_id,
                "wb_sales_90d": int(sales_90d),
                "current_price": _money(price_obj.get("price")),
                "current_min_price": _money(current_min),
                "target_action_id": (action or {}).get("action_id", ""),
                "target_action_name": (action or {}).get("action_name", ""),
                "target_action_price": _money(target_action_price),
                "target_boost_percent": (
                    _money((action or {}).get("boost_score")) if boost_is_exact else ""
                ),
                "boost_source": (
                    (action or {}).get("boost_source", "")
                    if boost_is_exact
                    else "ozon_50_percent_min_price_floor"
                ),
                "action_operation": action_operation,
                "target_min_price": _preserve_min_price(current_min),
                "min_price_policy": "keep_current_floor",
                "target_min_price_for_auto_actions_enabled": "keep",
                "price_below_min_by": _money(
                    max(Decimal("0"), current_min - target_action_price)
                    if target_action_price > 0
                    else Decimal("0")
                ),
                "estimated_margin_before_cpc": _money(estimated_margin),
                "economics_assumption": "price*43.47%-94-85*pack_qty; Ozon compensation excluded",
                "cpc_campaign_id": CAMPAIGN_ID,
                "cpc_action": cpc_action,
                "current_cpc_bid": _money(current_bid),
                "target_cpc_bid": _money(target_bid),
                "cpc_reason": cpc_reason,
                "cpc_views_30d": int(_decimal(metric.get("views"))),
                "cpc_clicks_30d": int(_decimal(metric.get("clicks"))),
                "cpc_orders_30d": int(_decimal(metric.get("orders"))),
                "cpc_spend_30d": _money(metric.get("spend")),
                "cpc_cpa_30d": _money(metric.get("cpa")),
                "cpc_drr_30d": _money(metric.get("drr_percent")),
                "cpc_stop_spend": _money(Decimal(50 * pack_qty)),
                "control_period_days": 14 if decision_group == "3" else 30,
                "content_action": (
                    "sale_only_no_content_investment"
                    if decision_group == "1"
                    else "full_seo_media_attributes_grouping_relaunch"
                ),
                "final_rule": (
                    "оставить при завершенных выкупах; иначе перевести в распродажу/вывод"
                    if decision_group == "3"
                    else "после срока: оставить только при завершенных выкупах; иначе вывести остаток"
                ),
            }
        )
    rows.sort(key=lambda row: (row["decision_group"], -int(row["stock_products"]), row["offer_id"]))

    groups: dict[str, dict[str, int]] = {}
    for group in ("1", "2", "3"):
        selected = [row for row in rows if row["decision_group"] == group]
        groups[group] = {
            "sku": len(selected),
            "stock_products": sum(int(row["stock_products"]) for row in selected),
            "stock_physical_pieces": sum(int(row["stock_physical_pieces"]) for row in selected),
            "projected_margin_full_stock_before_cpc": int(
                sum(
                    _decimal(row["estimated_margin_before_cpc"]) * int(row["stock_products"])
                    for row in selected
                )
            ),
        }
    boost_distribution: dict[str, int] = {}
    cpc_distribution: dict[str, int] = {}
    for row in rows:
        boost_key = row["target_boost_percent"] or "max_allowed_by_min_price"
        boost_distribution[boost_key] = boost_distribution.get(boost_key, 0) + 1
        cpc_distribution[row["cpc_action"]] = cpc_distribution.get(row["cpc_action"], 0) + 1

    checksum = _sha(rows)
    summary = {
        "run_id": run_id,
        "started_at": started.isoformat(timespec="seconds"),
        "mode": "dry-run",
        "overall_status": "ok" if len(rows) == 138 and all(row["target_action_id"] for row in rows) else "warning",
        "apply_performed": False,
        "owner_policy": {
            "groups_1_2": "absolute maximum confirmed boost; current min_price remains a separate lower floor",
            "group_3": "14-day final launch with min_price enforced",
            "cpc": "3 RUB controlled launch bid; stop threshold 50 RUB per physical item without completed buyout",
        },
        "groups": groups,
        "target_boost_distribution": boost_distribution,
        "cpc_action_distribution": cpc_distribution,
        "below_min_rows": sum(_decimal(row["price_below_min_by"]) > 0 for row in rows),
        "negative_margin_before_cpc_rows": sum(
            _decimal(row["estimated_margin_before_cpc"]) < 0 for row in rows
        ),
        "action_blocked_rows": sum(not row["target_action_id"] for row in rows),
        "rows_checksum_sha256": checksum,
        "sources": {
            "dormant_inventory": str(args.dormant_csv),
            "actions": str(actions_run_dir),
            "boost_probe": str(probe_run_dir),
            "cpc": str(cpc_run_dir),
            "wb_sales": str(args.wb_signals_csv),
            "unified_catalog": str(args.unified_csv),
        },
        "limitations": [
            "Projected margin is conservative and excludes possible Ozon action compensation.",
            "WB sales field is sourced from a 90-day read-only signal ending 2026-07-29.",
            "The official Performance API POST add route is implemented; marketplace apply remains approval-gated.",
            "No marketplace state was changed.",
        ],
        "artifacts": {},
    }

    csv_path = run_dir / "ozon_dormant_reset_plan.csv"
    xlsx_path = run_dir / "ozon_dormant_reset_plan.xlsx"
    html_path = run_dir / "report.html"
    md_path = run_dir / "report.md"
    json_path = run_dir / "plan.json"
    payload_path = run_dir / "payload_preview.json"
    summary_path = run_dir / "summary.json"
    _write_csv(csv_path, rows)
    _write_json(json_path, rows)
    _write_json(
        payload_path,
        {
            "dry_run": True,
            "apply_performed": False,
            "rows_checksum_sha256": checksum,
            "price_policy_updates": [],
            "action_updates": [
                {
                    "action_id": row["target_action_id"],
                    "product_id": row["product_id"],
                    "action_price": row["target_action_price"],
                    "operation": row["action_operation"],
                }
                for row in rows
                if row["target_action_id"]
            ],
            "cpc_updates": [
                {
                    "campaign_id": row["cpc_campaign_id"],
                    "sku": row["ozon_sku"],
                    "bid": row["target_cpc_bid"],
                    "operation": row["cpc_action"],
                }
                for row in rows
            ],
        },
    )
    summary_rows = [["Показатель", "Значение"]]
    for group, values in groups.items():
        summary_rows.extend(
            [
                [f"Группа {group}, SKU", values["sku"]],
                [f"Группа {group}, товары", values["stock_products"]],
                [f"Группа {group}, изделия", values["stock_physical_pieces"]],
                [
                    f"Группа {group}, консервативный результат всего остатка до CPC",
                    values["projected_margin_full_stock_before_cpc"],
                ],
            ]
        )
    summary_rows.extend(
        [
            ["Целевая акция заблокирована", summary["action_blocked_rows"]],
            ["Цена ниже текущей минимальной", summary["below_min_rows"]],
            ["Отрицательная консервативная маржа до CPC", summary["negative_margin_before_cpc_rows"]],
            ["Checksum", checksum],
        ]
    )
    _xlsx(xlsx_path, rows, summary_rows)
    _html(html_path, rows=rows, summary=summary, run_id=run_id)
    md_path.write_text(
        "\n".join(
            [
                "# Ozon dormant assortment reset dry-run",
                "",
                f"Run ID: `{run_id}`",
                "Marketplace writes: `0`.",
                "",
                f"- Group 1: `{groups['1']['sku']}` SKU / `{groups['1']['stock_products']}` products.",
                f"- Group 2: `{groups['2']['sku']}` SKU / `{groups['2']['stock_products']}` products.",
                f"- Group 3: `{groups['3']['sku']}` SKU / `{groups['3']['stock_products']}` products.",
                f"- Target boost 75%: `{boost_distribution.get('75.00', 0)}` SKU.",
                f"- Below current min_price: `{summary['below_min_rows']}` SKU.",
                f"- CPC add-to-campaign review: `{cpc_distribution.get('add_to_campaign_review', 0)}` SKU.",
                f"- CPC bid changes: `{sum(count for key, count in cpc_distribution.items() if key not in {'keep_bid','keep_effective_bid'})}` SKU.",
                "",
                "Review `report.html` and `ozon_dormant_reset_plan.xlsx` before any apply.",
            ]
        ),
        encoding="utf-8",
    )
    summary["artifacts"] = {
        "html": str(html_path),
        "xlsx": str(xlsx_path),
        "csv": str(csv_path),
        "json": str(json_path),
        "payload_preview": str(payload_path),
        "report": str(md_path),
        "summary": str(summary_path),
    }
    _write_json(summary_path, summary)
    manifest = write_summary_run_manifest(
        data_dir=data_dir,
        run_dir=run_dir,
        summary=summary,
        task="ozon-dormant-reset-plan",
        mode="dry_run",
        risk="high",
        marketplaces=["ozon"],
        inputs={
            "candidate_rows": len(rows),
            "owner_override_min_price_groups": ["1", "2"],
            "rows_checksum_sha256": checksum,
        },
        lifecycle_status="closed",
        closed=True,
    )
    summary["artifacts"].update(manifest)
    _write_json(summary_path, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
