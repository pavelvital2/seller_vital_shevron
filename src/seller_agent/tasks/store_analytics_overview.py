from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

from seller_agent.config import AppCredentials
from seller_agent.core.run_manifest import write_summary_run_manifest
from seller_agent.marketplaces.parser_data_api import ParserDataApiClient
from seller_agent.reports.writer import ensure_dir, write_json
from seller_agent.tasks.marketplace_period_report import run_marketplace_period_report
from seller_agent.tasks.ozon_stock_supply_monitor import run_ozon_stock_supply_monitor
from seller_agent.tasks.wb_stock_supply_monitor import run_wb_stock_supply_monitor


MOSCOW = ZoneInfo("Europe/Moscow")
PERIOD_OPTIONS = frozenset({7, 30, 90})
MARKETPLACES = frozenset({"ozon", "wb"})
PARSER_FRESH_SECONDS = 48 * 60 * 60
PARSER_FUTURE_SKEW_SECONDS = 5 * 60
DEFAULT_WB_SUPPLIER_ID = "4516781"
DEFAULT_OZON_SELLER_SLUG = "vital-shevron"
DEFAULT_QUERY_PACK_ID = "shevron-core"
REGIONS_BY_MARKETPLACE = {
    "ozon": frozenset({"moscow", "rostov-on-don"}),
    "wb": frozenset({"moscow", "rostov-on-don", "novosibirsk", "kazan"}),
}
OZON_RUN_QUALITY_LIMIT = 100
OZON_DATE_PROBE_LIMIT = 32
OWNER_STATUS_LABELS = {
    "ok": "готово",
    "success": "готово",
    "partial_success": "готово частично",
    "warning": "требует внимания",
    "failed": "ошибка",
    "timeout": "превышено время ожидания",
    "cancelled": "отменено",
}
OWNER_MARKETPLACE_LABELS = {"ozon": "Ozon", "wb": "Wildberries"}
OWNER_REGION_LABELS = {
    "moscow": "Москва",
    "rostov-on-don": "Ростов-на-Дону",
    "novosibirsk": "Новосибирск",
    "kazan": "Казань",
}
PROBLEM_LABELS = {
    "confirmed_zero_stock": "Подтверждён нулевой доступный остаток.",
    "orders_declined": "Заказы снизились к предыдущему равному периоду.",
    "parser_data_partial": "Parser вернул неполный набор данных.",
    "parser_data_stale": "Данные Parser устарели.",
    "parser_data_unavailable": "Данные Parser недоступны.",
    "parser_comparison_unavailable": "Для сравнения Parser пока недостаточно подтверждённых срезов.",
    "parser_freshness_unknown": "Свежесть Parser не подтверждена.",
    "parser_visibility_losses": "Parser подтвердил снижения или выпадения видимости.",
    "period_report_unavailable": "Отчёт продаж недоступен.",
    "period_report_warning": "Отчёт продаж содержит предупреждения источника.",
    "returns_increased": "Возвраты выросли к предыдущему равному периоду.",
    "revenue_declined": "Выручка снизилась к предыдущему равному периоду.",
    "stock_monitor_unavailable": "Данные остатков недоступны.",
    "stock_monitor_warning": "Монитор остатков содержит предупреждения источника.",
}
ACTION_LABELS = {
    "retry_read_only_source": "Безопасно повторить недоступный read-only источник.",
    "review_source_warnings": "Проверить предупреждения исходного отчёта.",
    "review_supply_plan": "Проверить потребность в поставке.",
    "wait_for_parser_refresh": "Дождаться свежего обновления Parser.",
    "review_parser_freshness": "Проверить состояние обновления Parser.",
    "review_parser_quality": "Учитывать ограничение качества Parser до решения.",
    "wait_for_parser_snapshot": "Дождаться второго подтверждённого Parser-среза этого региона.",
    "review_returns": "Проверить причины роста возвратов.",
    "review_sales_decline": "Проверить товары и источники снижения продаж.",
    "review_visibility_losses": "Проверить выпавшие запросы и карточки без изменения marketplace state.",
}
PeriodRunner = Callable[..., dict[str, Any]]
StockRunner = Callable[..., dict[str, Any]]


