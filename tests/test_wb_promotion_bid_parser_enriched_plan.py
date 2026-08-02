from __future__ import annotations

import csv
import json
from pathlib import Path

from seller_agent.cli import main
from seller_agent.tasks.registry import get_task_definition
from seller_agent.tasks.wb_promotion_bid_parser_enriched_plan import build_wb_promotion_parser_enriched_rows


def test_wb_promotion_parser_enriched_classifies_ready_review_and_blocked() -> None:
    base_rows = [
        {
            "advert_id": "101",
            "nm_id": "1",
            "name": "Ready",
            "recommended_action": "scale_candidate",
            "current_bid": "2.00",
            "current_bid_place": "search",
            "current_bid_source": "current_bid_api",
            "target_bid": "2.40",
            "requested_bid_change_percent": "20",
            "orders": "4",
            "spend": "10",
        },
        {
            "advert_id": "101",
            "nm_id": "2",
            "name": "Parser test",
            "recommended_action": "keep_monitor",
            "current_bid": "1.50",
            "target_bid": "1.50",
            "requested_bid_change_percent": "0",
            "orders": "0",
            "spend": "2",
        },
        {
            "advert_id": "101",
            "nm_id": "3",
            "name": "No stock",
            "recommended_action": "scale_candidate",
            "current_bid": "1.50",
            "target_bid": "1.80",
            "requested_bid_change_percent": "20",
            "orders": "3",
            "spend": "10",
        },
    ]
    signal_rows = [
        {
            "source": "/api/v1/supplier/sales",
            "wb_nm_id": "1",
            "sales_units_30d": "5",
            "sales_revenue_30d": "1500",
        },
        {
            "source": "parser:wb_parser_signals.csv",
            "wb_nm_id": "1",
            "wb_stock_total": "9",
            "sales_units_30d": "99",
            "parser_best_position": "45",
            "parser_visible_queries": "3",
        },
        {"wb_nm_id": "2", "wb_stock_total": "8", "parser_best_position": "80", "parser_visible_queries": "2"},
        {"wb_nm_id": "3", "wb_stock_total": "0", "parser_best_position": "30", "parser_visible_queries": "1"},
    ]

    rows, summary = build_wb_promotion_parser_enriched_rows(base_rows, signal_rows)
    by_nm = {row["nm_id"]: row for row in rows}

    assert by_nm["1"]["parser_enriched_action"] == "apply_ready"
    assert by_nm["1"]["parser_enriched_apply_allowed"] == "true"
    assert by_nm["1"]["final_target_bid"] == "2.40"
    assert by_nm["1"]["wb_statistics_sales_signal"] == "true"
    assert by_nm["1"]["sales_units_30d"] == "5.00"
    assert by_nm["2"]["parser_enriched_action"] == "review_only"
    assert by_nm["2"]["final_target_bid"] == "1.65"
    assert "missing_wb_statistics_sales_signal" in by_nm["2"]["risk_flags"]
    assert by_nm["3"]["parser_enriched_action"] == "blocked"
    assert "missing_or_zero_stock" in by_nm["3"]["risk_flags"]
    assert summary["apply_ready_rows"] == 1
    assert summary["apply_payload_rows"] == 1
    assert summary["review_only_rows"] == 1
    assert summary["blocked_rows"] == 1
    assert summary["rows_with_wb_statistics_sales_signal"] == 1


def test_wb_promotion_parser_enriched_cli_writes_artifacts(tmp_path: Path, capsys) -> None:
    data_dir = tmp_path / "data"
    base_dir = data_dir / "runs" / "2026-07-05" / "wb_promotion_bid_plan_test"
    signals_dir = data_dir / "catalog" / "content" / "signals"
    _write_csv(
        base_dir / "wb_promotion_bid_plan.csv",
        [
            {
                "advert_id": "101",
                "nm_id": "1",
                "name": "Ready",
                "recommended_action": "scale_candidate",
                "current_bid": "2.00",
                "current_bid_place": "search",
                "current_bid_source": "current_bid_api",
                "target_bid": "2.40",
                "requested_bid_change_percent": "20",
                "orders": "4",
                "spend": "10",
            }
        ],
    )
    _write_csv(
        signals_dir / "parser_signals.csv",
        [
            {
                "source": "parser:wb_parser_signals.csv",
                "wb_nm_id": "1",
                "wb_stock_total": "9",
                "parser_best_position": "45",
                "parser_visible_queries": "3",
            }
        ],
    )

    assert main(
        [
            "plan-wb-promotion-bids-parser-enriched",
            "--data-dir",
            str(data_dir),
            "--run-id",
            "parser_enriched_test",
            "--base-plan-run-id",
            "wb_promotion_bid_plan_test",
        ]
    ) == 0
    result = json.loads(capsys.readouterr().out)

    assert result["run_id"] == "parser_enriched_test"
    assert result["summary"]["apply_ready_rows"] == 1
    assert result["summary"]["apply_payload_rows"] == 1
    assert result["summary"]["rows_missing_wb_statistics_sales_signal"] == 1
    assert Path(result["artifacts"]["candidates_csv"]).exists()
    assert Path(result["artifacts"]["apply_preview_csv"]).exists()
    assert Path(result["artifacts"]["report"]).exists()


def test_wb_promotion_parser_enriched_apply_count_uses_apply_payload_gates() -> None:
    rows, summary = build_wb_promotion_parser_enriched_rows(
        [
            {
                "advert_id": "101",
                "nm_id": "1",
                "recommended_action": "scale_candidate",
                "current_bid": "2.00",
                "current_bid_place": "search",
                "current_bid_source": "missing_current_bid",
                "target_bid": "2.40",
                "orders": "4",
                "spend": "10",
            }
        ],
        [{"wb_nm_id": "1", "wb_stock_total": "9"}],
    )

    assert rows[0]["parser_enriched_action"] == "apply_ready"
    assert summary["apply_ready_rows"] == 1
    assert summary["apply_payload_rows"] == 0


def test_task_registry_contains_wb_promotion_parser_enriched_plan() -> None:
    task = get_task_definition("wb-promotion-bid-parser-enriched-plan")

    assert task["command"] == "plan-wb-promotion-bids-parser-enriched"
    assert task["mode"] == "dry_run"
    assert task["requires_mapping"] is True


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
