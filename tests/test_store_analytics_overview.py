from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from seller_agent.tasks.store_analytics_overview import _write_report, run_store_analytics_overview


MOSCOW = ZoneInfo("Europe/Moscow")
NOW = datetime(2026, 8, 2, 12, 0, tzinfo=MOSCOW)
FIXTURE_ROOT = Path("tests/fixtures/parser_data_api")


def _fixture(name: str) -> dict:
    return json.loads((FIXTURE_ROOT / name).read_text(encoding="utf-8"))


class FakeParserClient:
    def __init__(self, *, built_at: str = "2026-08-02T08:00:00Z", fail: bool = False) -> None:
        self.built_at = built_at
        self.fail = fail
        self.calls: list[tuple[str, dict]] = []

    def get(self, path: str, *, params: dict | None = None) -> dict:
        self.calls.append((path, dict(params or {})))
        if self.fail:
            raise RuntimeError("secret-parser-error")
        if path.endswith("/summary"):
            return {"manifest": {"built_at_utc": self.built_at}}
        if path == "/warehouse/wb/aggregates/store-period-comparison":
            return _fixture("wb_store_period_comparison_live.json")
        if path == "/warehouse/ozon/run-quality":
            return _fixture("ozon_run_quality_live.json")
        if path == "/warehouse/ozon/query-positions":
            requested_date = str((params or {}).get("date_from") or "")
            rows = [
                row
                for row in _fixture("ozon_query_positions_live.json")["rows"]
                if row["run_date"] == requested_date
            ]
            return {
                "rows": rows,
                "returned_rows": len(rows),
                "total_rows": len(rows),
            }
        if path == "/warehouse/ozon/aggregates/store-period-comparison":
            return _fixture("ozon_store_period_comparison_live.json")
        raise AssertionError(f"unexpected parser path: {path}")


def _period_runner(**kwargs) -> dict:  # type: ignore[no-untyped-def]
    current = kwargs["date_to"] == "2026-08-01"
    return {
        "run_id": "period-current" if current else "period-previous",
        "started_at": "2026-08-02T09:00:00+03:00",
        "overall_status": "ok",
        "metrics": {
            "orders": 20 if current else 10,
            "order_amount": 40000.0 if current else 25000.0,
            "returns": 2 if current else 1,
            "net": 21000.0 if current else 14000.0,
        },
        "artifacts": {"report": "/safe/report.md"},
    }


def _stock_runner(**kwargs) -> dict:  # type: ignore[no-untyped-def]
    marketplace = kwargs["marketplace"]
    metrics = (
        {"general_fbo": {"present": 83}}
        if marketplace == "ozon"
        else {"stocks": {"quantity": 91}}
    )
    return {
        "run_id": "stock-run",
        "started_at": "2026-08-02T10:00:00+03:00",
        "overall_status": "ok",
        "metrics": metrics,
    }


