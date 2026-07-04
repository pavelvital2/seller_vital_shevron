from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
import socket
import subprocess
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen

from seller_agent.config import AppCredentials
from seller_agent.core.run_manifest import manifest_from_summary, write_run_manifest
from seller_agent.marketplaces.ozon.adapter import OzonSellerAdapter
from seller_agent.marketplaces.ozon.performance_adapter import OzonPerformanceAdapter
from seller_agent.marketplaces.wb.adapter import WbContentAdapter
from seller_agent.reports.writer import ensure_dir, write_json
from seller_agent.sessions.state import process_or_systemd_status, refresh_freshness


PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _safe_error(exc: BaseException) -> str:
    return str(exc).replace("\n", " ")[:800]


def _status(ok: bool, *, skipped: bool = False) -> str:
    if skipped:
        return "skipped"
    return "ok" if ok else "error"


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _process_is_running(pid_file: Path) -> dict[str, Any]:
    if not pid_file.exists():
        return {"status": "error", "pid_file": str(pid_file), "running": False, "error": "pid file missing"}

    raw_pid = pid_file.read_text(encoding="utf-8").strip()
    if not raw_pid.isdigit():
        return {"status": "error", "pid_file": str(pid_file), "running": False, "error": "invalid pid file"}

    pid = int(raw_pid)
    try:
        Path(f"/proc/{pid}").stat()
    except FileNotFoundError:
        return {"status": "error", "pid_file": str(pid_file), "pid": pid, "running": False, "error": "process not running"}

    return {"status": "ok", "pid_file": str(pid_file), "pid": pid, "running": True}


def _tcp_port_open(host: str, port: int, *, timeout: float = 2.0) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        return sock.connect_ex((host, port)) == 0


def _check_ozon_cdp() -> dict[str, Any]:
    port = int(os.environ.get("OZON_REMOTE_DEBUGGING_PORT", "9544"))
    result: dict[str, Any] = {
        "status": "error",
        "host": "127.0.0.1",
        "port": port,
        "listening": _tcp_port_open("127.0.0.1", port),
    }
    if not result["listening"]:
        result["error"] = "CDP port is not listening"
        return result

    try:
        with urlopen(f"http://127.0.0.1:{port}/json/version", timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
        result.update(
            {
                "status": "ok",
                "browser": payload.get("Browser", ""),
                "protocol_version": payload.get("Protocol-Version", ""),
            }
        )
    except (URLError, TimeoutError, json.JSONDecodeError) as exc:
        result["error"] = _safe_error(exc)

    return result


def _run_json_command(
    args: list[str],
    *,
    timeout: int,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            args,
            cwd=PROJECT_ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"status": "error", "returncode": None, "error": _safe_error(exc)}

    stdout = completed.stdout.strip()
    parsed: Any = {}
    if stdout:
        try:
            parsed = json.loads(stdout)
        except json.JSONDecodeError:
            parsed = {"raw_stdout": stdout[-1200:]}

    if isinstance(parsed, dict):
        parsed.setdefault("returncode", completed.returncode)
        parsed["status"] = "ok" if completed.returncode == 0 and parsed.get("ok", True) else "error"
        if completed.stderr.strip():
            parsed["stderr_tail"] = completed.stderr.strip()[-1200:]
        return parsed

    return {
        "status": "error" if completed.returncode else "ok",
        "returncode": completed.returncode,
        "parsed_stdout": parsed,
        "stderr_tail": completed.stderr.strip()[-1200:],
    }


def _check_ozon_api(credentials: AppCredentials) -> dict[str, Any]:
    if not credentials.ozon_seller:
        return {"status": "skipped", "error": "missing Ozon Seller API credentials"}

    try:
        data = OzonSellerAdapter(credentials.ozon_seller).fetch_product_list_page(limit=1)
        result = data.get("result") or {}
        items = result.get("items") if isinstance(result, dict) else []
        return {
            "status": "ok",
            "endpoint": "/v3/product/list",
            "sample_items": len(items) if isinstance(items, list) else 0,
            "has_result": bool(result),
            "has_next_page": bool(result.get("last_id")) if isinstance(result, dict) else False,
        }
    except Exception as exc:  # noqa: BLE001 - status report must capture external failures
        return {"status": "error", "endpoint": "/v3/product/list", "error": _safe_error(exc)}


def _check_ozon_performance_api(credentials: AppCredentials) -> dict[str, Any]:
    if not credentials.ozon_performance:
        return {"status": "skipped", "error": "missing Ozon Performance API credentials"}

    try:
        metadata = OzonPerformanceAdapter(credentials.ozon_performance).fetch_access_token_metadata()
        ok = bool(metadata["has_access_token"]) and str(metadata.get("token_type", "")).lower() == "bearer"
        return {
            "status": _status(ok),
            "endpoint": "/api/client/token",
            "host": "https://api-performance.ozon.ru",
            "token_received": bool(metadata["has_access_token"]),
            "token_type": metadata.get("token_type", ""),
            "expires_in": metadata.get("expires_in"),
        }
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "endpoint": "/api/client/token", "error": _safe_error(exc)}