def run_store_analytics_overview(
    *,
    credentials: AppCredentials | object,
    data_dir: Path,
    marketplace: str,
    period_days: int,
    run_id: str | None = None,
    now: datetime | None = None,
    period_report_runner: PeriodRunner = run_marketplace_period_report,
    stock_monitor_runner: StockRunner | None = None,
    parser_client: ParserDataApiClient | None = None,
    wb_supplier_id: str = DEFAULT_WB_SUPPLIER_ID,
    ozon_seller_slug: str = DEFAULT_OZON_SELLER_SLUG,
    region_id: str = "moscow",
    query_pack_id: str = DEFAULT_QUERY_PACK_ID,
) -> dict[str, Any]:
    """Compose existing read-only sources into one deterministic owner view."""
    normalized_marketplace = str(marketplace).strip().lower()
    if normalized_marketplace not in MARKETPLACES:
        raise ValueError("marketplace must be ozon or wb")
    if int(period_days) not in PERIOD_OPTIONS:
        raise ValueError("period_days must be 7, 30 or 90")
    wb_supplier_id = str(wb_supplier_id).strip()
    ozon_seller_slug = str(ozon_seller_slug).strip()
    region_id = str(region_id).strip().lower()
    query_pack_id = str(query_pack_id).strip()
    if not wb_supplier_id.isdigit() or not wb_supplier_id:
        raise ValueError("wb ownership filter is invalid")
    if not ozon_seller_slug or any(
        char not in "abcdefghijklmnopqrstuvwxyz0123456789-_" for char in ozon_seller_slug
    ):
        raise ValueError("ozon ownership filter is invalid")
    if region_id not in REGIONS_BY_MARKETPLACE[normalized_marketplace]:
        raise ValueError("parser region is invalid for marketplace")
    if query_pack_id != DEFAULT_QUERY_PACK_ID:
        raise ValueError("parser query pack is invalid")

    started_at = now or datetime.now(MOSCOW)
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=MOSCOW)
    started_at = started_at.astimezone(MOSCOW)
    run_id = run_id or (
        f"store_analytics_overview_{normalized_marketplace}_"
        f"{started_at.strftime('%Y%m%dT%H%M%S')}"
    )
    run_dir = ensure_dir(data_dir / "runs" / started_at.date().isoformat() / run_id)
    period = _period_boundaries(started_at.date(), int(period_days))
    problems: list[dict[str, str]] = []
    next_actions: list[dict[str, str]] = []
    source_run_ids: list[str] = []

    sales = _collect_sales(
        runner=period_report_runner,
        credentials=credentials,
        data_dir=data_dir,
        marketplace=normalized_marketplace,
        period=period,
        run_id=run_id,
    )
    source_run_ids.extend(sales.pop("_source_run_ids", []))
    if not sales["data_available"]:
        _add_problem_and_action(
            problems,
            next_actions,
            code="period_report_unavailable",
            source="marketplace-period-report",
            action="retry_read_only_source",
        )
    elif sales.get("quality") != "ok":
        _add_problem_and_action(
            problems,
            next_actions,
            code="period_report_warning",
            source="marketplace-period-report",
            action="review_source_warnings",
        )
    if sales.get("data_available") is True:
        sales_changes = sales.get("changes") if isinstance(sales.get("changes"), dict) else {}
        for code, metric, action in (
            ("orders_declined", "orders", "review_sales_decline"),
            ("revenue_declined", "revenue", "review_sales_decline"),
        ):
            if _number(sales_changes.get(metric)) < 0:
                _add_problem_and_action(
                    problems,
                    next_actions,
                    code=code,
                    source="marketplace-period-report",
                    action=action,
                )
        if _number(sales_changes.get("returns")) > 0:
            _add_problem_and_action(
                problems,
                next_actions,
                code="returns_increased",
                source="marketplace-period-report",
                action="review_returns",
            )

    stock_runner = stock_monitor_runner or _default_stock_runner
    stocks = _collect_stocks(
        runner=stock_runner,
        credentials=credentials,
        data_dir=data_dir,
        marketplace=normalized_marketplace,
        run_id=run_id,
    )
    stock_source_run_id = stocks.pop("_source_run_id", "")
    if stock_source_run_id:
        source_run_ids.append(stock_source_run_id)
    if not stocks["data_available"]:
        _add_problem_and_action(
            problems,
            next_actions,
            code="stock_monitor_unavailable",
            source=f"{normalized_marketplace}-stock-supply-monitor",
            action="retry_read_only_source",
        )
    else:
        if stocks.get("quality") != "ok":
            _add_problem_and_action(
                problems,
                next_actions,
                code="stock_monitor_warning",
                source=f"{normalized_marketplace}-stock-supply-monitor",
                action="review_source_warnings",
            )
        if stocks.get("total_units") == 0:
            _add_problem_and_action(
                problems,
                next_actions,
                code="confirmed_zero_stock",
                source=f"{normalized_marketplace}-stock-supply-monitor",
                action="review_supply_plan",
            )

    parser_visibility = _collect_parser_visibility(
        client=parser_client or ParserDataApiClient(),
        marketplace=normalized_marketplace,
        now=started_at,
        wb_supplier_id=wb_supplier_id,
        ozon_seller_slug=ozon_seller_slug,
        region_id=region_id,
        query_pack_id=query_pack_id,
    )
    if not parser_visibility["data_available"]:
        _add_problem_and_action(
            problems,
            next_actions,
            code="parser_data_unavailable",
            source="parser-data-api",
            action="retry_read_only_source",
        )
    elif parser_visibility["freshness"]["state"] == "stale":
        _add_problem_and_action(
            problems,
            next_actions,
            code="parser_data_stale",
            source="parser-data-api",
            action="wait_for_parser_refresh",
        )
    elif parser_visibility["freshness"]["state"] == "unknown":
        _add_problem_and_action(
            problems,
            next_actions,
            code="parser_freshness_unknown",
            source="parser-data-api",
            action="review_parser_freshness",
        )
    if (
        parser_visibility.get("data_available") is True
        and parser_visibility.get("comparison", {}).get("available") is False
    ):
        _add_problem_and_action(
            problems,
            next_actions,
            code="parser_comparison_unavailable",
            source="parser-data-api",
            action="wait_for_parser_snapshot",
        )
    if (
        parser_visibility.get("data_available") is True
        and (
            parser_visibility.get("quality", {}).get("partial") is True
            or parser_visibility.get("quality", {}).get("complete") is not True
        )
    ):
        _add_problem_and_action(
            problems,
            next_actions,
            code="parser_data_partial",
            source="parser-data-api",
            action="review_parser_quality",
        )
    if parser_visibility.get("data_available") is True:
        movements = (
            parser_visibility.get("movements")
            if isinstance(parser_visibility.get("movements"), dict)
            else {}
        )
        if _number(movements.get("declined")) > 0 or _number(movements.get("lost")) > 0:
            _add_problem_and_action(
                problems,
                next_actions,
                code="parser_visibility_losses",
                source="parser-data-api",
                action="review_visibility_losses",
            )

    summary: dict[str, Any] = {
        "schema_version": "store-analytics-overview/v1",
        "task": "store-analytics-overview",
        "run_id": run_id,
        "started_at": started_at.isoformat(timespec="seconds"),
        "overall_status": "warning" if problems else "ok",
        "marketplace": normalized_marketplace,
        "period_days": int(period_days),
        "region_id": region_id,
        "query_pack_id": query_pack_id,
        "period": period,
        "sales": sales,
        "stocks": stocks,
        "parser_visibility": parser_visibility,
        "problems": problems,
        "next_actions": next_actions,
        "artifacts": {
            "summary": str(run_dir / "summary.json"),
            "report": str(run_dir / "store_analytics_overview.md"),
        },
    }
    _write_report(run_dir / "store_analytics_overview.md", summary)
    write_json(run_dir / "summary.json", summary)
    manifest_artifacts = write_summary_run_manifest(
        data_dir=data_dir,
        run_dir=run_dir,
        summary=summary,
        task="store-analytics-overview",
        mode="read_only",
        risk="low",
        marketplaces=[normalized_marketplace],
        inputs={
            "marketplace": normalized_marketplace,
            "period_days": int(period_days),
            "region_id": region_id,
            "query_pack_id": query_pack_id,
        },
        source_run_ids=source_run_ids,
        lifecycle_status="closed",
        closed=True,
    )
    summary["artifacts"].update(manifest_artifacts)
    write_json(run_dir / "summary.json", summary)
    return summary


