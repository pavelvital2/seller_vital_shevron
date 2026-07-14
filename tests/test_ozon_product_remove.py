import json
from pathlib import Path

from seller_agent.config import AppCredentials, OzonSellerCredentials
from seller_agent.tasks.ozon_product_remove import (
    run_ozon_product_remove_apply,
    run_ozon_product_remove_plan,
    run_ozon_product_remove_verify,
)
from seller_agent.tasks.registry import get_task_definition


def _credentials() -> AppCredentials:
    return AppCredentials(OzonSellerCredentials("id", "key"), None, None)


def _not_created_info() -> dict:
    return {
        "id": 5330427391,
        "offer_id": "chev_pz_ng_text0074",
        "name": "Шеврон на липучке позывной Сталкер нагрудный олива",
        "sku": 0,
        "is_archived": False,
        "stocks": {"has_stock": False, "stocks": []},
        "statuses": {
            "is_created": False,
            "status_description": "Не создан",
            "status_failed": "imported",
            "status_tooltip": "Не прошел валидацию",
        },
        "errors": [{"code": "FB_UNWANTED"}],
    }


def test_ozon_product_remove_plan_selects_delete_for_not_created_no_sku(tmp_path: Path, monkeypatch) -> None:
    class FakeOzon:
        def __init__(self, credentials):
            self.credentials = credentials

        def fetch_product_info(self, product_ids):
            return [_not_created_info()]

        def fetch_product_attributes(self, offer_ids):
            return []

    monkeypatch.setattr("seller_agent.tasks.ozon_product_remove.OzonSellerAdapter", FakeOzon)

    result = run_ozon_product_remove_plan(
        credentials=_credentials(),
        data_dir=tmp_path,
        offer_id="chev_pz_ng_text0074",
        product_id="5330427391",
        reason="owner approved FB_UNWANTED cleanup",
    )

    assert result["overall_status"] == "ok"
    assert result["action"] == "delete"
    assert result["ready"] is True
    assert "ozon_not_created_or_without_sku_delete_instead_of_archive" in result["warnings"]


def test_ozon_product_remove_plan_blocks_archive_with_stock(tmp_path: Path, monkeypatch) -> None:
    info = {
        "id": 1,
        "offer_id": "created",
        "sku": 123,
        "is_archived": False,
        "stocks": {"has_stock": True},
        "statuses": {"is_created": True},
    }

    class FakeOzon:
        def __init__(self, credentials):
            self.credentials = credentials

        def fetch_product_info(self, product_ids):
            return [info]

        def fetch_product_attributes(self, offer_ids):
            return []

    monkeypatch.setattr("seller_agent.tasks.ozon_product_remove.OzonSellerAdapter", FakeOzon)

    result = run_ozon_product_remove_plan(
        credentials=_credentials(),
        data_dir=tmp_path,
        offer_id="created",
        product_id="1",
        action="archive",
    )

    assert result["overall_status"] == "blocked"
    assert result["action"] == "archive"
    assert "ozon_archive_requires_zero_stock" in result["errors"]


def test_ozon_product_remove_apply_deletes_and_verifies(tmp_path: Path, monkeypatch) -> None:
    class FakeOzonPlan:
        def __init__(self, credentials):
            self.credentials = credentials

        def fetch_product_info(self, product_ids):
            return [_not_created_info()]

        def fetch_product_attributes(self, offer_ids):
            return []

    monkeypatch.setattr("seller_agent.tasks.ozon_product_remove.OzonSellerAdapter", FakeOzonPlan)
    plan = run_ozon_product_remove_plan(
        credentials=_credentials(),
        data_dir=tmp_path,
        offer_id="chev_pz_ng_text0074",
        product_id="5330427391",
    )

    class FakeOzonApply:
        def __init__(self, credentials):
            self.credentials = credentials

        def delete_products(self, offer_ids):
            return {"status": [{"offer_id": offer_ids[0], "is_deleted": True, "error": ""}]}

        def archive_products(self, product_ids):
            raise AssertionError("archive should not be called")

        def fetch_product_info(self, product_ids):
            info = _not_created_info()
            info["is_archived"] = True
            return [info]

    monkeypatch.setattr("seller_agent.tasks.ozon_product_remove.OzonSellerAdapter", FakeOzonApply)
    result = run_ozon_product_remove_apply(
        credentials=_credentials(),
        data_dir=tmp_path,
        plan_run_id=plan["run_id"],
        confirmed_by_user=True,
    )

    assert result["overall_status"] == "ok"
    assert result["action"] == "delete"
    assert result["verified"] is True


def test_ozon_product_remove_verify_checks_delete_state_without_write(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    plan_dir = tmp_path / "runs" / "2026-07-14" / "ozon_product_remove_plan_test"
    plan_dir.mkdir(parents=True)
    (plan_dir / "ozon_product_remove_plan.json").write_text(
        json.dumps(
            {
                "ready": True,
                "action": "delete",
                "offer_id": "chev_test_0001",
                "product_id": "123",
            }
        ),
        encoding="utf-8",
    )

    class FakeOzon:
        def __init__(self, credentials):
            self.credentials = credentials

        def fetch_product_info(self, product_ids):
            return []

        def fetch_product_attributes(self, offer_ids):
            return []

        def delete_products(self, offer_ids):
            raise AssertionError("verify must not delete products")

        def archive_products(self, product_ids):
            raise AssertionError("verify must not archive products")

    monkeypatch.setattr("seller_agent.tasks.ozon_product_remove.OzonSellerAdapter", FakeOzon)

    result = run_ozon_product_remove_verify(
        credentials=_credentials(),
        data_dir=tmp_path,
        plan_run_id="ozon_product_remove_plan_test",
        run_id="ozon_product_remove_verify_test",
    )

    assert result["overall_status"] == "ok"
    assert result["verified"] is True


def test_ozon_product_remove_tasks_registered() -> None:
    plan = get_task_definition("plan-ozon-product-remove")
    apply = get_task_definition("apply-ozon-product-remove")
    verify = get_task_definition("verify-ozon-product-remove")

    assert plan["mode"] == "dry_run"
    assert apply["mode"] == "apply"
    assert apply["requires_confirmation"] is True
    assert apply["verify_task"] == "ozon-product-remove-verify"
    assert verify["mode"] == "verify"