@pytest.mark.parametrize(
    ("marketplace", "owner_field", "owner_value", "stock_total"),
    [
        ("ozon", "seller_slug", "vital-shevron", 83),
        ("wb", "supplier_id", "4516781", 91),
    ],
)
def test_overview_normalizes_sources_and_exact_owner_filters(
    tmp_path: Path,
    marketplace: str,
    owner_field: str,
    owner_value: str,
    stock_total: int,
) -> None:
    parser = FakeParserClient()
    result = run_store_analytics_overview(
        credentials=object(),
        data_dir=tmp_path,
        marketplace=marketplace,
        period_days=30,
        run_id=f"overview-{marketplace}",
        now=NOW,
        period_report_runner=_period_runner,
        stock_monitor_runner=_stock_runner,
        parser_client=parser,
        wb_supplier_id="4516781",
        ozon_seller_slug="vital-shevron",
        region_id="moscow",
        query_pack_id="shevron-core",
    )

    assert result["overall_status"] == "warning"
    assert result["sales"]["data_available"] is True
    assert result["sales"]["current"]["orders"] == 20
    assert result["sales"]["changes"]["orders"] == 10
    assert result["sales"]["margin"]["data_available"] is False
    assert result["sales"]["margin"]["available_proxy"]["value"] == 21000.0
    assert result["stocks"]["total_units"] == stock_total
    assert result["parser_visibility"]["data_available"] is True
    visibility = result["parser_visibility"]
    assert visibility["freshness"]["state"] == "fresh"
    assert visibility["comparison"]["available"] is True
    assert visibility["comparison"]["previous_date"] != result["period"]["previous_to"]
    assert visibility["comparison"]["current_date"] != result["period"]["current_to"]
    assert visibility["metrics"] == {
        "visible_products": 12,
        "query_count": 30,
        "total_rows": 42,
    }
    assert visibility["movements"] == {
        "improved": 4,
        "declined": 2,
        "lost": 1,
        "new": 3,
        "unchanged": 20,
    }
    assert {item["code"] for item in result["problems"]} == {
        "parser_visibility_losses",
        "returns_increased",
    }
    assert {item["code"] for item in result["next_actions"]} == {
        "review_returns",
        "review_visibility_losses",
    }
    comparison_call = next(
        call for call in parser.calls if call[0].endswith("store-period-comparison")
    )
    summary_call = next(call for call in parser.calls if call[0].endswith("/summary"))
    assert summary_call[1] == {
        "region_id": "moscow",
        "query_pack_id": "shevron-core",
    }
    assert comparison_call[1][owner_field] == owner_value
    assert "supplier_id" not in comparison_call[1] if marketplace == "ozon" else True
    assert "seller_slug" not in comparison_call[1] if marketplace == "wb" else True
    assert comparison_call[1]["previous_region_id"] == "moscow"
    assert comparison_call[1]["current_region_id"] == "moscow"
    assert comparison_call[1]["previous_query_pack_id"] == "shevron-core"
    assert comparison_call[1]["current_query_pack_id"] == "shevron-core"
    if marketplace == "wb":
        assert comparison_call[1]["query_scope"] == "intersection"
        assert "previous_date" not in comparison_call[1]
        assert "current_date" not in comparison_call[1]
    else:
        assert comparison_call[1]["query_scope"] == "union"
        assert comparison_call[1]["previous_date"] == "2026-07-30"
        assert comparison_call[1]["current_date"] == "2026-07-31"
        probes = [call for call in parser.calls if call[0].endswith("/query-positions")]
        assert probes
        assert all(
            call[1]["region_id"] == "moscow"
            and call[1]["query_pack_id"] == "shevron-core"
            and call[1]["limit"] == 1
            and call[1]["date_from"] == call[1]["date_to"]
            for call in probes
        )
    assert result["period"] == {
        "current_from": "2026-07-03",
        "current_to": "2026-08-01",
        "previous_from": "2026-06-03",
        "previous_to": "2026-07-02",
    }
    assert Path(result["artifacts"]["summary"]).is_file()
    assert Path(result["artifacts"]["report"]).is_file()
    assert Path(result["artifacts"]["manifest"]).is_file()


