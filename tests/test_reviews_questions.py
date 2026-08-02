from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path

import pytest

from seller_agent.config import (
    AppCredentials,
    WbCredentials,
    load_ozon_performance_credentials,
    load_ozon_seller_credentials,
    load_wb_credentials,
)
from seller_agent.marketplaces.wb.communications_adapter import WbCommunicationsAdapter
from seller_agent.tasks import reviews_questions
from seller_agent.tasks.reviews_questions import (
    _approved_actions,
    _build_report,
    build_actions,
    classify_item,
    draft_review_reply,
    draft_question_reply,
    normalize_wb_feedback,
    normalize_wb_question,
    run_reviews_questions_apply,
    run_reviews_questions_prepare_approved,
    run_reviews_questions_verify,
)


def test_missing_wb_token_file_returns_none(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("WB_API_TOKEN", raising=False)
    monkeypatch.setenv("VITAL_SHEVRON_WB_TOKEN_FILE", str(tmp_path / "missing-token.txt"))

    assert load_wb_credentials() is None


def test_missing_ozon_token_files_return_none(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("OZON_SELLER_CLIENT_ID", raising=False)
    monkeypatch.delenv("OZON_SELLER_API_KEY", raising=False)
    monkeypatch.delenv("OZON_PERFORMANCE_CLIENT_ID", raising=False)
    monkeypatch.delenv("OZON_PERFORMANCE_CLIENT_SECRET", raising=False)
    monkeypatch.setenv(
        "VITAL_SHEVRON_OZON_SELLER_CREDENTIALS_FILE",
        str(tmp_path / "missing-ozon-seller.txt"),
    )
    monkeypatch.setenv(
        "VITAL_SHEVRON_OZON_PERFORMANCE_CREDENTIALS_FILE",
        str(tmp_path / "missing-ozon-performance.txt"),
    )

    assert load_ozon_seller_credentials() is None
    assert load_ozon_performance_credentials() is None


def test_takterra_ozon_token_files_are_not_used_as_fallback(monkeypatch, tmp_path: Path) -> None:
    seller_file = tmp_path / "ozon-seller.txt"
    performance_file = tmp_path / "ozon-performance.txt"
    seller_file.write_text("wrong-client\nwrong-api-key\n", encoding="utf-8")
    performance_file.write_text("wrong-client\nwrong-secret\n", encoding="utf-8")
    monkeypatch.delenv("OZON_SELLER_CLIENT_ID", raising=False)
    monkeypatch.delenv("OZON_SELLER_API_KEY", raising=False)
    monkeypatch.delenv("OZON_PERFORMANCE_CLIENT_ID", raising=False)
    monkeypatch.delenv("OZON_PERFORMANCE_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("VITAL_SHEVRON_OZON_SELLER_CREDENTIALS_FILE", raising=False)
    monkeypatch.delenv("VITAL_SHEVRON_OZON_PERFORMANCE_CREDENTIALS_FILE", raising=False)
    monkeypatch.delenv("SELLER_OZON_SELLER_CREDENTIALS_FILE", raising=False)
    monkeypatch.delenv("SELLER_OZON_PERFORMANCE_CREDENTIALS_FILE", raising=False)
    monkeypatch.setenv("TAKTERRA_OZON_SELLER_CREDENTIALS_FILE", str(seller_file))
    monkeypatch.setenv("TAKTERRA_OZON_PERFORMANCE_CREDENTIALS_FILE", str(performance_file))

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


def test_normalize_wb_feedback_detects_photo_links_and_single_video() -> None:
    item = normalize_wb_feedback(
        {
            "id": "feedback-media",
            "text": "",
            "productValuation": 5,
            "answer": None,
            "photoLinks": ["https://example.test/review-photo.jpg"],
            "video": {"link": "https://example.test/review-video.mp4"},
            "productDetails": {
                "nmId": 123,
                "supplierArticle": "chev_test0001",
                "productName": "Шеврон на липучке тестовый",
            },
        }
    )

    assert item["has_media"] is True
    assert item["photos_count"] == 1
    assert item["videos_count"] == 1
    assert item["media_urls"] == [
        "https://example.test/review-photo.jpg",
        "https://example.test/review-video.mp4",
    ]
    assert classify_item(item) == "needs_media_review_for_public_reply"


def test_normalize_wb_feedback_prefers_full_size_photo_link_object() -> None:
    item = normalize_wb_feedback(
        {
            "id": "feedback-photo-object",
            "text": "Цвет отличается",
            "productValuation": 2,
            "answer": None,
            "photoLinks": [
                {
                    "fullSize": "https://example.test/review-photo-full.webp",
                    "miniSize": "https://example.test/review-photo-mini.webp",
                }
            ],
            "productDetails": {
                "nmId": 123,
                "supplierArticle": "chev_test0001",
                "productName": "Шеврон на липучке тестовый",
            },
        }
    )

    assert item["photos_count"] == 1
    assert item["media_urls"] == ["https://example.test/review-photo-full.webp"]


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


def test_question_about_attachment_side_does_not_get_generic_velcro_answer() -> None:
    reply = draft_question_reply(
        {
            "product_title": "Шеврон на липучке нагрудный",
            "text": "На какую сторону цепляется: левую или правую?",
        }
    )

    assert "с любой стороны" in reply
    assert "если это указано" not in reply


def test_legal_usage_question_has_cautious_answer() -> None:
    reply = draft_question_reply(
        {
            "product_title": "Шеврон на липучке Прокуратура",
            "text": "Что будет, если я буду носить шеврон прокуратура на рюкзаке?",
        }
    )

    assert "не консультируем по правовым последствиям" in reply
    assert "требования законодательства" in reply


def test_question_size_uses_approved_passport_context(
    monkeypatch,
    tmp_path: Path,
) -> None:
    approved_dir = (
        tmp_path / "data" / "catalog" / "master_passport" / "approved"
    )
    approved_dir.mkdir(parents=True)
    (approved_dir / "chev_kit2_pz_text0006.json").write_text(
        json.dumps(
            {"physical": {"product_size_mm": "125*25 мм; 80*50 мм"}},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(reviews_questions, "PROJECT_ROOT", tmp_path)

    reply = draft_question_reply(
        {
            "offer_id": "chev_kit2_pz_text0006",
            "sku": "605329721",
            "product_title": "Шевроны на липучке позывной Малой, комплект 2 шт., мох",
            "text": "Какой размер у позывного?",
        }
    )

    assert "125*25 мм" in reply
    assert "80*50 мм" in reply


def test_low_rating_without_text_does_not_invent_product_problem() -> None:
    reply = draft_review_reply(
        {
            "product_title": "Комплект шевронов",
            "text": "",
            "rating": 3,
        }
    )

    assert "не оправдал ожиданий" in reply
    assert "не подошел" not in reply


def test_wb_token_file_reads_first_line(monkeypatch, tmp_path: Path) -> None:
    token_file = tmp_path / "wb-token.txt"
    token_file.write_text("secret-token\n", encoding="utf-8")
    monkeypatch.delenv("WB_API_TOKEN", raising=False)
    monkeypatch.setenv("VITAL_SHEVRON_WB_TOKEN_FILE", str(token_file))

    creds = load_wb_credentials()

    assert creds is not None
    assert creds.token == "secret-token"


def test_takterra_token_file_is_not_used_as_fallback(monkeypatch, tmp_path: Path) -> None:
    token_file = tmp_path / "wb-token.txt"
    token_file.write_text("wrong-contour-token\n", encoding="utf-8")
    monkeypatch.delenv("WB_API_TOKEN", raising=False)
    monkeypatch.delenv("VITAL_SHEVRON_WB_TOKEN_FILE", raising=False)
    monkeypatch.delenv("SELLER_WB_TOKEN_FILE", raising=False)
    monkeypatch.setenv("TAKTERRA_WB_TOKEN_FILE", str(token_file))

    assert load_wb_credentials() is None


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


def test_positive_review_replies_are_not_identical_for_different_texts() -> None:
    first = draft_review_reply(
        {
            "id": "review-good",
            "offer_id": "pzol0014",
            "product_title": "Шеврон Позывной Иваныч",
            "text": "Сделано хорошо!",
            "rating": 5,
        }
    )
    second = draft_review_reply(
        {
            "id": "review-beautiful",
            "offer_id": "text0001",
            "product_title": "Шеврон на липучке Славянский корпус",
            "text": "Красиво сделано",
            "rating": 5,
        }
    )

    assert first != second
    assert "качество изготовления" in first
    assert "внешний вид" in second


def test_empty_review_with_media_requires_public_reply_not_mark_viewed() -> None:
    item = {
        "platform": "ozon",
        "source_type": "review",
        "id": "review-media",
        "rating": 5,
        "offer_id": "chev_test0001",
        "sku": "123",
        "product_title": "Шеврон на липучке тестовый",
        "text": "",
        "photos_count": 1,
        "videos_count": 0,
        "has_media": True,
        "can_mark_viewed": True,
    }

    actions = build_actions([{**item, "processing_status": classify_item(item)}])

    assert classify_item(item) == "needs_media_review_for_public_reply"
    assert actions[0]["action_type"] == "public_review_reply"
    assert actions[0]["draft_text"]
    assert "фото" in actions[0]["draft_text"].lower()
    assert actions[0]["photos_count"] == 1
    assert "media" in actions[0]["notes"].lower()


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
    assert "## Отзывы без текста и без медиа к отметке просмотренными" in report
    assert "/ оценка `4`" in report


def test_report_includes_media_counts_for_reply_drafts() -> None:
    action = {
        "platform": "ozon",
        "source_type": "review",
        "source_id": "review-media",
        "offer_id": "chev_test0001",
        "sku": "123",
        "rating": 5,
        "product_title": "Шеврон на липучке тестовый",
        "source_text": "",
        "has_media": True,
        "photos_count": 1,
        "videos_count": 0,
        "media_urls": [],
        "processing_status": "needs_media_review_for_public_reply",
        "action_type": "public_review_reply",
        "state": "pending_owner_confirmation",
        "risk": "normal",
        "draft_text": "Спасибо за высокую оценку и прикрепленное фото!",
        "notes": "Review attached media before approval.",
    }

    report = _build_report(
        run_id="reviews_questions_test",
        started_at=datetime(2026, 6, 13, 12, 0, 0),
        items=[],
        actions=[action],
        sources={},
        artifacts={},
    )

    assert "- Оценка: `5`" in report
    assert "- Медиа: фото `1`, видео `0`" in report


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


def test_prepare_reviews_questions_approved_builds_replies_package(tmp_path: Path) -> None:
    pending_id = "reviews_questions_test_pending"
    pending_dir = tmp_path / "pending" / pending_id
    pending_dir.mkdir(parents=True)
    (pending_dir / "manifest.json").write_text(
        json.dumps(
            {
                "pending_id": pending_id,
                "run_id": "reviews_questions_test",
                "status": "pending_owner_review",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    actions = [
        {
            "platform": "wb",
            "source_type": "review",
            "source_id": "feedback-1",
            "action_type": "public_review_reply",
            "state": "pending_owner_confirmation",
            "draft_text": "Спасибо за отзыв!",
        },
        {
            "platform": "ozon",
            "source_type": "review",
            "source_id": "review-1",
            "action_type": "mark_review_viewed",
            "state": "pending_owner_confirmation",
            "draft_text": "",
        },
        {
            "platform": "wb",
            "source_type": "question",
            "source_id": "question-1",
            "action_type": "manual_question_review",
            "state": "needs_owner_input",
            "draft_text": "",
        },
    ]
    (pending_dir / "draft_answers.json").write_text(
        json.dumps({"run_id": "reviews_questions_test", "actions": actions}, ensure_ascii=False),
        encoding="utf-8",
    )

    result = run_reviews_questions_prepare_approved(
        data_dir=tmp_path,
        source_pending=pending_id,
        mode="replies-only",
        approved_by="owner-test",
    )

    package_path = Path(result["artifacts"]["approved_package"])
    package = json.loads(package_path.read_text(encoding="utf-8"))

    assert result["selected_actions_count"] == 1
    assert result["skipped_actions_count"] == 2
    assert package["status"] == "approved"
    assert package["schema_version"] == "approval-package/v1"
    assert package["pending_id"] == pending_id
    assert package["actions"][0]["approved"] is True
    assert package["actions"][0]["state"] == "approved"
    assert package["actions"][0]["approved_by"] == "owner-test"
    assert _approved_actions(package_path) == package["actions"]


def test_prepare_reviews_questions_approved_builds_mark_viewed_package(tmp_path: Path) -> None:
    pending_id = "reviews_questions_test_pending"
    pending_dir = tmp_path / "pending" / pending_id
    pending_dir.mkdir(parents=True)
    (pending_dir / "manifest.json").write_text(
        json.dumps({"run_id": "reviews_questions_test"}, ensure_ascii=False),
        encoding="utf-8",
    )
    actions = [
        {
            "platform": "ozon",
            "source_type": "review",
            "source_id": "review-1",
            "action_type": "mark_review_viewed",
            "state": "pending_owner_confirmation",
            "draft_text": "",
        },
        {
            "platform": "wb",
            "source_type": "review",
            "source_id": "feedback-1",
            "action_type": "public_review_reply",
            "state": "pending_owner_confirmation",
            "draft_text": "Спасибо!",
        },
    ]
    (pending_dir / "draft_answers.json").write_text(
        json.dumps({"actions": actions}, ensure_ascii=False),
        encoding="utf-8",
    )

    result = run_reviews_questions_prepare_approved(
        data_dir=tmp_path,
        source_pending=pending_id,
        mode="mark-viewed-only",
    )

    package = json.loads(Path(result["artifacts"]["approved_package"]).read_text(encoding="utf-8"))

    assert [action["action_type"] for action in package["actions"]] == ["mark_review_viewed"]
    assert result["selected_actions_count"] == 1


def test_prepare_reviews_questions_approved_includes_ozon_question_answers(tmp_path: Path) -> None:
    pending_id = "reviews_questions_test_pending"
    pending_dir = tmp_path / "pending" / pending_id
    pending_dir.mkdir(parents=True)
    (pending_dir / "manifest.json").write_text(
        json.dumps({"run_id": "reviews_questions_test"}, ensure_ascii=False),
        encoding="utf-8",
    )
    actions = [
        {
            "platform": "ozon",
            "source_type": "question",
            "source_id": "question-1",
            "sku": "123",
            "action_type": "question_answer",
            "state": "pending_owner_confirmation",
            "draft_text": "Здравствуйте! Ответ на вопрос.",
        },
    ]
    (pending_dir / "draft_answers.json").write_text(
        json.dumps({"actions": actions}, ensure_ascii=False),
        encoding="utf-8",
    )

    result = run_reviews_questions_prepare_approved(
        data_dir=tmp_path,
        source_pending=pending_id,
        mode="all",
    )

    package = json.loads(Path(result["artifacts"]["approved_package"]).read_text(encoding="utf-8"))

    assert result["selected_actions_count"] == 1
    assert result["skipped_actions_count"] == 0
    assert package["actions"][0]["platform"] == "ozon"
    assert package["actions"][0]["action_type"] == "question_answer"
    assert package["actions"][0]["approved"] is True


def test_apply_reviews_questions_allows_ozon_question_answers(monkeypatch, tmp_path: Path) -> None:
    approved_dir = tmp_path / "approved" / "ozon_questions_approved"
    approved_dir.mkdir(parents=True)
    approved_path = approved_dir / "approved_apply_plan.json"
    action = {
        "platform": "ozon",
        "source_type": "question",
        "source_id": "question-1",
        "sku": "123",
        "action_type": "question_answer",
        "draft_text": "Здравствуйте! Ответ на вопрос.",
        "approved": True,
        "state": "approved",
    }
    from seller_agent.safety.approvals import action_rows_checksum

    approved_path.write_text(
        json.dumps(
            {
                "schema_version": "approval-package/v1",
                "status": "approved",
                "pending_id": "pending-1",
                "source_run_id": "run-1",
                "actions_checksum": action_rows_checksum([action]),
                "actions": [action],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "seller_agent.tasks.reviews_questions._apply_wb_public_replies",
        lambda **kwargs: {"ok": True, "sent": [], "questions_answered": [], "blocker": ""},
    )
    monkeypatch.setattr(
        "seller_agent.tasks.reviews_questions._apply_ozon_public_replies",
        lambda **kwargs: {"ok": True, "sent": [], "questions_answered": [{"ok": True}], "marked_viewed": []},
    )

    result = run_reviews_questions_apply(
        credentials=AppCredentials(ozon_seller=None, ozon_performance=None, wb=None),
        data_dir=tmp_path,
        approved_path=approved_path,
        confirmed_by_user=True,
    )

    assert result["overall_status"] == "ok"
    assert result["applied_counts"]["ozon_question_answers"] == 1


def test_reviews_questions_verify_checks_approved_actions_absent_from_pending(monkeypatch, tmp_path: Path) -> None:
    approved_dir = tmp_path / "approved" / "reviews_questions_approved"
    approved_dir.mkdir(parents=True)
    approved_path = approved_dir / "approved_apply_plan.json"
    action = {
        "platform": "wb",
        "source_type": "review",
        "source_id": "feedback-1",
        "action_type": "public_review_reply",
        "draft_text": "Спасибо за отзыв!",
        "approved": True,
        "state": "approved",
    }
    from seller_agent.safety.approvals import action_rows_checksum

    approved_path.write_text(
        json.dumps(
            {
                "schema_version": "approval-package/v1",
                "status": "approved",
                "pending_id": "pending-1",
                "source_run_id": "reviews_questions_test",
                "actions_checksum": action_rows_checksum([action]),
                "actions": [action],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "seller_agent.tasks.reviews_questions._collect_reviews_questions_verify_state",
        lambda **kwargs: ([], {"wb_api": {"status": "ok"}}),
    )

    result = run_reviews_questions_verify(
        credentials=AppCredentials(ozon_seller=None, ozon_performance=None, wb=WbCredentials(token="token")),
        data_dir=tmp_path,
        approved_path=approved_path,
        run_id="reviews_questions_verify_test",
    )

    assert result["overall_status"] == "ok"
    assert result["still_pending_count"] == 0
    verify_rows = (tmp_path / "runs" / datetime.now().date().isoformat() / "reviews_questions_verify_test" / "processed" / "verify_rows.csv").read_text(
        encoding="utf-8"
    )
    assert "verified_absent_from_pending" in verify_rows


def test_approved_actions_rejects_checksum_mismatch(tmp_path: Path) -> None:
    pending_id = "reviews_questions_test_pending"
    pending_dir = tmp_path / "pending" / pending_id
    pending_dir.mkdir(parents=True)
    (pending_dir / "manifest.json").write_text(
        json.dumps({"run_id": "reviews_questions_test"}, ensure_ascii=False),
        encoding="utf-8",
    )
    (pending_dir / "draft_answers.json").write_text(
        json.dumps(
            {
                "actions": [
                    {
                        "platform": "wb",
                        "source_type": "review",
                        "source_id": "feedback-1",
                        "action_type": "public_review_reply",
                        "state": "pending_owner_confirmation",
                        "draft_text": "Спасибо!",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    result = run_reviews_questions_prepare_approved(data_dir=tmp_path, source_pending=pending_id)
    package_path = Path(result["artifacts"]["approved_package"])
    package = json.loads(package_path.read_text(encoding="utf-8"))
    package["actions"][0]["draft_text"] = "Другой текст"
    package_path.write_text(json.dumps(package, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(RuntimeError, match="checksum mismatch"):
        _approved_actions(package_path)


def test_prepare_reviews_questions_approved_rejects_empty_selection(tmp_path: Path) -> None:
    pending_id = "reviews_questions_test_pending"
    pending_dir = tmp_path / "pending" / pending_id
    pending_dir.mkdir(parents=True)
    (pending_dir / "manifest.json").write_text(
        json.dumps({"run_id": "reviews_questions_test"}, ensure_ascii=False),
        encoding="utf-8",
    )
    (pending_dir / "draft_answers.json").write_text(
        json.dumps(
            {
                "actions": [
                    {
                        "platform": "wb",
                        "source_type": "question",
                        "source_id": "question-1",
                        "action_type": "manual_question_review",
                        "state": "needs_owner_input",
                        "draft_text": "",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="No approvable"):
        run_reviews_questions_prepare_approved(data_dir=tmp_path, source_pending=pending_id)
