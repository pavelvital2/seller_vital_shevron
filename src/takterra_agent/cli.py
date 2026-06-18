from __future__ import annotations

import argparse
from decimal import Decimal
import json
from pathlib import Path
import sys

from takterra_agent.config import load_credentials
from takterra_agent.core.run_manifest import find_run, latest_run, list_runs
from takterra_agent.tasks.approvals import run_approvals_close, run_approvals_status
from takterra_agent.tasks.catalog_fetch import run_catalog_fetch
from takterra_agent.tasks.actions_apply import run_actions_apply
from takterra_agent.tasks.daily_morning_report import run_daily_morning_report
from takterra_agent.tasks.ozon_cpc_bids_apply import run_ozon_cpc_bids_apply
from takterra_agent.tasks.ozon_elastic_apply import run_ozon_elastic_apply
from takterra_agent.tasks.ozon_cpc_optimization_plan import CpcOptimizationThresholds, run_ozon_cpc_optimization_plan
from takterra_agent.tasks.ozon_elastic_plan import run_ozon_elastic_plan
from takterra_agent.tasks.reviews_questions import (
    run_reviews_questions,
    run_reviews_questions_apply,
    run_reviews_questions_prepare_approved,
)
from takterra_agent.tasks.registry import get_task_definition, list_task_definitions
from takterra_agent.tasks.status_preflight import run_status_preflight
from takterra_agent.tasks.wb_actions_discount_apply import run_wb_actions_discount_apply
from takterra_agent.tasks.wb_actions_discount_plan import run_wb_actions_discount_plan
from takterra_agent.tasks.wb_card_create_apply import run_wb_card_create_apply
from takterra_agent.tasks.wb_card_create_plan import run_wb_card_create_plan
from takterra_agent.tasks.wb_promotion_bid_plan import WbPromotionBidThresholds, run_wb_promotion_bid_plan
from takterra_agent.tasks.wb_promotion_bids_apply import run_wb_promotion_bids_apply
from takterra_agent.tasks.wb_promotion_report import run_wb_promotion_report
from takterra_agent.sessions.manager import install_systemd_units, restore_ozon_session, run_session_manager


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="takterra-agent")
    subparsers = parser.add_subparsers(dest="command", required=True)

    runs = subparsers.add_parser(
        "runs",
        help="List and inspect RunManifest entries from data/runs/index.jsonl.",
    )
    runs.add_argument(
        "action",
        choices=("list", "latest", "show"),
        help="Run manifest action.",
    )
    runs.add_argument(
        "--data-dir",
        default="data",
        help="Project data directory.",
    )
    runs.add_argument(
        "--task",
        default=None,
        help="Optional task filter, for example status-preflight.",
    )
    runs.add_argument(
        "--status",
        default=None,
        choices=("ok", "warning", "blocked", "error"),
        help="Optional manifest status filter.",
    )
    runs.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Maximum rows for runs list.",
    )
    runs.add_argument(
        "--run-id",
        default=None,
        help="Run id for runs show.",
    )

    tasks = subparsers.add_parser(
        "tasks",
        help="List and inspect task metadata from TaskRegistry.",
    )
    tasks.add_argument(
        "action",
        choices=("list", "show"),
        help="Task registry action.",
    )
    tasks.add_argument(
        "--task",
        default=None,
        help="Task name or CLI command for tasks show.",
    )
    tasks.add_argument(
        "--mode",
        choices=("read_only", "dry_run", "apply", "verify", "maintenance"),
        default=None,
        help="Optional mode filter for tasks list.",
    )
    tasks.add_argument(
        "--risk",
        choices=("none", "low", "normal", "high"),
        default=None,
        help="Optional risk filter for tasks list.",
    )
    tasks.add_argument(
        "--marketplace",
        choices=("all", "ozon", "wb"),
        default=None,
        help="Optional marketplace filter for tasks list.",
    )
    tasks.add_argument(
        "--telegram-only",
        action="store_true",
        help="Show only tasks enabled for future Telegram bot.",
    )

    approvals = subparsers.add_parser(
        "approvals",
        help="List or close pending/approved approval packages.",
    )
    approvals.add_argument(
        "action",
        choices=("status", "close"),
        help="Approval action.",
    )
    approvals.add_argument(
        "--data-dir",
        default="data",
        help="Project data directory.",
    )
    approvals.add_argument(
        "--id",
        default=None,
        help="Pending/approved package id or approved package path identity.",
    )
    approvals.add_argument(
        "--kind",
        choices=("all", "pending", "approved", "auto"),
        default="all",
        help="Target kind. Use auto only with approvals close.",
    )
    approvals.add_argument(
        "--include-closed",
        action="store_true",
        help="Include closed approval rows in status output.",
    )
    approvals.add_argument(
        "--limit",
        type=int,
        default=50,
        help="Maximum rows for approvals status.",
    )
    approvals.add_argument(
        "--closed-by",
        default="owner",
        help="Actor label stored in close marker.",
    )
    approvals.add_argument(
        "--reason",
        default="",
        help="Close reason stored in close marker.",
    )
    approvals.add_argument(
        "--force",
        action="store_true",
        help="Allow closing unapplied approved packages or already closed targets.",
    )
    approvals.add_argument(
        "--run-id",
        default=None,
        help="Optional stable run id for approvals close.",
    )

    fetch_catalog = subparsers.add_parser(
        "fetch-catalog",
        help="Fetch Ozon/WB catalogs and build read-only master catalog report.",
    )
    fetch_catalog.add_argument(
        "--data-dir",
        default="data",
        help="Project data directory.",
    )
    fetch_catalog.add_argument(
        "--run-id",
        default=None,
        help="Optional stable run id.",
    )

    status_preflight = subparsers.add_parser(
        "status-preflight",
        help="Run read-only project preflight for APIs, LK sessions and master catalog.",
    )
    status_preflight.add_argument(
        "--data-dir",
        default="data",
        help="Project data directory.",
    )
    status_preflight.add_argument(
        "--run-id",
        default=None,
        help="Optional stable run id.",
    )
    status_preflight.add_argument(
        "--skip-lk",
        action="store_true",
        help="Skip browser LK session checks and run API/catalog checks only.",
    )

    daily_report = subparsers.add_parser(
        "daily-morning-report",
        help="Build read-only daily morning management report.",
    )
    daily_report.add_argument(
        "--data-dir",
        default="data",
        help="Project data directory.",
    )
    daily_report.add_argument(
        "--run-id",
        default=None,
        help="Optional stable run id.",
    )
    daily_report.add_argument(
        "--skip-preflight-refresh",
        action="store_true",
        help="Use latest saved status-preflight instead of running a fresh one.",
    )
    daily_report.add_argument(
        "--seller-v2",
        action="store_true",
        help="Build seller-focused v2 report with read-only business metrics.",
    )
    daily_report.add_argument(
        "--seller-v3",
        action="store_true",
        help="Build seller-focused v3 report: yesterday 00:00-23:59 MSK, Ozon/WB side-by-side.",
    )

    sessions = subparsers.add_parser(
        "sessions",
        help="Manage local Ozon/WB LK session contours.",
    )
    sessions.add_argument(
        "action",
        choices=("status", "start", "stop", "restart"),
        help="Session action.",
    )
    sessions.add_argument(
        "--marketplace",
        choices=("all", "ozon", "wb"),
        default="all",
        help="Marketplace session contour.",
    )
    sessions.add_argument(
        "--data-dir",
        default="data",
        help="Project data directory.",
    )
    sessions.add_argument(
        "--run-id",
        default=None,
        help="Optional stable run id.",
    )

    restore_ozon = subparsers.add_parser(
        "restore-ozon-session",
        help="Stop Ozon keeper/watchdog, run interactive login, then start them again.",
    )
    restore_ozon.add_argument("--email", default=None, help="Ozon seller login email.")
    restore_ozon.add_argument("--expected-store", default="Vital Shevron", help="Expected Ozon store marker.")
    restore_ozon.add_argument("--max-codes", type=int, default=3, help="Max sequential OTP/SMS/email codes.")
    restore_ozon.add_argument("--dry-run", action="store_true", help="Show planned restore steps without login.")
    restore_ozon.add_argument("--data-dir", default="data", help="Project data directory.")
    restore_ozon.add_argument("--run-id", default=None, help="Optional stable run id.")

    install_systemd = subparsers.add_parser(
        "install-session-systemd",
        help="Install Vital Shevron systemd --user units for Ozon/WB session timers.",
    )
    install_systemd.add_argument(
        "--apply",
        action="store_true",
        help="Copy units and enable them. Without this flag only prints the plan.",
    )
    install_systemd.add_argument(
        "--switch",
        action="store_true",
        help="Stop current bash-loop watchdogs and start systemd keeper/timers.",
    )

    ozon_elastic = subparsers.add_parser(
        "plan-ozon-elastic",
        help="Build dry-run plan for Ozon Elastic Boosting without uploading changes.",
    )
    ozon_elastic.add_argument(
        "--data-dir",
        default="data",
        help="Project data directory.",
    )
    ozon_elastic.add_argument(
        "--run-id",
        default=None,
        help="Optional stable run id.",
    )

    apply_ozon_elastic = subparsers.add_parser(
        "apply-ozon-elastic",
        help="Apply approved Ozon Elastic Boosting dry-run after fresh preflight and drift-check.",
    )
    apply_ozon_elastic.add_argument(
        "--data-dir",
        default="data",
        help="Project data directory.",
    )
    apply_ozon_elastic.add_argument(
        "--plan-run-id",
        default=None,
        help="Approved Ozon Elastic plan run id. Defaults to the latest ozon_elastic_plan_* run.",
    )
    apply_ozon_elastic.add_argument(
        "--run-id",
        default=None,
        help="Optional stable run id.",
    )
    apply_ozon_elastic.add_argument(
        "--confirmed-by-user",
        action="store_true",
        help="Required explicit confirmation for external Ozon write operations.",
    )

    ozon_cpc_optimization = subparsers.add_parser(
        "plan-ozon-cpc-optimization",
        help="Build dry-run optimization recommendations from an Ozon CPC efficiency report.",
    )
    ozon_cpc_optimization.add_argument(
        "--data-dir",
        default="data",
        help="Project data directory.",
    )
    ozon_cpc_optimization.add_argument(
        "--run-id",
        default=None,
        help="Optional stable run id.",
    )
    ozon_cpc_optimization.add_argument(
        "--source-run-id",
        default=None,
        help="Ozon CPC efficiency run id. Defaults to latest ozon_cpc_efficiency_* run.",
    )
    ozon_cpc_optimization.add_argument(
        "--rows-csv",
        default=None,
        help="Explicit processed rows.csv from an Ozon CPC efficiency run.",
    )
    ozon_cpc_optimization.add_argument(
        "--current-bids-json",
        default=None,
        help="Optional current_bids.json snapshot from Ozon Performance API /v2/products.",
    )
    ozon_cpc_optimization.add_argument("--zero-orders-spend", default="50")
    ozon_cpc_optimization.add_argument("--high-drr-percent", default="12")
    ozon_cpc_optimization.add_argument("--high-drr-min-spend", default="100")
    ozon_cpc_optimization.add_argument("--scale-min-orders", type=int, default=8)
    ozon_cpc_optimization.add_argument("--scale-max-drr-percent", default="5")
    ozon_cpc_optimization.add_argument("--card-review-reduce-percent", default="30")
    ozon_cpc_optimization.add_argument("--max-reduce-percent", default="50")
    ozon_cpc_optimization.add_argument("--scale-low-drr-percent", default="20")
    ozon_cpc_optimization.add_argument("--scale-mid-drr-percent", default="15")
    ozon_cpc_optimization.add_argument("--scale-high-drr-percent", default="10")

    apply_ozon_cpc_bids = subparsers.add_parser(
        "apply-ozon-cpc-bids",
        help="Apply approved Ozon CPC bid changes after fresh preflight, current-bid snapshot and drift-check.",
    )
    apply_ozon_cpc_bids.add_argument(
        "--data-dir",
        default="data",
        help="Project data directory.",
    )
    apply_ozon_cpc_bids.add_argument(
        "--plan-run-id",
        default=None,
        help="Approved Ozon CPC optimization plan run id. Defaults to the latest ozon_cpc_optimization_plan_* run.",
    )
    apply_ozon_cpc_bids.add_argument(
        "--run-id",
        default=None,
        help="Optional stable run id.",
    )
    apply_ozon_cpc_bids.add_argument(
        "--confirmed-by-user",
        action="store_true",
        help="Required explicit confirmation for external Ozon write operations.",
    )
    apply_ozon_cpc_bids.add_argument(
        "--min-bid",
        default="1.00",
        help="Safe minimum bid in rubles. Lower target bids are skipped, not clamped.",
    )

    wb_actions = subparsers.add_parser(
        "plan-wb-actions-discounts",
        help="Download WB active actions and build discount dry-run plan.",
    )
    wb_actions.add_argument(
        "--data-dir",
        default="data",
        help="Project data directory.",
    )
    wb_actions.add_argument(
        "--run-id",
        default=None,
        help="Optional stable run id.",
    )
    wb_actions.add_argument(
        "--scheme",
        default="70-55-55",
        help="Discount scheme THRESHOLD-NO_PROMO_FALLBACK-OVER_THRESHOLD_FALLBACK.",
    )
    wb_actions.add_argument(
        "--actions-dir",
        default=None,
        help="Existing WB actions snapshot directory with cabinet-actions-snapshot.json and excel/.",
    )
    wb_actions.add_argument(
        "--prices-json",
        default=None,
        help="Existing current prices JSON from WB LK snapshot.",
    )

    apply_wb_actions = subparsers.add_parser(
        "apply-wb-actions-discounts",
        help="Apply approved WB actions discount dry-run after fresh preflight, dry-run and drift-check.",
    )
    apply_wb_actions.add_argument(
        "--data-dir",
        default="data",
        help="Project data directory.",
    )
    apply_wb_actions.add_argument(
        "--plan-run-id",
        default=None,
        help="Approved WB actions discount plan run id. Defaults to the latest wb_actions_discount_plan_* run.",
    )
    apply_wb_actions.add_argument(
        "--run-id",
        default=None,
        help="Optional stable run id.",
    )
    apply_wb_actions.add_argument(
        "--confirmed-by-user",
        action="store_true",
        help="Required explicit confirmation for external WB write operations.",
    )

    wb_promotion = subparsers.add_parser(
        "wb-promotion-report",
        help="Build read-only WB Promotion campaigns and statistics report.",
    )
    wb_promotion.add_argument(
        "--data-dir",
        default="data",
        help="Project data directory.",
    )
    wb_promotion.add_argument(
        "--run-id",
        default=None,
        help="Optional stable run id.",
    )
    wb_promotion.add_argument(
        "--date-from",
        default=None,
        help="Period start date YYYY-MM-DD. Defaults to last 14 days.",
    )
    wb_promotion.add_argument(
        "--date-to",
        default=None,
        help="Period end date YYYY-MM-DD. Defaults to today.",
    )
    wb_promotion.add_argument(
        "--payment-type",
        choices=("cpc", "cpm"),
        default=None,
        help="Optional campaign payment type filter.",
    )

    wb_promotion_bid_plan = subparsers.add_parser(
        "plan-wb-promotion-bids",
        help="Build dry-run WB Promotion bid optimization recommendations from a WB promotion report.",
    )
    wb_promotion_bid_plan.add_argument(
        "--data-dir",
        default="data",
        help="Project data directory.",
    )
    wb_promotion_bid_plan.add_argument(
        "--run-id",
        default=None,
        help="Optional stable run id.",
    )
    wb_promotion_bid_plan.add_argument(
        "--source-run-id",
        default=None,
        help="WB promotion report run id. Defaults to latest wb_promotion_report_* run.",
    )
    wb_promotion_bid_plan.add_argument(
        "--products-csv",
        default=None,
        help="Explicit wb_promotion_products.csv from a WB promotion report.",
    )
    wb_promotion_bid_plan.add_argument(
        "--campaigns-json",
        default=None,
        help="Explicit raw/campaigns.json from a WB promotion report.",
    )
    wb_promotion_bid_plan.add_argument("--include-inactive", action="store_true")
    wb_promotion_bid_plan.add_argument("--zero-orders-spend", default="10")
    wb_promotion_bid_plan.add_argument("--high-drr-percent", default="5")
    wb_promotion_bid_plan.add_argument("--high-drr-min-spend", default="30")
    wb_promotion_bid_plan.add_argument("--scale-min-orders", type=int, default=3)
    wb_promotion_bid_plan.add_argument("--scale-max-drr-percent", default="1")
    wb_promotion_bid_plan.add_argument("--card-review-reduce-percent", default="20")
    wb_promotion_bid_plan.add_argument("--zero-no-cart-reduce-percent", default="30")
    wb_promotion_bid_plan.add_argument("--max-reduce-percent", default="30")
    wb_promotion_bid_plan.add_argument("--scale-low-drr-percent", default="20")
    wb_promotion_bid_plan.add_argument("--scale-mid-drr-percent", default="15")
    wb_promotion_bid_plan.add_argument("--scale-high-drr-percent", default="10")
    wb_promotion_bid_plan.add_argument("--min-bid", default="1.00")

    apply_wb_promotion_bids = subparsers.add_parser(
        "apply-wb-promotion-bids",
        help="Apply approved WB Promotion bid changes after fresh report, fresh dry-run, drift-check and verify.",
    )
    apply_wb_promotion_bids.add_argument(
        "--data-dir",
        default="data",
        help="Project data directory.",
    )
    apply_wb_promotion_bids.add_argument(
        "--plan-run-id",
        default=None,
        help="Approved WB promotion bid plan run id. Defaults to latest wb_promotion_bid_plan_* run.",
    )
    apply_wb_promotion_bids.add_argument(
        "--run-id",
        default=None,
        help="Optional stable run id.",
    )
    apply_wb_promotion_bids.add_argument(
        "--confirmed-by-user",
        action="store_true",
        help="Required explicit confirmation for external WB write operations.",
    )
    apply_wb_promotion_bids.add_argument(
        "--actions",
        default="scale_candidate",
        help="Comma-separated recommended_action values to apply. Defaults to scale_candidate.",
    )
    apply_wb_promotion_bids.add_argument(
        "--wait-seconds",
        type=int,
        default=45,
        help="Seconds to wait before verify because WB bid changes are asynchronous.",
    )

    apply_actions = subparsers.add_parser(
        "apply-actions",
        help="Legacy combined apply for confirmed Ozon Elastic and WB actions after fresh preflight/dry-run/drift-check.",
    )
    apply_actions.add_argument(
        "--data-dir",
        default="data",
        help="Project data directory.",
    )
    apply_actions.add_argument(
        "--run-id",
        default=None,
        help="Optional stable run id.",
    )
    apply_actions.add_argument(
        "--pending-id",
        default="actions_apply_pending_20260610T232837",
        help="Pending package id to use for drift-check.",
    )
    apply_actions.add_argument(
        "--confirmed-by-user",
        action="store_true",
        help="Required explicit confirmation for external marketplace write operations.",
    )

    plan_wb_cards = subparsers.add_parser(
        "plan-wb-card-create",
        help="Build dry-run plan for creating Ozon-only product cards in WB.",
    )
    plan_wb_cards.add_argument(
        "--data-dir",
        default="data",
        help="Project data directory.",
    )
    plan_wb_cards.add_argument(
        "--run-id",
        default=None,
        help="Optional stable run id.",
    )

    apply_wb_cards = subparsers.add_parser(
        "apply-wb-card-create",
        help="Create planned Ozon-only product cards in WB and verify the result.",
    )
    apply_wb_cards.add_argument(
        "--data-dir",
        default="data",
        help="Project data directory.",
    )
    apply_wb_cards.add_argument(
        "--plan-run-id",
        default=None,
        help="Plan run id. Defaults to the latest wb_card_create_plan_* run.",
    )
    apply_wb_cards.add_argument(
        "--run-id",
        default=None,
        help="Optional stable run id.",
    )
    apply_wb_cards.add_argument(
        "--confirmed-by-user",
        action="store_true",
        help="Required explicit confirmation for external WB write operations.",
    )
    apply_wb_cards.add_argument(
        "--allow-manual-review",
        action="store_true",
        help="Allow applying a plan that still has manual-review notes.",
    )
    apply_wb_cards.add_argument(
        "--wait-seconds",
        type=int,
        default=600,
        help="How long to wait for WB cards to become visible before media upload.",
    )
    apply_wb_cards.add_argument(
        "--poll-interval",
        type=int,
        default=30,
        help="Polling interval in seconds while waiting for WB nmID.",
    )

    reviews_questions = subparsers.add_parser(
        "reviews-questions",
        help="Build read-only reviews/questions snapshot and owner-review reply drafts.",
    )
    reviews_questions.add_argument(
        "--data-dir",
        default="data",
        help="Project data directory.",
    )
    reviews_questions.add_argument(
        "--run-id",
        default=None,
        help="Optional stable run id.",
    )
    reviews_questions.add_argument(
        "--marketplace",
        choices=("all", "ozon", "wb"),
        default="all",
        help="Marketplace to process.",
    )
    reviews_questions.add_argument(
        "--limit",
        type=int,
        default=100,
        help="Maximum number of reviews/questions per source.",
    )

    apply_reviews_questions = subparsers.add_parser(
        "apply-reviews-questions",
        help="Apply approved reviews/questions replies and verify the result.",
    )
    apply_reviews_questions.add_argument(
        "--approved-path",
        required=True,
        help="Path to approved reviews/questions apply JSON.",
    )
    apply_reviews_questions.add_argument(
        "--data-dir",
        default="data",
        help="Project data directory.",
    )
    apply_reviews_questions.add_argument(
        "--run-id",
        default=None,
        help="Optional stable run id.",
    )
    apply_reviews_questions.add_argument(
        "--confirmed-by-user",
        action="store_true",
        help="Required explicit confirmation for marketplace write operations.",
    )

    prepare_reviews_questions_approved = subparsers.add_parser(
        "prepare-reviews-questions-approved",
        help="Create approved reviews/questions package from a pending dry-run package.",
    )
    prepare_reviews_questions_approved.add_argument(
        "--source-pending",
        required=True,
        help="Pending package id from data/pending/<id>.",
    )
    prepare_reviews_questions_approved.add_argument(
        "--mode",
        choices=("all", "replies-only", "mark-viewed-only"),
        default="all",
        help="Which approved actions to include.",
    )
    prepare_reviews_questions_approved.add_argument(
        "--approved-id",
        default=None,
        help="Optional approved package id. Defaults to <source-pending>_approved.",
    )
    prepare_reviews_questions_approved.add_argument(
        "--approved-by",
        default="owner",
        help="Approval actor label stored in package metadata.",
    )
    prepare_reviews_questions_approved.add_argument(
        "--overwrite",
        action="store_true",
        help="Allow replacing an existing approved package directory.",
    )
    prepare_reviews_questions_approved.add_argument(
        "--data-dir",
        default="data",
        help="Project data directory.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "runs":
        data_dir = Path(args.data_dir)
        if args.action == "list":
            result = {
                "rows": list_runs(data_dir=data_dir, task=args.task, status=args.status, limit=args.limit),
                "artifacts": {"index": str(data_dir / "runs" / "index.jsonl")},
            }
        elif args.action == "latest":
            result = {
                "run": latest_run(data_dir=data_dir, task=args.task, status=args.status),
                "artifacts": {"index": str(data_dir / "runs" / "index.jsonl")},
            }
        else:
            if not args.run_id:
                parser.error("runs show requires --run-id")
            result = {
                "run": find_run(data_dir=data_dir, run_id=args.run_id),
                "artifacts": {"index": str(data_dir / "runs" / "index.jsonl")},
            }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("run") is not None or result.get("rows") is not None else 2

    if args.command == "tasks":
        if args.action == "list":
            result = {
                "rows": list_task_definitions(
                    mode=args.mode,
                    risk=args.risk,
                    marketplace=args.marketplace,
                    telegram_only=args.telegram_only,
                )
            }
        else:
            if not args.task:
                parser.error("tasks show requires --task")
            result = {"task": get_task_definition(args.task)}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    if args.command == "approvals":
        data_dir = Path(args.data_dir)
        if args.action == "status":
            if args.kind == "auto":
                parser.error("approvals status does not support --kind auto")
            result = run_approvals_status(
                data_dir=data_dir,
                target_id=args.id,
                kind=args.kind,
                include_closed=args.include_closed,
                limit=args.limit,
            )
        else:
            if not args.id:
                parser.error("approvals close requires --id")
            close_kind = "auto" if args.kind == "all" else args.kind
            result = run_approvals_close(
                data_dir=data_dir,
                target_id=args.id,
                kind=close_kind,
                closed_by=args.closed_by,
                reason=args.reason,
                force=args.force,
                run_id=args.run_id,
            )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["overall_status"] in {"ok", "warning"} else 2

    if args.command == "fetch-catalog":
        result = run_catalog_fetch(
            credentials=load_credentials(),
            data_dir=Path(args.data_dir),
            run_id=args.run_id,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if not result["errors"] else 2

    if args.command == "plan-wb-card-create":
        result = run_wb_card_create_plan(
            credentials=load_credentials(),
            data_dir=Path(args.data_dir),
            run_id=args.run_id,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    if args.command == "status-preflight":
        result = run_status_preflight(
            credentials=load_credentials(),
            data_dir=Path(args.data_dir),
            run_id=args.run_id,
            include_lk=not args.skip_lk,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["overall_status"] in {"ok", "warning"} else 2

    if args.command == "daily-morning-report":
        result = run_daily_morning_report(
            credentials=load_credentials(),
            data_dir=Path(args.data_dir),
            run_id=args.run_id,
            refresh_preflight=not args.skip_preflight_refresh,
            seller_v2=args.seller_v2,
            seller_v3=args.seller_v3,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["overall_status"] in {"ok", "warning"} else 2

    if args.command == "sessions":
        result = run_session_manager(
            action=args.action,
            marketplace=args.marketplace,
            data_dir=Path(args.data_dir),
            run_id=args.run_id,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["overall_status"] in {"ok", "warning"} else 2

    if args.command == "restore-ozon-session":
        result = restore_ozon_session(
            email=args.email,
            expected_store=args.expected_store,
            max_codes=args.max_codes,
            dry_run=args.dry_run,
            data_dir=Path(args.data_dir),
            run_id=args.run_id,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["overall_status"] in {"ok", "warning"} else 2

    if args.command == "install-session-systemd":
        result = install_systemd_units(switch=args.switch, dry_run=not args.apply)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["overall_status"] in {"ok", "warning"} else 2

    if args.command == "plan-ozon-elastic":
        result = run_ozon_elastic_plan(
            credentials=load_credentials(),
            data_dir=Path(args.data_dir),
            run_id=args.run_id,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    if args.command == "apply-ozon-elastic":
        result = run_ozon_elastic_apply(
            credentials=load_credentials(),
            data_dir=Path(args.data_dir),
            plan_run_id=args.plan_run_id,
            run_id=args.run_id,
            confirmed_by_user=args.confirmed_by_user,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["overall_status"] in {"ok", "warning"} else 2

    if args.command == "plan-ozon-cpc-optimization":
        result = run_ozon_cpc_optimization_plan(
            data_dir=Path(args.data_dir),
            run_id=args.run_id,
            source_run_id=args.source_run_id,
            rows_csv=Path(args.rows_csv) if args.rows_csv else None,
            current_bids_json=Path(args.current_bids_json) if args.current_bids_json else None,
            thresholds=CpcOptimizationThresholds(
                zero_orders_spend=args.zero_orders_spend,
                high_drr_percent=args.high_drr_percent,
                high_drr_min_spend=args.high_drr_min_spend,
                scale_min_orders=args.scale_min_orders,
                scale_max_drr_percent=args.scale_max_drr_percent,
                card_review_reduce_percent=args.card_review_reduce_percent,
                max_reduce_percent=args.max_reduce_percent,
                scale_low_drr_percent=args.scale_low_drr_percent,
                scale_mid_drr_percent=args.scale_mid_drr_percent,
                scale_high_drr_percent=args.scale_high_drr_percent,
            ),
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    if args.command == "apply-ozon-cpc-bids":
        result = run_ozon_cpc_bids_apply(
            credentials=load_credentials(),
            data_dir=Path(args.data_dir),
            plan_run_id=args.plan_run_id,
            run_id=args.run_id,
            confirmed_by_user=args.confirmed_by_user,
            min_bid=Decimal(args.min_bid),
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["overall_status"] in {"ok", "warning"} else 2

    if args.command == "plan-wb-actions-discounts":
        result = run_wb_actions_discount_plan(
            credentials=load_credentials(),
            data_dir=Path(args.data_dir),
            run_id=args.run_id,
            scheme_text=args.scheme,
            actions_dir=Path(args.actions_dir) if args.actions_dir else None,
            prices_json=Path(args.prices_json) if args.prices_json else None,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    if args.command == "apply-wb-actions-discounts":
        result = run_wb_actions_discount_apply(
            credentials=load_credentials(),
            data_dir=Path(args.data_dir),
            plan_run_id=args.plan_run_id,
            run_id=args.run_id,
            confirmed_by_user=args.confirmed_by_user,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["overall_status"] in {"ok", "warning"} else 2

    if args.command == "wb-promotion-report":
        result = run_wb_promotion_report(
            credentials=load_credentials(),
            data_dir=Path(args.data_dir),
            run_id=args.run_id,
            date_from=args.date_from,
            date_to=args.date_to,
            payment_type=args.payment_type,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    if args.command == "plan-wb-promotion-bids":
        result = run_wb_promotion_bid_plan(
            data_dir=Path(args.data_dir),
            run_id=args.run_id,
            source_run_id=args.source_run_id,
            products_csv=Path(args.products_csv) if args.products_csv else None,
            campaigns_json=Path(args.campaigns_json) if args.campaigns_json else None,
            active_cpc_only=not args.include_inactive,
            thresholds=WbPromotionBidThresholds(
                zero_orders_spend=args.zero_orders_spend,
                high_drr_percent=args.high_drr_percent,
                high_drr_min_spend=args.high_drr_min_spend,
                scale_min_orders=args.scale_min_orders,
                scale_max_drr_percent=args.scale_max_drr_percent,
                card_review_reduce_percent=args.card_review_reduce_percent,
                zero_no_cart_reduce_percent=args.zero_no_cart_reduce_percent,
                max_reduce_percent=args.max_reduce_percent,
                scale_low_drr_percent=args.scale_low_drr_percent,
                scale_mid_drr_percent=args.scale_mid_drr_percent,
                scale_high_drr_percent=args.scale_high_drr_percent,
                min_bid=args.min_bid,
            ),
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    if args.command == "apply-wb-promotion-bids":
        result = run_wb_promotion_bids_apply(
            credentials=load_credentials(),
            data_dir=Path(args.data_dir),
            plan_run_id=args.plan_run_id,
            run_id=args.run_id,
            confirmed_by_user=args.confirmed_by_user,
            allowed_actions={item.strip() for item in args.actions.split(",") if item.strip()},
            wait_seconds=args.wait_seconds,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["overall_status"] in {"ok", "warning"} else 2

    if args.command == "apply-actions":
        result = run_actions_apply(
            credentials=load_credentials(),
            data_dir=Path(args.data_dir),
            run_id=args.run_id,
            pending_id=args.pending_id,
            confirmed_by_user=args.confirmed_by_user,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["overall_status"] in {"ok", "warning"} else 2

    if args.command == "apply-wb-card-create":
        result = run_wb_card_create_apply(
            credentials=load_credentials(),
            data_dir=Path(args.data_dir),
            plan_run_id=args.plan_run_id,
            run_id=args.run_id,
            confirmed_by_user=args.confirmed_by_user,
            allow_manual_review=args.allow_manual_review,
            wait_seconds=args.wait_seconds,
            poll_interval=args.poll_interval,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    if args.command == "reviews-questions":
        result = run_reviews_questions(
            credentials=load_credentials(),
            data_dir=Path(args.data_dir),
            run_id=args.run_id,
            marketplace=args.marketplace,
            limit=args.limit,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["overall_status"] in {"ok", "warning"} else 2

    if args.command == "apply-reviews-questions":
        result = run_reviews_questions_apply(
            credentials=load_credentials(),
            approved_path=Path(args.approved_path),
            data_dir=Path(args.data_dir),
            run_id=args.run_id,
            confirmed_by_user=args.confirmed_by_user,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["overall_status"] in {"ok", "warning"} else 2

    if args.command == "prepare-reviews-questions-approved":
        result = run_reviews_questions_prepare_approved(
            data_dir=Path(args.data_dir),
            source_pending=args.source_pending,
            mode=args.mode,
            approved_id=args.approved_id,
            approved_by=args.approved_by,
            overwrite=args.overwrite,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    parser.error(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
