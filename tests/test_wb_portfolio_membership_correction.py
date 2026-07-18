from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "analytics" / "build_wb_portfolio_membership_correction.py"
SPEC = spec_from_file_location("build_wb_portfolio_membership_correction", SCRIPT)
assert SPEC and SPEC.loader
MODULE = module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_build_correction_separates_change_noop_and_missing() -> None:
    rows = [
        {"Первая волна": "да", "Сегмент": "launch_priority", "nmID": "1", "План ставок": "новая:1.10"},
        {"Первая волна": "да", "Сегмент": "launch_discovery", "nmID": "2", "План ставок": "новая:1.00"},
        {"Первая волна": "да", "Сегмент": "launch_discovery", "nmID": "3", "План ставок": "новая:1.00"},
    ]
    memberships = {
        "1": [{"advert_id": "10", "campaign_name": "A", "current_bid": MODULE.Decimal("1.00")}],
        "2": [{"advert_id": "20", "campaign_name": "B", "current_bid": MODULE.Decimal("1.00")}],
    }

    correction, no_ops, missing, ambiguous = MODULE.build_correction(rows, memberships)

    assert correction[0]["План ставок"] == "10:1.00->1.10"
    assert no_ops[0]["nmID"] == "2"
    assert missing[0]["nmID"] == "3"
    assert ambiguous == []
