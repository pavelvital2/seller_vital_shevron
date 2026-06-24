from __future__ import annotations

import csv
import json
from pathlib import Path

from seller_agent.config import AppCredentials
from seller_agent.cli import main
from seller_agent.tasks.pricing_status import build_pricing_status, normalize_ozon_price_item, run_pricing_status
from seller_agent.tasks.registry import get_task_definition


def test_build_pricing_status_joins_marketplace_prices_and_flags_targets() -> None:
    rows, summary = build_pricing_status(
        products=[
            {
                "internal_product_id": "chev_nr_svo_pict0001",
                "internal_sku": "chev_nr_svo_pict0001",
                "product_name": "Шеврон СВО",
                "mapping_status": "confirmed",
                "pack_qty": "1",
                "cost_total": "85",
                "ozon_offer_id": "pict0152",
                "ozon_product_id": "2729922386",
                "ozon_sku": "2864486048",
                "wb_vendor_code": "svopict0028_pict0152",
                "wb_nm_id": "605088924",
                "active_ozon": "true",
                "active_wb": "true",
            },
            {
                "internal_product_id": "ozon:only1",
                "product_name": "Ozon only",
                "mapping_status": "ozon_only",
                "pack_qty": "1",
                "cost_total": "",
                "ozon_offer_id": "only1",
                "active_ozon": "true",
                "active_wb": "false",
            },
        ],
        ozon_prices={
            "pict0152": {
                "offer_id": "pict0152",
                "price": "180",
                "old_price": "550",
                "min_price": "400",
                "marketing_seller_price": "170",
            }
        },
        wb_prices={
            "svopict0028_pict0152": {
                "vendorCode": "svopict0028_pict0152",
                "nmID": 605088924,
                "sizes": [{"price": 550, "discountedPrice": 165}],
                "discount": 70,
            }
        },
        ozon_action_prices={
            "pict0152": {
                "offer_id": "pict0152",
                "planned_action": "update_action_price",
                "calculated_action_price": "160",
            }
        },
        wb_action_prices={
            "svopict0028_pict0152": {
                "Артикул поставщика": "svopict0028_pict0152",
                "Финальная цена": "155.00",
                "Действие": "изменить",
            }
        },
    )

    assert summary["rows"] == 2
    assert summary["ozon_price_rows"] == 1
    assert summary["wb_price_rows"] == 1
    assert summary["missing_cost"] == 1
    assert summary["ozon_missing_price_rows"] == 1
    assert summary["wb_min_price_not_confirmed_rows"] == 1
    assert summary["ozon_action_price_rows"] == 1
    assert summary["wb_action_price_rows"] == 1
    assert "ozon_price_below_target_profit_85_ads_20" in rows[0].warning_codes
    assert "ozon_action_price_below_target_profit_60_ads_25" in rows[0].warning_codes
    assert "wb_price_below_target_profit_60_ads_25" in rows[0].warning_codes
    assert "wb_action_price_below_target_profit_60_ads_25" in rows[0].warning_codes
    assert rows[0].target_net_profit_85_ads_20 == "190"
    assert rows[0].ozon_action_price == "160"
    assert rows[0].wb_action_price == "155"


