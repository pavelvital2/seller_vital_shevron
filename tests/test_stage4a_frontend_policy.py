from __future__ import annotations

from pathlib import Path
import re


STATIC = Path("src/seller_agent/control_plane/static")


def test_stage4a_frontend_activates_all_four_read_only_sections() -> None:
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    rendered = f"{html}\n{js}"

    for endpoint in (
        "/operations/summary",
        "/jobs?limit=20",
        "/approvals",
    ):
        assert endpoint in js
    for element_id in (
        "operations-status",
        "run-daily-report",
        "jobs-status",
        "jobs-list",
        "approvals-status",
        "approvals-list",
    ):
        assert f'id="{element_id}"' in html
    for label in (
        "Запустить утренний отчёт",
        "Задания control plane",
        "Ожидают решения",
        "Требуют сверки",
        "Повторный apply недоступен",
    ):
        assert label in rendered
    assert "СЛЕДУЮЩИЙ ЭТАП" not in html
    assert "MAX_POLL_ATTEMPTS" in js
    assert "daily-morning-report" in js
    assert "requires_reconciliation" in js
    assert not re.search(
        r">\s*(?:Применить|Согласовать|Отклонить|Approve|Apply|Verify)\s*<",
        html,
        re.IGNORECASE,
    )


def test_stage4a_frontend_keeps_safe_dom_and_bounded_polling_contract() -> None:
    js = (STATIC / "app.js").read_text(encoding="utf-8")

    for forbidden in (
        "innerHTML",
        "outerHTML",
        "insertAdjacentHTML",
        "document.write",
        "eval(",
        "new Function",
        "localStorage",
        "sessionStorage",
    ):
        assert forbidden not in js
    assert "createElement" in js
    assert ".textContent" in js
    assert "replaceChildren" in js
    assert "TERMINAL.has" in js
    assert re.search(r"attempt\s*>=\s*MAX_POLL_ATTEMPTS", js)


def test_stage4a_layout_has_wrapping_and_three_viewport_breakpoints() -> None:
    css = (STATIC / "app.css").read_text(encoding="utf-8")

    assert "overflow-wrap: anywhere" in css
    assert "min-width: 0" in css
    assert "@media (min-width: 720px)" in css
    assert "@media (min-width: 1200px)" in css
    assert ".operational-grid" in css
    assert ".record-list" in css
