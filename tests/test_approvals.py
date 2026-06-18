from __future__ import annotations

import json
from pathlib import Path

import pytest

from takterra_agent.core.run_manifest import manifest_from_summary, write_run_manifest
from takterra_agent.safety.approvals import (
    apply_marker_for,
    assert_apply_not_repeated,
    canonical_checksum,
    mark_approved_applied,
)


def test_canonical_checksum_is_stable_for_key_order() -> None:
    assert canonical_checksum({"b": 2, "a": 1}) == canonical_checksum({"a": 1, "b": 2})


def test_apply_marker_blocks_repeated_apply(tmp_path: Path) -> None:
    marker = mark_approved_applied(
        data_dir=tmp_path,
        approved_id="approved-plan-1",
        apply_run_id="apply-run-1",
        task="wb-actions-discount-apply",
        status="ok",
        run_manifest_path="data/runs/2026-06-18/apply-run-1/manifest.json",
        checksum="abc",
    )

    assert Path(marker["path"]) == apply_marker_for(data_dir=tmp_path, approved_id="approved-plan-1")
    with pytest.raises(RuntimeError, match="already applied"):
        assert_apply_not_repeated(data_dir=tmp_path, approved_id="approved-plan-1")


def test_run_index_blocks_repeated_apply_without_marker(tmp_path: Path) -> None:
    run_dir = tmp_path / "runs" / "2026-06-18" / "apply-run-1"
    manifest = manifest_from_summary(
        summary={
            "run_id": "apply-run-1",
            "started_at": "2026-06-18T10:00:00",
            "overall_status": "ok",
            "approved_id": "approved-plan-1",
            "verify": {"status": "ok"},
            "artifacts": {},
        },
        task="ozon-elastic-apply",
        mode="apply",
        risk="high",
        marketplaces=["ozon"],
    )
    write_run_manifest(data_dir=tmp_path, run_dir=run_dir, manifest=manifest)

    with pytest.raises(RuntimeError, match="apply-run-1"):
        assert_apply_not_repeated(data_dir=tmp_path, approved_id="approved-plan-1")


def test_marker_file_contains_no_secret_like_runtime_payload(tmp_path: Path) -> None:
    marker = mark_approved_applied(
        data_dir=tmp_path,
        approved_id="approved-plan-2",
        apply_run_id="apply-run-2",
        task="reviews-questions-apply",
        status="ok",
    )

    data = json.loads(Path(marker["path"]).read_text(encoding="utf-8"))
    assert data["approved_id"] == "approved-plan-2"
    assert data["apply_run_id"] == "apply-run-2"
    assert "token" not in json.dumps(data, ensure_ascii=False).lower()
