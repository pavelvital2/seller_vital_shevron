from __future__ import annotations

import csv
import json
from pathlib import Path

from seller_agent.cli import main
from seller_agent.tasks.card_content_audit_backlog import build_card_content_audit_backlog, build_signal_index
from seller_agent.tasks.registry import get_task_definition


def test_build_card_content_audit_backlog_scores_core_reasons() -> None:
    rows, summary = build_card_content_audit_backlog(
        [
            {
                "internal_product_id": "chev-1",
                "internal_sku": "chev_nr_svo_pict0001",
                "product_name": "Шеврон 1",
                "mapping_status": "confirmed",
                "marketplace_presence": "ozon_wb",
                "title_alignment_status": "mismatch",
                "full_snapshot_status": "both_found",
                "ozon_offer_id": "oz-1",
                "wb_vendor_code": "wb-1",
                "ozon_photo_count": "3",
                "wb_photo_count": "5",
                "ozon_description_present": "true",
                "wb_description_present": "true",
                "ozon_attribute_count": "10",
                "wb_attribute_count": "8",
                "ozon_hashtags": "",
                "cost_total": "85",
            },
            {
                "internal_product_id": "ozon:only",
                "product_name": "Ozon only",
                "mapping_status": "ozon_only",
                "marketplace_presence": "ozon_only",
                "full_snapshot_status": "ozon_found",
                "ozon_offer_id": "only",
                "ozon_photo_count": "5",
                "ozon_description_present": "true",
                "ozon_attribute_count": "10",
                "cost_total": "",
            },
        ]
    )

    by_id = {row.internal_product_id: row for row in rows}
    assert summary["backlog_rows"] == 2
    assert summary["title_mismatch_rows"] == 1
    assert summary["marketplace_only_rows"] == 1
    assert summary["photo_lt5_rows"] == 1
    assert "title_mismatch" in by_id["chev-1"].reasons
    assert "ozon_photo_lt5" in by_id["chev-1"].reasons
    assert "marketplace_only" in by_id["ozon:only"].reasons
    assert by_id["chev-1"].backlog_rank == "1"


def test_build_card_content_audit_backlog_enriches_business_signals() -> None:
    signal_index = build_signal_index(
        {
            "sales": [
                {
                    "offer_id": "oz-1",
                    "ordered_units": "4",
                    "revenue": "1200",
                }
            ],
            "stocks": [
                {
                    "offer_id": "oz-1",
                    "fbo_present": "8",
                },
                {
                    "vendorCode": "wb-1",
                    "wb_stock": "3",
                }
            ],
            "parser": [
                {
                    "offer_id": "oz-1",
                    "best_position": "12",
                    "queries_found_count": "5",
                    "top30_count": "2",
                    "max_query_popularity_7d": "7000",
                }
            ],
        }
    )

    rows, summary = build_card_content_audit_backlog(
        [
            {
                "internal_product_id": "chev-1",
                "product_name": "Шеврон 1",
                "mapping_status": "confirmed",
                "marketplace_presence": "ozon_wb",
                "title_alignment_status": "match",
                "full_snapshot_status": "both_found",
                "ozon_offer_id": "oz-1",
                "wb_vendor_code": "wb-1",
                "ozon_photo_count": "5",
                "wb_photo_count": "5",
                "ozon_description_present": "true",
                "wb_description_present": "true",
                "ozon_attribute_count": "10",
                "wb_attribute_count": "8",
                "cost_total": "85",
            }
        ],
        signal_index=signal_index,
    )

    assert summary["backlog_rows"] == 1
    assert summary["business_priority_now_rows"] == 0
    assert rows[0].sales_units_30d == "4"
    assert rows[0].sales_revenue_30d == "1200"
    assert rows[0].stock_total == "11"
    assert rows[0].ozon_stock_total == "8"
    assert rows[0].wb_stock_total == "3"
    assert rows[0].parser_best_position == "12"
    assert rows[0].parser_visible_queries == "5"
    assert rows[0].business_priority == "watch"
    assert rows[0].business_reasons == "sales_positive;stock_positive;parser_top30_visible"


