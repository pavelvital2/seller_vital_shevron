from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import time
from typing import Any, Callable, TypeVar

from seller_agent.config import AppCredentials
from seller_agent.core.run_manifest import write_summary_run_manifest
from seller_agent.http import ApiError
from seller_agent.marketplaces.ozon.adapter import OzonSellerAdapter
from seller_agent.reports.writer import ensure_dir, write_json
from seller_agent.tasks.liquidation_daily_control import _decimal
from seller_agent.tasks.marketplace_period_report import run_marketplace_period_report


DEACTIVATED_AT_UTC = datetime.fromisoformat("2026-07-29T13:56:22.594+00:00")
RECONNECT_THRESHOLD_PCT = 21.19
RATE_LIMIT_RETRY_DELAYS_SECONDS = (5.0, 10.0, 20.0)
SELLER_API_PHASE_COOLDOWN_SECONDS = 1.0


T = TypeVar("T")


def retry_ozon_read_after_rate_limit(
    operation: Callable[[], T],
    *,
    sleep: Callable[[float], None] = time.sleep,
    delays: tuple[float, ...] = RATE_LIMIT_RETRY_DELAYS_SECONDS,
) -> tuple[T, int]:
    """Retry a read-only Ozon operation only after an explicit HTTP 429."""
    retries_used = 0
    while True:
        try:
            return operation(), retries_used
        except ApiError as exc:
            if exc.status != 429 or retries_used >= len(delays):
                raise
            sleep(delays[retries_used])
            retries_used += 1


def stars_fee_breakdown(operations: list[dict[str, Any]]) -> dict[str, Any]:
    rows = 0
    amount = 0.0
    post_deactivation_rows = 0
    post_deactivation_amount = 0.0
    missing_order_date = 0
    for row in operations:
        if str(row.get("operation_type") or "") != "StarsMembership":
            continue
        rows += 1
        fee = float(abs(_decimal(row.get("amount"))))
        amount += fee
        posting = row.get("posting") if isinstance(row.get("posting"), dict) else {}
        raw_order_date = str(posting.get("order_date") or "")
        try:
            order_date = datetime.fromisoformat(raw_order_date.replace("Z", "+00:00")).astimezone(timezone.utc)
        except ValueError:
            missing_order_date += 1
            continue
        if order_date >= DEACTIVATED_AT_UTC:
            post_deactivation_rows += 1
            post_deactivation_amount += fee
    return {
        "rows": rows,
        "amount": round(amount, 2),
        "post_deactivation_order_rows": post_deactivation_rows,
        "post_deactivation_order_amount": round(post_deactivation_amount, 2),
        "missing_order_date": missing_order_date,
    }


def _change_pct(current: float, baseline: float) -> float | None:
    if not baseline:
        return None
    return round((current - baseline) / baseline * 100, 2)


