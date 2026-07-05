from __future__ import annotations

import json
from pathlib import Path

from seller_agent.tasks.inbox_workflow import _build_wb_report


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