def test_build_card_content_audit_backlog_prioritizes_stock_with_bad_card_and_no_sales() -> None:
    signal_index = build_signal_index(
        {
            "stocks": [{"offer_id": "oz-1", "fbo_present": "24"}],
            "sales": [{"offer_id": "oz-1", "ordered_units": "0"}],
            "parser": [{"offer_id": "oz-1", "best_position": "145", "queries_found_count": "1"}],
        }
    )

    rows, summary = build_card_content_audit_backlog(
        [
            {
                "internal_product_id": "chev-1",
                "product_name": "Шеврон с плохой карточкой",
                "mapping_status": "confirmed",
                "marketplace_presence": "ozon_wb",
                "title_alignment_status": "mismatch",
                "full_snapshot_status": "both_found",
                "ozon_offer_id": "oz-1",
                "wb_vendor_code": "wb-1",
                "ozon_photo_count": "3",
                "wb_photo_count": "4",
                "ozon_description_present": "false",
                "wb_description_present": "true",
                "ozon_attribute_count": "10",
                "wb_attribute_count": "8",
                "cost_total": "85",
            }
        ],
        signal_index=signal_index,
    )

    assert summary["business_priority_now_rows"] == 1
    assert summary["stock_underperforming_bad_card_rows"] == 1
    assert rows[0].business_priority == "now"
    assert "stock_underperforming_bad_card" in rows[0].business_reasons
    assert "SEO/content-аудита" in rows[0].next_step


def test_card_content_audit_backlog_cli_writes_artifacts(tmp_path: Path, capsys) -> None:
    data_dir = tmp_path / "data"
    content_master_path = data_dir / "catalog" / "content" / "content_master.csv"
    _write_csv(
        content_master_path,
        [
            {
                "internal_product_id": "chev-1",
                "product_name": "Шеврон 1",
                "mapping_status": "confirmed",
                "marketplace_presence": "ozon_wb",
                "title_alignment_status": "mismatch",
                "full_snapshot_status": "both_found",
                "ozon_offer_id": "oz-1",
                "wb_vendor_code": "wb-1",
                "ozon_photo_count": "5",
                "wb_photo_count": "5",
                "ozon_description_present": "true",
                "wb_description_present": "true",
                "ozon_attribute_count": "10",
                "wb_attribute_count": "8",
                "cost_total": "85",
            }
        ],
    )
    sales_path = data_dir / "signals" / "sales.csv"
    parser_path = data_dir / "signals" / "parser.csv"
    _write_csv(sales_path, [{"offer_id": "oz-1", "ordered_units": "2", "revenue": "600"}])
    _write_csv(parser_path, [{"offer_id": "oz-1", "best_position": "25", "queries_found_count": "3"}])

    assert main(
        [
            "card-content-audit-backlog",
            "--data-dir",
            str(data_dir),
            "--run-id",
            "card_backlog_test",
            "--sales-signals-csv",
            str(sales_path),
            "--parser-signals-csv",
            str(parser_path),
        ]
    ) == 0
    result = json.loads(capsys.readouterr().out)

    assert result["run_id"] == "card_backlog_test"
    assert result["summary"]["backlog_rows"] == 1
    assert result["summary"]["sales_input_rows"] == 1
    assert result["summary"]["parser_input_rows"] == 1
    assert Path(result["artifacts"]["backlog_csv"]).exists()
    assert Path(result["artifacts"]["report"]).exists()


def test_card_content_audit_backlog_uses_default_saved_signals(tmp_path: Path, capsys) -> None:
    data_dir = tmp_path / "data"
    content_master_path = data_dir / "catalog" / "content" / "content_master.csv"
    _write_csv(
        content_master_path,
        [
            {
                "internal_product_id": "chev-wb",
                "product_name": "Шеврон WB",
                "mapping_status": "confirmed",
                "marketplace_presence": "ozon_wb",
                "title_alignment_status": "match",
                "full_snapshot_status": "both_found",
                "wb_vendor_code": "wb-code",
                "wb_nm_id": "123",
                "ozon_photo_count": "5",
                "wb_photo_count": "5",
                "ozon_description_present": "true",
                "wb_description_present": "true",
                "ozon_attribute_count": "10",
                "wb_attribute_count": "8",
                "cost_total": "85",
            }
        ],
    )
    signals_dir = data_dir / "catalog" / "content" / "signals"
    _write_csv(
        signals_dir / "parser_signals.csv",
        [
            {
                "marketplace": "wb",
                "wb_nm_id": "123",
                "parser_best_position": "88",
                "parser_visible_queries": "4",
                "parser_top30_queries": "0",
                "stock_total": "16",
                "wb_stock_total": "16",
            }
        ],
    )

    assert main(
        [
            "card-content-audit-backlog",
            "--data-dir",
            str(data_dir),
            "--run-id",
            "card_backlog_default_signals_test",
        ]
    ) == 0
    result = json.loads(capsys.readouterr().out)

    assert result["summary"]["parser_input_rows"] == 1
    assert result["summary"]["stock_underperforming_bad_card_rows"] == 1
    row = result["backlog_sample"][0]
    assert row["parser_best_position"] == "88"
    assert row["wb_stock_total"] == "16"
    assert row["business_priority"] == "now"


def test_task_registry_contains_card_content_audit_backlog() -> None:
    task = get_task_definition("card-content-audit-backlog")

    assert task["name"] == "card-content-audit-backlog"
    assert task["mode"] == "read_only"
    assert task["requires_mapping"] is True


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0])
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