def _period_boundaries(today: date, period_days: int) -> dict[str, str]:
    current_to = today - timedelta(days=1)
    current_from = current_to - timedelta(days=period_days - 1)
    previous_to = current_from - timedelta(days=1)
    previous_from = previous_to - timedelta(days=period_days - 1)
    return {
        "current_from": current_from.isoformat(),
        "current_to": current_to.isoformat(),
        "previous_from": previous_from.isoformat(),
        "previous_to": previous_to.isoformat(),
    }


def _collect_sales(
    *,
    runner: PeriodRunner,
    credentials: object,
    data_dir: Path,
    marketplace: str,
    period: dict[str, str],
    run_id: str,
) -> dict[str, Any]:
    try:
        current = runner(
            credentials=credentials,
            data_dir=data_dir,
            marketplace=marketplace,
            report_type="financial",
            date_from=period["current_from"],
            date_to=period["current_to"],
            run_id=f"{run_id}_sales_current",
        )
        previous = runner(
            credentials=credentials,
            data_dir=data_dir,
            marketplace=marketplace,
            report_type="financial",
            date_from=period["previous_from"],
            date_to=period["previous_to"],
            run_id=f"{run_id}_sales_previous",
        )
        current_metrics = _sales_metrics(current)
        previous_metrics = _sales_metrics(previous)
        return {
            "data_available": True,
            "freshness": {
                "observed_at": str(current.get("started_at") or ""),
                "state": "fresh",
            },
            "current": current_metrics,
            "previous": previous_metrics,
            "changes": {
                key: _number(current_metrics.get(key)) - _number(previous_metrics.get(key))
                for key in ("orders", "revenue", "returns", "net_before_cogs")
            },
            "quality": (
                "ok"
                if str(current.get("overall_status") or "") == "ok"
                and str(previous.get("overall_status") or "") == "ok"
                else "warning"
            ),
            "margin": {
                "data_available": False,
                "reason_code": "cogs_not_available_in_period_report",
                "available_proxy": {
                    "name": "net_before_cogs",
                    "value": current_metrics["net_before_cogs"],
                },
            },
            "_source_run_ids": [
                str(current.get("run_id") or ""),
                str(previous.get("run_id") or ""),
            ],
        }
    except Exception:  # noqa: BLE001 - each source fails independently with a safe code.
        return {"data_available": False, "reason_code": "period_report_unavailable"}


