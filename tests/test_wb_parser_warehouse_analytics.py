from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from seller_agent.tasks.wb_parser_warehouse_analytics import run_wb_parser_warehouse_analytics


class FakeParserDataApiClient:
    config = SimpleNamespace(base_url="http://127.0.0.1:8787")

    def get(self, path: str, *, params: dict | None = None) -> dict:
        if path == "/warehouse/wb/summary":
            return {
                "manifest": {"built_at_utc": "2026-07-05T06:07:13+00:00"},
                "metrics": {
                    "min_run_date": "2026-06-12",
                    "max_run_date": "2026-07-04",
                    "query_position_rows": 389949,
                    "products": 22728,
                    "suppliers": 1341,
                },
            }
        if path == "/warehouse/wb/run-quality":
            return {"rows": [{"status": "success", "run_id": "20260704_211504Z"}]}
        if path == "/warehouse/wb/query-positions":
            return {
                "rows": [
                    {
                        "query": "именной шеврон",
                        "product_id": "593342198",
                        "product_name": "Шевроны на липучке позывной Филин, комплект 2 шт, мох",
                        "supplier_id": "4516781",
                        "absolute_position": 184,
                        "total_quantity": 10,
                    },
                    {
                        "query": "шеврон",
                        "product_id": "999",
                        "product_name": "Чужой товар",
                        "supplier_id": "other",
                        "absolute_position": 1,
                        "total_quantity": 100,
                    },
                    {
                        "query": "шеврон фсин",
                        "product_id": "111",
                        "product_name": "Наш товар без остатка",
                        "supplier_id": "4516781",
                        "absolute_position": 20,
                        "total_quantity": 0,
                    },
                ]
            }
        if path == "/warehouse/wb/daily-changes":
            return {
                "rows": [
                    {
                        "query": "именной шеврон",
                        "product_id": "593342198",
                        "product_name": "Наш товар",
                        "supplier_id": "4516781",
                        "previous_position": 45,
                        "current_position": None,
                        "change_status": "missing",
                    },
                    {
                        "query": "шеврон",
                        "product_id": "999",
                        "product_name": "Чужой товар",
                        "supplier_id": "other",
                        "change_status": "missing",
                    },
                ]
            }
        if path == "/warehouse/wb/top-movers":
            return {"rows": [{"supplier_id": "4516781", "product_id": "593342198", "position_delta": -10}]}
        if path == "/warehouse/wb/seller-changes":
            return {
                "rows": [
                    {
                        "supplier_id": "4516781",
                        "current_product_count": 171,
                        "previous_product_count": 124,
                        "current_query_count": 24,
                        "previous_query_count": 22,
                        "current_feedbacks_count": 785,
                        "previous_feedbacks_count": 754,
                    }
                ]
            }
        raise AssertionError(path)


def test_wb_parser_warehouse_analytics_filters_supplier_and_writes_artifacts(tmp_path) -> None:
    result = run_wb_parser_warehouse_analytics(
        data_dir=tmp_path,
        run_id="wb_parser_warehouse_analytics_test",
        client=FakeParserDataApiClient(),
    )

    assert result["overall_status"] == "ok"
    assert result["metrics"]["visible_rows"] == 2
    assert result["metrics"]["unique_products"] == 2
    assert result["metrics"]["best_position"] == 20
    assert result["metrics"]["stock_visible_rows"] == 1
    assert result["metrics"]["weak_visible_candidates"] == 1
    assert result["metrics"]["parser_signal_products"] == 2
    assert result["metrics"]["missing_rows"] == 1
    query_positions = result["artifacts"]["query_positions_csv"]
    assert "Чужой товар" not in query_positions and "wb_query_positions.csv" in query_positions
    assert "Чужой товар" not in Path(query_positions).read_text(encoding="utf-8")
    parser_signals = Path(result["artifacts"]["parser_signals_csv"]).read_text(encoding="utf-8")
    assert "593342198" in parser_signals
    assert "parser_visible_queries" in parser_signals
    assert Path(result["artifacts"]["summary"]).with_name("manifest.json").exists()
