import pytest

from seller_agent.config import AppCredentials, WbCredentials
from seller_agent.tasks.wb_actions_discount_apply import (
    _assert_no_drift,
    _build_partial_drift_payload,
    _classify_verify_status,
    _latest_history_data,
    _payload_from_rows,
    _requires_staged_discount,
    _split_regular_and_staged_rows,
    run_wb_actions_discount_apply,
)


def test_payload_from_rows_includes_only_changed_discounts() -> None:
    payload, changed_rows = _payload_from_rows(
        [
            {
                "Артикул WB": "101",
                "Базовая цена": "1100",
                "Финальная скидка": "55",
                "Дельта, п.п.": "-10",
            },
            {
                "Артикул WB": "102",
                "Базовая цена": "2000",
                "Финальная скидка": "68",
                "Дельта, п.п.": "0",
            },
        ]
    )

    assert changed_rows == [
        {
            "Артикул WB": "101",
            "Базовая цена": "1100",
            "Финальная скидка": "55",
            "Дельта, п.п.": "-10",
        }
    ]
    assert payload == {"data": [{"nmID": 101, "price": 1100, "discount": 55}]}


def test_assert_no_drift_compares_nm_price_discount_signature() -> None:
    approved = {"data": [{"nmID": 101, "price": 1100, "discount": 55}]}
    fresh = {"data": [{"nmID": 101, "price": 1100, "discount": 55}]}

    assert _assert_no_drift(approved_payload=approved, fresh_payload=fresh) == {
        "approved_payload_rows": 1,
        "fresh_payload_rows": 1,
        "added": [],
        "removed": [],
    }


def test_assert_no_drift_raises_on_changed_discount() -> None:
    approved = {"data": [{"nmID": 101, "price": 1100, "discount": 55}]}
    fresh = {"data": [{"nmID": 101, "price": 1100, "discount": 56}]}

    with pytest.raises(RuntimeError, match="drift-check failed"):
        _assert_no_drift(approved_payload=approved, fresh_payload=fresh)


def test_build_partial_drift_payload_uploads_only_unchanged_rows() -> None:
    approved = {
        "data": [
            {"nmID": 101, "price": 1100, "discount": 55},
            {"nmID": 102, "price": 1200, "discount": 55},
        ]
    }
    fresh = {
        "data": [
            {"nmID": 101, "price": 1100, "discount": 55},
            {"nmID": 102, "price": 1200, "discount": 56},
        ]
    }

    drift, upload_payload = _build_partial_drift_payload(
        approved_payload=approved,
        fresh_payload=fresh,
    )

    assert upload_payload == {"data": [{"nmID": 101, "price": 1100, "discount": 55}]}
    assert drift["mode"] == "partial_apply_unchanged_rows"
    assert drift["skipped_due_to_drift_nm_ids"] == [102]
    assert drift["skipped_due_to_drift_product_count"] == 1
    assert drift["skipped_due_to_drift_count"] == 2


def test_latest_history_data_returns_last_non_empty_poll() -> None:
    polls = [
        {"status": {"history": {"data": {"data": {}}}}},
        {
            "status": {
                "history": {
                    "data": {
                        "data": {
                            "uploadID": 1,
                            "overAllGoodsNumber": 302,
                            "successGoodsNumber": 302,
                        }
                    }
                }
            }
        },
    ]

    assert _latest_history_data(polls) == {
        "uploadID": 1,
        "overAllGoodsNumber": 302,
        "successGoodsNumber": 302,
    }


def test_staged_discount_detects_more_than_double_price_drop() -> None:
    row = {
        "Артикул WB": "101",
        "Базовая цена": "1100",
        "Текущая скидка": "0",
        "Финальная скидка": "55",
        "Дельта, п.п.": "55",
    }

    assert _requires_staged_discount(row) is True


def test_split_regular_and_staged_rows_keeps_target_payload_approval() -> None:
    fresh_rows = [
        {
            "Артикул WB": "101",
            "Базовая цена": "1100",
            "Текущая скидка": "0",
            "Финальная скидка": "55",
            "Дельта, п.п.": "55",
        },
        {
            "Артикул WB": "102",
            "Базовая цена": "1200",
            "Текущая скидка": "70",
            "Финальная скидка": "55",
            "Дельта, п.п.": "-15",
        },
    ]
    eligible_payload = {
        "data": [
            {"nmID": 101, "price": 1100, "discount": 55},
            {"nmID": 102, "price": 1200, "discount": 55},
        ]
    }

    regular_rows, staged_rows = _split_regular_and_staged_rows(
        eligible_payload=eligible_payload,
        fresh_changed_rows=fresh_rows,
    )

    assert [row["Артикул WB"] for row in staged_rows] == ["101"]
    assert [row["Артикул WB"] for row in regular_rows] == ["102"]