def _check_wb_api(credentials: AppCredentials) -> dict[str, Any]:
    if not credentials.wb:
        return {"status": "skipped", "error": "missing Wildberries API token"}

    try:
        data = WbContentAdapter(credentials.wb).fetch_cards_page(limit=1)
        cards = data.get("cards") or []
        cursor = data.get("cursor") or {}
        return {
            "status": "ok",
            "endpoint": "/content/v2/get/cards/list",
            "sample_items": len(cards) if isinstance(cards, list) else 0,
            "cursor_total": cursor.get("total") if isinstance(cursor, dict) else None,
        }
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "endpoint": "/content/v2/get/cards/list", "error": _safe_error(exc)}


def _summarize_master_catalog(data_dir: Path) -> dict[str, Any]:
    catalog_path = data_dir / "catalog" / "processed" / "master_catalog.json"
    if not catalog_path.exists():
        return {
            "status": "error",
            "path": str(catalog_path),
            "error": "master_catalog.json missing",
        }

    try:
        rows = _read_json(catalog_path)
    except (OSError, json.JSONDecodeError) as exc:
        return {"status": "error", "path": str(catalog_path), "error": _safe_error(exc)}

    if not isinstance(rows, list):
        return {"status": "error", "path": str(catalog_path), "error": "master catalog is not a list"}

    counts = {
        "rows": len(rows),
        "matched_rows": 0,
        "ozon_only_rows": 0,
        "wb_only_rows": 0,
        "barcode_mismatch_rows": 0,
        "ozon_platform_barcode_rows": 0,
    }
    for row in rows:
        if not isinstance(row, dict):
            continue
        match_status = row.get("match_status")
        if match_status == "matched":
            counts["matched_rows"] += 1
        elif match_status == "ozon_only":
            counts["ozon_only_rows"] += 1
        elif match_status == "wb_only":
            counts["wb_only_rows"] += 1
        notes = str(row.get("notes") or "")
        if "barcode_mismatch" in notes:
            counts["barcode_mismatch_rows"] += 1
        if "ozon_platform_barcode" in notes:
            counts["ozon_platform_barcode_rows"] += 1

    seller_sku_mode = os.environ.get("SELLER_SKU_MODE", "separate").strip().lower()
    if seller_sku_mode == "unified":
        ok = (
            counts["rows"] > 0
            and counts["rows"] == counts["matched_rows"]
            and counts["ozon_only_rows"] == 0
            and counts["wb_only_rows"] == 0
            and counts["barcode_mismatch_rows"] == 0
        )
        match_gate = "full_match_required"
    else:
        ok = counts["rows"] > 0 and counts["barcode_mismatch_rows"] == 0
        match_gate = "separate_catalogs_mapping_optional"
    stat = catalog_path.stat()
    return {
        "status": _status(ok),
        "path": str(catalog_path),
        "modified_at": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
        "seller_sku_mode": seller_sku_mode or "separate",
        "match_gate": match_gate,
        **counts,
    }


