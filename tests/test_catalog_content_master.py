from __future__ import annotations

import csv
import json
from pathlib import Path

from seller_agent.cli import main
from seller_agent.tasks.catalog_content_master import build_content_master
from seller_agent.tasks.registry import get_task_definition


def test_build_content_master_flags_title_mismatch_and_marketplace_only() -> None:
    rows, audit_rows, summary = build_content_master(
        products=[
            {
                "internal_product_id": "chev_nr_svo_pict0001",
                "internal_sku": "chev_nr_svo_pict0001",
                "product_name": "Шеврон СВО",
                "mapping_status": "confirmed",
                "product_group": "chev",
                "pack_qty": "1",
                "cost_total": "85",
                "cost_per_unit": "85",
                "ozon_offer_id": "oz-1",
                "wb_vendor_code": "wb-1",
                "active_ozon": "true",
                "active_wb": "true",
            },
            {
                "internal_product_id": "ozon:only1",
                "product_name": "Только Ozon",
                "mapping_status": "ozon_only",
                "ozon_offer_id": "only1",
                "active_ozon": "true",
                "active_wb": "false",
            },
        ],
        ozon_rows=[
            {"offer_id": "oz-1", "product_id": "101", "sku": "901", "status": "visible", "title": "Шеврон СВО"},
            {"offer_id": "only1", "status": "visible", "title": "Только Ozon"},
        ],
        wb_rows=[
            {
                "vendor_code": "wb-1",
                "nm_id": "201",
                "status": "present",
                "subject": "Аксессуары",
                "title": "Патч СВО",
            }
        ],
        pricing_rows=[
            {
                "internal_product_id": "chev_nr_svo_pict0001",
                "ozon_action_price": "433",
                "wb_action_price": "341",
            }
        ],
        card_content_rows=[
            {
                "marketplace": "ozon",
                "internal_product_id": "chev_nr_svo_pict0001",
                "description_present": "true",
                "description_length": "120",
                "photo_count": "5",
                "attribute_count": "12",
                "hashtags_or_tags": "#шеврон #патч",
                "raw_snapshot_status": "found",
            },
            {
                "marketplace": "wb",
                "internal_product_id": "chev_nr_svo_pict0001",
                "description_present": "true",
                "description_length": "95",
                "photo_count": "4",
                "attribute_count": "8",
                "raw_snapshot_status": "found",
            },
        ],
    )

    by_id = {row.internal_product_id: row for row in rows}
    assert summary["content_master_rows"] == 2
    assert summary["title_mismatch_rows"] == 1
    assert summary["ozon_only_rows"] == 1
    assert summary["missing_cost_rows"] == 1
    assert by_id["chev_nr_svo_pict0001"].title_alignment_status == "mismatch"
    assert by_id["chev_nr_svo_pict0001"].ozon_action_price == "433"
    assert by_id["chev_nr_svo_pict0001"].full_snapshot_status == "both_found"
    assert by_id["chev_nr_svo_pict0001"].photo_audit_status == "photo_count_lt5_not_inspected"
    assert by_id["chev_nr_svo_pict0001"].ozon_hashtags == "#шеврон #патч"
    assert by_id["ozon:only1"].transfer_direction == "create_on_wb_candidate"
    assert {row["issue"] for row in audit_rows} == {"title_mismatch", "marketplace_only", "missing_cost"}


def test_build_content_master_overlays_owner_review_internal_sku() -> None:
    rows, _audit_rows, summary = build_content_master(
        products=[
            {
                "internal_product_id": "ozon:back0018",
                "internal_sku": "",
                "product_name": "Шеврон Полиция",
                "mapping_status": "ozon_only",
                "ozon_offer_id": "back0018",
                "active_ozon": "true",
                "active_wb": "false",
                "notes": "not_confirmed_in_mapping",
            }
        ],
        ozon_rows=[
            {
                "offer_id": "back0018",
                "product_id": "2161557598",
                "sku": "2432512033",
                "status": "visible",
                "title": "Шеврон Полиция",
            }
        ],
        wb_rows=[],
        owner_review_rows=[
            {
                "review_status": "owner_corrected_internal_sku",
                "source_marketplace": "ozon",
                "source_id": "back0018",
                "current_internal_product_id": "ozon:back0018",
                "approved_internal_sku": "chev_back_mvd_text0004",
            }
        ],
    )

    assert rows[0].internal_product_id == "ozon:back0018"
    assert rows[0].internal_sku == "chev_back_mvd_text0004"
    assert rows[0].notes == "owner_approved_internal_sku;not_cross_marketplace_mapped"
    assert summary["owner_review_applied_rows"] == 1


def test_content_master_cli_writes_artifacts(tmp_path: Path, capsys) -> None:
    data_dir = tmp_path / "data"
    products_path = data_dir / "catalog" / "unified" / "products.csv"
    ozon_path = data_dir / "catalog" / "ozon" / "processed" / "ozon_catalog.csv"
    wb_path = data_dir / "catalog" / "wb" / "processed" / "wb_catalog.csv"
    _write_csv(
        products_path,
        [
            {
                "internal_product_id": "chev_nr_svo_pict0001",
                "internal_sku": "chev_nr_svo_pict0001",
                "product_name": "Шеврон СВО",
                "mapping_status": "confirmed",
                "product_group": "chev",
                "pack_qty": "1",
                "cost_total": "85",
                "cost_per_unit": "85",
                "ozon_offer_id": "oz-1",
                "wb_vendor_code": "wb-1",
                "active_ozon": "true",
                "active_wb": "true",
            }
        ],
    )
    _write_csv(ozon_path, [{"offer_id": "oz-1", "status": "visible", "title": "Шеврон СВО"}])
    _write_csv(wb_path, [{"vendor_code": "wb-1", "status": "present", "title": "Патч СВО"}])
    _write_csv(
        data_dir / "catalog" / "content" / "card_content_index.csv",
        [
            {
                "marketplace": "ozon",
                "internal_product_id": "chev_nr_svo_pict0001",
                "photo_count": "5",
                "raw_snapshot_status": "found",
            },
            {
                "marketplace": "wb",
                "internal_product_id": "chev_nr_svo_pict0001",
                "photo_count": "5",
                "raw_snapshot_status": "found",
            },
        ],
    )

    assert main(
        [
            "build-content-master",
            "--data-dir",
            str(data_dir),
            "--run-id",
            "content_master_test",
        ]
    ) == 0
    result = json.loads(capsys.readouterr().out)

    assert result["run_id"] == "content_master_test"
    assert result["summary"]["content_master_rows"] == 1
    assert result["summary"]["title_mismatch_rows"] == 1
    assert result["summary"]["full_snapshot_found_rows"] == 1
    assert Path(result["artifacts"]["content_master_csv"]).exists()
    assert Path(result["artifacts"]["content_audit_csv"]).exists()
    assert Path(result["artifacts"]["report"]).exists()


def test_task_registry_contains_content_master() -> None:
    task = get_task_definition("build-content-master")

    assert task["name"] == "catalog-content-master"
    assert task["mode"] == "read_only"
    assert task["requires_mapping"] is True


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0])
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