def _sales_metrics(value: dict[str, Any]) -> dict[str, float | int]:
    metrics = value.get("metrics") if isinstance(value.get("metrics"), dict) else {}
    required = ("orders", "order_amount", "returns", "net")
    if any(key not in metrics for key in required):
        raise ValueError("period metrics incomplete")
    return {
        "orders": int(_required_number(metrics["orders"])),
        "revenue": _required_number(metrics["order_amount"]),
        "returns": int(_required_number(metrics["returns"])),
        "net_before_cogs": _required_number(metrics["net"]),
    }


def _collect_stocks(
    *,
    runner: StockRunner,
    credentials: object,
    data_dir: Path,
    marketplace: str,
    run_id: str,
) -> dict[str, Any]:
    try:
        value = runner(
            marketplace=marketplace,
            credentials=credentials,
            data_dir=data_dir,
            run_id=f"{run_id}_stocks",
        )
        metrics = value.get("metrics") if isinstance(value.get("metrics"), dict) else {}
        if marketplace == "ozon":
            section = metrics.get("general_fbo") if isinstance(metrics.get("general_fbo"), dict) else {}
            if "present" not in section:
                raise ValueError("stock metrics incomplete")
            total_units = int(_required_number(section["present"]))
        else:
            section = metrics.get("stocks") if isinstance(metrics.get("stocks"), dict) else {}
            if "quantity" not in section:
                raise ValueError("stock metrics incomplete")
            total_units = int(_required_number(section["quantity"]))
        return {
            "data_available": True,
            "total_units": total_units,
            "freshness": {
                "observed_at": str(value.get("started_at") or ""),
                "state": "fresh",
            },
            "quality": "ok" if str(value.get("overall_status") or "") == "ok" else "warning",
            "_source_run_id": str(value.get("run_id") or ""),
        }
    except Exception:  # noqa: BLE001 - safe source isolation.
        return {"data_available": False, "reason_code": "stock_monitor_unavailable"}