def test_overview_isolates_source_failures_and_marks_parser_stale(tmp_path: Path) -> None:
    def failed_period(**kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("secret-sales-error")

    def failed_stock(**kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("secret-stock-error")

    result = run_store_analytics_overview(
        credentials=object(),
        data_dir=tmp_path,
        marketplace="wb",
        period_days=7,
        run_id="overview-failures",
        now=NOW,
        period_report_runner=failed_period,
        stock_monitor_runner=failed_stock,
        parser_client=FakeParserClient(built_at="2026-07-20T00:00:00Z"),
        region_id="moscow",
        query_pack_id="shevron-core",
    )

    assert result["overall_status"] == "warning"
    assert result["sales"] == {
        "data_available": False,
        "reason_code": "period_report_unavailable",
    }
    assert result["stocks"] == {
        "data_available": False,
        "reason_code": "stock_monitor_unavailable",
    }
    assert result["parser_visibility"]["freshness"]["state"] == "stale"
    codes = {item["code"] for item in result["problems"]}
    assert {"period_report_unavailable", "stock_monitor_unavailable", "parser_data_stale"} <= codes
    serialized = str(result)
    assert "secret-sales-error" not in serialized
    assert "secret-stock-error" not in serialized


def test_overview_parser_failure_is_data_unavailable_without_fabrication(tmp_path: Path) -> None:
    result = run_store_analytics_overview(
        credentials=object(),
        data_dir=tmp_path,
        marketplace="ozon",
        period_days=90,
        run_id="overview-parser-failure",
        now=NOW,
        period_report_runner=_period_runner,
        stock_monitor_runner=_stock_runner,
        parser_client=FakeParserClient(fail=True),
        region_id="moscow",
        query_pack_id="shevron-core",
    )

    assert result["overall_status"] == "warning"
    assert result["parser_visibility"] == {
        "data_available": False,
        "reason_code": "parser_data_unavailable",
    }
    assert "visibility" not in result["parser_visibility"]
    assert "secret-parser-error" not in str(result)


def test_overview_parser_timestamp_materially_in_future_has_unknown_freshness(
    tmp_path: Path,
) -> None:
    result = run_store_analytics_overview(
        credentials=object(),
        data_dir=tmp_path,
        marketplace="ozon",
        period_days=7,
        run_id="overview-parser-future",
        now=NOW,
        period_report_runner=_period_runner,
        stock_monitor_runner=_stock_runner,
        parser_client=FakeParserClient(built_at="2026-08-02T13:00:00Z"),
        region_id="moscow",
        query_pack_id="shevron-core",
    )

    assert result["overall_status"] == "warning"
    assert result["parser_visibility"]["freshness"] == {
        "observed_at": "2026-08-02T13:00:00Z",
        "age_seconds": None,
        "state": "unknown",
    }
    assert "parser_freshness_unknown" in {
        item["code"] for item in result["problems"]
    }


def test_ozon_one_actual_snapshot_is_comparison_unavailable_not_growth(
    tmp_path: Path,
) -> None:
    class OneSnapshotParser(FakeParserClient):
        def get(self, path: str, *, params: dict | None = None) -> dict:
            self.calls.append((path, dict(params or {})))
            if path.endswith("/summary"):
                return {"manifest": {"built_at_utc": self.built_at}}
            if path.endswith("/run-quality"):
                return {
                    "rows": [
                        {
                            "analytical_date": "2026-07-30",
                            "component": "serp",
                            "is_published": True,
                            "is_analytics_eligible": True,
                        }
                    ]
                }
            if path.endswith("/query-positions"):
                return {
                    "rows": [
                        {
                            "run_date": "2026-07-30",
                            "region_id": "moscow",
                            "query_pack_id": "shevron-core",
                        }
                    ],
                    "returned_rows": 1,
                    "total_rows": 12,
                }
            raise AssertionError("comparison endpoint must not be called for one snapshot")

    parser = OneSnapshotParser()
    result = run_store_analytics_overview(
        credentials=object(),
        data_dir=tmp_path,
        marketplace="ozon",
        period_days=30,
        run_id="overview-ozon-one-snapshot",
        now=NOW,
        period_report_runner=_period_runner,
        stock_monitor_runner=_stock_runner,
        parser_client=parser,
        ozon_seller_slug="vital-shevron",
        region_id="moscow",
        query_pack_id="shevron-core",
    )

    visibility = result["parser_visibility"]
    assert visibility["data_available"] is True
    assert visibility["comparison"] == {
        "available": False,
        "reason_code": "parser_comparison_unavailable",
        "snapshot_dates": ["2026-07-30"],
    }
    assert visibility["movements"] == {}
    assert "parser_visibility_losses" not in {
        item["code"] for item in result["problems"]
    }
    assert "parser_comparison_unavailable" in {
        item["code"] for item in result["problems"]
    }
    assert not any(path.endswith("store-period-comparison") for path, _ in parser.calls)


def test_overview_malformed_source_payloads_fail_closed(tmp_path: Path) -> None:
    class MalformedParser:
        def get(self, path: str, *, params: dict | None = None) -> dict:
            if path.endswith("/summary"):
                return {"manifest": {"built_at_utc": "2026-08-02T08:00:00Z"}}
            return {"complete": True, "previous": {}, "current": {}}

    result = run_store_analytics_overview(
        credentials=object(),
        data_dir=tmp_path,
        marketplace="ozon",
        period_days=7,
        run_id="overview-malformed",
        now=NOW,
        period_report_runner=lambda **kwargs: {"overall_status": "ok", "metrics": {}},
        stock_monitor_runner=lambda **kwargs: {"overall_status": "ok", "metrics": {}},
        parser_client=MalformedParser(),  # type: ignore[arg-type]
        region_id="moscow",
        query_pack_id="shevron-core",
    )

    assert result["overall_status"] == "warning"
    assert result["sales"]["reason_code"] == "period_report_unavailable"
    assert result["stocks"]["reason_code"] == "stock_monitor_unavailable"
    assert result["parser_visibility"]["reason_code"] == "parser_data_unavailable"


def test_stage3_markdown_owner_copy_is_russian_and_hides_dynamic_codes(
    tmp_path: Path,
) -> None:
    report = tmp_path / "overview.md"
    _write_report(
        report,
        {
            "marketplace": "ozon",
            "period_days": 30,
            "region_id": "moscow",
            "overall_status": "secret_dynamic_status",
            "sales": {"data_available": False},
            "stocks": {"data_available": True},
            "parser_visibility": {
                "data_available": True,
                "comparison": {
                    "available": False,
                    "reason_code": "secret_dynamic_reason",
                },
            },
        },
    )

    text = report.read_text(encoding="utf-8")
    for forbidden in (
        "data unavailable",
        "Parser snapshots",
        "Status:",
        "available",
        "Parser visibility",
        "Marketplace",
        "reason_code",
        "secret_dynamic_status",
        "secret_dynamic_reason",
    ):
        assert forbidden not in text
    assert "Площадка: `Ozon`" in text
    assert "Регион поиска: `Москва`" in text
    assert "Снимки поиска: `сравнение недоступно`" in text
    assert "Статус: `неизвестно`" in text
    assert "Продажи: `данные недоступны`" in text
    assert "Видимость в поиске: `доступна`" in text
