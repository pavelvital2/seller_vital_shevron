from __future__ import annotations

import json
from pathlib import Path

from seller_agent.config import AppCredentials, OzonSellerCredentials
from seller_agent.tasks import inbox_workflow
from seller_agent.tasks.inbox_workflow import _apply_ozon_messenger, _build_wb_report


def test_wb_inbox_report_includes_product_rating_rows(tmp_path: Path) -> None:
    actions_path = tmp_path / "actions.json"
    actions_path.write_text(
        json.dumps(
            {
                "actions": [
                    {
                        "platform": "wb",
                        "source_type": "review",
                        "action_type": "public_review_reply",
                        "offer_id": "chev_test_0001",
                        "sku": "123",
                        "rating": 2,
                        "product_title": "Шеврон тестовый",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    report = _build_wb_report(
        run_id="wb_inbox_test",
        reviews_summary={"actions_count": 1, "artifacts": {"actions": str(actions_path)}},
        wb_notifications={
            "status": "ok",
            "source": "WB LK news-v2 read-only",
            "items": [{"title": "Новость WB", "href": "https://seller.wildberries.ru/news-v2"}],
            "important_items": [{"title": "Изменение тарифов", "href": "https://seller.wildberries.ru/news-v2/news-details?id=1"}],
        },
        artifacts={},
    )

    assert "## Оценки по конкретным товарам" in report
    assert "`chev_test_0001` / оценка `2`" in report
    assert "Шеврон тестовый" in report
    assert "низких оценок 1-3 по товарам: `1`" in report
    assert "важных WB новостей/уведомлений: `1`" in report


def test_ozon_messenger_apply_mark_read_only_does_not_run_send_helper(
    monkeypatch,
    tmp_path: Path,
) -> None:
    class FakeOzonAdapter:
        def __init__(self, credentials: OzonSellerCredentials) -> None:
            self.credentials = credentials

        def post(self, path: str, payload: dict) -> dict:
            assert path == "/v2/chat/read"
            assert payload == {"chat_id": "chat-1", "from_message_id": "100"}
            return {"unread_count": 0}

    def fail_subprocess_run(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("send helper must not run when there are no send_chat_message actions")

    monkeypatch.setattr(inbox_workflow, "OzonSellerAdapter", FakeOzonAdapter)
    monkeypatch.setattr(inbox_workflow.subprocess, "run", fail_subprocess_run)

    result = _apply_ozon_messenger(
        credentials=AppCredentials(
            ozon_seller=OzonSellerCredentials(client_id="client", api_key="key"),
            ozon_performance=None,
            wb=None,
        ),
        data_dir=tmp_path,
        source_run_id="ozon_inbox_test",
        actions=[
            {
                "action_type": "mark_chat_read",
                "chat_id": "chat-1",
                "from_message_id": "100",
            }
        ],
        run_dir=tmp_path / "runs" / "ozon_inbox_test_apply",
    )

    assert result["status"] == "ok"
    assert result["send"] == {
        "ok": True,
        "skipped": True,
        "reason": "no approved send_chat_message actions",
    }
    assert result["mark_read"] == [
        {
            "chat_id": "chat-1",
            "from_message_id": "100",
            "ok": True,
            "error": "",
            "response": {"unread_count": 0},
        }
    ]