def run_ozon_stars_control(
    *,
    credentials: AppCredentials,
    data_dir: Path = Path("data"),
    window_days: int = 3,
    date_to: str | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    if window_days not in {3, 7, 14}:
        raise ValueError("window_days must be 3, 7 or 14")
    if not credentials.ozon_seller:
        raise RuntimeError("Ozon Seller credentials are required")
    completed_to = date.fromisoformat(date_to) if date_to else date.today() - timedelta(days=1)
    current_from = date(2026, 7, 30)
    expected_to = current_from + timedelta(days=window_days - 1)
    if completed_to < expected_to:
        raise RuntimeError(f"{window_days}-day Stars control is not due before {expected_to.isoformat()}")
    current_to = expected_to
    baseline_to = date(2026, 7, 28) if window_days == 14 else current_to - timedelta(days=7)
    baseline_from = baseline_to - timedelta(days=window_days - 1)
    started = datetime.now().astimezone()
    run_id = run_id or f"ozon_stars_{window_days}d_control_{started:%Y%m%dT%H%M%S}"
    run_dir = ensure_dir(data_dir / "runs" / started.strftime("%Y-%m-%d") / run_id)

    current, current_retries = retry_ozon_read_after_rate_limit(
        lambda: run_marketplace_period_report(
            credentials=credentials,
            data_dir=data_dir,
            marketplace="ozon",
            report_type="financial",
            date_from=current_from.isoformat(),
            date_to=current_to.isoformat(),
            run_id=f"{run_id}_current",
        )
    )
    time.sleep(SELLER_API_PHASE_COOLDOWN_SECONDS)
    baseline, baseline_retries = retry_ozon_read_after_rate_limit(
        lambda: run_marketplace_period_report(
            credentials=credentials,
            data_dir=data_dir,
            marketplace="ozon",
            report_type="financial",
            date_from=baseline_from.isoformat(),
            date_to=baseline_to.isoformat(),
            run_id=f"{run_id}_baseline",
        )
    )
    time.sleep(SELLER_API_PHASE_COOLDOWN_SECONDS)
    operations, finance_retries = retry_ozon_read_after_rate_limit(
        lambda: OzonSellerAdapter(credentials.ozon_seller).fetch_finance_transactions(
            date_from=f"{current_from.isoformat()}T00:00:00.000Z",
            date_to=f"{current_to.isoformat()}T23:59:59.999Z",
        )
    )
    fees = stars_fee_breakdown(operations)
    current_metrics = current.get("metrics") or {}
    baseline_metrics = baseline.get("metrics") or {}
    order_change = _change_pct(float(current_metrics.get("buyout_units") or 0), float(baseline_metrics.get("buyout_units") or 0))
    gross_change = _change_pct(float(current_metrics.get("gross") or 0), float(baseline_metrics.get("gross") or 0))
    reconnect = order_change is not None and order_change < -RECONNECT_THRESHOLD_PCT
    summary = {
        "run_id": run_id,
        "started_at": started.isoformat(timespec="seconds"),
        "overall_status": "warning" if fees["post_deactivation_order_rows"] or reconnect else "ok",
        "mode": "read_only",
        "window_days": window_days,
        "current_period": {"date_from": current_from.isoformat(), "date_to": current_to.isoformat()},
        "baseline_period": {"date_from": baseline_from.isoformat(), "date_to": baseline_to.isoformat()},
        "current_metrics": current_metrics,
        "baseline_metrics": baseline_metrics,
        "buyout_units_change_pct": order_change,
        "gross_change_pct": gross_change,
        "stars_fees": fees,
        "reconnect_review_required": reconnect,
        "reconnect_threshold_pct": RECONNECT_THRESHOLD_PCT,
        "rate_limit_retries": {
            "current_report": current_retries,
            "baseline_report": baseline_retries,
            "finance_check": finance_retries,
            "total": current_retries + baseline_retries + finance_retries,
        },
        "decision": "owner_review_reconnect" if reconnect else "keep_disabled",
        "apply_performed": False,
        "artifacts": {"report": str(run_dir / "report.md"), "summary": str(run_dir / "summary.json"), "raw_finance": str(run_dir / "raw_finance.json"), "current_report": str(current.get("artifacts", {}).get("report", "")), "baseline_report": str(baseline.get("artifacts", {}).get("report", ""))},
    }
    write_json(run_dir / "raw_finance.json", operations)
    (run_dir / "report.md").write_text("\n".join([f"# Ozon Stars: контроль {window_days} дней", "", f"Текущий период: `{current_from} - {current_to}`; baseline: `{baseline_from} - {baseline_to}`.", f"Изменение выкупов: **{order_change if order_change is not None else 'н/д'}%**; оборота: **{gross_change if gross_change is not None else 'н/д'}%**.", f"StarsMembership по заказам после отключения: **{fees['post_deactivation_order_rows']} строк / {fees['post_deactivation_order_amount']:.2f} руб.**", f"Решение: **{summary['decision']}**. Повторное подключение автоматически не выполнялось."]), encoding="utf-8")
    write_json(run_dir / "summary.json", summary)
    summary["artifacts"].update(write_summary_run_manifest(data_dir=data_dir, run_dir=run_dir, summary=summary, task="ozon-stars-control", mode="read_only", risk="low", marketplaces=["ozon"], inputs={"window_days": window_days, "date_to": completed_to.isoformat()}, lifecycle_status="closed", closed=True))
    write_json(run_dir / "summary.json", summary)
    return summary
