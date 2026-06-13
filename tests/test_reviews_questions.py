from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path

from takterra_agent.config import (
    WbCredentials,
    load_ozon_performance_credentials,
    load_ozon_seller_credentials,
    load_wb_credentials,
)
from takterra_agent.marketplaces.wb.communications_adapter import WbCommunicationsAdapter
from takterra_agent.tasks.reviews_questions import (
    _build_report,
    build_actions,
    classify_item,
    draft_review_reply,
    draft_question_reply,
    normalize_wb_feedback,
    normalize_wb_question,
)


def test_missing_wb_token_file_returns_none(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("WB_API_TOKEN", raising=False)
    monkeypatch.setenv("TAKTERRA_WB_TOKEN_FILE", str(tmp_path / "missing-token.txt"))

    assert load_wb_credentials() is None


def test_missing_ozon_token_files_return_none(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("OZON_SELLER_CLIENT_ID", raising=False)
    monkeypatch.delenv("OZON_SELLER_API_KEY", raising=False)
    monkeypatch.delenv("OZON_PERFORMANCE_CLIENT_ID", raising=False)
    monkeypatch.delenv("OZON_PERFORMANCE_CLIENT_SECRET", raising=False)
    monkeypatch.setenv("TAKTERRA_OZON_SELLER_CREDENTIALS_FILE", str(tmp_path / "missing-ozon-seller.txt"))
    monkeypatch.setenv("TAKTERRA_OZON_PERFORMANCE_CREDENTIALS_FILE", str(tmp_path / "missing-ozon-performance.txt"))

    assert load_ozon_seller_credentials() is None
    assert load_ozon_performance_credentials() is None


def test_normalize_wb_feedback_and_draft_positive_empty_review() -> None:
    item = normalize_wb_feedback(
        {
            "id": "feedback-1",
            "text": "",
            "pros": "",
            "cons": "",
            "productValuation": 5,
            "createdDate": "2026-06-11T10:00:00Z",
            "answer": None,
            "productDetails": {
                "nmId": 123,
                "supplierArticle": "chev_test0001",
                "productName": "Шеврон на липучке тестовый",
            },
        }
    )

    assert item["platform"] == "wb"
    assert item["source_type"] == "review"
    assert item["offer_id"] == "chev_test0001"
    assert classify_item(item) == "needs_owner_review_for_public_reply"

    actions = build_actions([{**item, "processing_status": classify_item(item)}])

    assert actions[0]["action_type"] == "public_review_reply"
    assert "шеврон" in actions[0]["draft_text"].lower()
    assert actions[0]["state"] == "pending_owner_confirmation"


def test_normalize_wb_question_quantity_requires_owner_input() -> None:
    item = normalize_wb_question(
        {
            "id": "question-1",
            "text": "Здравствуйте, есть 30 штук?",
            "answer": None,
            "productDetails": {
                "nmId": 123,
                "supplierArticle": "chev_test0001",
                "productName": "Шеврон на липучке тестовый",
            },
        }
    )

    assert classify_item(item) == "needs_stock_check"
    assert draft_question_reply(item) == ""

    actions = build_actions([{**item, "processing_status": classify_item(item)}])

    assert actions[0]["action_type"] == "manual_question_review"
    assert actions[0]["state"] == "needs_owner_input"


def test_wb_token_file_reads_first_line(monkeypatch, tmp_path: Path) -> None:
    token_file = tmp_path / "wb-token.txt"
    token_file.write_text("secret-token\n", encoding="utf-8")
    monkeypatch.delenv("WB_API_TOKEN", raising=False)
    monkeypatch.setenv("TAKTERRA_WB_TOKEN_FILE", str(token_file))

    creds = load_wb_credentials()

    assert creds is not None
    assert creds.token == "secret-token"


def test_review_reply_uses_feminine_product_phrase() -> None:
    reply = draft_review_reply(
        {
            "product_title": "Тканевая петлица на липучке ФСБ",
            "text": "Все отлично",
            "rating": 5,
        }
    )

    assert "петлица вам понравилась и подошла" in reply


def test_review_reply_detects_problem_text_with_four_star_rating() -> None:
    reply = draft_review_reply(
        {
            "product_title": "Шеврон тестовый",
            "text": "Сшито кривовато",
            "rating": 4,
        }
    )

    assert "жаль" in reply.lower()
    assert "проверим" in reply.lower()
    assert "рады" not in reply.lower()


def test_question_about_callsign_requires_manual_context() -> None:
    item = normalize_wb_question(
        {
            "id": "question-callsign",
            "text": "Здравствуйте есть с позывным ПУХ",
            "answer": None,
            "productDetails": {
                "nmId": 123,
                "supplierArticle": "pzkit2mh0007_pzmh0025",
                "productName": "Шеврон на липучке позывной Лис комплект мох",
            },
        }
    )

    assert draft_question_reply(item) == ""


def test_ozon_empty_review_builds_mark_viewed_action() -> None:
    item = {
        "platform": "ozon",
        "source_type": "review",
        "id": "review-1",
        "rating": 5,
        "offer_id": "chev_test0001",
        "sku": "123",
        "product_title": "Шеврон на липучке тестовый",
        "text": "",
        "can_mark_viewed": True,
    }

    actions = build_actions([{**item, "processing_status": classify_item(item)}])

    assert classify_item(item) == "can_mark_viewed_after_owner_confirmation"
    assert actions[0]["action_type"] == "mark_review_viewed"
    assert actions[0]["draft_text"] == ""


def test_report_includes_rating_summary_and_viewed_review_ratings() -> None:
    action = {
        "platform": "ozon",
        "source_type": "review",
        "source_id": "review-1",
        "offer_id": "chev_test0001",
        "sku": "123",
        "rating": 4,
        "product_title": "Шеврон на липучке тестовый",
        "source_text": "",
        "processing_status": "can_mark_viewed_after_owner_confirmation",
        "action_type": "mark_review_viewed",
        "state": "pending_owner_confirmation",
        "risk": "low",
        "draft_text": "",
        "notes": "",
    }

    report = _build_report(
        run_id="reviews_questions_test",
        started_at=datetime(2026, 6, 13, 12, 0, 0),
        items=[],
        actions=[action],
        sources={},
        artifacts={},
    )

    assert "## Оценки" in report
    assert "- `4`: `1`" in report
    assert "## Отзывы без текста к отметке просмотренными" in report
    assert "/ оценка `4`" in report


def test_wb_question_answer_uses_answer_object_payload(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_patch(self, path, payload):  # noqa: ANN001
        captured["path"] = path
        captured["payload"] = payload
        return {"data": None, "error": False}

    monkeypatch.setattr(WbCommunicationsAdapter, "patch", fake_patch)
    adapter = WbCommunicationsAdapter(WbCredentials(token="test-token"))

    adapter.answer_question(question_id="question-1", text="Ответ")

    assert captured["path"] == "/api/v1/questions"
    assert captured["payload"] == {
        "id": "question-1",
        "answer": {"text": "Ответ"},
        "state": "wbRu",
    }
