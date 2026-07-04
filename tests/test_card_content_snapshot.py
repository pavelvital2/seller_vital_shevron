from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from seller_agent.config import AppCredentials
from seller_agent.tasks.card_content_snapshot import (
    build_card_content_index,
    run_card_content_snapshot,
)
from seller_agent.tasks.registry import get_task_definition


def test_build_card_content_index_normalizes_ozon_and_wb_content() -> None:
    rows, summary = build_card_content_index(
        products=[
            {
                "internal_product_id": "chev_nr_svo_pict0001",
                "internal_sku": "chev_nr_svo_pict0001",
                "mapping_status": "confirmed",
                "product_name": "Шеврон СВО",
                "ozon_offer_id": "oz-1",
                "ozon_product_id": "101",
                "wb_vendor_code": "wb-1",
                "wb_nm_id": "201",
            }
        ],
        ozon_attributes=[
            {
                "offer_id": "oz-1",
                "product_id": 101,
                "primary_image": "https://example.test/1.jpg",
                "images": ["https://example.test/2.jpg"],
                "width": 100,
                "height": 100,
                "depth": 6,
                "weight": 10,
                "description_category_id": 17028963,
                "type_id": 970886657,
                "attributes": [
                    {"id": 4180, "values": [{"value": "Шеврон СВО"}]},
                    {"id": 23171, "values": [{"value": "#шеврон"}, {"value": "#патч"}]},
                ],
            }
        ],
        ozon_descriptions=[
            {"offer_id": "oz-1", "name": "Шеврон СВО", "description": "Описание Ozon"}
        ],
        wb_cards=[
            {
                "vendorCode": "wb-1",
                "nmID": 201,
                "title": "Шеврон WB",
                "description": "Описание WB",
                "subjectName": "Декор для одежды",
                "photos": [{"big": "https://example.test/wb1.jpg"}],
                "characteristics": [{"id": 1, "value": ["x"]}],
            }
        ],
    )

    by_marketplace = {row.marketplace: row for row in rows}
    assert summary["content_index_rows"] == 2
    assert summary["found_rows"] == 2
    assert by_marketplace["ozon"].photo_count == "2"
    assert by_marketplace["ozon"].hashtags_or_tags == "#шеврон #патч"
    assert by_marketplace["wb"].subject_or_category == "Декор для одежды"
    assert by_marketplace["wb"].description_present == "true"


def test_card_content_snapshot_writes_artifacts_with_fake_adapters(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    products_path = data_dir / "catalog" / "unified" / "products.csv"
    _write_csv(
        products_path,
        [
            {
                "internal_product_id": "chev_nr_svo_pict0001",
                "internal_sku": "chev_nr_svo_pict0001",
                "mapping_status": "confirmed",
                "product_name": "Шеврон СВО",
                "ozon_offer_id": "oz-1",
                "wb_vendor_code": "wb-1",
            }
        ],
    )

    result = run_card_content_snapshot(
        credentials=AppCredentials(ozon_seller=None, ozon_performance=None, wb=None),
        data_dir=data_dir,
        products_path=products_path,
        run_id="card_content_snapshot_test",
        ozon_adapter=_FakeOzonCardAdapter(),
        wb_adapter=_FakeWbCardAdapter(),
    )

    assert result["overall_status"] == "ok"
    assert result["summary"]["content_index_rows"] == 2
    assert result["summary"]["found_rows"] == 2
    assert Path(result["artifacts"]["card_content_index_csv"]).exists()
    assert Path(result["artifacts"]["report"]).exists()
    raw = json.loads(Path(result["artifacts"]["summary"]).read_text(encoding="utf-8"))
    assert raw["run_id"] == "card_content_snapshot_test"


def test_card_content_snapshot_merge_existing_updates_only_target_rows(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    products_path = data_dir / "catalog" / "unified" / "products.csv"
    _write_csv(
        products_path,
        [
            {
                "internal_product_id": "p1",
                "internal_sku": "sku-1",
                "mapping_status": "confirmed",
                "product_name": "Old",
                "ozon_offer_id": "oz-1",
                "wb_vendor_code": "wb-1",
            },
            {
                "internal_product_id": "p2",
                "internal_sku": "sku-2",
                "mapping_status": "confirmed",
                "product_name": "Keep",
                "ozon_offer_id": "oz-2",
                "wb_vendor_code": "wb-2",
            },
        ],
    )
    output_dir = data_dir / "catalog" / "content"
    _write_csv(
        output_dir / "card_content_index.csv",
        [
            {
                "marketplace": "ozon",
                "internal_product_id": "p1",
                "internal_sku": "sku-1",
                "native_id": "oz-1",
                "title": "Old title",
            },
            {
                "marketplace": "ozon",
                "internal_product_id": "p2",
                "internal_sku": "sku-2",
                "native_id": "oz-2",
                "title": "Keep title",
            },
        ],
    )
    (output_dir / "ozon_card_content.json").write_text(
        json.dumps({"attributes": [{"offer_id": "oz-2"}], "descriptions": [{"offer_id": "oz-2"}]}),
        encoding="utf-8",
    )
    (output_dir / "wb_card_content.json").write_text(json.dumps([{"vendorCode": "wb-2"}]), encoding="utf-8")

    result = run_card_content_snapshot(
        credentials=AppCredentials(ozon_seller=None, ozon_performance=None, wb=None),
        data_dir=data_dir,
        products_path=products_path,
        output_dir=output_dir,
        run_id="merge_test",
        marketplace="ozon",
        internal_skus=["sku-1"],
        merge_existing=True,
        ozon_adapter=_FakeOzonCardAdapter(),
    )

    assert result["overall_status"] == "ok"
    rows = list(csv.DictReader((output_dir / "card_content_index.csv").open(encoding="utf-8")))
    by_key = {(row["marketplace"], row["native_id"]): row for row in rows}
    assert by_key[("ozon", "oz-1")]["title"] == "Шеврон СВО"
    assert by_key[("ozon", "oz-2")]["title"] == "Keep title"
    ozon_content = json.loads((output_dir / "ozon_card_content.json").read_text(encoding="utf-8"))
    assert {row["offer_id"] for row in ozon_content["attributes"]} == {"oz-1", "oz-2"}


def test_task_registry_contains_card_content_snapshot() -> None:
    task = get_task_definition("fetch-card-content")

    assert task["name"] == "card-content-snapshot"
    assert task["mode"] == "read_only"
    assert task["requires_credentials"] is True


class _FakeOzonCardAdapter:
    def fetch_product_attributes(self, offer_ids: list[str]) -> list[dict[str, Any]]:
        assert offer_ids == ["oz-1"]
        return [
            {
                "offer_id": "oz-1",
                "primary_image": "https://example.test/1.jpg",
                "images": [],
                "attributes": [{"id": 4180, "values": [{"value": "Шеврон СВО"}]}],
            }
        ]

    def fetch_product_descriptions(self, offer_ids: list[str]) -> list[dict[str, Any]]:
        assert offer_ids == ["oz-1"]
        return [{"offer_id": "oz-1", "name": "Шеврон СВО", "description": "Описание"}]


class _FakeWbCardAdapter:
    def fetch_cards(self) -> list[dict[str, Any]]:
        return [
            {
                "vendorCode": "wb-1",
                "nmID": 201,
                "title": "Шеврон WB",
                "description": "Описание WB",
                "photos": [{"big": "https://example.test/wb1.jpg"}],
            }
        ]


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0])
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
