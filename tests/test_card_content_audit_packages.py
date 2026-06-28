from __future__ import annotations

import csv
import json
from pathlib import Path

from seller_agent.cli import main
from seller_agent.tasks.card_content_audit_packages import build_card_audit_packages
from seller_agent.tasks.registry import get_task_definition


def test_build_card_audit_packages_marks_visual_audit_pending() -> None:
    packages, excluded_rows, summary = build_card_audit_packages(
        backlog_rows=[
            {
                "backlog_rank": "1",
                "audit_priority": "high",
                "business_priority": "now",
                "internal_product_id": "chev_nr_svo_pict0001",
                "internal_sku": "chev_nr_svo_pict0001",
                "product_name": "Шеврон СВО",
                "marketplace_presence": "ozon_wb",
                "mapping_status": "confirmed",
                "ozon_offer_id": "pict0001",
                "wb_vendor_code": "wb0001",
            }
        ],
        content_rows=[
            {
                "internal_product_id": "chev_nr_svo_pict0001",
                "internal_sku": "chev_nr_svo_pict0001",
                "canonical_title": "Шеврон СВО",
                "marketplace_presence": "ozon_wb",
                "pack_qty": "1",
                "cost_total": "85",
                "ozon_offer_id": "pict0001",
                "wb_vendor_code": "wb0001",
            }
        ],
        ozon_content={
            "attributes": [
                {
                    "offer_id": "pict0001",
                    "id": 100,
                    "primary_image": "https://example.test/ozon-main.jpg",
                    "images": ["https://example.test/ozon-main.jpg", "https://example.test/ozon-2.jpg"],
                    "attributes": [
                        {"id": 4180, "values": [{"value": "Шеврон СВО"}]},
                        {"id": 10096, "values": [{"value": "черный"}, {"value": "белый"}]},
                    ],
                    "model_info": {"model_id": 123, "count": 2},
                }
            ],
            "descriptions": [{"offer_id": "pict0001", "name": "Шеврон СВО", "description": "Описание"}],
        },
        wb_content=[
            {
                "vendorCode": "wb0001",
                "nmID": 200,
                "title": "Шеврон СВО WB",
                "description": "Описание WB",
                "imtID": 300,
                "photos": [{"big": "https://example.test/wb-main.webp"}],
                "characteristics": [{"id": 14177450, "name": "Состав", "value": ["полиэстер"]}],
            }
        ],
        passport_schema={"properties": {"internal_product_id": {}, "canonical_title": {}}},
        attribute_mapping_rows=[
            {"ozon_attribute_id": "4180", "ozon_attribute_name": "Название"},
            {"ozon_attribute_id": "10096", "ozon_attribute_name": "Цвет товара"},
            {"wb_characteristic_id": "14177450", "wb_characteristic_name": "Состав"},
        ],
        seo_target_rows=[
            {
                "internal_product_id": "chev_nr_svo_pict0001",
                "internal_sku": "chev_nr_svo_pict0001",
                "query_pack_status": "ready",
                "primary_target": "шеврон СВО",
                "secondary_targets": "шеврон на липучке СВО",
                "broad_identity_terms": "шеврон; шеврон на липучке",
                "placement_terms": "шеврон на рукав; шеврон СВО на рукав",
                "ozon_confirmed_queries": "шеврон сво:100",
                "wb_confirmed_queries": "шеврон сво:90",
                "confirmed_query_rows": [
                    {
                        "query": "шеврон сво",
                        "marketplace": "ozon",
                        "role": "primary_target",
                        "frequency": 100,
                        "period": "days_7",
                        "source": "ozon_lk_ui_response",
                        "seed_query": "шеврон",
                        "rank": 1,
                    }
                ],
                "target_query_clusters": {
                    "primary_target": ["шеврон СВО"],
                    "secondary_target": ["шеврон на липучке СВО"],
                    "broad_identity": ["шеврон", "шеврон на липучке"],
                    "placement": ["шеврон на рукав"],
                    "exclude": ["шеврон ФСБ"],
                },
            }
        ],
    )

    assert summary["packages"] == 1
    assert excluded_rows == []
    package = packages[0]
    assert package["audit_requires_agent_visual_review"] is True
    assert package["visual_audit_status"] == "pending_agent_review"
    assert package["recommendation_status"] == "not_prepared"
    assert len(package["media_assets"]) == 3
    assert package["current_marketplace_content"]["ozon"]["attributes"][0]["attribute_name"] == "Название"
    assert package["current_marketplace_content"]["wb"]["characteristics"][0]["characteristic_name"] == "Состав"
    assert package["master_product_passport_draft"]["field_status"]["image_subject"] == "needs_agent_visual_review"
    assert package["seo_query_pack"]["query_pack_status"] == "ready"
    assert package["seo_query_pack"]["primary_target"] == "шеврон СВО"
    assert package["seo_query_pack"]["confirmed_query_rows"][0]["query"] == "шеврон сво"
    assert package["seo_query_pack"]["confirmed_query_rows"][0]["frequency"] == 100
    assert package["master_product_passport_draft"]["values"]["search_queries"][:2] == [
        "шеврон СВО",
        "шеврон на липучке СВО",
    ]


