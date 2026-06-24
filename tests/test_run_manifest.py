from __future__ import annotations

import json
from pathlib import Path

from seller_agent.config import AppCredentials
from seller_agent.core.run_manifest import (
    find_run,
    latest_run,
    list_runs,
    manifest_from_summary,
    read_run_index,
    write_summary_run_manifest,
    write_run_manifest,
)
from seller_agent.tasks.status_preflight import run_status_preflight


def test_manifest_write_upserts_index_and_redacts_inputs(tmp_path: Path) -> None:
    run_dir = tmp_path / "runs" / "2026-06-18" / "status_preflight_test"
    summary = {
        "run_id": "status_preflight_test",
        "started_at": "2026-06-18T10:00:00",
        "overall_status": "ok",
        "artifacts": {
            "summary": str(run_dir / "summary.json"),
            "cookie_dump": "must-not-be-indexed",
        },
    }
    manifest = manifest_from_summary(
        summary=summary,
        task="status-preflight",
        mode="read_only",
        risk="none",
        marketplaces=["ozon", "wb"],
        inputs={"api_key": "secret", "nested": {"token": "secret", "safe": "value"}},
    )

    write_run_manifest(data_dir=tmp_path, run_dir=run_dir, manifest=manifest)
    write_run_manifest(data_dir=tmp_path, run_dir=run_dir, manifest=manifest)

    manifest_json = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    index_rows = read_run_index(tmp_path)

    assert manifest_json["inputs"]["api_key"] == "<redacted>"
    assert manifest_json["inputs"]["nested"]["token"] == "<redacted>"
    assert manifest_json["inputs"]["nested"]["safe"] == "value"
    assert "cookie_dump" not in manifest_json["artifacts"]
    assert len(index_rows) == 1
    assert index_rows[0]["run_id"] == "status_preflight_test"


def test_run_index_readers_skip_broken_lines_and_find_latest(tmp_path: Path) -> None:
    index_path = tmp_path / "runs" / "index.jsonl"
    index_path.parent.mkdir(parents=True)
    index_path.write_text(
        "\n".join(
            [
                "{broken json",
                json.dumps(
                    {
                        "run_id": "old",
                        "task": "status-preflight",
                        "status": "ok",
                        "started_at": "2026-06-18T09:00:00",
                    }
                ),
                json.dumps(
                    {
                        "run_id": "new",
                        "task": "status-preflight",
                        "status": "warning",
                        "started_at": "2026-06-18T10:00:00",
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )

    assert [row["run_id"] for row in list_runs(data_dir=tmp_path)] == ["new", "old"]
    assert latest_run(data_dir=tmp_path, task="status-preflight")["run_id"] == "new"
    assert latest_run(data_dir=tmp_path, task="status-preflight", status="ok")["run_id"] == "old"
    assert find_run(data_dir=tmp_path, run_id="old")["status"] == "ok"


def test_manifest_infers_pending_review_lifecycle_for_dry_run() -> None:
    manifest = manifest_from_summary(
        summary={
            "run_id": "ozon_elastic_plan_test",
            "started_at": "2026-06-18T10:00:00",
            "overall_status": "ok",
            "pending_id": "ozon_elastic_plan_test_pending",
            "source_run_id": "source_report",
            "artifacts": {},
        },
        task="ozon-elastic-plan",
        mode="dry_run",
        risk="normal",
        marketplaces=["ozon"],
    )

    assert manifest.lifecycle_status == "pending_review"
    assert manifest.pending_id == "ozon_elastic_plan_test_pending"
    assert manifest.source_run_ids == ["source_report"]
    assert manifest.closed is False


def test_manifest_infers_verified_apply_links() -> None:
    manifest = manifest_from_summary(
        summary={
            "run_id": "wb_promotion_bids_apply_test",
            "started_at": "2026-06-18T11:00:00",
            "overall_status": "ok",
            "approved_plan_run_id": "wb_promotion_bid_plan_test",
            "preflight": {"run_id": "status_preflight_test"},
            "fresh_report": {"run_id": "wb_promotion_report_test"},
            "fresh_plan": {"run_id": "wb_promotion_bid_plan_fresh"},
            "verify": {"status": "ok"},
            "artifacts": {},
        },
        task="wb-promotion-bids-apply",
        mode="apply",
        risk="high",
        marketplaces=["wb"],
    )

    assert manifest.lifecycle_status == "verified"
    assert manifest.approved_id == "wb_promotion_bid_plan_test"
    assert manifest.applied_by_run_id == "wb_promotion_bids_apply_test"
    assert manifest.closed is True
    assert manifest.source_run_ids == [
        "wb_promotion_bid_plan_test",
        "status_preflight_test",
        "wb_promotion_bid_plan_fresh",
        "wb_promotion_report_test",
    ]


def test_write_summary_run_manifest_writes_manifest_and_index(tmp_path: Path) -> None:
    run_dir = tmp_path / "runs" / "2026-06-18" / "catalog_fetch_test"
    summary = {
        "run_id": "catalog_fetch_test",
        "started_at": "2026-06-18T12:00:00",
        "overall_status": "ok",
        "artifacts": {"summary": str(run_dir / "summary.json")},
    }

    paths = write_summary_run_manifest(
        data_dir=tmp_path,
        run_dir=run_dir,
        summary=summary,
        task="catalog-fetch",
        mode="read_only",
        risk="low",
        marketplaces=["ozon", "wb"],
    )

    assert Path(paths["manifest"]).exists()
    assert latest_run(data_dir=tmp_path, task="catalog-fetch")["lifecycle_status"] == "closed"


def test_status_preflight_writes_run_manifest(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "seller_agent.tasks.status_preflight._check_ozon_api",
        lambda credentials: {"status": "ok"},
    )
    monkeypatch.setattr(
        "seller_agent.tasks.status_preflight._check_ozon_performance_api",
        lambda credentials: {"status": "ok"},
    )
    monkeypatch.setattr(
        "seller_agent.tasks.status_preflight._check_wb_api",
        lambda credentials: {"status": "ok"},
    )
    monkeypatch.setattr(
        "seller_agent.tasks.status_preflight._summarize_master_catalog",
        lambda data_dir: {"status": "ok", "rows": 1},
    )
    monkeypatch.setattr(
        "seller_agent.tasks.status_preflight._check_lk_sessions",
        lambda include_lk: {"ozon_cdp": {"status": "skipped"}},
    )

    result = run_status_preflight(
        credentials=AppCredentials(ozon_seller=None, ozon_performance=None, wb=None),
        data_dir=tmp_path,
        run_id="status_preflight_manifest_test",
        include_lk=False,
    )

    manifest_path = Path(result["artifacts"]["run_manifest"])
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    latest = latest_run(data_dir=tmp_path, task="status-preflight")

    assert manifest["run_id"] == "status_preflight_manifest_test"
    assert manifest["task"] == "status-preflight"
    assert manifest["mode"] == "read_only"
    assert manifest["risk"] == "none"
    assert latest["run_id"] == "status_preflight_manifest_test"
