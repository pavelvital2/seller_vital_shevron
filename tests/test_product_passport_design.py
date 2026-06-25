from __future__ import annotations

import csv
import json
from pathlib import Path

from seller_agent.cli import main
from seller_agent.tasks.product_passport_design import build_product_passport_schema, run_product_passport_design
from seller_agent.tasks.registry import get_task_definition


def test_build_product_passport_schema_has_required_marketplace_sections() -> None:
    schema = build_product_passport_schema()

    assert schema["title"] == "MasterProductPassport"
    assert "internal_product_id" in schema["required"]
    assert "internal_sku" in schema["required"]
    assert schema["properties"]["canonical_title"]["x-section"] == "content"
    assert schema["properties"]["media_assets"]["x-section"] == "media"
    assert schema["properties"]["target_group_key"]["x-section"] == "grouping"


def test_product_passport_design_cli_writes_schema_and_mapping(tmp_path: Path, capsys) -> None:
    data_dir = tmp_path / "data"
    inventory_dir = data_dir / "catalog" / "content" / "parameter_inventory"
    _write_csv(
        inventory_dir / "ozon_schema_attributes.csv",
        [
            {"attribute_id": "85", "attribute_name": "Бренд"},
            {"attribute_id": "8229", "attribute_name": "Тип"},
            {"attribute_id": "4180", "attribute_name": "Название"},
            {"attribute_id": "4191", "attribute_name": "Аннотация"},
            {"attribute_id": "4382", "attribute_name": "Размеры, мм"},
            {"attribute_id": "4497", "attribute_name": "Вес с упаковкой, г"},
            {"attribute_id": "7405", "attribute_name": "Материал"},
            {"attribute_id": "10096", "attribute_name": "Цвет товара"},
            {"attribute_id": "10097", "attribute_name": "Название цвета"},
            {"attribute_id": "22232", "attribute_name": "ТН ВЭД коды ЕАЭС"},
            {"attribute_id": "23171", "attribute_name": "#Хештеги"},
            {"attribute_id": "9048", "attribute_name": "Название модели"},
            {"attribute_id": "22390", "attribute_name": "Объединить в похожие товары"},
        ],
    )
    _write_csv(
        inventory_dir / "wb_schema_characteristics.csv",
        [
            {"subject_id": "2367", "characteristic_id": "14177446", "characteristic_name": "Бренд"},
            {"subject_id": "2367", "characteristic_id": "384944", "characteristic_name": "Вид декора"},
            {"subject_id": "2367", "characteristic_id": "15000000", "characteristic_name": "Наименование"},
            {"subject_id": "2367", "characteristic_id": "14177452", "characteristic_name": "Описание"},
            {"subject_id": "2367", "characteristic_id": "14177450", "characteristic_name": "Состав"},
            {"subject_id": "2367", "characteristic_id": "14177449", "characteristic_name": "Цвет"},
            {"subject_id": "2367", "characteristic_id": "15000001", "characteristic_name": "ТНВЭД"},
            {"subject_id": "2367", "characteristic_id": "14177451", "characteristic_name": "Страна"},
            {"subject_id": "2367", "characteristic_id": "179792", "characteristic_name": "Количество"},
            {"subject_id": "2367", "characteristic_id": "378533", "characteristic_name": "Комплектация"},
        ],
    )

    assert main(["design-product-passport", "--data-dir", str(data_dir), "--run-id", "passport_test"]) == 0
    result = json.loads(capsys.readouterr().out)

    assert result["run_id"] == "passport_test"
    assert result["overall_status"] == "ok"
    assert result["summary"]["passport_fields"] > 20
    assert result["summary"]["attribute_mapping_rows"] > 10
    assert result["validation_issues"] == []
    assert Path(result["artifacts"]["json_schema"]).exists()
    assert Path(result["artifacts"]["attribute_mapping_csv"]).exists()
    assert Path(result["artifacts"]["report"]).exists()


def test_product_passport_design_warns_on_missing_inventory(tmp_path: Path) -> None:
    result = run_product_passport_design(data_dir=tmp_path / "data", run_id="passport_missing")

    assert result["overall_status"] == "warning"
    assert "ozon_schema_attributes" in result["errors"]
    assert Path(result["artifacts"]["json_schema"]).exists()


def test_task_registry_contains_product_passport_design() -> None:
    task = get_task_definition("design-product-passport")

    assert task["name"] == "product-passport-design"
    assert task["mode"] == "read_only"
    assert task["requires_mapping"] is True
    assert task["runbook_path"] == "data/planning/master_product_passport_runbook.md"


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = sorted({key for row in rows for key in row})
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
