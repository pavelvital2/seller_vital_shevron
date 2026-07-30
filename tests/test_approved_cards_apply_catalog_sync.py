import csv
import json
from pathlib import Path

from seller_agent.tasks import approved_cards_apply
from seller_agent.tasks.approved_cards_apply import (
    _passport_wants_wb_create,
    _passport_wants_ozon_create,
    _sync_approved_card_catalog_layers,
    _verified_skus_from_content_verify,
    run_apply_approved_cards,
    run_plan_approved_cards,
)


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _seed_layers(data_dir: Path) -> None:
    rows = [
        {
            "internal_sku": "old_ozon",
            "master_sku": "old_ozon",
            "ozon_offer_id": "old_ozon",
            "ozon_product_id": "2122666896",
            "wb_vendor_code": "",
            "wb_nm_id": "",
            "wb_barcode": "",
            "marketplace_presence": "ozon_only",
            "match_status": "ozon_only",
            "notes": "not_found_in_wb",
        },
        {
            "internal_sku": "old_wb",
            "master_sku": "old_wb",
            "ozon_offer_id": "",
            "ozon_product_id": "",
            "wb_vendor_code": "old_wb",
            "wb_nm_id": "648292160",
            "wb_barcode": "2047478988209",
            "marketplace_presence": "wb_only",
            "match_status": "wb_only",
            "notes": "not_found_in_ozon",
        },
    ]
    for rel_path in [
        "catalog/processed/master_catalog.csv",
        "catalog/unified/products.csv",
        "catalog/content/content_master.csv",
    ]:
        _write_csv(data_dir / rel_path, rows)
    for rel_path in [
        "catalog/processed/master_catalog.json",
        "catalog/unified/products.json",
        "catalog/content/content_master.json",
    ]:
        _write_json(data_dir / rel_path, rows)


