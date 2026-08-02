from __future__ import annotations

import argparse
from dataclasses import asdict
from decimal import Decimal
import json
import os
from pathlib import Path
import sys

from seller_agent.bot.dispatcher import dispatch_message
from seller_agent.bot.telegram_runner import (
    DEFAULT_LOCK_FILE,
    DEFAULT_STATE_FILE,
    load_telegram_bot_token,
    poll_loop,
    poll_once,
    send_preview_command,
)
from seller_agent.bot.job_notifier import notify_telegram_job_result
from seller_agent.config import load_credentials
from seller_agent.core.job_runner import JobRunner
from seller_agent.core.job_service import JobService
from seller_agent.core.job_store import DEFAULT_RUNTIME_DB, TERMINAL_JOB_STATUSES, JobStore
from seller_agent.core.job_worker import JobWorker
from seller_agent.core.run_manifest import find_run, latest_run, list_runs
from seller_agent.tasks.approvals import run_approvals_close, run_approvals_status
from seller_agent.tasks.approved_cards_apply import run_plan_approved_cards
from seller_agent.tasks.card_content_audit_backlog import run_card_content_audit_backlog
from seller_agent.tasks.card_content_audit_packages import run_card_content_audit_packages
from seller_agent.tasks.card_content_parameter_inventory import run_card_content_parameter_inventory
from seller_agent.tasks.card_content_signals import run_collect_card_signals
from seller_agent.tasks.card_content_snapshot import run_card_content_snapshot
from seller_agent.tasks.card_content_update import (
    run_card_content_update_plan,
    run_card_content_update_verify,
)
from seller_agent.tasks.card_passport_promotion import run_promote_approved_card_passport
from seller_agent.tasks.catalog_fetch import run_catalog_fetch
from seller_agent.tasks.catalog_content_master import run_catalog_content_master
from seller_agent.tasks.catalog_internal_sku_plan import run_internal_sku_plan
from seller_agent.tasks.catalog_unified import run_build_unified_catalog
from seller_agent.tasks.daily_morning_report import run_daily_morning_report
from seller_agent.tasks.marketplace_period_report import run_marketplace_period_report
from seller_agent.tasks.ozon_card_create_plan import run_ozon_card_create_plan
from seller_agent.tasks.ozon_actions_optimizer_plan import run_ozon_actions_optimizer_plan
from seller_agent.tasks.ozon_cpc_optimization_plan import CpcOptimizationThresholds, run_ozon_cpc_optimization_plan
from seller_agent.tasks.ozon_elastic_plan import run_ozon_elastic_plan
from seller_agent.tasks.inbox_workflow import (
    run_ozon_inbox_triage,
    run_wb_inbox_triage,
)
from seller_agent.tasks.ozon_product_remove import run_ozon_product_remove_plan
from seller_agent.tasks.ozon_pricing_margin import run_ozon_pricing_margin
from seller_agent.tasks.ozon_production_work_plan import run_ozon_production_work_plan
from seller_agent.tasks.ozon_stock_supply_monitor import run_ozon_stock_supply_monitor
from seller_agent.tasks.pricing_status import run_pricing_status
from seller_agent.tasks.product_passport_design import run_product_passport_design
from seller_agent.tasks.reviews_questions import (
    run_reviews_questions,
    run_reviews_questions_prepare_approved,
)
from seller_agent.tasks.registry import default_task_registry, get_task_definition, list_task_definitions
from seller_agent.tasks.seo_query_pack import build_seo_query_pack
from seller_agent.tasks.seller_sku_update import run_seller_sku_update_plan
from seller_agent.tasks.ozon_partial_approved import (
    run_ozon_partial_approved_diagnose,
)
from seller_agent.tasks.status_preflight import run_status_preflight
from seller_agent.tasks.supply_workbooks_plan import run_supply_workbooks_plan
from seller_agent.tasks.telegram_report_sender import run_send_telegram_report
from seller_agent.tasks.wb_actions_discount_plan import run_wb_actions_discount_plan
from seller_agent.tasks.wb_card_create_plan import run_wb_card_create_plan
from seller_agent.tasks.wb_parser_warehouse_analytics import run_wb_parser_warehouse_analytics
from seller_agent.tasks.wb_production_work_plan import run_wb_production_work_plan
from seller_agent.tasks.wb_promotion_bid_parser_enriched_plan import run_wb_promotion_bid_parser_enriched_plan
from seller_agent.tasks.wb_promotion_bid_plan import WbPromotionBidThresholds, run_wb_promotion_bid_plan
from seller_agent.tasks.wb_promotion_report import run_wb_promotion_report
from seller_agent.tasks.wb_stock_supply_monitor import run_wb_stock_supply_monitor
from seller_agent.sessions.manager import install_systemd_units, restore_ozon_session, run_session_manager