def test_card_content_audit_packages_cli_writes_package_files(tmp_path: Path, capsys) -> None:
    data_dir = tmp_path / "data"
    content_dir = data_dir / "catalog" / "content"
    passport_dir = content_dir / "product_passport"

    _write_csv(
        content_dir / "card_content_audit_backlog.csv",
        [
            {
                "backlog_rank": "1",
                "audit_priority": "high",
                "business_priority": "now",
                "internal_product_id": "chev_nr_svo_pict0001",
                "internal_sku": "chev_nr_svo_pict0001",
                "product_name": "Шеврон СВО",
                "marketplace_presence": "ozon_wb",
                "mapping_status": "confirmed",
                "ozon_offer_id": "pict0001",
                "wb_vendor_code": "wb0001",
            }
        ],
    )
    _write_csv(
        content_dir / "content_master.csv",
        [
            {
                "internal_product_id": "chev_nr_svo_pict0001",
                "internal_sku": "chev_nr_svo_pict0001",
                "canonical_title": "Шеврон СВО",
                "marketplace_presence": "ozon_wb",
                "pack_qty": "1",
                "cost_total": "85",
                "ozon_offer_id": "pict0001",
                "wb_vendor_code": "wb0001",
            }
        ],
    )
    _write_json(
        content_dir / "ozon_card_content.json",
        {
            "attributes": [
                {
                    "offer_id": "pict0001",
                    "primary_image": "https://example.test/ozon-main.jpg",
                    "images": ["https://example.test/ozon-2.jpg"],
                    "attributes": [{"id": 4180, "values": [{"value": "Шеврон СВО"}]}],
                }
            ],
            "descriptions": [{"offer_id": "pict0001", "name": "Шеврон СВО", "description": "Описание"}],
        },
    )
    _write_json(
        content_dir / "wb_card_content.json",
        [
            {
                "vendorCode": "wb0001",
                "title": "Шеврон СВО WB",
                "photos": [{"big": "https://example.test/wb-main.webp"}],
                "characteristics": [],
            }
        ],
    )
    _write_json(passport_dir / "master_product_passport.schema.json", {"properties": {"internal_product_id": {}}})
    _write_csv(
        passport_dir / "passport_attribute_mapping.csv",
        [{"ozon_attribute_id": "4180", "ozon_attribute_name": "Название"}],
    )
    _write_json(
        content_dir / "seo_query_pack" / "card_seo_targets.json",
        [
            {
                "internal_product_id": "chev_nr_svo_pict0001",
                "internal_sku": "chev_nr_svo_pict0001",
                "query_pack_status": "ready",
                "primary_target": "шеврон СВО",
                "manual_review_reason": "",
                "target_query_clusters": {
                    "primary_target": ["шеврон СВО"],
                    "secondary_target": [],
                    "broad_identity": ["шеврон"],
                    "placement": ["шеврон на рукав"],
                    "exclude": [],
                },
            }
        ],
    )

    assert main(["card-content-audit-packages", "--data-dir", str(data_dir), "--run-id", "packages_test"]) == 0
    result = json.loads(capsys.readouterr().out)

    assert result["overall_status"] == "warning"
    assert result["summary"]["packages"] == 1
    index_path = Path(result["artifacts"]["package_index_csv"])
    assert index_path.exists()
    rows = list(csv.DictReader(index_path.open(encoding="utf-8")))
    assert rows[0]["visual_audit_status"] == "pending_agent_review"
    assert rows[0]["seo_query_pack_status"] == "ready"
    assert Path(result["artifacts"]["excluded_package_index_csv"]).exists()
    assert Path(rows[0]["audit_package_json"]).exists()
    assert Path(rows[0]["audit_report"]).exists()
    assert Path(rows[0]["photos_html"]).exists()


def test_task_registry_contains_card_content_audit_packages() -> None:
    task = get_task_definition("card-content-audit-packages")

    assert task["name"] == "card-content-audit-packages"
    assert task["mode"] == "read_only"
    assert task["requires_mapping"] is True
    assert task["runbook_path"] == "data/planning/product_card_work_runbook.md"


def test_card_content_audit_packages_excludes_blocked_seo_statuses() -> None:
    packages, excluded_rows, summary = build_card_audit_packages(
        backlog_rows=[
            {
                "backlog_rank": "1",
                "audit_priority": "high",
                "business_priority": "now",
                "internal_product_id": "ready_card",
                "internal_sku": "chev_nr_svo_pict0001",
                "product_name": "Шеврон СВО",
                "marketplace_presence": "ozon_wb",
                "mapping_status": "confirmed",
            },
            {
                "backlog_rank": "2",
                "audit_priority": "high",
                "business_priority": "now",
                "internal_product_id": "manual_card",
                "internal_sku": "",
                "product_name": "Шеврон 3109063147",
                "marketplace_presence": "ozon_only",
                "mapping_status": "ozon_only",
            },
            {
                "backlog_rank": "3",
                "audit_priority": "high",
                "business_priority": "now",
                "internal_product_id": "other_card",
                "internal_sku": "",
                "product_name": "Подсумок тактический",
                "marketplace_presence": "wb_only",
                "mapping_status": "wb_only",
            },
        ],
        content_rows=[],
        ozon_content={"attributes": [], "descriptions": []},
        wb_content=[],
        seo_target_rows=[
            {"internal_product_id": "ready_card", "query_pack_status": "ready"},
            {
                "internal_product_id": "manual_card",
                "query_pack_status": "needs_manual_review",
                "manual_review_reason": "theme_not_detected",
            },
            {
                "internal_product_id": "other_card",
                "query_pack_status": "excluded_non_patch_assortment",
                "manual_review_reason": "non_patch_assortment_or_unclassified_product",
            },
        ],
    )

    assert len(packages) == 1
    assert packages[0]["backlog"]["internal_product_id"] == "ready_card"
    assert len(excluded_rows) == 2
    assert summary["excluded_by_seo_status"] == 2
    assert summary["excluded_needs_manual_review"] == 1
    assert summary["excluded_non_patch_assortment"] == 1


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = sorted({key for row in rows for key in row})
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
