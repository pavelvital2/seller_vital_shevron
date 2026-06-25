from __future__ import annotations

import json
from pathlib import Path

from seller_agent.cli import main
from seller_agent.config import AppCredentials
from seller_agent.tasks.card_content_parameter_inventory import (
    build_parameter_inventory,
    run_card_content_parameter_inventory,
)
from seller_agent.tasks.registry import get_task_definition


def test_build_parameter_inventory_separates_current_and_schema() -> None:
    result = build_parameter_inventory(
        ozon_content={
            "attributes": [
                {
                    "description_category_id": 17028963,
                    "type_id": 970886657,
                    "attributes": [
                        {"id": 7405, "values": [{"value": "Габардин"}, {"value": "Полиэстер"}]},
                        {"id": 4180, "values": [{"value": "Шеврон"}]},
                    ],
                },
                {
                    "description_category_id": 17028963,
                    "type_id": 970886657,
                    "attributes": [{"id": 7405, "values": [{"value": "Габардин"}]}],
                },
            ]
        },
        wb_cards=[
            {
                "subjectID": 2367,
                "subjectName": "Декор для одежды",
                "characteristics": [
                    {"id": 14177450, "name": "Состав", "value": ["полиэстер", "нейлон"]},
                ],
            }
        ],
        ozon_schema_items={
            ("17028963", "970886657"): [
                {"id": 7405, "name": "Материал", "type": "String", "is_required": True, "dictionary_id": 0},
                {"id": 4180, "name": "Название", "type": "String", "is_required": True, "dictionary_id": 0},
            ]
        },
        wb_subject_characteristics={
            "2367": [
                {"charcID": 14177450, "name": "Состав", "charcType": 1, "required": True},
            ]
        },
    )

    assert result["summary"]["ozon_cards"] == 2
    assert result["summary"]["ozon_current_attributes"] == 2
    material = next(row for row in result["ozon_current_rows"] if row["attribute_id"] == "7405")
    assert material["attribute_name"] == "Материал"
    assert material["used_in_cards"] == "2"
    assert material["fill_rate"] == "100.0%"
    assert material["unique_values_count"] == "2"
    wb_composition = result["wb_current_rows"][0]
    assert wb_composition["characteristic_name"] == "Состав"
    assert wb_composition["fill_rate"] == "100.0%"


def test_card_content_parameter_inventory_cli_writes_artifacts(tmp_path: Path, capsys) -> None:
    data_dir = tmp_path / "data"
    content_dir = data_dir / "catalog" / "content"
    content_dir.mkdir(parents=True)
    (content_dir / "ozon_card_content.json").write_text(
        json.dumps(
            {
                "attributes": [
                    {
                        "description_category_id": 17028963,
                        "type_id": 970886657,
                        "attributes": [{"id": 4180, "values": [{"value": "Шеврон"}]}],
                    }
                ],
                "descriptions": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (content_dir / "wb_card_content.json").write_text(
        json.dumps(
            [
                {
                    "subjectID": 2367,
                    "subjectName": "Декор для одежды",
                    "characteristics": [{"id": 14177450, "name": "Состав", "value": ["полиэстер"]}],
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    assert main(
        [
            "card-content-parameter-inventory",
            "--data-dir",
            str(data_dir),
            "--run-id",
            "params_test",
            "--skip-schema",
        ]
    ) == 0
    result = json.loads(capsys.readouterr().out)

    assert result["run_id"] == "params_test"
    assert result["overall_status"] == "ok"
    assert Path(result["artifacts"]["ozon_current_attributes_csv"]).exists()
    assert Path(result["artifacts"]["wb_current_characteristics_csv"]).exists()
    assert Path(result["artifacts"]["report"]).exists()


def test_card_content_parameter_inventory_accepts_fake_schema_adapters(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    content_dir = data_dir / "catalog" / "content"
    content_dir.mkdir(parents=True)
    (content_dir / "ozon_card_content.json").write_text(
        json.dumps({"attributes": [], "descriptions": []}, ensure_ascii=False),
        encoding="utf-8",
    )
    (content_dir / "wb_card_content.json").write_text("[]", encoding="utf-8")

    result = run_card_content_parameter_inventory(
        credentials=AppCredentials(ozon_seller=None, ozon_performance=None, wb=None),
        data_dir=data_dir,
        run_id="params_no_schema",
        fetch_schema=False,
    )

    assert result["overall_status"] == "ok"
    assert result["summary"]["ozon_cards"] == 0
    assert Path(result["artifacts"]["summary"]).exists()


def test_task_registry_contains_card_content_parameter_inventory() -> None:
    task = get_task_definition("card-content-parameter-inventory")

    assert task["name"] == "card-content-parameter-inventory"
    assert task["mode"] == "read_only"
    assert task["requires_credentials"] is True
