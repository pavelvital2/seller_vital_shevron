from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "analytics" / "build_wb_parser_change_report.py"
SPEC = spec_from_file_location("build_wb_parser_change_report", SCRIPT)
assert SPEC and SPEC.loader
MODULE = module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_compare_pairs_classifies_movement_and_price() -> None:
    previous = [
        {"query": "шеврон", "product_id": "1", "absolute_position": 50, "final_price": 200},
        {"query": "шеврон", "product_id": "2", "absolute_position": 20, "final_price": 200},
    ]
    current = [
        {"query": "шеврон", "product_id": "1", "absolute_position": 10, "final_price": 300},
        {"query": "шеврон", "product_id": "3", "absolute_position": 30, "final_price": 300},
    ]

    values = {(row["query"], row["nmID"]): row for row in MODULE.compare_pairs(previous, current)}

    assert values[("шеврон", "1")]["status"] == "improved"
    assert values[("шеврон", "1")]["position_improvement"] == 40
    assert values[("шеврон", "1")]["price_change"] == 100
    assert values[("шеврон", "2")]["status"] == "lost"
    assert values[("шеврон", "3")]["status"] == "new"