def _check_lk_sessions(*, include_lk: bool) -> dict[str, Any]:
    if not include_lk:
        return {}

    session_dir = PROJECT_ROOT / ".sessions"
    env = dict(os.environ)
    env.setdefault("OZON_EXPECTED_STORE", "Vital Shevron")
    env.setdefault("WB_EXPECTED_SELLER", "")

    checks: dict[str, Any] = {}
    checks["ozon_keeper_pid"] = process_or_systemd_status(
        pid_file=session_dir / "ozon" / "ozon_keeper.pid",
        unit="vital-shevron-ozon-keeper.service",
        kind="keeper",
    )
    checks["ozon_watchdog_pid"] = process_or_systemd_status(
        pid_file=session_dir / "ozon" / "ozon_session_watchdog.pid",
        unit="vital-shevron-ozon-session-refresh.timer",
        kind="watchdog",
    )
    checks["ozon_cdp"] = _check_ozon_cdp()
    checks["ozon_keepalive"] = _run_json_command(
        ["node", "scripts/sessions/ozon_session_keepalive_cdp.js"],
        timeout=120,
        env=env,
    )
    checks["ozon_refresh_state"] = refresh_freshness("ozon")
    checks["wb_watchdog_pid"] = process_or_systemd_status(
        pid_file=session_dir / "wb" / "wb_session_watchdog.pid",
        unit="vital-shevron-wb-session-refresh.timer",
        kind="watchdog",
    )
    checks["wb_keepalive"] = _run_json_command(
        ["node", "scripts/sessions/wb_session_keepalive.js"],
        timeout=150,
        env=env,
    )
    checks["wb_refresh_state"] = refresh_freshness("wb")
    return checks


def _overall_status(checks: dict[str, Any]) -> str:
    statuses: list[str] = []
    for value in checks.values():
        if isinstance(value, dict):
            status = value.get("status")
            if isinstance(status, str):
                statuses.append(status)

    if any(status == "error" for status in statuses):
        return "error"
    if any(status == "warning" for status in statuses):
        return "warning"
    if any(status == "skipped" for status in statuses):
        return "warning"
    return "ok"


