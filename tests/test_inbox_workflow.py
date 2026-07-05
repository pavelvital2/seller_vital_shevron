from __future__ import annotations

import json
from pathlib import Path

from seller_agent.config import AppCredentials, OzonSellerCredentials
from seller_agent.tasks import inbox_workflow
from seller_agent.tasks.inbox_workflow import (
    _apply_ozon_messenger,
    _build_ozon_report,
    _build_wb_report,
    _collect_ozon_messenger_actions,
)


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


def test_ozon_inbox_report_includes_customer_questions(tmp_path: Path) -> None:
    actions_path = tmp_path / "actions.json"
    actions_path.write_text(
        json.dumps(
            {
                "actions": [
                    {
                        "platform": "ozon",
                        "source_type": "question",
                        "action_type": "manual_question_review",
                        "offer_id": "chev_test_0001",
                        "product_title": "Шеврон тестовый",
                        "source_text": "Можно другой позывной?",
                        "draft_text": "",
                    },
                    {
                        "platform": "ozon",
                        "source_type": "question",
                        "action_type": "question_answer",
                        "offer_id": "chev_test_0002",
                        "product_title": "Шеврон на липучке",
                        "source_text": "Есть липучка?",
                        "draft_text": "Здравствуйте! Да, товар на липучке.",
                    },
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    report = _build_ozon_report(
        run_id="ozon_inbox_test",
        reviews_summary={"actions_count": 2, "artifacts": {"actions": str(actions_path)}},
        messenger_summary={"actions": []},
        artifacts={},
    )

    assert "## Вопросы покупателей" in report
    assert "вопросы покупателей: `2`" in report
    assert "автоответы на вопросы: `1`" in report
    assert "вопросы на ручную проверку: `1`" in report
    assert "Можно другой позывной?" in report
    assert "Здравствуйте! Да, товар на липучке." in report


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


def test_ozon_messenger_collection_paginates_chat_list(
    monkeypatch,
    tmp_path: Path,
) -> None:
    class FakeOzonAdapter:
        def __init__(self, credentials: OzonSellerCredentials) -> None:
            self.credentials = credentials
            self.chat_list_calls: list[dict] = []

        def post(self, path: str, payload: dict) -> dict:
            if path == "/v3/chat/list":
                self.chat_list_calls.append(payload)
                if "cursor" not in payload:
                    return {
                        "chats": [
                            {
                                "chat": {"chat_id": "chat-page-1"},
                                "unread_count": 0,
                            }
                        ],
                        "cursor": "cursor-1",
                        "has_next": True,
                        "total_unread_count": 1,
                    }
                assert payload["cursor"] == "cursor-1"
                return {
                    "chats": [
                        {
                            "chat": {"chat_id": "chat-page-2"},
                            "unread_count": 1,
                        }
                    ],
                    "cursor": "",
                    "has_next": False,
                    "total_unread_count": 1,
                }
            if path == "/v3/chat/history":
                if payload["chat_id"] == "chat-page-1":
                    return {"messages": [{"message_id": "1", "is_read": True, "user": {"type": "NotificationUser"}, "data": ["read"]}]}
                return {
                    "messages": [
                        {
                            "message_id": "2",
                            "is_read": False,
                            "user": {"type": "NotificationUser"},
                            "data": ["Новые характеристики товаров"],
                        }
                    ]
                }
            raise AssertionError(path)

    monkeypatch.setattr(inbox_workflow, "OzonSellerAdapter", FakeOzonAdapter)

    result = _collect_ozon_messenger_actions(
        credentials=AppCredentials(
            ozon_seller=OzonSellerCredentials(client_id="client", api_key="key"),
            ozon_performance=None,
            wb=None,
        ),
        run_dir=tmp_path / "run",
        limit=300,
    )

    assert result["status"] == "ok"
    assert result["pages_checked"] == 2
    assert result["chats_checked"] == 2
    assert result["histories_checked"] == 2
    assert len(result["actions"]) == 1
    assert result["actions"][0]["chat_id"] == "chat-page-2"
    assert result["actions"][0]["action_type"] == "mark_chat_read"