# Kept as a non-callable compatibility seam for regression tests and external
# monkeypatches; production CLI routing never invokes a task-level apply here.
run_actions_apply: object | None = None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="seller-agent")
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

    jobs = subparsers.add_parser(
        "jobs",
        help="Submit, run and inspect SQLite runtime jobs.",
    )
    jobs.add_argument(
        "action",
        choices=("list", "show", "submit", "run", "run-next", "recover-approvals", "cancel"),
        help="Runtime job action.",
    )
    jobs.add_argument("--runtime-db", default="runtime/runtime.db", help="SQLite runtime DB path.")
    jobs.add_argument("--data-dir", default="data", help="Project data directory.")
    jobs.add_argument("--job-id", default=None, help="Job id for show/run/cancel.")
    jobs.add_argument("--task", default=None, help="Task id for submit.")
    jobs.add_argument("--actor", default="cli", help="Actor label stored in job request.")
    jobs.add_argument("--source", default="cli", help="Request source stored in task_requests.")
    jobs.add_argument("--params-json", default="{}", help="JSON object with task parameters.")
    jobs.add_argument("--status", default=None, help="Optional status filter for jobs list.")
    jobs.add_argument("--limit", type=int, default=20, help="Maximum rows for jobs list.")
    jobs.add_argument("--reason", default="cancelled_by_cli", help="Cancel reason.")
    jobs.add_argument(
        "--run-verify",
        action="store_true",
        help="For jobs recover-approvals: run safe verify jobs immediately after queueing.",
    )

    tasks = subparsers.add_parser(
        "tasks",
        help="List and inspect task metadata from TaskRegistry.",
    )
    tasks.add_argument(
        "action",
        choices=("list", "show", "policy"),
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

    bot = subparsers.add_parser(
        "bot",
        help="Preview and run read-only Telegram MVP command responses.",
    )
    bot.add_argument(
        "action",
        choices=("preview", "send-preview", "poll-once", "poll-loop", "run-job-next", "run-job-loop"),
        help="Bot action.",
    )
    bot.add_argument(
        "--message",
        default="/help",
        help="Telegram command text, for example /status.",
    )
    bot.add_argument(
        "--data-dir",
        default="data",
        help="Project data directory.",
    )
    bot.add_argument(
        "--json",
        action="store_true",
        help="Print full JSON result instead of Telegram text.",
    )
    bot.add_argument(
        "--token-file",
        default=None,
        help="External Telegram bot token file. Do not store it in the repository.",
    )
    bot.add_argument(
        "--chat-id",
        type=int,
        default=None,
        help="Telegram chat id for bot send-preview.",
    )
    bot.add_argument(
        "--thread-id",
        type=int,
        default=None,
        help="Telegram topic/thread id for bot send-preview.",
    )
    bot.add_argument(
        "--allowed-chat-id",
        type=int,
        action="append",
        default=None,
        help="Allowed chat id for bot poll-once. Can be repeated.",
    )
    bot.add_argument(
        "--state-file",
        default=str(DEFAULT_STATE_FILE),
        help="Runtime polling state file under ignored .sessions/ by default.",
    )
    bot.add_argument(
        "--lock-file",
        default=str(DEFAULT_LOCK_FILE),
        help="Runtime lock file for bot poll-loop.",
    )
    bot.add_argument(
        "--timeout",
        type=int,
        default=0,
        help="Telegram getUpdates timeout seconds for bot poll-once.",
    )
    bot.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Telegram getUpdates limit for bot poll-once.",
    )
    bot.add_argument(
        "--poll-interval",
        type=float,
        default=1.0,
        help="Seconds to sleep between poll-loop iterations after successful polling.",
    )
    bot.add_argument(
        "--max-iterations",
        type=int,
        default=None,
        help="Stop poll-loop after N iterations. Intended for tests/smoke checks.",
    )
    bot.add_argument(
        "--live-today",
        action="store_true",
        help="Allow /today to build a fresh read-only seller-v3 daily report.",
    )
    bot.add_argument(
        "--live-status",
        action="store_true",
        help="Allow /status to build a fresh read-only status preflight.",
    )
    bot.add_argument(
        "--runtime-jobs",
        action="store_true",
        help="Queue all Telegram business operations through SQLite JobService instead of running inside polling.",
    )
    bot.add_argument(
        "--runtime-db",
        default="runtime/runtime.db",
        help="SQLite runtime DB path for --runtime-jobs.",
    )

    send_report = subparsers.add_parser(
        "send-telegram-report",
        help="Send an owner-facing Telegram summary and attach a saved report file.",
    )
    send_report.add_argument("--data-dir", default="data", help="Project data directory.")
    send_report.add_argument(
        "--token-file",
        default=None,
        help="External Telegram bot token file. Do not store it in the repository.",
    )
    send_report.add_argument("--chat-id", type=int, required=True, help="Telegram chat id.")
    send_report.add_argument("--thread-id", type=int, default=None, help="Telegram topic/thread id.")
    send_report.add_argument("--report", required=True, help="Saved report file under data/runs or data/reports.")
    send_report.add_argument("--summary", default=None, help="Short Telegram summary text.")
    send_report.add_argument("--summary-file", default=None, help="File with short Telegram summary text.")
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

    build_unified_catalog = subparsers.add_parser(
        "build-unified-catalog",
        help="Build read-only internal product-level catalog from confirmed Ozon/WB mapping.",
    )
    build_unified_catalog.add_argument(
        "--data-dir",
        default="data",
        help="Project data directory.",
    )
    build_unified_catalog.add_argument(
        "--run-id",
        default=None,
        help="Optional stable run id.",
    )
    build_unified_catalog.add_argument(
        "--mapping-path",
        default=None,
        help="Confirmed mapping CSV. Defaults to data/catalog/mapping/ozon_wb_internal_sku_confirmed.csv.",
    )
    build_unified_catalog.add_argument(
        "--owner-review-path",
        default=None,
        help="Owner-approved internal SKU review CSV. Defaults to data/catalog/unified/internal_sku_assignment_owner_review.csv if present.",
    )
    build_unified_catalog.add_argument(
        "--ozon-catalog-path",
        default=None,
        help="Ozon processed catalog CSV. Defaults to data/catalog/ozon/processed/ozon_catalog.csv.",
    )
    build_unified_catalog.add_argument(
        "--wb-catalog-path",
        default=None,
        help="WB processed catalog CSV. Defaults to data/catalog/wb/processed/wb_catalog.csv.",
    )
    build_unified_catalog.add_argument(
        "--output-dir",
        default=None,
        help="Unified catalog output directory. Defaults to data/catalog/unified.",
    )

    internal_sku_plan = subparsers.add_parser(
        "plan-internal-skus",
        help="Build read-only internal SKU proposal plan for Ozon-only and WB-only products.",
    )
    internal_sku_plan.add_argument(
        "--data-dir",
        default="data",
        help="Project data directory.",
    )
    internal_sku_plan.add_argument(
        "--run-id",
        default=None,
        help="Optional stable run id.",
    )
    internal_sku_plan.add_argument(
        "--products-path",
        default=None,
        help="Unified products CSV. Defaults to data/catalog/unified/products.csv.",
    )
    internal_sku_plan.add_argument(
        "--output-dir",
        default=None,
        help="Plan output directory. Defaults to data/catalog/unified.",
    )

    content_master = subparsers.add_parser(
        "build-content-master",
        help="Build read-only unified content master and card-work audit from local catalogs.",
    )
    content_master.add_argument(
        "--data-dir",
        default="data",
        help="Project data directory.",
    )
    content_master.add_argument(
        "--run-id",
        default=None,
        help="Optional stable run id.",
    )
    content_master.add_argument(
        "--products-path",
        default=None,
        help="Unified products CSV. Defaults to data/catalog/unified/products.csv.",
    )
    content_master.add_argument(
        "--owner-review-path",
        default=None,
        help="Owner-approved internal SKU review CSV. Defaults to data/catalog/unified/internal_sku_assignment_owner_review.csv if present.",
    )
    content_master.add_argument(
        "--ozon-catalog-path",
        default=None,
        help="Ozon processed catalog CSV. Defaults to data/catalog/ozon/processed/ozon_catalog.csv.",
    )
    content_master.add_argument(
        "--wb-catalog-path",
        default=None,
        help="WB processed catalog CSV. Defaults to data/catalog/wb/processed/wb_catalog.csv.",
    )
    content_master.add_argument(
        "--pricing-status-path",
        default=None,
        help="Optional pricing status CSV. Defaults to data/pricing/pricing_status.csv if present.",
    )
    content_master.add_argument(
        "--card-content-index",
        default=None,
        help="Optional card content index CSV. Defaults to data/catalog/content/card_content_index.csv if present.",
    )
    content_master.add_argument(
        "--output-dir",
        default=None,
        help="Content master output directory. Defaults to data/catalog/content.",
    )

    card_content = subparsers.add_parser(
        "fetch-card-content",
        help="Fetch read-only Ozon/WB card content snapshots for content master and SEO audits.",
    )
    card_content.add_argument(
        "--data-dir",
        default="data",
        help="Project data directory.",
    )
    card_content.add_argument(
        "--run-id",
        default=None,
        help="Optional stable run id.",
    )
    card_content.add_argument(
        "--products-path",
        default=None,
        help="Unified products CSV. Defaults to data/catalog/unified/products.csv.",
    )
    card_content.add_argument(
        "--output-dir",
        default=None,
        help="Card content output directory. Defaults to data/catalog/content.",
    )
    card_content.add_argument(
        "--marketplace",
        choices=("all", "ozon", "wb"),
        default="all",
        help="Marketplace scope.",
    )
    card_content.add_argument(
        "--limit-products",
        type=int,
        default=None,
        help="Optional product limit for smoke checks.",
    )
    card_content.add_argument(
        "--internal-sku",
        action="append",
        default=[],
        help="Fetch only selected internal SKU. Can be repeated.",
    )
    card_content.add_argument(
        "--merge-existing",
        action="store_true",
        help="Merge selected rows into existing content index instead of replacing it.",
    )

    card_backlog = subparsers.add_parser(
        "card-content-audit-backlog",
        help="Build read-only backlog for card content and SEO audit from content master.",
    )
    card_backlog.add_argument(
        "--data-dir",
        default="data",
        help="Project data directory.",
    )
    card_backlog.add_argument(
        "--run-id",
        default=None,
        help="Optional stable run id.",
    )
    card_backlog.add_argument(
        "--content-master-path",
        default=None,
        help="Content master CSV. Defaults to data/catalog/content/content_master.csv.",
    )
    card_backlog.add_argument(
        "--output-dir",
        default=None,
        help="Backlog output directory. Defaults to data/catalog/content.",
    )
    card_backlog.add_argument(
        "--include-low",
        action="store_true",
        help="Include low/no-issue rows as ready_for_visual_seo_audit.",
    )
    card_backlog.add_argument(
        "--sales-signals-csv",
        action="append",
        default=[],
        help="Optional normalized sales signal CSV. Can be passed multiple times.",
    )
    card_backlog.add_argument(
        "--stock-signals-csv",
        action="append",
        default=[],
        help="Optional normalized stock signal CSV. Can be passed multiple times.",
    )
    card_backlog.add_argument(
        "--parser-signals-csv",
        action="append",
        default=[],
        help="Optional parser visibility signal CSV. Can be passed multiple times.",
    )

    card_signals = subparsers.add_parser(
        "collect-card-signals",
        help="Collect read-only sales, stock and parser signals for card content backlog.",
    )
    card_signals.add_argument(
        "--data-dir",
        default="data",
        help="Project data directory.",
    )
    card_signals.add_argument(
        "--run-id",
        default=None,
        help="Optional stable run id.",
    )
    card_signals.add_argument(
        "--content-master-path",
        default=None,
        help="Content master CSV. Defaults to data/catalog/content/content_master.csv.",
    )
    card_signals.add_argument(
        "--output-dir",
        default=None,
        help="Signals output directory. Defaults to data/catalog/content/signals.",
    )
    card_signals.add_argument(
        "--marketplace",
        choices=("all", "ozon", "wb"),
        default="all",
        help="Marketplace API scope.",
    )
    card_signals.add_argument(
        "--period-days",
        type=int,
        default=30,
        help="Sales signal period ending yesterday, default 30 days.",
    )
    card_signals.add_argument(
        "--skip-api",
        action="store_true",
        help="Do not call Ozon/WB APIs; collect only parser signals from local derived CSV.",
    )
    card_signals.add_argument(
        "--parser-source",
        choices=("latest", "none"),
        default="latest",
        help="Parser source mode for derived local parser CSV.",
    )
    card_signals.add_argument(
        "--parser-csv",
        action="append",
        default=[],
        help="Explicit parser-derived CSV to normalize. Can be passed multiple times.",
    )

    card_params = subparsers.add_parser(
        "card-content-parameter-inventory",
        help="Build read-only inventory of Ozon/WB card parameters and category schemas.",
    )
    card_params.add_argument(
        "--data-dir",
        default="data",
        help="Project data directory.",
    )
    card_params.add_argument(
        "--run-id",
        default=None,
        help="Optional stable run id.",
    )
    card_params.add_argument(
        "--content-dir",
        default=None,
        help="Card content snapshot directory. Defaults to data/catalog/content.",
    )
    card_params.add_argument(
        "--output-dir",
        default=None,
        help="Parameter inventory output directory. Defaults to data/catalog/content/parameter_inventory.",
    )
    card_params.add_argument(
        "--skip-schema",
        action="store_true",
        help="Do not call Ozon/WB schema APIs; summarize only current local snapshots.",
    )

    ozon_product_remove_plan = subparsers.add_parser(
        "plan-ozon-product-remove",
        help="Build a dry-run to delete an uncreated Ozon product or archive an existing Ozon product.",
    )
    ozon_product_remove_plan.add_argument("--data-dir", default="data", help="Project data directory.")
    ozon_product_remove_plan.add_argument("--run-id", default=None, help="Optional stable run id.")
    ozon_product_remove_plan.add_argument("--offer-id", required=True, help="Ozon offer_id / seller SKU.")
    ozon_product_remove_plan.add_argument("--product-id", default=None, help="Optional Ozon product_id.")
    ozon_product_remove_plan.add_argument(
        "--action",
        choices=("auto", "delete", "archive"),
        default="auto",
        help="auto selects delete for not-created/no-SKU cards and archive for created cards.",
    )
    ozon_product_remove_plan.add_argument("--reason", default="", help="Human-readable owner-approved reason.")

    ozon_product_remove_apply = subparsers.add_parser(
        "apply-ozon-product-remove",
        help="Apply an approved Ozon product remove/archive dry-run.",
    )
    ozon_product_remove_apply.add_argument("--data-dir", default="data", help="Project data directory.")
    ozon_product_remove_apply.add_argument("--run-id", default=None, help="Optional stable run id.")
    ozon_product_remove_apply.add_argument("--plan-run-id", default=None, help="Approved plan run id.")
    ozon_product_remove_apply.add_argument(
        "--confirmed-by-user",
        action="store_true",
        help="Required explicit confirmation for external Ozon write operations.",
    )

    ozon_partial_diagnose = subparsers.add_parser(
        "ozon-partial-approved-diagnose",
        help="Diagnose Ozon PARTIAL_APPROVED cards and build exact recovery dry-run.",
    )
    ozon_partial_diagnose.add_argument("--data-dir", default="data", help="Project data directory.")
    ozon_partial_diagnose.add_argument("--run-id", default=None, help="Optional stable run id.")

    ozon_partial_apply = subparsers.add_parser(
        "apply-ozon-partial-approved-recovery",
        help="Apply approved Ozon PARTIAL_APPROVED recovery dry-run and verify.",
    )
    ozon_partial_apply.add_argument("--data-dir", default="data", help="Project data directory.")
    ozon_partial_apply.add_argument("--run-id", default=None, help="Optional stable run id.")
    ozon_partial_apply.add_argument("--plan-run-id", default=None, help="Approved diagnose run id.")
    ozon_partial_apply.add_argument(
        "--confirmed-by-user",
        action="store_true",
        help="Required explicit confirmation for external Ozon write operations.",
    )
    ozon_partial_apply.add_argument("--wait-seconds", type=int, default=120)
    ozon_partial_apply.add_argument("--poll-interval", type=int, default=5)

    passport_design = subparsers.add_parser(
        "design-product-passport",
        help="Build read-only master product passport schema and Ozon/WB attribute mapping.",
    )
    passport_design.add_argument(
        "--data-dir",
        default="data",
        help="Project data directory.",
    )
    passport_design.add_argument(
        "--run-id",
        default=None,
        help="Optional stable run id.",
    )
    passport_design.add_argument(
        "--parameter-inventory-dir",
        default=None,
        help="Parameter inventory directory. Defaults to data/catalog/content/parameter_inventory.",
    )
    passport_design.add_argument(
        "--output-dir",
        default=None,
        help="Product passport design output directory. Defaults to data/catalog/content/product_passport.",
    )

    card_audit_packages = subparsers.add_parser(
        "card-content-audit-packages",
        help="Build saved read-only card audit source packages from backlog and snapshots.",
    )
    card_audit_packages.add_argument(
        "--data-dir",
        default="data",
        help="Project data directory.",
    )
    card_audit_packages.add_argument(
        "--run-id",
        default=None,
        help="Optional stable run id.",
    )
    card_audit_packages.add_argument(
        "--backlog-path",
        default=None,
        help="Backlog CSV. Defaults to data/catalog/content/card_content_audit_backlog.csv.",
    )
    card_audit_packages.add_argument(
        "--content-master-path",
        default=None,
        help="Content master CSV. Defaults to data/catalog/content/content_master.csv.",
    )
    card_audit_packages.add_argument(
        "--ozon-content-path",
        default=None,
        help="Ozon card content JSON. Defaults to data/catalog/content/ozon_card_content.json.",
    )
    card_audit_packages.add_argument(
        "--wb-content-path",
        default=None,
        help="WB card content JSON. Defaults to data/catalog/content/wb_card_content.json.",
    )
    card_audit_packages.add_argument(
        "--passport-schema-path",
        default=None,
        help="Master product passport JSON schema path.",
    )
    card_audit_packages.add_argument(
        "--attribute-mapping-path",
        default=None,
        help="Product passport attribute mapping CSV path.",
    )
    card_audit_packages.add_argument(
        "--seo-query-pack-path",
        default=None,
        help="SEO query pack card targets JSON path. Defaults to data/catalog/content/seo_query_pack/card_seo_targets.json if present.",
    )
    card_audit_packages.add_argument(
        "--output-dir",
        default=None,
        help="Output package directory. Defaults to data/catalog/content/card_audit_packages/<run-id>.",
    )
    card_audit_packages.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Optional package limit for smoke checks or batch work.",
    )
    card_audit_packages.add_argument(
        "--business-priority",
        default="now",
        help="Backlog business_priority filter. Use 'all' to include all rows.",
    )

    seo_query_pack = subparsers.add_parser(
        "seo-query-pack",
        help="Build read-only SEO query pack and target query clusters for card auditors.",
    )
    seo_query_pack.add_argument(
        "--data-dir",
        default="data",
        help="Project data directory.",
    )
    seo_query_pack.add_argument(
        "--run-id",
        default=None,
        help="Optional stable run id.",
    )
    seo_query_pack.add_argument(
        "--content-master-path",
        default=None,
        help="Content master CSV. Defaults to data/catalog/content/content_master.csv.",
    )
    seo_query_pack.add_argument(
        "--query-source",
        action="append",
        default=[],
        help="Query source CSV/JSON path. Can be repeated.",
    )
    seo_query_pack.add_argument(
        "--output-dir",
        default=None,
        help="Latest SEO query pack output dir. Defaults to data/catalog/content/seo_query_pack.",
    )

    pricing_status = subparsers.add_parser(
        "pricing-status",
        help="Build read-only Ozon/WB pricing and margin-readiness status from local snapshots.",
    )
    pricing_status.add_argument(
        "--data-dir",
        default="data",
        help="Project data directory.",
    )
    pricing_status.add_argument(
        "--run-id",
        default=None,
        help="Optional stable run id.",
    )
    pricing_status.add_argument(
        "--products-path",
        default=None,
        help="Unified products CSV. Defaults to data/catalog/unified/products.csv.",
    )
    pricing_status.add_argument(
        "--ozon-prices-json",
        default=None,
        help="Optional Ozon prices JSON snapshot, for example product/info/prices output.",
    )
    pricing_status.add_argument(
        "--wb-prices-json",
        default=None,
        help="Optional WB goods/prices JSON snapshot.",
    )
    pricing_status.add_argument(
        "--ozon-actions-csv",
        default=None,
        help="Optional Ozon Elastic action price CSV, for example ozon_elastic_dry_run.csv.",
    )
    pricing_status.add_argument(
        "--wb-actions-csv",
        default=None,
        help="Optional WB actions discount CSV with semicolon delimiter.",
    )
    pricing_status.add_argument(
        "--output-dir",
        default=None,
        help="Pricing status output directory. Defaults to data/pricing.",
    )
    pricing_status.add_argument(
        "--refresh-api",
        action="store_true",
        help="Fetch fresh read-only Ozon/WB price snapshots before building the report.",
    )
    pricing_status.add_argument(
        "--refresh-marketplace",
        choices=("all", "ozon", "wb"),
        default="all",
        help="Marketplace scope for --refresh-api.",
    )

    ozon_pricing_margin = subparsers.add_parser(
        "ozon-pricing-margin",
        help="Build a read-only Ozon FBO expense model and price ladder.",
    )
    ozon_pricing_margin.add_argument("--data-dir", default="data", help="Project data directory.")
    ozon_pricing_margin.add_argument("--run-id", default=None, help="Optional stable run id.")
    ozon_pricing_margin.add_argument(
        "--unit-cost",
        required=True,
        help="Cost of one physical item in rubles.",
    )
    ozon_pricing_margin.add_argument(
        "--target-margin",
        required=True,
        help="Target margin per physical item in rubles.",
    )
    ozon_pricing_margin.add_argument(
        "--period-days",
        type=int,
        choices=(15, 30),
        required=True,
        help="Completed Ozon finance period in days.",
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

    period_report = subparsers.add_parser(
        "marketplace-period-report",
        help="Build a read-only Ozon or WB report for a selected period.",
    )
    period_report.add_argument("--data-dir", default="data", help="Project data directory.")
    period_report.add_argument("--run-id", default=None, help="Optional stable run id.")
    period_report.add_argument("--marketplace", choices=("ozon", "wb"), required=True)
    period_report.add_argument("--report-type", choices=("short", "financial", "full"), required=True)
    period_report.add_argument("--date-from", required=True, help="Start date, YYYY-MM-DD.")
    period_report.add_argument("--date-to", required=True, help="End date, YYYY-MM-DD.")

    wb_stock_supply = subparsers.add_parser(
        "wb-stock-supply-monitor",
        help="Build a fresh read-only WB warehouse stock and active supply report.",
    )
    wb_stock_supply.add_argument("--data-dir", default="data", help="Project data directory.")
    wb_stock_supply.add_argument("--run-id", default=None, help="Optional stable run id.")

    ozon_stock_supply = subparsers.add_parser(
        "ozon-stock-supply-monitor",
        help="Build a fresh read-only Ozon FBO stock and active supply report.",
    )
    ozon_stock_supply.add_argument("--data-dir", default="data", help="Project data directory.")
    ozon_stock_supply.add_argument("--run-id", default=None, help="Optional stable run id.")

    ozon_work_plan = subparsers.add_parser(
        "ozon-production-work-plan",
        help="Build a read-only Ozon production workbook by capacity or coverage days.",
    )
    ozon_work_plan.add_argument("--data-dir", default="data", help="Project data directory.")
    ozon_work_plan.add_argument("--run-id", default=None, help="Optional stable run id.")
    ozon_work_plan.add_argument("--mode", choices=("capacity", "coverage_days"), required=True)
    ozon_work_plan.add_argument("--value", type=int, required=True)
    ozon_work_plan.add_argument("--cluster-count", type=int, required=True)

    wb_work_plan = subparsers.add_parser(
        "wb-production-work-plan",
        help="Build a read-only WB production workbook by capacity or coverage days.",
    )
    wb_work_plan.add_argument("--data-dir", default="data", help="Project data directory.")
    wb_work_plan.add_argument("--run-id", default=None, help="Optional stable run id.")
    wb_work_plan.add_argument("--mode", choices=("capacity", "coverage_days"), required=True)
    wb_work_plan.add_argument("--value", type=int, required=True)
    wb_work_plan.add_argument("--cluster-count", type=int, required=True)

    supply_workbooks = subparsers.add_parser(
        "plan-supply-workbooks",
        help="Build the read-only supply workbook automation plan and source readiness report.",
    )
    supply_workbooks.add_argument("--data-dir", default="data", help="Project data directory.")
    supply_workbooks.add_argument("--run-id", default=None, help="Optional stable run id.")
    supply_workbooks.add_argument("--target-days", type=int, default=30, help="Target stock horizon in days.")
    supply_workbooks.add_argument("--cycle-days", type=int, default=5, help="Production cycle length in days.")
    supply_workbooks.add_argument("--daily-capacity", type=int, default=200, help="Physical pieces per production day.")
    supply_workbooks.add_argument("--ozon-weekly-physical", type=int, default=1400, help="Ozon weekly target in physical pieces.")
    supply_workbooks.add_argument("--wb-target-physical", type=int, default=1200, help="WB target in physical pieces.")

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
    sessions.add_argument(
        "--runtime-db",
        default=str(DEFAULT_RUNTIME_DB),
        help="SQLite runtime DB used for canonical profile leases.",
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
    restore_ozon.add_argument(
        "--runtime-db",
        default=str(DEFAULT_RUNTIME_DB),
        help="SQLite runtime DB used for the canonical Ozon profile lease.",
    )

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
    install_systemd.add_argument(
        "--runtime-db",
        default=str(DEFAULT_RUNTIME_DB),
        help="SQLite runtime DB used for atomic profile leases during --switch.",
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

    ozon_actions_optimizer = subparsers.add_parser(
        "plan-ozon-actions-optimizer",
        help="Build dry-run recommendations across all available Ozon actions without uploading changes.",
    )
    ozon_actions_optimizer.add_argument(
        "--data-dir",
        default="data",
        help="Project data directory.",
    )
    ozon_actions_optimizer.add_argument(
        "--run-id",
        default=None,
        help="Optional stable run id.",
    )
    ozon_actions_optimizer.add_argument(
        "--lk-boost-summary-json",
        default=None,
        help="Optional LK boost source summary JSON from ozon_actions_boost_probe.",
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

    apply_ozon_actions_optimizer = subparsers.add_parser(
        "apply-ozon-actions-optimizer",
        help="Apply approved Ozon all-actions optimizer dry-run after fresh preflight and drift-check.",
    )
    apply_ozon_actions_optimizer.add_argument(
        "--data-dir",
        default="data",
        help="Project data directory.",
    )
    apply_ozon_actions_optimizer.add_argument(
        "--plan-run-id",
        default=None,
        help="Approved Ozon all-actions optimizer plan run id. Defaults to the latest plan.",
    )
    apply_ozon_actions_optimizer.add_argument(
        "--run-id",
        default=None,
        help="Optional stable run id.",
    )
    apply_ozon_actions_optimizer.add_argument(
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

    wb_parser_warehouse = subparsers.add_parser(
        "wb-parser-warehouse-analytics",
        help="Build read-only WB parser warehouse visibility analytics from Parser Data API.",
    )
    wb_parser_warehouse.add_argument("--data-dir", default="data", help="Project data directory.")
    wb_parser_warehouse.add_argument("--run-id", default=None, help="Optional stable run id.")
    wb_parser_warehouse.add_argument(
        "--supplier-id",
        default="4516781",
        help="WB supplier_id to treat as Vital Shevron. Defaults to 4516781.",
    )
    wb_parser_warehouse.add_argument(
        "--limit",
        type=int,
        default=500,
        help="Maximum rows to request from each Parser Data API endpoint. Parser API currently allows up to 500.",
    )
    wb_parser_warehouse.add_argument(
        "--report-limit",
        type=int,
        default=50,
        help="Maximum derived candidate rows shown in report sections.",
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

    wb_promotion_parser_enriched = subparsers.add_parser(
        "plan-wb-promotion-bids-parser-enriched",
        help="Build dry-run WB Promotion bid recommendations enriched with parser, stock and sales signals.",
    )
    wb_promotion_parser_enriched.add_argument("--data-dir", default="data", help="Project data directory.")
    wb_promotion_parser_enriched.add_argument("--run-id", default=None, help="Optional stable run id.")
    wb_promotion_parser_enriched.add_argument(
        "--base-plan-run-id",
        default=None,
        help="Base wb_promotion_bid_plan_* run id. Defaults to latest.",
    )
    wb_promotion_parser_enriched.add_argument(
        "--base-plan-csv",
        default=None,
        help="Explicit base wb_promotion_bid_plan.csv.",
    )
    wb_promotion_parser_enriched.add_argument(
        "--signals-csv",
        action="append",
        default=[],
        help="Signal CSV with parser/stock/sales fields. Can be passed multiple times. Defaults to data/catalog/content/signals/*.csv.",
    )
    wb_promotion_parser_enriched.add_argument("--min-stock", type=int, default=4)
    wb_promotion_parser_enriched.add_argument("--parser-test-increase-percent", default="10")
    wb_promotion_parser_enriched.add_argument("--min-bid", default="1.00")

    apply_wb_promotion_parser_enriched = subparsers.add_parser(
        "apply-wb-promotion-bids-parser-enriched",
        help=(
            "Apply owner-approved WB Promotion parser-enriched bid changes after "
            "fresh report, fresh signals, fresh enriched plan, partial drift skip and verify."
        ),
    )
    apply_wb_promotion_parser_enriched.add_argument("--data-dir", default="data", help="Project data directory.")
    apply_wb_promotion_parser_enriched.add_argument(
        "--plan-run-id",
        default=None,
        help="Approved parser-enriched WB promotion bid plan run id. Defaults to latest.",
    )
    apply_wb_promotion_parser_enriched.add_argument("--run-id", default=None, help="Optional stable run id.")
    apply_wb_promotion_parser_enriched.add_argument(
        "--confirmed-by-user",
        action="store_true",
        help="Required explicit confirmation for external WB write operations.",
    )
    apply_wb_promotion_parser_enriched.add_argument(
        "--wait-seconds",
        type=int,
        default=45,
        help="Seconds to wait before verify because WB bid changes are asynchronous.",
    )
    apply_wb_promotion_parser_enriched.add_argument(
        "--approved-actions",
        default="apply_ready",
        help=(
            "Comma-separated parser_enriched_action values approved for this apply. "
            "Defaults to apply_ready. Use review_only only after exact owner approval."
        ),
    )

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
    plan_wb_cards.add_argument(
        "--internal-sku",
        action="append",
        default=[],
        help="Owner-approved internal SKU to create on WB from Layer 3 passport. Can be repeated.",
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

    plan_ozon_cards = subparsers.add_parser(
        "plan-ozon-card-create",
        help="Build dry-run plan for creating WB-only product cards in Ozon.",
    )
    plan_ozon_cards.add_argument(
        "--data-dir",
        default="data",
        help="Project data directory.",
    )
    plan_ozon_cards.add_argument(
        "--run-id",
        default=None,
        help="Optional stable run id.",
    )
    plan_ozon_cards.add_argument(
        "--internal-sku",
        action="append",
        default=[],
        help="Owner-approved internal SKU to create on Ozon from Layer 3 passport. Can be repeated.",
    )
    plan_ozon_cards.add_argument(
        "--allow-wb-price-fallback",
        action="store_true",
        help="Use WB discounted/base price as Ozon create price source when Ozon price is missing; plan keeps manual-review flag.",
    )
    plan_ozon_cards.add_argument(
        "--min-price",
        default=None,
        help="Ozon min_price to set through /v1/product/import/prices after card create, for example 400.",
    )
    plan_ozon_cards.add_argument(
        "--skip-schema-api",
        action="store_true",
        help="Do not call Ozon category schema/dictionary APIs. For diagnostics only; apply should use schema API.",
    )

    apply_ozon_cards = subparsers.add_parser(
        "apply-ozon-card-create",
        help="Create planned WB-only product cards in Ozon and verify the result.",
    )
    apply_ozon_cards.add_argument(
        "--data-dir",
        default="data",
        help="Project data directory.",
    )
    apply_ozon_cards.add_argument(
        "--plan-run-id",
        default=None,
        help="Plan run id. Defaults to the latest ozon_card_create_plan_* run.",
    )
    apply_ozon_cards.add_argument(
        "--run-id",
        default=None,
        help="Optional stable run id.",
    )
    apply_ozon_cards.add_argument(
        "--confirmed-by-user",
        action="store_true",
        help="Required explicit confirmation for external Ozon write operations.",
    )
    apply_ozon_cards.add_argument(
        "--allow-manual-review",
        action="store_true",
        help="Allow applying a plan that still has manual-review notes, for example WB price fallback.",
    )
    apply_ozon_cards.add_argument(
        "--wait-seconds",
        type=int,
        default=300,
        help="How long to wait for Ozon import and card visibility.",
    )
    apply_ozon_cards.add_argument(
        "--poll-interval",
        type=int,
        default=10,
        help="Polling interval in seconds while waiting for Ozon import/card visibility.",
    )

    plan_seller_sku_update = subparsers.add_parser(
        "plan-seller-sku-update",
        help="Build dry-run plan for replacing Ozon/WB seller SKU with internal SKU.",
    )
    plan_seller_sku_update.add_argument(
        "--data-dir",
        default="data",
        help="Project data directory.",
    )
    plan_seller_sku_update.add_argument(
        "--run-id",
        default=None,
        help="Optional stable run id.",
    )
    plan_seller_sku_update.add_argument(
        "--input",
        default=None,
        help="JSON operation package: list or {operations: [...]} with Ozon/WB old/new IDs.",
    )
    plan_seller_sku_update.add_argument(
        "--internal-sku",
        action="append",
        default=[],
        help="Internal SKU from data/catalog/unified/products.csv. Can be repeated.",
    )
    plan_seller_sku_update.add_argument(
        "--products-path",
        default=None,
        help="Unified products CSV. Defaults to data/catalog/unified/products.csv.",
    )
    plan_seller_sku_update.add_argument(
        "--skip-api",
        action="store_true",
        help="Do not call Ozon/WB APIs. Leaves rows for manual review and blocks apply.",
    )

    apply_seller_sku_update = subparsers.add_parser(
        "apply-seller-sku-update",
        help="Apply approved Ozon/WB seller SKU replacement plan and verify the result.",
    )
    apply_seller_sku_update.add_argument(
        "--data-dir",
        default="data",
        help="Project data directory.",
    )
    apply_seller_sku_update.add_argument(
        "--plan-run-id",
        default=None,
        help="Plan run id. Defaults to the latest seller_sku_update_plan_* run.",
    )
    apply_seller_sku_update.add_argument(
        "--run-id",
        default=None,
        help="Optional stable run id.",
    )
    apply_seller_sku_update.add_argument(
        "--confirmed-by-user",
        action="store_true",
        help="Required explicit confirmation for external Ozon/WB write operations.",
    )
    apply_seller_sku_update.add_argument(
        "--wait-seconds",
        type=int,
        default=60,
        help="How long to wait for WB vendorCode changes to become visible.",
    )
    apply_seller_sku_update.add_argument(
        "--poll-interval",
        type=int,
        default=5,
        help="Polling interval while waiting for WB vendorCode visibility.",
    )
    apply_seller_sku_update.add_argument(
        "--skip-local-layer-update",
        action="store_true",
        help="Do not update local CSV/JSON catalog layers after verified marketplace apply.",
    )

    plan_card_content_update = subparsers.add_parser(
        "plan-card-content-update",
        help="Build dry-run plan for applying owner-approved card passport to existing Ozon/WB cards.",
    )
    plan_card_content_update.add_argument("--data-dir", default="data", help="Project data directory.")
    plan_card_content_update.add_argument("--run-id", default=None, help="Optional stable run id.")
    plan_card_content_update.add_argument(
        "--passport",
        action="append",
        default=[],
        help="Approved master passport JSON path. Can be repeated.",
    )
    plan_card_content_update.add_argument(
        "--internal-sku",
        action="append",
        default=[],
        help="Internal SKU from data/catalog/master_passport/approved. Can be repeated.",
    )
    plan_card_content_update.add_argument(
        "--skip-api",
        action="store_true",
        help="Use local snapshots only. Intended for local smoke checks; apply must use approved ready plan.",
    )
    plan_card_content_update.add_argument(
        "--marketplace",
        action="append",
        choices=("ozon", "wb"),
        default=[],
        help="Limit the plan to one marketplace. Repeat to include both; default is both.",
    )

    promote_card_passport = subparsers.add_parser(
        "promote-approved-card-passport",
        help="Promote owner-approved Layer 2 audit/HTML into Layer 3 approved master passport.",
    )
    promote_card_passport.add_argument("--data-dir", default="data", help="Project data directory.")
    promote_card_passport.add_argument("--run-id", default=None, help="Optional stable run id.")
    promote_card_passport.add_argument(
        "--internal-sku",
        action="append",
        default=[],
        help="Owner-approved internal SKU to promote from card_audits. Can be repeated.",
    )
    promote_card_passport.add_argument(
        "--audit",
        action="append",
        default=[],
        help="Explicit Layer 2 audit.json path. Can be repeated.",
    )
    promote_card_passport.add_argument(
        "--audit-root",
        default=None,
        help="Optional card_audits root for searching audit.json files.",
    )
    promote_card_passport.add_argument(
        "--write",
        action="store_true",
        help="Write approved passport files. Without this flag the command is dry-run only.",
    )
    promote_card_passport.add_argument(
        "--overwrite",
        action="store_true",
        help="Allow replacing an existing approved passport.",
    )

    apply_card_content_update = subparsers.add_parser(
        "apply-card-content-update",
        help="Apply approved Ozon/WB card content update plan and verify submission.",
    )
    apply_card_content_update.add_argument("--data-dir", default="data", help="Project data directory.")
    apply_card_content_update.add_argument(
        "--plan-run-id",
        default=None,
        help="Plan run id. Defaults to the latest card_content_update_plan_* run.",
    )
    apply_card_content_update.add_argument("--run-id", default=None, help="Optional stable run id.")
    apply_card_content_update.add_argument(
        "--confirmed-by-user",
        action="store_true",
        help="Required explicit confirmation for external Ozon/WB write operations.",
    )
    apply_card_content_update.add_argument(
        "--wait-seconds",
        type=int,
        default=180,
        help="How long to wait for Ozon import task status.",
    )
    apply_card_content_update.add_argument(
        "--poll-interval",
        type=int,
        default=10,
        help="Polling interval for Ozon import task status.",
    )

    verify_card_content_update = subparsers.add_parser(
        "verify-card-content-update",
        help="Read-only verify existing Ozon/WB cards against owner-approved Layer 3 passports.",
    )
    verify_card_content_update.add_argument("--data-dir", default="data", help="Project data directory.")
    verify_card_content_update.add_argument("--run-id", default=None, help="Optional stable run id.")
    verify_card_content_update.add_argument(
        "--passport",
        action="append",
        default=[],
        help="Approved master passport JSON path. Can be repeated.",
    )
    verify_card_content_update.add_argument(
        "--internal-sku",
        action="append",
        default=[],
        help="Internal SKU from data/catalog/master_passport/approved. Can be repeated.",
    )
    verify_card_content_update.add_argument(
        "--skip-api",
        action="store_true",
        help="Use local snapshots only. Intended for local smoke checks.",
    )

    apply_approved_card = subparsers.add_parser(
        "apply-approved-card",
        help="Fast path: plan, apply and targeted-verify owner-approved existing card passports.",
    )
    apply_approved_card.add_argument("--data-dir", default="data", help="Project data directory.")
    apply_approved_card.add_argument("--run-id", default=None, help="Optional stable base run id.")
    apply_approved_card.add_argument(
        "--passport",
        action="append",
        default=[],
        help="Approved master passport JSON path. Can be repeated.",
    )
    apply_approved_card.add_argument(
        "--internal-sku",
        action="append",
        default=[],
        help="Internal SKU from data/catalog/master_passport/approved. Can be repeated.",
    )
    apply_approved_card.add_argument(
        "--confirmed-by-user",
        action="store_true",
        help="Required explicit confirmation for external Ozon/WB write operations.",
    )
    apply_approved_card.add_argument(
        "--wait-seconds",
        type=int,
        default=180,
        help="How long to wait for Ozon import task status.",
    )
    apply_approved_card.add_argument(
        "--poll-interval",
        type=int,
        default=10,
        help="Polling interval for Ozon import task status.",
    )

    apply_approved_cards = subparsers.add_parser(
        "apply-approved-cards",
        help="Batch fast path: apply owner-approved content, seller SKU and WB-create stages for multiple cards.",
    )
    apply_approved_cards.add_argument("--data-dir", default="data", help="Project data directory.")
    apply_approved_cards.add_argument("--run-id", default=None, help="Optional stable base run id.")
    apply_approved_cards.add_argument(
        "--internal-sku",
        action="append",
        help="Owner-approved internal SKU. Repeat for each card in the approved batch.",
    )
    apply_approved_cards.add_argument("--plan-run-id", default="", help="Approved plan run id from plan-approved-cards.")
    apply_approved_cards.add_argument(
        "--confirmed-by-user",
        action="store_true",
        help="Required explicit confirmation for external Ozon/WB write operations.",
    )
    apply_approved_cards.add_argument("--content-wait-seconds", type=int, default=180)
    apply_approved_cards.add_argument("--content-poll-interval", type=int, default=10)
    apply_approved_cards.add_argument("--seller-sku-wait-seconds", type=int, default=60)
    apply_approved_cards.add_argument("--seller-sku-poll-interval", type=int, default=5)
    apply_approved_cards.add_argument("--wb-create-wait-seconds", type=int, default=600)
    apply_approved_cards.add_argument("--wb-create-poll-interval", type=int, default=30)
    apply_approved_cards.add_argument(
        "--ozon-create-min-price",
        default="",
        help="Required when the approved batch contains WB-only cards that must be created on Ozon.",
    )
    apply_approved_cards.add_argument(
        "--ozon-create-allow-manual-review",
        action="store_true",
        help="Allow Ozon create rows that use owner-approved manual-review price fallback.",
    )
    apply_approved_cards.add_argument("--ozon-create-wait-seconds", type=int, default=300)
    apply_approved_cards.add_argument("--ozon-create-poll-interval", type=int, default=10)
    apply_approved_cards.add_argument("--runtime-db", default="runtime/runtime.db", help="SQLite runtime DB path.")
    apply_approved_cards.add_argument("--no-runtime-db", action="store_true", help="Do not update card lifecycle in SQLite.")

    plan_approved_cards = subparsers.add_parser(
        "plan-approved-cards",
        help="Dry-run batch owner-approved card plan with passport checksums before apply.",
    )
    plan_approved_cards.add_argument("--data-dir", default="data", help="Project data directory.")
    plan_approved_cards.add_argument("--run-id", default=None, help="Optional stable base run id.")
    plan_approved_cards.add_argument(
        "--internal-sku",
        action="append",
        required=True,
        help="Owner-approved internal SKU. Repeat for each card in the approved batch.",
    )
    plan_approved_cards.add_argument(
        "--ozon-create-min-price",
        default="",
        help="Required when the approved batch contains WB-only cards that must be created on Ozon.",
    )
    plan_approved_cards.add_argument(
        "--ozon-create-allow-manual-review",
        action="store_true",
        help="Allow Ozon create rows that use owner-approved manual-review price fallback.",
    )
    plan_approved_cards.add_argument("--runtime-db", default="runtime/runtime.db", help="SQLite runtime DB path.")
    plan_approved_cards.add_argument("--no-runtime-db", action="store_true", help="Do not update card lifecycle in SQLite.")

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

    ozon_messenger_workflow = subparsers.add_parser(
        "ozon-messenger-workflow",
        help="Run or inspect the Ozon Messenger workflow lifecycle.",
    )
    ozon_messenger_workflow.add_argument("--data-dir", default="data", help="Project data directory.")
    ozon_messenger_workflow.add_argument("--run-id", default=None, help="Optional stable run id.")
    ozon_messenger_workflow.add_argument(
        "--stage",
        choices=("triage", "prepare-approved", "apply", "verify", "cleanup"),
        required=True,
        help="Workflow stage to execute.",
    )
    ozon_messenger_workflow.add_argument(
        "--approved-path",
        default=None,
        help="Approved apply package for apply/cleanup stages.",
    )
    ozon_messenger_workflow.add_argument(
        "--confirmed-by-user",
        action="store_true",
        help="Required explicit confirmation for external Ozon write operations.",
    )

    ozon_inbox = subparsers.add_parser(
        "ozon-inbox",
        help="Build fresh Ozon reviews/questions plus Messenger/notifications approval package.",
    )
    ozon_inbox.add_argument("--data-dir", default="data", help="Project data directory.")
    ozon_inbox.add_argument("--run-id", default=None, help="Optional stable run id.")
    ozon_inbox.add_argument("--limit", type=int, default=300, help="Maximum rows/chats to inspect.")

    apply_ozon_inbox = subparsers.add_parser(
        "apply-ozon-inbox",
        help="Apply owner-approved Ozon inbox package from a Telegram dry-run.",
    )
    apply_ozon_inbox.add_argument("--data-dir", default="data", help="Project data directory.")
    apply_ozon_inbox.add_argument("--source-run-id", required=True, help="Ozon inbox dry-run id.")
    apply_ozon_inbox.add_argument(
        "--confirmed-by-user",
        action="store_true",
        help="Required explicit confirmation for marketplace write operations.",
    )

    wb_inbox = subparsers.add_parser(
        "wb-inbox",
        help="Build fresh WB reviews/questions approval package and WB notification status.",
    )
    wb_inbox.add_argument("--data-dir", default="data", help="Project data directory.")
    wb_inbox.add_argument("--run-id", default=None, help="Optional stable run id.")
    wb_inbox.add_argument("--limit", type=int, default=100, help="Maximum rows to inspect.")

    apply_wb_inbox = subparsers.add_parser(
        "apply-wb-inbox",
        help="Apply owner-approved WB inbox package from a Telegram dry-run.",
    )
    apply_wb_inbox.add_argument("--data-dir", default="data", help="Project data directory.")
    apply_wb_inbox.add_argument("--source-run-id", required=True, help="WB inbox dry-run id.")
    apply_wb_inbox.add_argument(
        "--confirmed-by-user",
        action="store_true",
        help="Required explicit confirmation for marketplace write operations.",
    )
    registry = default_task_registry()
    for command_name, command_parser in subparsers.choices.items():
        try:
            task = registry.get(command_name)
        except KeyError:
            continue
        if task.is_write:
            command_parser.add_argument(
                "--approval-id",
                default="",
                help="Existing approved seller.approval_package.v1 record to apply.",
            )
    return parser


def _allowed_chat_ids(values: list[int] | None) -> set[int] | None:
    result = set(values or [])
    raw = os.environ.get("VITAL_SHEVRON_TELEGRAM_ALLOWED_CHAT_IDS", "")
    for chunk in raw.replace(",", " ").split():
        try:
            result.add(int(chunk))
        except ValueError as exc:
            raise ValueError("VITAL_SHEVRON_TELEGRAM_ALLOWED_CHAT_IDS must contain integer chat ids") from exc
    return result or None


def _json_object_arg(raw: str, label: str) -> dict[str, object]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label} must be a JSON object") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _enqueue_cli_write(args: argparse.Namespace) -> int | None:
    registry = default_task_registry()
    try:
        task = registry.get(args.command)
    except KeyError:
        return None
    if not task.is_write:
        return None
    if not task.enabled:
        result = {
            "ok": False,
            "status": "blocked",
            "blocked_reason": "task_disabled",
            "task": task.name,
            "message": (
                f"Write command `{args.command}` is disabled in TaskRegistry; "
                "direct marketplace apply is forbidden."
            ),
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2
    if bool(getattr(args, "no_runtime_db", False)):
        result = {
            "ok": False,
            "status": "blocked",
            "blocked_reason": "runtime_db_required",
            "task": task.name,
            "message": "Write command requires the runtime JobService database.",
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2

    runtime_db_arg = getattr(args, "runtime_db", None)
    runtime_db = Path(runtime_db_arg) if runtime_db_arg else Path(
        os.environ.get("VITAL_SHEVRON_RUNTIME_DB") or DEFAULT_RUNTIME_DB
    )
    data_dir = Path(getattr(args, "data_dir", "data"))
    approval_id = str(getattr(args, "approval_id", "") or "").strip()
    if not approval_id:
        result = {
            "ok": False,
            "status": "blocked",
            "blocked_reason": "approval_id_required",
            "task": task.name,
            "message": (
                "Write command requires an existing approved "
                "seller.approval_package.v1 approval_id."
            ),
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2
    store = JobStore(runtime_db)
    service = JobService(store=store, data_dir=data_dir, runtime_db=runtime_db)
    try:
        job = service.submit_approval_apply(
            approval_id,
            actor="cli",
            expected_task_id=task.name,
        )
    except (KeyError, RuntimeError, ValueError) as exc:
        result = {
            "ok": False,
            "status": "blocked",
            "blocked_reason": "runtime_enqueue_failed",
            "task": task.name,
            "message": " ".join(str(exc).split())[:500],
            "artifacts": {"runtime_db": str(runtime_db)},
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2
    result = {
        "ok": True,
        "status": "queued",
        "job": asdict(job),
        "artifacts": {"runtime_db": str(runtime_db)},
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    write_result = _enqueue_cli_write(args)
    if write_result is not None:
        return write_result

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

    if args.command == "jobs":
        runtime_db = Path(args.runtime_db)
        store = JobStore(runtime_db)
        service = JobService(store=store, data_dir=Path(args.data_dir), runtime_db=runtime_db)
        if args.action == "list":
            result = {
                "rows": [asdict(job) for job in store.list_jobs(limit=args.limit, status=args.status)],
                "artifacts": {"runtime_db": str(runtime_db)},
            }
        elif args.action == "show":
            if not args.job_id:
                parser.error("jobs show requires --job-id")
            job = store.get_job(args.job_id)
            result = {
                "job": asdict(job) if job else None,
                "events": [asdict(event) for event in store.list_events(args.job_id)] if job else [],
                "artifacts": {"runtime_db": str(runtime_db)},
            }
        elif args.action == "submit":
            if not args.task:
                parser.error("jobs submit requires --task")
            try:
                params = _json_object_arg(args.params_json, "jobs submit --params-json")
            except ValueError as exc:
                parser.error(str(exc))
            try:
                task = default_task_registry().get(args.task)
                if task.is_write:
                    approval_id = str(params.get("approval_id") or "").strip()
                    if not approval_id:
                        raise RuntimeError(
                            "Write jobs submit requires an existing approved approval_id."
                        )
                    job = service.submit_approval_apply(
                        approval_id,
                        actor=args.actor,
                        expected_task_id=task.name,
                    )
                else:
                    job = service.submit(
                        task_id=task.name,
                        params=params,
                        actor=args.actor,
                        source=args.source,
                    )
            except (KeyError, RuntimeError, ValueError) as exc:
                result = {
                    "ok": False,
                    "status": "blocked",
                    "blocked_reason": "runtime_enqueue_failed",
                    "message": " ".join(str(exc).split())[:500],
                    "artifacts": {"runtime_db": str(runtime_db)},
                }
                print(json.dumps(result, ensure_ascii=False, indent=2))
                return 2
            result = {"job": asdict(job), "artifacts": {"runtime_db": str(runtime_db)}}
        elif args.action == "run":
            if not args.job_id:
                parser.error("jobs run requires --job-id")
            job_result = service.run(args.job_id)
            result = {
                "ok": job_result.ok,
                "status": job_result.status,
                "message": job_result.message,
                "job": asdict(job_result.job),
                "artifacts": {"runtime_db": str(runtime_db)},
            }
        elif args.action == "run-next":
            runner_result = JobRunner(service).run_next()
            result = {
                "ok": runner_result.ok,
                "ran": runner_result.ran,
                "message": runner_result.message,
                "job": asdict(runner_result.job) if runner_result.job else None,
                "artifacts": {"runtime_db": str(runtime_db)},
            }
        elif args.action == "recover-approvals":
            statuses = (
                (args.status,)
                if args.status
                else ("applying", "applying_unknown", "applied")
            )
            manifest_reconciliation = service.reconcile_runtime_manifests(
                limit=args.limit
            )
            result = service.recover_runtime_approvals(
                statuses=statuses,
                limit=args.limit,
                run_verify=args.run_verify,
                actor=args.actor,
            )
            result["manifest_reconciliation"] = manifest_reconciliation
            if manifest_reconciliation["overall_status"] != "ok":
                result["overall_status"] = "warning"
            result["artifacts"] = {"runtime_db": str(runtime_db)}
        else:
            if not args.job_id:
                parser.error("jobs cancel requires --job-id")
            job_result = service.cancel(args.job_id, reason=args.reason)
            result = {
                "ok": job_result.ok,
                "status": job_result.status,
                "message": job_result.message,
                "job": asdict(job_result.job),
                "artifacts": {"runtime_db": str(runtime_db)},
            }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("ok", True) else 2

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
        elif args.action == "show":
            if not args.task:
                parser.error("tasks show requires --task")
            result = {"task": get_task_definition(args.task)}
        else:
            result = {"rows": default_task_registry().policy_issues()}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    if args.command == "bot":
        data_dir = Path(args.data_dir)
        if args.action == "preview":
            result = dispatch_message(
                args.message,
                data_dir=data_dir,
                live_today=args.live_today,
                live_status=args.live_status,
                runtime_db=Path(args.runtime_db),
            )
            if args.json:
                print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
            else:
                print(result.text)
            return 0 if result.ok else 2

        token = load_telegram_bot_token(token_file=args.token_file)
        if not token:
            result = {
                "ok": False,
                "error": (
                    "missing Telegram bot token; set "
                    "VITAL_SHEVRON_TELEGRAM_BOT_TOKEN_FILE or pass --token-file"
                ),
            }
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 2

        if args.action == "send-preview":
            if args.chat_id is None:
                parser.error("bot send-preview requires --chat-id")
            result = send_preview_command(
                token=token,
                chat_id=args.chat_id,
                thread_id=args.thread_id,
                message=args.message,
                data_dir=data_dir,
                live_today=args.live_today,
                live_status=args.live_status,
                runtime_db=Path(args.runtime_db),
            )
        else:
            try:
                allowed_chat_ids = _allowed_chat_ids(args.allowed_chat_id)
            except ValueError as exc:
                parser.error(str(exc))
            if args.action == "poll-loop" and not allowed_chat_ids:
                result = {
                    "ok": False,
                    "error": (
                        "bot poll-loop requires --allowed-chat-id or "
                        "VITAL_SHEVRON_TELEGRAM_ALLOWED_CHAT_IDS"
                    ),
                }
                print(json.dumps(result, ensure_ascii=False, indent=2))
                return 2
        result: dict
        if args.action == "run-job-next":
            runtime_db = Path(args.runtime_db)
            store = JobStore(runtime_db)
            service = JobService(store=store, data_dir=data_dir, runtime_db=runtime_db)
            runner_result = JobRunner(service).run_next()
            notification = None
            if (
                runner_result.job is not None
                and runner_result.job.status in TERMINAL_JOB_STATUSES
            ):
                notification = notify_telegram_job_result(
                    token=token,
                    job_id=runner_result.job.job_id,
                    store=store,
                    runtime_db=runtime_db,
                    data_dir=data_dir,
                )
            result = {
                "ok": runner_result.ok and (notification.ok if notification else True),
                "ran": runner_result.ran,
                "deferred": runner_result.deferred,
                "message": runner_result.message,
                "job": asdict(runner_result.job) if runner_result.job else None,
                "notification": asdict(notification) if notification else None,
                "artifacts": {"runtime_db": str(runtime_db)},
            }
        elif args.action == "run-job-loop":
            runtime_db = Path(args.runtime_db)
            store = JobStore(runtime_db)
            service = JobService(store=store, data_dir=data_dir, runtime_db=runtime_db)
            notifications: list[dict] = []

            def notify(job) -> None:  # type: ignore[no-untyped-def]
                notification = notify_telegram_job_result(
                    token=token,
                    job_id=job.job_id,
                    store=store,
                    runtime_db=runtime_db,
                    data_dir=data_dir,
                )
                notifications.append(asdict(notification))

            summary = JobWorker(JobRunner(service), after_run=notify).run_loop(
                max_iterations=args.max_iterations or 1,
                poll_interval_seconds=args.poll_interval,
                stop_when_empty=True,
            )
            result = {
                "ok": summary.ok and all(item.get("ok") for item in notifications),
                "summary": asdict(summary),
                "notifications": notifications,
                "artifacts": {"runtime_db": str(runtime_db)},
            }
        elif args.action == "poll-loop":
            result = poll_loop(
                token=token,
                data_dir=data_dir,
                state_file=Path(args.state_file),
                lock_file=Path(args.lock_file),
                allowed_chat_ids=allowed_chat_ids or set(),
                live_today=args.live_today,
                live_status=args.live_status,
                runtime_jobs=args.runtime_jobs,
                runtime_db=Path(args.runtime_db),
                timeout_seconds=args.timeout,
                limit=args.limit,
                poll_interval_seconds=args.poll_interval,
                max_iterations=args.max_iterations,
            )
        elif args.action == "poll-once":
            result = poll_once(
                token=token,
                data_dir=data_dir,
                state_file=Path(args.state_file),
                allowed_chat_ids=allowed_chat_ids,
                live_today=args.live_today,
                live_status=args.live_status,
                runtime_jobs=args.runtime_jobs,
                runtime_db=Path(args.runtime_db),
                timeout_seconds=args.timeout,
                limit=args.limit,
            )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("ok") else 2

    if args.command == "send-telegram-report":
        token = load_telegram_bot_token(token_file=args.token_file)
        if not token:
            result = {
                "overall_status": "blocked",
                "blocked_reason": (
                    "missing Telegram bot token; set "
                    "VITAL_SHEVRON_TELEGRAM_BOT_TOKEN_FILE or pass --token-file"
                ),
            }
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 2
        result = run_send_telegram_report(
            token=token,
            chat_id=args.chat_id,
            thread_id=args.thread_id,
            data_dir=Path(args.data_dir),
            report_path=Path(args.report),
            summary=args.summary,
            summary_path=Path(args.summary_file) if args.summary_file else None,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["overall_status"] == "ok" else 2

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

    if args.command == "build-unified-catalog":
        result = run_build_unified_catalog(
            data_dir=Path(args.data_dir),
            mapping_path=Path(args.mapping_path) if args.mapping_path else None,
            owner_review_path=Path(args.owner_review_path) if args.owner_review_path else None,
            ozon_catalog_path=Path(args.ozon_catalog_path) if args.ozon_catalog_path else None,
            wb_catalog_path=Path(args.wb_catalog_path) if args.wb_catalog_path else None,
            output_dir=Path(args.output_dir) if args.output_dir else None,
            run_id=args.run_id,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["overall_status"] in {"ok", "warning"} else 2

    if args.command == "plan-internal-skus":
        result = run_internal_sku_plan(
            data_dir=Path(args.data_dir),
            products_path=Path(args.products_path) if args.products_path else None,
            output_dir=Path(args.output_dir) if args.output_dir else None,
            run_id=args.run_id,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["overall_status"] in {"ok", "warning"} else 2

    if args.command == "build-content-master":
        result = run_catalog_content_master(
            data_dir=Path(args.data_dir),
            products_path=Path(args.products_path) if args.products_path else None,
            owner_review_path=Path(args.owner_review_path) if args.owner_review_path else None,
            ozon_catalog_path=Path(args.ozon_catalog_path) if args.ozon_catalog_path else None,
            wb_catalog_path=Path(args.wb_catalog_path) if args.wb_catalog_path else None,
            pricing_status_path=Path(args.pricing_status_path) if args.pricing_status_path else None,
            card_content_index_path=Path(args.card_content_index) if args.card_content_index else None,
            output_dir=Path(args.output_dir) if args.output_dir else None,
            run_id=args.run_id,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["overall_status"] in {"ok", "warning"} else 2

    if args.command == "fetch-card-content":
        result = run_card_content_snapshot(
            credentials=load_credentials(),
            data_dir=Path(args.data_dir),
            products_path=Path(args.products_path) if args.products_path else None,
            output_dir=Path(args.output_dir) if args.output_dir else None,
            run_id=args.run_id,
            marketplace=args.marketplace,
            limit_products=args.limit_products,
            internal_skus=args.internal_sku,
            merge_existing=args.merge_existing,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["overall_status"] in {"ok", "warning"} else 2

    if args.command == "card-content-audit-backlog":
        result = run_card_content_audit_backlog(
            data_dir=Path(args.data_dir),
            content_master_path=Path(args.content_master_path) if args.content_master_path else None,
            output_dir=Path(args.output_dir) if args.output_dir else None,
            run_id=args.run_id,
            include_low=args.include_low,
            sales_signals_paths=[Path(path) for path in args.sales_signals_csv],
            stock_signals_paths=[Path(path) for path in args.stock_signals_csv],
            parser_signals_paths=[Path(path) for path in args.parser_signals_csv],
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["overall_status"] in {"ok", "warning"} else 2

    if args.command == "collect-card-signals":
        result = run_collect_card_signals(
            credentials=None if args.skip_api else load_credentials(),
            data_dir=Path(args.data_dir),
            content_master_path=Path(args.content_master_path) if args.content_master_path else None,
            output_dir=Path(args.output_dir) if args.output_dir else None,
            run_id=args.run_id,
            marketplace=args.marketplace,
            period_days=args.period_days,
            skip_api=args.skip_api,
            parser_source=args.parser_source,
            parser_paths=[Path(path) for path in args.parser_csv],
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["overall_status"] in {"ok", "warning"} else 2

    if args.command == "card-content-parameter-inventory":
        result = run_card_content_parameter_inventory(
            credentials=None if args.skip_schema else load_credentials(),
            data_dir=Path(args.data_dir),
            content_dir=Path(args.content_dir) if args.content_dir else None,
            output_dir=Path(args.output_dir) if args.output_dir else None,
            run_id=args.run_id,
            fetch_schema=not args.skip_schema,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["overall_status"] in {"ok", "warning"} else 2

    if args.command == "plan-ozon-product-remove":
        result = run_ozon_product_remove_plan(
            credentials=load_credentials(),
            data_dir=Path(args.data_dir),
            offer_id=args.offer_id,
            product_id=args.product_id,
            action=args.action,
            reason=args.reason,
            run_id=args.run_id,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["overall_status"] in {"ok", "warning"} else 2

    if args.command == "ozon-partial-approved-diagnose":
        result = run_ozon_partial_approved_diagnose(
            credentials=load_credentials(),
            data_dir=Path(args.data_dir),
            run_id=args.run_id,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["overall_status"] in {"ok", "warning"} else 2

    if args.command == "design-product-passport":
        result = run_product_passport_design(
            data_dir=Path(args.data_dir),
            parameter_inventory_dir=Path(args.parameter_inventory_dir) if args.parameter_inventory_dir else None,
            output_dir=Path(args.output_dir) if args.output_dir else None,
            run_id=args.run_id,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["overall_status"] in {"ok", "warning"} else 2

    if args.command == "card-content-audit-packages":
        business_priority = None if args.business_priority == "all" else args.business_priority
        result = run_card_content_audit_packages(
            data_dir=Path(args.data_dir),
            backlog_path=Path(args.backlog_path) if args.backlog_path else None,
            content_master_path=Path(args.content_master_path) if args.content_master_path else None,
            ozon_content_path=Path(args.ozon_content_path) if args.ozon_content_path else None,
            wb_content_path=Path(args.wb_content_path) if args.wb_content_path else None,
            passport_schema_path=Path(args.passport_schema_path) if args.passport_schema_path else None,
            attribute_mapping_path=Path(args.attribute_mapping_path) if args.attribute_mapping_path else None,
            seo_query_pack_path=Path(args.seo_query_pack_path) if args.seo_query_pack_path else None,
            output_dir=Path(args.output_dir) if args.output_dir else None,
            run_id=args.run_id,
            limit=args.limit,
            business_priority=business_priority,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["overall_status"] in {"ok", "warning"} else 2

    if args.command == "seo-query-pack":
        result = build_seo_query_pack(
            data_dir=Path(args.data_dir),
            content_master_path=Path(args.content_master_path) if args.content_master_path else Path("catalog/content/content_master.csv"),
            query_source_paths=[Path(path) for path in args.query_source],
            output_dir=Path(args.output_dir) if args.output_dir else Path(args.data_dir) / "catalog/content/seo_query_pack",
            run_id=args.run_id,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["overall_status"] in {"ok", "warning"} else 2

    if args.command == "pricing-status":
        result = run_pricing_status(
            credentials=load_credentials() if args.refresh_api else None,
            data_dir=Path(args.data_dir),
            products_path=Path(args.products_path) if args.products_path else None,
            ozon_prices_path=Path(args.ozon_prices_json) if args.ozon_prices_json else None,
            wb_prices_path=Path(args.wb_prices_json) if args.wb_prices_json else None,
            ozon_actions_path=Path(args.ozon_actions_csv) if args.ozon_actions_csv else None,
            wb_actions_path=Path(args.wb_actions_csv) if args.wb_actions_csv else None,
            output_dir=Path(args.output_dir) if args.output_dir else None,
            run_id=args.run_id,
            refresh_api=args.refresh_api,
            refresh_marketplace=args.refresh_marketplace,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["overall_status"] in {"ok", "warning"} else 2

    if args.command == "ozon-pricing-margin":
        result = run_ozon_pricing_margin(
            credentials=load_credentials(),
            data_dir=Path(args.data_dir),
            unit_cost=args.unit_cost,
            target_margin=args.target_margin,
            period_days=args.period_days,
            run_id=args.run_id,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["overall_status"] in {"ok", "warning"} else 2

    if args.command == "plan-wb-card-create":
        result = run_wb_card_create_plan(
            credentials=load_credentials(),
            data_dir=Path(args.data_dir),
            run_id=args.run_id,
            internal_skus=args.internal_sku,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    if args.command == "plan-ozon-card-create":
        result = run_ozon_card_create_plan(
            credentials=load_credentials(),
            data_dir=Path(args.data_dir),
            run_id=args.run_id,
            internal_skus=args.internal_sku,
            allow_wb_price_fallback=args.allow_wb_price_fallback,
            min_price=args.min_price,
            skip_schema_api=args.skip_schema_api,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["overall_status"] in {"ok", "warning"} else 2

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

    if args.command == "marketplace-period-report":
        result = run_marketplace_period_report(
            credentials=load_credentials(),
            data_dir=Path(args.data_dir),
            marketplace=args.marketplace,
            report_type=args.report_type,
            date_from=args.date_from,
            date_to=args.date_to,
            run_id=args.run_id,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["overall_status"] in {"ok", "warning"} else 2

    if args.command == "wb-stock-supply-monitor":
        result = run_wb_stock_supply_monitor(
            credentials=load_credentials(),
            data_dir=Path(args.data_dir),
            run_id=args.run_id,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["overall_status"] in {"ok", "warning"} else 2

    if args.command == "ozon-stock-supply-monitor":
        result = run_ozon_stock_supply_monitor(
            credentials=load_credentials(),
            data_dir=Path(args.data_dir),
            run_id=args.run_id,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["overall_status"] in {"ok", "warning"} else 2

    if args.command == "ozon-production-work-plan":
        result = run_ozon_production_work_plan(
            credentials=load_credentials(),
            data_dir=Path(args.data_dir),
            run_id=args.run_id,
            mode=args.mode,
            value=args.value,
            cluster_count=args.cluster_count,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["overall_status"] in {"ok", "warning"} else 2

    if args.command == "wb-production-work-plan":
        result = run_wb_production_work_plan(
            credentials=load_credentials(),
            data_dir=Path(args.data_dir),
            run_id=args.run_id,
            mode=args.mode,
            value=args.value,
            cluster_count=args.cluster_count,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["overall_status"] in {"ok", "warning"} else 2

    if args.command == "plan-supply-workbooks":
        result = run_supply_workbooks_plan(
            credentials=load_credentials(),
            data_dir=Path(args.data_dir),
            run_id=args.run_id,
            target_days=args.target_days,
            cycle_days=args.cycle_days,
            daily_capacity=args.daily_capacity,
            ozon_weekly_physical=args.ozon_weekly_physical,
            wb_target_physical=args.wb_target_physical,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["overall_status"] in {"ok", "warning"} else 2

    if args.command == "sessions":
        result = run_session_manager(
            action=args.action,
            marketplace=args.marketplace,
            data_dir=Path(args.data_dir),
            run_id=args.run_id,
            runtime_db=Path(args.runtime_db),
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
            runtime_db=Path(args.runtime_db),
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["overall_status"] in {"ok", "warning"} else 2

    if args.command == "install-session-systemd":
        result = install_systemd_units(
            switch=args.switch,
            dry_run=not args.apply,
            runtime_db=Path(args.runtime_db),
        )
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

    if args.command == "plan-ozon-actions-optimizer":
        result = run_ozon_actions_optimizer_plan(
            credentials=load_credentials(),
            data_dir=Path(args.data_dir),
            run_id=args.run_id,
            lk_boost_summary_json=Path(args.lk_boost_summary_json) if args.lk_boost_summary_json else None,
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

    if args.command == "wb-parser-warehouse-analytics":
        result = run_wb_parser_warehouse_analytics(
            data_dir=Path(args.data_dir),
            run_id=args.run_id,
            supplier_id=args.supplier_id,
            limit=args.limit,
            report_limit=args.report_limit,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["overall_status"] in {"ok", "warning"} else 2

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

    if args.command == "plan-wb-promotion-bids-parser-enriched":
        result = run_wb_promotion_bid_parser_enriched_plan(
            data_dir=Path(args.data_dir),
            run_id=args.run_id,
            base_plan_run_id=args.base_plan_run_id,
            base_plan_csv=Path(args.base_plan_csv) if args.base_plan_csv else None,
            signals_csv=[Path(path) for path in args.signals_csv],
            min_stock=args.min_stock,
            parser_test_increase_percent=Decimal(args.parser_test_increase_percent),
            min_bid=Decimal(args.min_bid),
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    if args.command == "plan-seller-sku-update":
        result = run_seller_sku_update_plan(
            credentials=load_credentials(),
            data_dir=Path(args.data_dir),
            input_path=Path(args.input) if args.input else None,
            internal_skus=args.internal_sku,
            products_path=Path(args.products_path) if args.products_path else None,
            run_id=args.run_id,
            skip_api=args.skip_api,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["overall_status"] in {"ok", "warning"} else 2

    if args.command == "plan-card-content-update":
        result = run_card_content_update_plan(
            credentials=load_credentials(),
            data_dir=Path(args.data_dir),
            passport_paths=[Path(path) for path in args.passport],
            internal_skus=args.internal_sku,
            run_id=args.run_id,
            skip_api=args.skip_api,
            marketplaces=args.marketplace or None,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["overall_status"] in {"ok", "warning"} else 2

    if args.command == "promote-approved-card-passport":
        result = run_promote_approved_card_passport(
            data_dir=Path(args.data_dir),
            internal_skus=args.internal_sku,
            audit_paths=[Path(path) for path in args.audit],
            audit_root=Path(args.audit_root) if args.audit_root else None,
            run_id=args.run_id,
            write=args.write,
            overwrite=args.overwrite,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["overall_status"] in {"ok", "warning"} else 2

    if args.command == "verify-card-content-update":
        result = run_card_content_update_verify(
            credentials=load_credentials(),
            data_dir=Path(args.data_dir),
            passport_paths=[Path(path) for path in args.passport],
            internal_skus=args.internal_sku,
            run_id=args.run_id,
            skip_api=args.skip_api,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["overall_status"] in {"ok", "warning"} else 2

    if args.command == "plan-approved-cards":
        result = run_plan_approved_cards(
            credentials=load_credentials(),
            data_dir=Path(args.data_dir),
            internal_skus=args.internal_sku,
            run_id=args.run_id,
            ozon_create_min_price=args.ozon_create_min_price,
            ozon_create_allow_manual_review=args.ozon_create_allow_manual_review,
            runtime_db=None if args.no_runtime_db else Path(args.runtime_db),
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["overall_status"] in {"ok", "warning"} else 2

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

    if args.command == "ozon-inbox":
        result = run_ozon_inbox_triage(
            credentials=load_credentials(),
            data_dir=Path(args.data_dir),
            run_id=args.run_id,
            limit=args.limit,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["overall_status"] in {"ok", "warning"} else 2

    if args.command == "wb-inbox":
        result = run_wb_inbox_triage(
            credentials=load_credentials(),
            data_dir=Path(args.data_dir),
            run_id=args.run_id,
            limit=args.limit,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["overall_status"] in {"ok", "warning"} else 2

    parser.error(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
