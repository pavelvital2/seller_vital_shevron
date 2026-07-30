import json
from pathlib import Path

from seller_agent.tasks.card_status_sync import sync_card_apply_status


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def test_sync_card_apply_status_closes_layer2_and_layer3(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    sku = "chev_test_001"
    passport_path = data_dir / "catalog" / "master_passport" / "approved" / f"{sku}.json"
    audit_path = data_dir / "catalog" / "card_audits" / sku / "audit.json"
    _write_json(
        passport_path,
        {
            "identity": {"internal_sku": sku},
            "approval": {
                "status": "owner_approved",
                "marketplace_apply": {
                    "last_verify_run_id": "verify_old",
                    "pending_verify_reason": "old warning",
                },
            },
        },
    )
    _write_json(
        audit_path,
        {
            "identity": {"internal_sku": sku},
            "owner_review": {"status": "owner_approved"},
            "marketplace_apply": {
                "last_verify_run_id": "verify_old",
                "pending_verify_reason": "old warning",
            },
        },
    )

    result = sync_card_apply_status(
        data_dir=data_dir,
        internal_skus=[sku],
        run_id="apply_1",
        summary_path="summary.json",
        report_path="report.md",
        post_verify_run_id="verify_1",
        content_update_run_id="content_1",
        seller_sku_update_run_id="seller_1",
        catalog_sync_run_id="apply_1",
        verified_at="2026-07-08T12:00:00",
    )

    assert result["status"] == "ok"
    passport = json.loads(passport_path.read_text(encoding="utf-8"))
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    assert passport["approval"]["status"] == "owner_approved_applied_verified"
    assert passport["approval"]["marketplace_apply"]["status"] == "applied_verified"
    assert passport["approval"]["marketplace_apply"]["last_verify_run_id"] == "verify_1"
    assert "pending_verify_reason" not in passport["approval"]["marketplace_apply"]
    assert audit["owner_review"]["queue_status"] == "closed_do_not_resubmit"
    assert audit["marketplace_apply"]["post_verify_run_id"] == "verify_1"
    assert audit["marketplace_apply"]["last_verify_run_id"] == "verify_1"
    assert "pending_verify_reason" not in audit["marketplace_apply"]
    snapshot = json.loads((data_dir / "catalog" / "card_status" / "latest.json").read_text(encoding="utf-8"))
    assert snapshot["last_sync"]["updated_passports"] == 1