def test_sync_approved_card_catalog_layers_updates_legacy_master_and_removes_wb_duplicate(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    sku = "chev_nr_oborg_pict0003"
    _seed_layers(data_dir)
    _write_json(
        data_dir / "catalog" / "master_passport" / "approved" / f"{sku}.json",
        {
            "identity": {
                "internal_sku": sku,
                "ozon_offer_id": sku,
                "ozon_product_id": "2122666896",
                "wb_vendor_code": sku,
                "wb_nm_id": "648292160",
                "wb_barcode": "",
            },
            "ozon": {
                "offer_id_before_seller_sku_update": "old_ozon",
                "offer_id_after_seller_sku_update": sku,
            },
            "wb": {
                "vendor_code_before_seller_sku_update": "old_wb",
                "vendor_code_after_seller_sku_update": sku,
                "vendor_code": sku,
                "nm_id": "648292160",
            },
        },
    )

    result = _sync_approved_card_catalog_layers(data_dir=data_dir, internal_skus=[sku], run_id="run_1")

    assert result["status"] == "ok"
    assert result["master_catalog_csv"] == {"updated": 1, "removed": 1}
    assert result["master_catalog_json"] == {"updated": 1, "removed": 1}
    for rel_path in [
        "catalog/processed/master_catalog.csv",
        "catalog/unified/products.csv",
        "catalog/content/content_master.csv",
    ]:
        with (data_dir / rel_path).open("r", encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        assert len(rows) == 1
        row = rows[0]
        assert row["internal_sku"] == sku
        assert row["master_sku"] == sku
        assert row["ozon_offer_id"] == sku
        assert row["wb_vendor_code"] == sku
        assert row["wb_barcode"] == "2047478988209"
        assert row["marketplace_presence"] == "ozon_wb"
        assert row["match_status"] == "ozon_wb"
        assert "not_found_in_wb" not in row["notes"]
        assert "not_found_in_ozon" not in row["notes"]
    passport = json.loads(
        (data_dir / "catalog" / "master_passport" / "approved" / f"{sku}.json").read_text(encoding="utf-8")
    )
    assert passport["identity"]["wb_barcode"] == "2047478988209"
    assert passport["approval"]["marketplace_apply"]["catalog_sync_run_id"] == "run_1"


def test_passport_wants_ozon_create_only_with_explicit_owner_approved_action(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    sku = "chev_kit2_pz_ng_text0001"
    passport_path = data_dir / "catalog" / "master_passport" / "approved" / f"{sku}.json"
    passport = {
        "identity": {
            "internal_sku": sku,
            "ozon_offer_id": "",
            "ozon_product_id": "",
            "wb_vendor_code": sku,
            "wb_nm_id": "123456789",
        },
        "safety": {
            "dangerous_actions": [
                "card_content_update",
                "seller_sku_update",
            ]
        },
    }
    _write_json(passport_path, passport)

    assert _passport_wants_ozon_create(data_dir, sku) is False

    passport["safety"]["dangerous_actions"].append("ozon_card_create")
    _write_json(passport_path, passport)

    assert _passport_wants_ozon_create(data_dir, sku) is True

    passport["identity"]["ozon_offer_id"] = sku
    _write_json(passport_path, passport)

    assert _passport_wants_ozon_create(data_dir, sku) is False


def test_passport_wants_wb_create_requires_action_and_numeric_nm_id(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    approved_dir = data_dir / "catalog" / "master_passport" / "approved"
    approved_dir.mkdir(parents=True)
    sku = "chev_nr_svo_pict0001"
    path = approved_dir / f"{sku}.json"
    passport = {
        "identity": {
            "internal_sku": sku,
            "wb_nm_id": "assigned_by_marketplace_after_create",
        },
        "safety": {"dangerous_actions": ["wb_card_create"]},
    }
    path.write_text(json.dumps(passport), encoding="utf-8")

    assert _passport_wants_wb_create(data_dir, sku) is True

    passport["safety"]["dangerous_actions"] = []
    path.write_text(json.dumps(passport), encoding="utf-8")
    assert _passport_wants_wb_create(data_dir, sku) is False

    passport["safety"]["dangerous_actions"] = ["wb_card_create"]
    passport["identity"]["wb_nm_id"] = "607710999"
    path.write_text(json.dumps(passport), encoding="utf-8")
    assert _passport_wants_wb_create(data_dir, sku) is False


def test_verified_skus_from_content_verify_keeps_successful_rows_in_partial_batch(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    for sku in ["sku_ok", "sku_warning"]:
        _write_json(
            data_dir / "catalog" / "master_passport" / "approved" / f"{sku}.json",
            {
                "identity": {
                    "internal_sku": sku,
                    "ozon_offer_id": sku,
                    "ozon_product_id": "123",
                    "wb_vendor_code": sku,
                    "wb_nm_id": "456",
                }
            },
        )
    verify = {
        "verify": {
            "ozon": {
                "results": [
                    {"offer_id": "sku_ok", "status": "ok"},
                    {"offer_id": "sku_warning", "status": "warning"},
                ]
            },
            "wb": {
                "results": [
                    {"vendorCode": "sku_ok", "status": "ok"},
                    {"vendorCode": "sku_warning", "status": "ok"},
                ]
            },
        }
    }

    assert _verified_skus_from_content_verify(
        data_dir=data_dir,
        internal_skus=["sku_ok", "sku_warning"],
        verify=verify,
    ) == ["sku_ok"]


def test_apply_approved_cards_runs_seller_sku_before_content(monkeypatch, tmp_path: Path) -> None:
    calls: list[tuple[str, list[str]]] = []

    monkeypatch.setattr(
        approved_cards_apply,
        "ensure_approved_passports_for_batch",
        lambda **kwargs: {"status": "ok"},
    )

    def fake_seller_sku_stage(**kwargs):
        calls.append(("seller_sku", list(kwargs["internal_skus"])))
        return {"status": "ok", "ready_skus": ["sku_final"], "blocked": [], "plan": {}, "apply": {}}

    def fake_content_stage(**kwargs):
        calls.append(("content", list(kwargs["internal_skus"])))
        return {"status": "ok", "ready_skus": list(kwargs["internal_skus"]), "blocked": [], "plan": {}, "apply": {}}

    def fake_wb_create_stage(**kwargs):
        calls.append(("wb_create", list(kwargs["internal_skus"])))
        return {"status": "skipped", "ready_skus": [], "blocked": [], "plan": None, "apply": None}

    def fake_ozon_create_stage(**kwargs):
        calls.append(("ozon_create", list(kwargs["internal_skus"])))
        return {"status": "skipped", "ready_skus": [], "blocked": [], "plan": None, "apply": None}

    def fake_catalog_sync(**kwargs):
        calls.append(("catalog_sync", list(kwargs["internal_skus"])))
        return {"status": "ok"}

    def fake_post_verify(**kwargs):
        calls.append(("post_verify", list(kwargs["internal_skus"])))
        return {"status": "ok", "ready_skus": list(kwargs["internal_skus"]), "blocked_count": 0}

    monkeypatch.setattr(approved_cards_apply, "_run_seller_sku_stage", fake_seller_sku_stage)
    monkeypatch.setattr(approved_cards_apply, "_run_content_stage", fake_content_stage)
    monkeypatch.setattr(approved_cards_apply, "_run_wb_create_stage", fake_wb_create_stage)
    monkeypatch.setattr(approved_cards_apply, "_run_ozon_create_stage", fake_ozon_create_stage)
    monkeypatch.setattr(approved_cards_apply, "_sync_approved_card_catalog_layers", fake_catalog_sync)
    monkeypatch.setattr(approved_cards_apply, "_run_post_apply_content_verify", fake_post_verify)

    result = run_apply_approved_cards(
        credentials=None,
        data_dir=tmp_path / "data",
        internal_skus=["sku_old"],
        run_id="run_order",
        confirmed_by_user=True,
    )

    assert result["overall_status"] == "ok"
    assert calls == [
        ("seller_sku", ["sku_old"]),
        ("content", ["sku_final"]),
        ("wb_create", ["sku_final"]),
        ("ozon_create", ["sku_final"]),
        ("catalog_sync", ["sku_final"]),
        ("post_verify", ["sku_final"]),
    ]


def test_plan_approved_cards_writes_checksum_and_apply_blocks_after_passport_change(monkeypatch, tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    sku = "chev_test_001"
    _write_json(
        data_dir / "catalog" / "master_passport" / "approved" / f"{sku}.json",
        {
            "identity": {
                "internal_sku": sku,
                "ozon_offer_id": sku,
                "wb_vendor_code": sku,
                "wb_nm_id": "123456789",
            },
            "approval": {"status": "owner_approved"},
        },
    )
    monkeypatch.setattr(
        approved_cards_apply,
        "ensure_approved_passports_for_batch",
        lambda **kwargs: {"status": "ok"},
    )

    def fake_seller_plan(**kwargs):
        plan_path = data_dir / "plans" / f"{kwargs['run_id']}.json"
        _write_json(plan_path, [{"internal_sku": sku, "ready": True}])
        return {"run_id": kwargs["run_id"], "overall_status": "ok", "artifacts": {"seller_sku_update_plan": str(plan_path)}}

    def fake_content_plan(**kwargs):
        plan_path = data_dir / "plans" / f"{kwargs['run_id']}.json"
        _write_json(plan_path, [{"internal_sku": sku, "ready": True}])
        return {"run_id": kwargs["run_id"], "overall_status": "ok", "artifacts": {"plan": str(plan_path)}}

    monkeypatch.setattr(approved_cards_apply, "run_seller_sku_update_plan", fake_seller_plan)
    monkeypatch.setattr(approved_cards_apply, "run_card_content_update_plan", fake_content_plan)

    plan = run_plan_approved_cards(
        credentials=None,
        data_dir=data_dir,
        internal_skus=[sku],
        run_id="plan_approved_cards_test",
        runtime_db=tmp_path / "runtime.db",
    )

    assert plan["overall_status"] == "ok"
    assert plan["plan_checksum"].startswith("sha256:")
    assert plan["runtime_lifecycle"]["status"] == "ok"

    passport_path = data_dir / "catalog" / "master_passport" / "approved" / f"{sku}.json"
    passport = json.loads(passport_path.read_text(encoding="utf-8"))
    passport["content"] = {"title": "changed after plan"}
    _write_json(passport_path, passport)

    result = run_apply_approved_cards(
        credentials=None,
        data_dir=data_dir,
        plan_run_id="plan_approved_cards_test",
        run_id="apply_should_block",
        confirmed_by_user=True,
        runtime_db=tmp_path / "runtime.db",
    )

    assert result["overall_status"] == "blocked"
    assert result["plan_validation"]["errors"][0]["reason"] == "passport_checksum_mismatch"
