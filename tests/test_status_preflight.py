import json
from pathlib import Path

from seller_agent.config import AppCredentials
from seller_agent.tasks.status_preflight import _overall_status, _summarize_master_catalog
from seller_agent.tasks.status_preflight import _check_ozon_performance_api


def test_overall_status_errors_win() -> None:
    assert (
        _overall_status(
            {
                "ozon_api": {"status": "ok"},
                "wb_api": {"status": "skipped"},
                "master_catalog": {"status": "error"},
            }
        )
        == "error"
    )


def test_overall_status_skipped_is_warning() -> None:
    assert (
        _overall_status(
            {
                "ozon_api": {"status": "ok"},
                "wb_api": {"status": "skipped"},
                "master_catalog": {"status": "ok"},
            }
        )
        == "warning"
    )


def test_overall_status_warning_is_warning() -> None:
    assert (
        _overall_status(
            {
                "ozon_api": {"status": "ok"},
                "ozon_refresh_state": {"status": "warning"},
                "master_catalog": {"status": "ok"},
            }
        )
        == "warning"
    )


def test_summarize_master_catalog_allows_separate_sku_mode(tmp_path: Path) -> None:
    processed_dir = tmp_path / "catalog" / "processed"
    processed_dir.mkdir(parents=True)
    (processed_dir / "master_catalog.json").write_text(
        json.dumps(
            [
                {"match_status": "matched", "notes": ""},
                {"match_status": "ozon_only", "notes": "not_found_in_wb"},
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    summary = _summarize_master_catalog(tmp_path)

    assert summary["status"] == "ok"
    assert summary["seller_sku_mode"] == "separate"
    assert summary["match_gate"] == "separate_catalogs_mapping_optional"
    assert summary["rows"] == 2
    assert summary["matched_rows"] == 1
    assert summary["ozon_only_rows"] == 1


def test_summarize_master_catalog_requires_full_match_in_unified_mode(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SELLER_SKU_MODE", "unified")
    processed_dir = tmp_path / "catalog" / "processed"
    processed_dir.mkdir(parents=True)
    (processed_dir / "master_catalog.json").write_text(
        json.dumps(
            [
                {"match_status": "matched", "notes": ""},
                {"match_status": "ozon_only", "notes": "not_found_in_wb"},
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    summary = _summarize_master_catalog(tmp_path)

    assert summary["status"] == "error"
    assert summary["seller_sku_mode"] == "unified"
    assert summary["match_gate"] == "full_match_required"


def test_summarize_master_catalog_ok_for_203_matched_rows(tmp_path: Path) -> None:
    processed_dir = tmp_path / "catalog" / "processed"
    processed_dir.mkdir(parents=True)
    rows = [{"match_status": "matched", "notes": "ozon_platform_barcode"} for _ in range(203)]
    (processed_dir / "master_catalog.json").write_text(
        json.dumps(rows, ensure_ascii=False),
        encoding="utf-8",
    )

    summary = _summarize_master_catalog(tmp_path)

    assert summary["status"] == "ok"
    assert summary["rows"] == 203
    assert summary["matched_rows"] == 203
    assert summary["ozon_platform_barcode_rows"] == 203


def test_ozon_performance_check_skips_missing_credentials() -> None:
    result = _check_ozon_performance_api(
        AppCredentials(ozon_seller=None, ozon_performance=None, wb=None)
    )

    assert result["status"] == "skipped"
    assert "Performance" in result["error"]