def _write_status_report(path: Path, *, result: dict[str, Any]) -> None:
    ensure_dir(path.parent)
    checks = result["checks"]
    lines = [
        "# Vital Shevron Status Preflight",
        "",
        f"Run ID: `{result['run_id']}`",
        f"Started at: `{result['started_at']}`",
        f"Overall status: `{result['overall_status']}`",
        "",
        "## Checks",
        "",
    ]
    for key in sorted(checks):
        check = checks[key]
        status = check.get("status") if isinstance(check, dict) else "unknown"
        lines.append(f"- `{key}`: `{status}`")
        if isinstance(check, dict) and check.get("error"):
            lines.append(f"  - error: {check['error']}")

    lines.extend(["", "## Catalog", ""])
    catalog = checks.get("master_catalog", {})
    if isinstance(catalog, dict):
        for key in (
            "rows",
            "matched_rows",
            "ozon_only_rows",
            "wb_only_rows",
            "barcode_mismatch_rows",
            "ozon_platform_barcode_rows",
            "modified_at",
        ):
            if key in catalog:
                lines.append(f"- `{key}`: {catalog[key]}")

    lines.extend(["", "## Session Freshness", ""])
    for key in ("ozon_refresh_state", "wb_refresh_state"):
        freshness = checks.get(key, {})
        if not isinstance(freshness, dict):
            continue
        lines.append(f"### `{key}`")
        for field in (
            "status",
            "finished_at",
            "age_seconds",
            "interval_seconds",
            "overdue_after_seconds",
            "overdue",
            "watchdog_running",
            "watchdog_source",
            "state_exported",
        ):
            if field in freshness:
                lines.append(f"- `{field}`: {freshness[field]}")
        if freshness.get("error"):
            lines.append(f"- `error`: {freshness['error']}")
        lines.append("")

    lines.extend(["", "## Artifacts", ""])
    for key, value in sorted(result["artifacts"].items()):
        lines.append(f"- `{key}`: `{value}`")

    lines.extend(
        [
            "",
            "## Recommendations",
            "",
            "1. Run this preflight before prices, promo, cards, ads, reviews or question-answer operations.",
            "2. Treat any `error` status as a blocker for write operations.",
            "3. If API checks pass but LK checks fail, use API-only tasks or restore the relevant LK session before browser-based operations.",
            "4. In `SELLER_SKU_MODE=separate`, use Ozon/WB native IDs for marketplace-local tasks and require mapping only for cross-marketplace product operations.",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def _normalize_marketplaces(marketplaces: tuple[str, ...] | list[str] | set[str] | None) -> tuple[str, ...]:
    if marketplaces is None:
        return ("ozon", "wb")
    selected = tuple(sorted({str(item).strip().lower() for item in marketplaces if str(item).strip()}))
    if not selected:
        return ("ozon", "wb")
    unknown = sorted(set(selected) - {"ozon", "wb"})
    if unknown:
        raise ValueError(f"unknown marketplaces for status preflight: {unknown}")
    return selected


def run_status_preflight(
    *,
    credentials: AppCredentials,
    data_dir: Path = Path("data"),
    run_id: str | None = None,
    include_lk: bool = True,
    marketplaces: tuple[str, ...] | list[str] | set[str] | None = None,
    include_catalog: bool = True,
    include_ozon_performance: bool = True,
) -> dict[str, Any]:
    started_at = datetime.now()
    run_id = run_id or f"status_preflight_{started_at.strftime('%Y%m%dT%H%M%S')}"
    run_dir = ensure_dir(data_dir / "runs" / started_at.strftime("%Y-%m-%d") / run_id)
    selected_marketplaces = _normalize_marketplaces(marketplaces)

    checks: dict[str, Any] = {}
    if "ozon" in selected_marketplaces:
        checks["ozon_api"] = _check_ozon_api(credentials)
        if include_ozon_performance:
            checks["ozon_performance_api"] = _check_ozon_performance_api(credentials)
    if "wb" in selected_marketplaces:
        checks["wb_api"] = _check_wb_api(credentials)
    if include_catalog:
        checks["master_catalog"] = _summarize_master_catalog(data_dir)
    checks.update(_check_lk_sessions(include_lk=include_lk))

    artifacts = {
        "run_dir": str(run_dir),
        "summary": str(run_dir / "summary.json"),
        "report": str(run_dir / "status_preflight_report.md"),
        "run_manifest": str(run_dir / "manifest.json"),
    }
    result = {
        "run_id": run_id,
        "started_at": started_at.isoformat(timespec="seconds"),
        "mode": "read_only",
        "overall_status": _overall_status(checks),
        "checks": checks,
        "artifacts": artifacts,
    }

    write_json(run_dir / "summary.json", result)
    _write_status_report(run_dir / "status_preflight_report.md", result=result)
    write_run_manifest(
        data_dir=data_dir,
        run_dir=run_dir,
        manifest=manifest_from_summary(
            summary=result,
            task="status-preflight",
            mode="read_only",
            risk="none",
            marketplaces=list(selected_marketplaces),
            inputs={
                "include_lk": include_lk,
                "marketplaces": list(selected_marketplaces),
                "include_catalog": include_catalog,
                "include_ozon_performance": include_ozon_performance,
            },
        ),
    )
    return result