def _default_stock_runner(
    *,
    marketplace: str,
    credentials: AppCredentials | object,
    data_dir: Path,
    run_id: str,
) -> dict[str, Any]:
    if marketplace == "ozon":
        return run_ozon_stock_supply_monitor(
            credentials=credentials,  # type: ignore[arg-type]
            data_dir=data_dir,
            run_id=run_id,
        )
    return run_wb_stock_supply_monitor(
        credentials=credentials,  # type: ignore[arg-type]
        data_dir=data_dir,
        run_id=run_id,
    )


def _collect_parser_visibility(
    *,
    client: ParserDataApiClient,
    marketplace: str,
    now: datetime,
    wb_supplier_id: str,
    ozon_seller_slug: str,
    region_id: str,
    query_pack_id: str,
) -> dict[str, Any]:
    try:
        summary = client.get(
            f"/warehouse/{marketplace}/summary",
            params={"region_id": region_id, "query_pack_id": query_pack_id},
        )
        owner_filter = (
            {"supplier_id": wb_supplier_id}
            if marketplace == "wb"
            else {"seller_slug": ozon_seller_slug}
        )
        if not isinstance(summary, dict):
            raise ValueError
        manifest = summary.get("manifest") if isinstance(summary.get("manifest"), dict) else {}
        observed_at = str(manifest.get("built_at_utc") or summary.get("built_at_utc") or "")
        freshness = _freshness(observed_at, now=now)
        if marketplace == "wb":
            comparison = client.get(
                "/warehouse/wb/aggregates/store-period-comparison",
                params={
                    **owner_filter,
                    "query_scope": "intersection",
                    "previous_region_id": region_id,
                    "current_region_id": region_id,
                    "previous_query_pack_id": query_pack_id,
                    "current_query_pack_id": query_pack_id,
                    "top_n": 30,
                    "page_size": 100,
                },
            )
            return _normalize_parser_comparison(
                comparison,
                freshness=freshness,
                owner_filter=owner_filter,
                region_id=region_id,
                query_pack_id=query_pack_id,
                query_scope="intersection",
            )

        snapshot_dates = _ozon_actual_snapshot_dates(
            client,
            region_id=region_id,
            query_pack_id=query_pack_id,
        )
        if not snapshot_dates:
            raise ValueError("parser snapshot is unavailable")
        if len(snapshot_dates) < 2:
            return {
                "data_available": True,
                "freshness": freshness,
                "region_id": region_id,
                "query_pack_id": query_pack_id,
                "ownership_filter": {
                    "field": next(iter(owner_filter)),
                    "value": next(iter(owner_filter.values())),
                },
                "comparison": {
                    "available": False,
                    "reason_code": "parser_comparison_unavailable",
                    "snapshot_dates": sorted(snapshot_dates),
                },
                "metrics": {},
                "movements": {},
                "quality": {"complete": False, "partial": True},
            }
        previous_date, current_date = snapshot_dates[-2:]
        comparison = client.get(
            "/warehouse/ozon/aggregates/store-period-comparison",
            params={
                **owner_filter,
                "previous_date": previous_date,
                "current_date": current_date,
                "query_scope": "union",
                "previous_region_id": region_id,
                "current_region_id": region_id,
                "previous_query_pack_id": query_pack_id,
                "current_query_pack_id": query_pack_id,
                "page_size": 100,
            },
        )
        return _normalize_parser_comparison(
            comparison,
            freshness=freshness,
            owner_filter=owner_filter,
            region_id=region_id,
            query_pack_id=query_pack_id,
            query_scope="union",
            expected_dates=(previous_date, current_date),
        )
    except Exception:  # noqa: BLE001 - never expose parser token/error payload.
        return {"data_available": False, "reason_code": "parser_data_unavailable"}


