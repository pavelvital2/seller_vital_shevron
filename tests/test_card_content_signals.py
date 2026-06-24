from __future__ import annotations

import csv
from datetime import date
import json
from pathlib import Path

from seller_agent.cli import main
from seller_agent.tasks.card_content_signals import (
    build_ozon_sales_signals,
    build_ozon_stock_signals,
    build_wb_sales_signals,
    build_wb_stock_signals,
    normalize_parser_signal_rows,
)
from seller_agent.tasks.registry import get_task_definition


def test_build_ozon_sales_and_stock_signals_keep_native_ids() -> None:
    content_rows = [
        {
            "internal_product_id": "chev-1",
            "internal_sku": "chev_nr_svo_pict0001",
            "ozon_offer_id": "oz-1",
            "ozon_product_id": "101",
            "ozon_sku": "501",
        }
    ]

    stock_rows = build_ozon_stock_signals(
        [{"product_id": "101", "offer_id": "oz-1", "stocks": [{"present": 3, "reserved": 1}]}],
        content_rows,
        period_to="2026-06-23",
    )
    sales_rows = build_ozon_sales_signals(
        [
            {
                "status": "delivered",
                "products": [{"offer_id": "oz-1", "sku": "501", "quantity": 2, "price": 190}],
            },
            {
                "status": "cancelled",
                "products": [{"offer_id": "oz-1", "sku": "501", "quantity": 10, "price": 190}],
            },
        ],
        content_rows,
        period_from="2026-05-25",
        period_to="2026-06-23",
    )

    assert stock_rows[0]["internal_product_id"] == "chev-1"
    assert stock_rows[0]["stock_total"] == "3"
    assert stock_rows[0]["ozon_stock_total"] == "3"
    assert sales_rows[0]["sales_units_30d"] == "2"
    assert sales_rows[0]["sales_revenue_30d"] == "380"


def test_build_wb_sales_and_stock_signals_aggregate_supplier_article() -> None:
    content_rows = [
        {
            "internal_product_id": "chev-2",
            "internal_sku": "chev_nr_svo_pict0002",
            "wb_vendor_code": "wb-1",
            "wb_nm_id": "701",
        }
    ]

    stock_rows = build_wb_stock_signals(
        [
            {"supplierArticle": "wb-1", "quantity": 2},
            {"supplierArticle": "wb-1", "quantity": 5},
        ],
        content_rows,
        period_to="2026-06-23",
    )
    sales_rows = build_wb_sales_signals(
        [
            {"supplierArticle": "wb-1", "saleID": "S1", "forPay": 150},
            {"supplierArticle": "wb-1", "saleID": "S2", "forPay": 160},
            {"supplierArticle": "wb-1", "saleID": "R3", "forPay": -160},
        ],
        content_rows,
        period_from="2026-05-25",
        period_to="2026-06-23",
    )

    assert stock_rows[0]["stock_total"] == "7"
    assert stock_rows[0]["wb_stock_total"] == "7"
    assert sales_rows[0]["sales_units_30d"] == "2"
    assert sales_rows[0]["sales_revenue_30d"] == "310"


def test_normalize_parser_signal_rows_supports_ozon_and_wb_keys(tmp_path: Path) -> None:
    content_rows = [
        {"internal_product_id": "ozon-product", "ozon_offer_id": "oz-1", "ozon_product_id": "101", "ozon_sku": "501"},
        {"internal_product_id": "wb-product", "wb_vendor_code": "wb-1", "wb_nm_id": "701"},
    ]
    source_path = tmp_path / "parser.csv"

    rows = normalize_parser_signal_rows(
        [
            {"offer_id": "oz-1", "best_position": "12", "queries_found_count": "4", "top30_count": "1"},
            {"vendor_code": "wb-1", "best_position": "55", "queries_count": "2", "top30": "0"},
        ],
        content_rows,
        source_path=source_path,
    )

    by_id = {row["internal_product_id"]: row for row in rows}
    assert by_id["ozon-product"]["parser_best_position"] == "12"
    assert by_id["ozon-product"]["parser_visible_queries"] == "4"
    assert by_id["wb-product"]["parser_best_position"] == "55"
    assert by_id["wb-product"]["parser_visible_queries"] == "2"


def test_collect_card_signals_cli_writes_parser_only_artifacts(tmp_path: Path, capsys) -> None:
    data_dir = tmp_path / "data"
    content_master_path = data_dir / "catalog" / "content" / "content_master.csv"
    parser_path = data_dir / "parser.csv"
    _write_csv(
        content_master_path,
        [
            {
                "internal_product_id": "chev-1",
                "internal_sku": "chev_nr_svo_pict0001",
                "ozon_offer_id": "oz-1",
                "ozon_product_id": "101",
                "ozon_sku": "501",
            }
        ],
    )
    _write_csv(parser_path, [{"offer_id": "oz-1", "best_position": "10", "queries_found_count": "3"}])

    assert main(
        [
            "collect-card-signals",
            "--data-dir",
            str(data_dir),
            "--run-id",
            "signals_test",
            "--skip-api",
            "--parser-csv",
            str(parser_path),
        ]
    ) == 0
    result = json.loads(capsys.readouterr().out)

    assert result["run_id"] == "signals_test"
    assert result["summary"]["parser_signal_rows"] == 1
    assert result["summary"]["sales_signal_rows"] == 0
    assert Path(result["artifacts"]["parser_signals_csv"]).exists()
    assert Path(result["artifacts"]["sales_signals_csv"]).exists()


def test_task_registry_contains_card_content_signals() -> None:
    task = get_task_definition("card-content-signals")

    assert task["command"] == "collect-card-signals"
    assert task["mode"] == "read_only"
    assert task["requires_credentials"] is True


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0])
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

