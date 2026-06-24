from __future__ import annotations

import csv
import json
from pathlib import Path

from seller_agent.cli import main
from seller_agent.tasks.pricing_status import build_pricing_status
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
    )

    assert summary["rows"] == 2
    assert summary["ozon_price_rows"] == 1
    assert summary["wb_price_rows"] == 1
    assert summary["missing_cost"] == 1
    assert summary["ozon_missing_price_rows"] == 1
    assert summary["wb_min_price_not_confirmed_rows"] == 1
    assert "ozon_price_below_target_profit_85_ads_20" in rows[0].warning_codes
    assert "wb_price_below_target_profit_60_ads_25" in rows[0].warning_codes
    assert rows[0].target_net_profit_85_ads_20 == "190"


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
