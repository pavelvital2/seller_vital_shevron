from __future__ import annotations

from pathlib import Path
import re

from seller_agent.control_plane.api import create_control_app
from seller_agent.control_plane.auth import SessionSigner
from seller_agent.control_plane.config import ControlPlaneConfig
from seller_agent.core.job_service import JobService
from seller_agent.core.job_store import JobStore
from seller_agent.tasks.registry import default_task_registry


STATIC = Path("src/seller_agent/control_plane/static")


def test_frontend_is_local_csp_compatible_and_has_no_unsafe_dom_sinks() -> None:
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    css = (STATIC / "app.css").read_text(encoding="utf-8")
    license_text = (STATIC / "LUCIDE_LICENSE.txt").read_text(encoding="utf-8")

    assert "https://telegram.org/js/telegram-web-app.js" in html
    external = re.findall(r'(?:src|href)="(https?://[^"]+)"', html)
    assert external == ["https://telegram.org/js/telegram-web-app.js"]
    assert "<script>" not in html
    assert "<style" not in html
    assert not re.search(r"\son[a-z]+\s*=", html, re.IGNORECASE)
    for forbidden in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval("):
        assert forbidden not in js
    assert "localStorage" not in js
    assert "sessionStorage" not in js
    assert ".textContent" in js
    assert "createElement" in js
    assert "--tg-theme-bg-color" in css
    assert "safe-area-inset-bottom" in css
    assert "ISC License" in license_text
    assert "Lucide Icons" in license_text


def test_frontend_has_required_sections_and_no_write_controls() -> None:
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    rendered = f"{html}\n{js}"

    for label in ("Состояние магазина", "Аналитика", "Задания", "Согласования"):
        assert label in rendered
    for label in (
        "данные недоступны",
        "Свежесть",
        "неизвестно",
        "свежие данные",
        "устаревшие данные",
        "требует внимания",
        "сравнение недоступно",
    ):
        assert label in rendered
    assert "7" in html and "30" in html and "90" in html
    assert "Ozon" in html and "Wildberries" in html
    for region in ("Москва", "Ростов-на-Дону", "Новосибирск", "Казань"):
        assert region in rendered
    assert "REGIONS_BY_MARKETPLACE" in js
    assert "region_id: selectedValue(\"region\")" in js
    assert "query_pack_id" not in html
    assert "sales.changes" in js
    assert "sales.margin" in js
    assert "Маржа" in js
    assert "к прошлому периоду" in js
    assert not re.search(r">\s*(?:Применить|Согласовать|Approve|Apply)\s*<", html, re.IGNORECASE)


def test_frontend_visual_tokens_and_owner_copy_follow_p1_contract() -> None:
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    css = (STATIC / "app.css").read_text(encoding="utf-8")

    assert "gradient(" not in css.lower()
    assert not re.search(r"font-size\s*:[^;{}]*(?:vw|vmin|vmax)", css, re.IGNORECASE)
    assert all(
        value.strip() == "0"
        for value in re.findall(r"letter-spacing\s*:\s*([^;{}]+)", css)
    )
    pixel_radii = [
        int(value)
        for value in re.findall(r"border-radius\s*:\s*(\d+)px", css)
    ]
    variable_radii = [
        int(value)
        for value in re.findall(r"--radius-[^:]+:\s*(\d+)px", css)
    ]
    assert pixel_radii and max([*pixel_radii, *variable_radii]) <= 8
    assert css.count("border-radius: 50%") == 1
    assert ".status-dot" in css

    owner_copy = f"{html}\n{js}"
    for forbidden in (
        "data unavailable",
        "Freshness",
        "comparison unavailable",
        "Marketplace write",
        "Loading state",
        "Error state",
        "Empty state",
        "Parser visibility",
        "Parser snapshots",
        "OWNER CONTROL",
        "COMING NEXT",
    ):
        assert forbidden not in owner_copy


def test_backend_csp_has_no_unsafe_inline_or_eval(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "runtime.db")
    registry = default_task_registry()
    app = create_control_app(
        config=ControlPlaneConfig(
            bot_token="test-token",
            allowed_owner_ids=frozenset({42}),
            session_signer=SessionSigner(b"q" * 32),
            public_app_url="https://example.test/vital-shevron/",
        ),
        store=store,
        service=JobService(store=store, registry=registry, data_dir=tmp_path / "data"),
    )
    csp = next(
        middleware
        for middleware in app.middlewares
        if middleware.__name__ == "_security_headers_middleware"
    )
    assert csp is not None
    source = (Path("src/seller_agent/control_plane/api.py")).read_text(encoding="utf-8")
    assert "unsafe-inline" not in source
    assert "unsafe-eval" not in source