def test_pricing_status_cli_writes_artifacts(tmp_path: Path, capsys) -> None:
    data_dir = tmp_path / "data"
    products_path = data_dir / "catalog" / "unified" / "products.csv"
    ozon_path = tmp_path / "ozon_prices.json"
    wb_path = tmp_path / "wb_prices.json"
    _write_csv(
        products_path,
        [
            {
                "internal_product_id": "chev_kit2_pz_text0001",
                "internal_sku": "chev_kit2_pz_text0001",
                "product_name": "Комплект позывных",
                "mapping_status": "confirmed",
                "pack_qty": "2",
                "cost_total": "170",
                "ozon_offer_id": "pzmh0039",
                "ozon_product_id": "2298065094",
                "ozon_sku": "2538924935",
                "wb_vendor_code": "pzkit2mh0001_pzmh0039",
                "wb_nm_id": "591212552",
                "active_ozon": "true",
                "active_wb": "true",
            }
        ],
    )
    ozon_path.write_text(
        json.dumps(
            [
                {
                    "offer_id": "pzmh0039",
                    "price": 420,
                    "old_price": 800,
                    "min_price": 400,
                    "marketing_seller_price": 390,
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    wb_path.write_text(
        json.dumps(
            [
                {
                    "vendorCode": "pzkit2mh0001_pzmh0039",
                    "nmID": 591212552,
                    "sizes": [{"price": 800, "discountedPrice": 360}],
                    "discount": 55,
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    assert main(
        [
            "pricing-status",
            "--data-dir",
            str(data_dir),
            "--products-path",
            str(products_path),
            "--ozon-prices-json",
            str(ozon_path),
            "--wb-prices-json",
            str(wb_path),
            "--run-id",
            "pricing_status_test",
        ]
    ) == 0
    result = json.loads(capsys.readouterr().out)

    assert result["run_id"] == "pricing_status_test"
    assert result["summary"]["rows"] == 1
    assert Path(result["artifacts"]["status_csv"]).exists()
    assert Path(result["artifacts"]["report"]).exists()


def test_normalize_ozon_price_item_flattens_v5_raw_price() -> None:
    row = normalize_ozon_price_item(
        {
            "product_id": 2119257268,
            "offer_id": "pict0001",
            "sku": None,
            "acquiring": 4.89,
            "price": {
                "price": 550,
                "old_price": 1100,
                "min_price": 400,
                "marketing_seller_price": 489,
                "net_price": 100,
                "currency_code": "RUB",
            },
            "commissions": {
                "sales_percent_fbo": 41,
                "fbo_deliv_to_customer_amount": 25,
                "fbo_return_flow_amount": 69,
            },
        }
    )

    assert row["price"] == 550
    assert row["old_price"] == 1100
    assert row["min_price"] == 400
    assert row["marketing_seller_price"] == 489
    assert row["sales_percent_fbo"] == 41
    assert row["fbo_deliv_to_customer_amount"] == 25


def test_pricing_status_refresh_api_uses_read_only_adapters(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    products_path = data_dir / "catalog" / "unified" / "products.csv"
    _write_csv(
        products_path,
        [
            {
                "internal_product_id": "chev_nr_svo_pict0001",
                "internal_sku": "chev_nr_svo_pict0001",
                "product_name": "Шеврон СВО",
                "mapping_status": "confirmed",
                "pack_qty": "1",
                "cost_total": "85",
                "ozon_offer_id": "pict0152",
                "ozon_product_id": "2729922386",
                "ozon_sku": "2864486048",
                "wb_vendor_code": "svopict0028_pict0152",
                "wb_nm_id": "605088924",
                "active_ozon": "true",
                "active_wb": "true",
            }
        ],
    )

    result = run_pricing_status(
        credentials=AppCredentials(ozon_seller=None, ozon_performance=None, wb=None),
        data_dir=data_dir,
        products_path=products_path,
        run_id="pricing_status_refresh_test",
        refresh_api=True,
        ozon_adapter=_FakeOzonPricesAdapter(),
        wb_prices_adapter=_FakeWbPricesAdapter(),
    )

    assert result["overall_status"] == "warning"
    assert result["refresh"]["ozon_refreshed"] is True
    assert result["refresh"]["wb_refreshed"] is True
    assert result["summary"]["ozon_price_rows"] == 1
    assert result["summary"]["wb_price_rows"] == 1
    assert Path(result["inputs"]["ozon_prices_path"]).exists()
    assert Path(result["inputs"]["wb_prices_path"]).exists()


def test_task_registry_contains_pricing_status() -> None:
    task = get_task_definition("pricing-status")

    assert task["name"] == "pricing-status"
    assert task["mode"] == "read_only"
    assert task["requires_mapping"] is True


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0])
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


class _FakeOzonPricesAdapter:
    def fetch_product_info_prices(self):  # type: ignore[no-untyped-def]
        return [
            {
                "product_id": 2729922386,
                "offer_id": "pict0152",
                "sku": 2864486048,
                "price": {
                    "price": 550,
                    "old_price": 1100,
                    "min_price": 400,
                    "marketing_seller_price": 489,
                    "net_price": 100,
                },
            }
        ]


class _FakeWbPricesAdapter:
    def fetch_goods_prices(self):  # type: ignore[no-untyped-def]
        return [
            {
                "nmID": 605088924,
                "vendorCode": "svopict0028_pict0152",
                "sizes": [{"price": 550, "discountedPrice": 165}],
                "discount": 70,
            }
        ]