def _ozon_actual_snapshot_dates(
    client: ParserDataApiClient,
    *,
    region_id: str,
    query_pack_id: str,
) -> list[str]:
    quality = client.get(
        "/warehouse/ozon/run-quality",
        params={"limit": OZON_RUN_QUALITY_LIMIT},
    )
    rows = quality.get("rows") if isinstance(quality, dict) else None
    if not isinstance(rows, list):
        raise ValueError("parser run quality payload is invalid")
    candidates: set[str] = set()
    for row in rows:
        if (
            not isinstance(row, dict)
            or row.get("component") != "serp"
            or row.get("is_published") is not True
            or row.get("is_analytics_eligible") is not True
        ):
            continue
        candidate = _iso_date_or_none(row.get("analytical_date"))
        if candidate:
            candidates.add(candidate)
    actual: list[str] = []
    for candidate in sorted(candidates, reverse=True)[:OZON_DATE_PROBE_LIMIT]:
        payload = client.get(
            "/warehouse/ozon/query-positions",
            params={
                "date_from": candidate,
                "date_to": candidate,
                "region_id": region_id,
                "query_pack_id": query_pack_id,
                "limit": 1,
            },
        )
        position_rows = payload.get("rows") if isinstance(payload, dict) else None
        if not isinstance(position_rows, list):
            raise ValueError("parser query positions payload is invalid")
        if any(
            isinstance(row, dict)
            and str(row.get("run_date") or row.get("date") or "") == candidate
            and str(row.get("region_id") or "") == region_id
            and str(row.get("query_pack_id") or "") == query_pack_id
            for row in position_rows
        ):
            actual.append(candidate)
            if len(actual) == 2:
                break
    return sorted(actual)


def _normalize_parser_comparison(
    value: Any,
    *,
    freshness: dict[str, Any],
    owner_filter: dict[str, str],
    region_id: str,
    query_pack_id: str,
    query_scope: str,
    expected_dates: tuple[str, str] | None = None,
) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("data_available") is False:
        raise ValueError("parser comparison payload is invalid")
    previous_date = _iso_date_or_none(value.get("previous_date"))
    current_date = _iso_date_or_none(value.get("current_date"))
    if not previous_date or not current_date or previous_date >= current_date:
        raise ValueError("parser comparison dates are invalid")
    if expected_dates is not None and (previous_date, current_date) != expected_dates:
        raise ValueError("parser comparison dates do not match selected snapshots")
    previous = _safe_metrics(value.get("previous"))
    current = _safe_metrics(value.get("current"))
    visible_products = (
        current.get("visible_products")
        if current
        else _current_count(value.get("visible_products"))
    )
    query_count = _current_count(value.get("query_count"))
    total_rows = _current_count(value.get("total_rows"))
    metrics = {
        "visible_products": _required_nonnegative_count(visible_products),
        "query_count": _required_nonnegative_count(query_count),
        "total_rows": _required_nonnegative_count(total_rows),
    }
    result: dict[str, Any] = {
        "data_available": True,
        "freshness": freshness,
        "region_id": region_id,
        "query_pack_id": query_pack_id,
        "ownership_filter": {
            "field": next(iter(owner_filter)),
            "value": next(iter(owner_filter.values())),
        },
        "comparison": {
            "available": True,
            "previous_date": previous_date,
            "current_date": current_date,
            "query_scope": query_scope,
        },
        "metrics": metrics,
        "movements": _normalized_movements(value.get("movement")),
        "quality": {
            "complete": value.get("complete") is True,
            "partial": value.get("partial") is True,
        },
    }
    if previous and current:
        result["previous"] = previous
        result["current"] = current
    return result


