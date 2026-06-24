from __future__ import annotations

import csv
import json
from pathlib import Path

from seller_agent.cli import main
from seller_agent.tasks.card_content_audit_backlog import build_card_content_audit_backlog
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

    assert main(
        [
            "card-content-audit-backlog",
            "--data-dir",
            str(data_dir),
            "--run-id",
            "card_backlog_test",
        ]
    ) == 0
    result = json.loads(capsys.readouterr().out)

    assert result["run_id"] == "card_backlog_test"
    assert result["summary"]["backlog_rows"] == 1
    assert Path(result["artifacts"]["backlog_csv"]).exists()
    assert Path(result["artifacts"]["report"]).exists()


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
