from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from takterra_agent.config import load_credentials
from takterra_agent.tasks.catalog_fetch import run_catalog_fetch
from takterra_agent.tasks.actions_apply import run_actions_apply
from takterra_agent.tasks.daily_morning_report import run_daily_morning_report
from takterra_agent.tasks.ozon_elastic_plan import run_ozon_elastic_plan
from takterra_agent.tasks.reviews_questions import run_reviews_questions, run_reviews_questions_apply
from takterra_agent.tasks.status_preflight import run_status_preflight
from takterra_agent.tasks.wb_actions_discount_plan import run_wb_actions_discount_plan
from takterra_agent.tasks.wb_card_create_apply import run_wb_card_create_apply
from takterra_agent.tasks.wb_card_create_plan import run_wb_card_create_plan
from takterra_agent.sessions.manager import install_systemd_units, restore_ozon_session, run_session_manager


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="takterra-agent")
    subparsers = parser.add_subparsers(dest="command", required=True)

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
        default="65-50-50",
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

    apply_actions = subparsers.add_parser(
        "apply-actions",
        help="Apply confirmed Ozon Elastic and WB 65-50-50 actions after fresh preflight/dry-run/drift-check.",
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
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

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

    parser.error(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
