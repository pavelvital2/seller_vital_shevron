import csv
import json
from pathlib import Path

from seller_agent.tasks.approved_cards_apply import _sync_approved_card_catalog_layers


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _seed_layers(data_dir: Path) -> None:
    rows = [
        {
            "internal_sku": "old_ozon",
            "master_sku": "old_ozon",
            "ozon_offer_id": "old_ozon",
            "ozon_product_id": "2122666896",
            "wb_vendor_code": "",
            "wb_nm_id": "",
            "wb_barcode": "",
            "marketplace_presence": "ozon_only",
            "match_status": "ozon_only",
            "notes": "not_found_in_wb",
        },
        {
            "internal_sku": "old_wb",
            "master_sku": "old_wb",
            "ozon_offer_id": "",
            "ozon_product_id": "",
            "wb_vendor_code": "old_wb",
            "wb_nm_id": "648292160",
            "wb_barcode": "2047478988209",
            "marketplace_presence": "wb_only",
            "match_status": "wb_only",
            "notes": "not_found_in_ozon",
        },
    ]
    for rel_path in [
        "catalog/processed/master_catalog.csv",
        "catalog/unified/products.csv",
        "catalog/content/content_master.csv",
    ]:
        _write_csv(data_dir / rel_path, rows)
    for rel_path in [
        "catalog/processed/master_catalog.json",
        "catalog/unified/products.json",
        "catalog/content/content_master.json",
    ]:
        _write_json(data_dir / rel_path, rows)


def test_sync_approved_card_catalog_layers_updates_legacy_master_and_removes_wb_duplicate(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    sku = "chev_nr_oborg_pict0003"
    _seed_layers(data_dir)
    _write_json(
        data_dir / "catalog" / "master_passport" / "approved" / f"{sku}.json",
        {
            "identity": {
                "internal_sku": sku,
                "ozon_offer_id": sku,
                "ozon_product_id": "2122666896",
                "wb_vendor_code": sku,
                "wb_nm_id": "648292160",
                "wb_barcode": "",
            },
            "ozon": {
                "offer_id_before_seller_sku_update": "old_ozon",
                "offer_id_after_seller_sku_update": sku,
            },
            "wb": {
                "vendor_code_before_seller_sku_update": "old_wb",
                "vendor_code_after_seller_sku_update": sku,
                "vendor_code": sku,
                "nm_id": "648292160",
            },
        },
    )

    result = _sync_approved_card_catalog_layers(data_dir=data_dir, internal_skus=[sku], run_id="run_1")

    assert result["status"] == "ok"
    assert result["master_catalog_csv"] == {"updated": 1, "removed": 1}
    assert result["master_catalog_json"] == {"updated": 1, "removed": 1}
    for rel_path in [
        "catalog/processed/master_catalog.csv",
        "catalog/unified/products.csv",
        "catalog/content/content_master.csv",
    ]:
        with (data_dir / rel_path).open("r", encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        assert len(rows) == 1
        row = rows[0]
        assert row["internal_sku"] == sku
        assert row["master_sku"] == sku
        assert row["ozon_offer_id"] == sku
        assert row["wb_vendor_code"] == sku
        assert row["wb_barcode"] == "2047478988209"
        assert row["marketplace_presence"] == "ozon_wb"
        assert row["match_status"] == "ozon_wb"
        assert "not_found_in_wb" not in row["notes"]
        assert "not_found_in_ozon" not in row["notes"]
    passport = json.loads(
        (data_dir / "catalog" / "master_passport" / "approved" / f"{sku}.json").read_text(encoding="utf-8")
    )
    assert passport["identity"]["wb_barcode"] == "2047478988209"
    assert passport["approval"]["marketplace_apply"]["catalog_sync_run_id"] == "run_1"