def _normalized_movements(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        raise ValueError("parser movement payload is invalid")
    aliases = {
        "improved": ("improved_pairs", "improved"),
        "declined": ("declined_pairs", "declined"),
        "lost": ("lost_pairs", "lost", "missing"),
        "new": ("new_pairs", "new"),
        "unchanged": ("unchanged_pairs", "unchanged"),
    }
    normalized: dict[str, int] = {}
    for target, candidates in aliases.items():
        source = next((name for name in candidates if name in value), "")
        if not source:
            raise ValueError("parser movement payload is incomplete")
        normalized[target] = _required_nonnegative_count(value[source])
    return normalized


def _current_count(value: Any) -> Any:
    if isinstance(value, dict):
        return value.get("current")
    return value


def _required_nonnegative_count(value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError("parser count is invalid")
    try:
        normalized = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("parser count is invalid") from exc
    if normalized < 0:
        raise ValueError("parser count is invalid")
    return normalized


def _iso_date_or_none(value: Any) -> str | None:
    try:
        normalized = str(value or "")
        date.fromisoformat(normalized)
    except (TypeError, ValueError):
        return None
    return normalized


def _freshness(observed_at: str, *, now: datetime) -> dict[str, Any]:
    state = "unknown"
    age_seconds: int | None = None
    try:
        parsed = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=MOSCOW)
        raw_age_seconds = int((now.astimezone(parsed.tzinfo) - parsed).total_seconds())
        if raw_age_seconds < -PARSER_FUTURE_SKEW_SECONDS:
            return {"observed_at": observed_at, "age_seconds": None, "state": "unknown"}
        age_seconds = max(0, raw_age_seconds)
        state = "fresh" if age_seconds <= PARSER_FRESH_SECONDS else "stale"
    except (TypeError, ValueError):
        pass
    return {"observed_at": observed_at, "age_seconds": age_seconds, "state": state}


def _safe_metrics(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, Any] = {}
    for key, item in value.items():
        if isinstance(item, bool) or item is None or isinstance(item, (int, float)):
            result[str(key)] = item
        elif isinstance(item, str) and len(item) <= 128:
            result[str(key)] = item
    return result


def _add_problem_and_action(
    problems: list[dict[str, str]],
    actions: list[dict[str, str]],
    *,
    code: str,
    source: str,
    action: str,
) -> None:
    problems.append(
        {
            "code": code,
            "label": PROBLEM_LABELS.get(code, "Подтверждённое ограничение источника."),
            "severity": "warning",
            "source": source,
        }
    )
    if not any(item["code"] == action for item in actions):
        actions.append(
            {
                "code": action,
                "label": ACTION_LABELS.get(action, "Проверить источник безопасным read-only способом."),
                "source": source,
            }
        )


def _number(value: Any) -> float:
    try:
        return round(float(value or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def _required_number(value: Any) -> float:
    if isinstance(value, bool) or value in (None, ""):
        raise ValueError("numeric source metric is invalid")
    try:
        return round(float(value), 2)
    except (TypeError, ValueError) as exc:
        raise ValueError("numeric source metric is invalid") from exc


def _write_report(path: Path, summary: dict[str, Any]) -> None:
    sales = summary["sales"]
    stocks = summary["stocks"]
    parser = summary["parser_visibility"]
    comparison = parser.get("comparison") if isinstance(parser.get("comparison"), dict) else {}
    comparison_text = (
        f"{comparison.get('previous_date')} → {comparison.get('current_date')}"
        if comparison.get("available") is True
        else "сравнение недоступно"
    )
    status_label = OWNER_STATUS_LABELS.get(
        str(summary.get("overall_status") or ""),
        "неизвестно",
    )
    marketplace_label = OWNER_MARKETPLACE_LABELS.get(
        str(summary.get("marketplace") or ""),
        "неизвестно",
    )
    region_label = OWNER_REGION_LABELS.get(
        str(summary.get("region_id") or ""),
        "неизвестно",
    )
    lines = [
        "# Аналитика магазина",
        "",
        f"Площадка: `{marketplace_label}`",
        f"Период: `{summary['period_days']}` дней",
        f"Регион поиска: `{region_label}`",
        f"Снимки поиска: `{comparison_text}`",
        f"Статус: `{status_label}`",
        "",
        "## Источники",
        "",
        f"- Продажи: `{'доступны' if sales['data_available'] else 'данные недоступны'}`",
        f"- Остатки: `{'доступны' if stocks['data_available'] else 'данные недоступны'}`",
        f"- Видимость в поиске: `{'доступна' if parser['data_available'] else 'данные недоступны'}`",
        "",
        "Маржа не утверждается без себестоимости; отдельно показываются только поступления до себестоимости.",
        "Операции записи на площадке этим отчётом не выполняются.",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