def test_payload_from_rows_uses_limited_upload_discount() -> None:
    payload, changed_rows = _payload_from_rows(
        [
            {
                "Артикул WB": "101",
                "Базовая цена": "1100",
                "Текущая скидка": "0",
                "Финальная скидка": "55",
                "Дельта, п.п.": "55",
                "Скидка к загрузке": "35",
                "Дельта загрузки, п.п.": "35",
            }
        ]
    )

    assert len(changed_rows) == 1
    assert payload == {"data": [{"nmID": 101, "price": 1100, "discount": 35}]}


def test_split_regular_and_staged_rows_does_not_quarantine_limited_step() -> None:
    fresh_rows = [
        {
            "Артикул WB": "101",
            "Базовая цена": "1100",
            "Текущая скидка": "0",
            "Финальная скидка": "55",
            "Дельта, п.п.": "55",
            "Скидка к загрузке": "35",
            "Дельта загрузки, п.п.": "35",
        }
    ]
    eligible_payload = {"data": [{"nmID": 101, "price": 1100, "discount": 35}]}

    regular_rows, staged_rows = _split_regular_and_staged_rows(
        eligible_payload=eligible_payload,
        fresh_changed_rows=fresh_rows,
    )

    assert [row["Артикул WB"] for row in regular_rows] == ["101"]
    assert staged_rows == []


def test_classify_verify_status_detects_price_quarantine() -> None:
    assert (
        _classify_verify_status(
            upload_ok=True,
            expected_rows=64,
            success_rows=0,
            overall_rows=64,
            error_summary={"price_quarantine_rows_count": 64},
        )
        == "price_quarantine"
    )


def test_wb_actions_apply_uses_wb_scoped_api_preflight(tmp_path, monkeypatch) -> None:
    plan_dir = tmp_path / "runs" / "2026-07-04" / "wb_actions_discount_plan_70-55-55_test"
    plan_dir.mkdir(parents=True)
    csv_path = plan_dir / "wb-discount-calculation-active-actions-70-55-55.csv"
    csv_path.write_text(
        "Артикул WB;Базовая цена;Финальная скидка;Дельта, п.п.\n"
        "101;1100;55;0\n",
        encoding="utf-8-sig",
    )
    (plan_dir / "summary.json").write_text(
        (
            '{"run_id":"wb_actions_discount_plan_70-55-55_test",'
            '"summary":{"scheme":"70-55-55"},'
            f'"artifacts":{{"csv":"{csv_path}"}}}}'
        ),
        encoding="utf-8",
    )
    calls = {}

    def fake_preflight(**kwargs):
        calls["preflight"] = kwargs
        return {
            "run_id": "status_preflight_wb_api_only",
            "overall_status": "ok",
            "artifacts": {},
        }

    def fake_fresh_plan(**kwargs):
        calls["fresh_plan"] = kwargs
        return {
            "run_id": "wb_actions_discount_plan_70-55-55_fresh",
            "summary": {"scheme": "70-55-55"},
            "artifacts": {"csv": str(csv_path)},
        }

    monkeypatch.setattr("seller_agent.tasks.wb_actions_discount_apply.run_status_preflight", fake_preflight)
    monkeypatch.setattr("seller_agent.tasks.wb_actions_discount_apply.run_wb_actions_discount_plan", fake_fresh_plan)

    result = run_wb_actions_discount_apply(
        credentials=AppCredentials(ozon_seller=None, ozon_performance=None, wb=WbCredentials(token="token")),
        data_dir=tmp_path,
        plan_run_id="wb_actions_discount_plan_70-55-55_test",
        run_id="wb_actions_discount_apply_70-55-55_test",
        confirmed_by_user=True,
    )

    assert result["preflight"]["run_id"] == "status_preflight_wb_api_only"
    assert calls["preflight"]["include_lk"] is False
    assert calls["preflight"]["marketplaces"] == ("wb",)
    assert calls["preflight"]["include_ozon_performance"] is False
    assert calls["fresh_plan"]["scheme_text"] == "70-55-55"


def test_wb_actions_apply_stages_zero_to_fifty_five_discount(tmp_path, monkeypatch) -> None:
    plan_dir = tmp_path / "runs" / "2026-07-05" / "wb_actions_discount_plan_70-55-55_test"
    plan_dir.mkdir(parents=True)
    csv_path = plan_dir / "wb-discount-calculation-active-actions-70-55-55.csv"
    csv_path.write_text(
        "Артикул WB;Базовая цена;Текущая скидка;Финальная скидка;Дельта, п.п.\n"
        "101;1100;0;55;55\n",
        encoding="utf-8-sig",
    )
    (plan_dir / "summary.json").write_text(
        (
            '{"run_id":"wb_actions_discount_plan_70-55-55_test",'
            '"summary":{"scheme":"70-55-55"},'
            f'"artifacts":{{"csv":"{csv_path}"}}}}'
        ),
        encoding="utf-8",
    )

    def fake_preflight(**kwargs):
        return {"run_id": "status_preflight_wb_api_only", "overall_status": "ok", "artifacts": {}}

    def fake_fresh_plan(**kwargs):
        return {
            "run_id": "wb_actions_discount_plan_70-55-55_fresh",
            "summary": {"scheme": "70-55-55"},
            "artifacts": {"csv": str(csv_path)},
        }

    upload_calls = []

    def fake_upload_and_verify(*, payload, token, raw_dir, label, poll_attempts=12):
        upload_calls.append((label, payload))
        expected = len(payload.get("data") or [])
        success = expected if label == "staged_stage55" else 0
        return {
            "label": label,
            "payload_rows_count": expected,
            "response": {"httpStatus": 200, "data": {"data": {"id": 100 + len(upload_calls)}}},
            "upload_id": 100 + len(upload_calls),
            "polls": [],
            "details": None,
            "upload_ok": expected > 0,
            "status_data": {"successGoodsNumber": success, "overAllGoodsNumber": expected},
            "verify_status": "ok" if success == expected and expected else "no_rows_to_apply",
            "success_rows": success,
            "overall_rows": expected,
            "failed_rows": expected - success,
            "error_summary": {"failed_rows_count": 0, "price_quarantine_rows_count": 0},
        }

    def fake_apply_new_price(**kwargs):
        return {
            "status": "ok",
            "matched_targets": [{"nmID": 101, "price": 1100, "discount": 49, "internal_id": 1}],
            "missing_targets": [],
        }

    price_call = {"count": 0}

    def fake_current_prices(credentials, nm_ids):
        price_call["count"] += 1
        discount = 49 if price_call["count"] == 1 else 55
        return {101: {"nmID": 101, "discount": discount}}

    monkeypatch.setattr("seller_agent.tasks.wb_actions_discount_apply.run_status_preflight", fake_preflight)
    monkeypatch.setattr("seller_agent.tasks.wb_actions_discount_apply.run_wb_actions_discount_plan", fake_fresh_plan)
    monkeypatch.setattr("seller_agent.tasks.wb_actions_discount_apply._wb_upload_and_verify", fake_upload_and_verify)
    monkeypatch.setattr("seller_agent.tasks.wb_actions_discount_apply._run_wb_quarantine_apply_new_price", fake_apply_new_price)
    monkeypatch.setattr("seller_agent.tasks.wb_actions_discount_apply._fetch_current_price_rows", fake_current_prices)

    result = run_wb_actions_discount_apply(
        credentials=AppCredentials(ozon_seller=None, ozon_performance=None, wb=WbCredentials(token="token")),
        data_dir=tmp_path,
        plan_run_id="wb_actions_discount_plan_70-55-55_test",
        run_id="wb_actions_discount_apply_70-55-55_staged_test",
        confirmed_by_user=True,
    )

    assert result["overall_status"] == "ok"
    assert result["applied"]["regular_payload_rows_count"] == 0
    assert result["applied"]["staged_payload_rows_count"] == 1
    assert result["applied"]["staged"]["status"] == "ok"
    assert result["applied"]["staged"]["stage49"]["payload_rows_count"] == 1
    assert result["applied"]["staged"]["stage55"]["payload_rows_count"] == 1
    assert result["verify"]["success_rows"] == 1
    assert upload_calls[0] == ("regular", {"data": []})
    assert upload_calls[1] == ("staged_stage49", {"data": [{"nmID": 101, "price": 1100, "discount": 49}]})
    assert upload_calls[2] == ("staged_stage55", {"data": [{"nmID": 101, "price": 1100, "discount": 55}]})
