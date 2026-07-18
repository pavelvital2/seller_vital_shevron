from __future__ import annotations

import csv
from collections import Counter
from datetime import datetime
import json
from pathlib import Path
import re
import subprocess
from typing import Any

from seller_agent.config import AppCredentials
from seller_agent.core.run_manifest import manifest_from_summary, write_run_manifest, write_summary_run_manifest
from seller_agent.http import ApiError
from seller_agent.marketplaces.ozon.adapter import OzonSellerAdapter
from seller_agent.marketplaces.wb.communications_adapter import WbCommunicationsAdapter
from seller_agent.reports.writer import ensure_dir, write_json
from seller_agent.safety.approvals import (
    action_rows_checksum,
    apply_marker_for,
    approval_identity_from_path,
    assert_apply_not_repeated,
    canonical_checksum,
    mark_approved_applied,
    verify_action_rows_checksum,
)


PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _now() -> datetime:
    return datetime.now().astimezone()


def _safe_error(exc: BaseException) -> str:
    if isinstance(exc, ApiError):
        if exc.status == 401:
            return "HTTP 401: unauthorized"
        if exc.status == 403:
            message = exc.message.replace("\n", " ").replace("\r", " ")
            if "not available with existing subscription" in message:
                return "HTTP 403: not available with existing subscription"
            return "HTTP 403: forbidden"
        if exc.status == 429:
            return "HTTP 429: rate limit / too many requests"
        return f"HTTP {exc.status}: {exc.message[:500]}"
    return str(exc).replace("\n", " ").replace("\r", " ")[:800]


def _safe_read_json(path: Path) -> Any | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    ensure_dir(path.parent)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def _unwrap_wb_data(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        return {}
    current = data
    if isinstance(current.get("data"), dict) and any(key in current["data"] for key in ("feedbacks", "questions")):
        return current["data"]
    if isinstance(current.get("data"), dict) and isinstance(current["data"].get("data"), dict):
        return current["data"]["data"]
    return current


def _product_details(item: dict[str, Any]) -> dict[str, Any]:
    details = item.get("productDetails") or item.get("product_details") or {}
    return details if isinstance(details, dict) else {}


def _join_review_text(*parts: Any) -> str:
    labels = ["", "Достоинства: ", "Недостатки: "]
    values: list[str] = []
    for label, part in zip(labels, parts):
        text = str(part or "").strip()
        if text:
            values.append(f"{label}{text}" if label else text)
    return " ".join(values).strip()


def normalize_wb_feedback(item: dict[str, Any]) -> dict[str, Any]:
    details = _product_details(item)
    normalized = {
        "platform": "wb",
        "source_type": "review",
        "id": str(item.get("id") or ""),
        "published_at": item.get("createdDate") or item.get("created_date") or "",
        "rating": item.get("productValuation") or item.get("product_valuation") or "",
        "offer_id": str(details.get("supplierArticle") or ""),
        "sku": str(details.get("nmId") or ""),
        "product_title": details.get("productName") or "",
        "text": _join_review_text(item.get("text"), item.get("pros"), item.get("cons")),
        "raw_text": item.get("text") or "",
        "pros": item.get("pros") or "",
        "cons": item.get("cons") or "",
        "was_viewed": item.get("wasViewed"),
        "answer_exists": bool(item.get("answer")),
        "can_mark_viewed": False,
        "needs_public_reply": not bool(item.get("answer")),
    }
    normalized.update(_review_media_fields(item))
    return normalized


def normalize_wb_question(item: dict[str, Any]) -> dict[str, Any]:
    details = _product_details(item)
    return {
        "platform": "wb",
        "source_type": "question",
        "id": str(item.get("id") or ""),
        "published_at": item.get("createdDate") or item.get("created_date") or "",
        "rating": "",
        "offer_id": str(details.get("supplierArticle") or ""),
        "sku": str(details.get("nmId") or ""),
        "product_title": details.get("productName") or "",
        "text": str(item.get("text") or "").strip(),
        "was_viewed": item.get("wasViewed"),
        "answer_exists": bool(item.get("answer")),
        "state": item.get("state") or "",
        "is_answerable": item.get("answer") is None,
    }


def _normalize_ozon_item(item: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(item)
    normalized.setdefault("id", item.get("uuid") or item.get("review_uuid") or "")
    normalized.setdefault("platform", "ozon")
    normalized.setdefault("rating", item.get("rating") or "")
    normalized.setdefault("text", str(item.get("text") or "").strip())
    normalized.setdefault("offer_id", item.get("offer_id") or "")
    normalized.setdefault("sku", str(item.get("sku") or ""))
    normalized.setdefault("product_title", item.get("product_title") or "")
    normalized.update(_review_media_fields(normalized))
    return normalized


def _int_value(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _list_count(*values: Any) -> int:
    for value in values:
        if isinstance(value, list):
            return len(value)
    return 0


def _single_media_present(*values: Any) -> int:
    for value in values:
        if isinstance(value, str) and value.strip():
            return 1
        if isinstance(value, dict) and value:
            return 1
    return 0


def _review_media_fields(item: dict[str, Any]) -> dict[str, Any]:
    photos_count = _int_value(
        item.get("photos_count")
        or item.get("photosCount")
        or item.get("photo_count")
        or item.get("photoCount")
        or _list_count(item.get("photos"), item.get("photoLinks"), item.get("photo_links"), item.get("images"))
    )
    videos_count = _int_value(
        item.get("videos_count")
        or item.get("videosCount")
        or item.get("video_count")
        or item.get("videoCount")
        or _list_count(item.get("videos"), item.get("videoLinks"), item.get("video_links"))
        or _single_media_present(item.get("video"), item.get("videoLink"), item.get("video_link"))
    )
    media_urls: list[str] = []
    for key in (
        "photos",
        "photoLinks",
        "photo_links",
        "images",
        "videos",
        "video",
        "videoLinks",
        "videoLink",
        "video_links",
        "video_link",
    ):
        value = item.get(key)
        if isinstance(value, list):
            for row in value:
                if isinstance(row, str) and row.startswith(("http://", "https://")):
                    media_urls.append(row)
                elif isinstance(row, dict):
                    url = row.get("url") or row.get("src") or row.get("link")
                    if isinstance(url, str) and url.startswith(("http://", "https://")):
                        media_urls.append(url)
        elif isinstance(value, str) and value.startswith(("http://", "https://")):
            media_urls.append(value)
        elif isinstance(value, dict):
            url = value.get("url") or value.get("src") or value.get("link")
            if isinstance(url, str) and url.startswith(("http://", "https://")):
                media_urls.append(url)
    return {
        "photos_count": photos_count,
        "videos_count": videos_count,
        "has_media": photos_count > 0 or videos_count > 0 or bool(media_urls),
        "media_urls": media_urls,
    }


def _rating_number(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _rating_sort_key(value: str) -> tuple[int, str]:
    if value == "н/д":
        return (-1, value)
    return (_rating_number(value), value)


def _has_problem_text(text: str) -> bool:
    return bool(
        re.search(
            r"не подош|маленьк|слом|брак|плохо|ужас|вернул|не соответствует|нет в комплект|обман|разочар|так себе|кривоват|криво|размер не тот|не .*как на фото|жаль.*(?:цвет|оливков)",
            text,
            flags=re.IGNORECASE,
        )
    )


def classify_item(item: dict[str, Any]) -> str:
    source_type = item.get("source_type")
    text = str(item.get("text") or "").strip()
    has_media = bool(item.get("has_media"))

    if source_type == "question":
        if not text:
            return "needs_manual_check"
        if re.search(r"налич|сколько|количеств|остат|шт|штук|парт", text, re.IGNORECASE):
            return "needs_stock_check"
        if re.search(r"размер|цвет|материал|липуч|велкро|комплект|состав|подойд", text, re.IGNORECASE):
            return "needs_product_context_check"
        return "needs_owner_review_for_answer"

    rating = _rating_number(item.get("rating"))
    if text and (rating in {1, 2, 3} or _has_problem_text(text)):
        return "priority_problem_review"
    if has_media:
        return "needs_media_review_for_public_reply"
    if item.get("platform") == "ozon" and item.get("can_mark_viewed"):
        return "can_mark_viewed_after_owner_confirmation"
    if item.get("needs_public_reply"):
        return "needs_owner_review_for_public_reply"
    return "needs_manual_check"


def _product_phrase(title: str) -> tuple[str, str, str]:
    lower = title.lower()
    if "патронташ" in lower:
        return "патронташ", "понравился", "подошел"
    if "комплект" in lower:
        return "комплект", "понравился", "подошел"
    if "шеврон" in lower:
        return "шеврон", "понравился", "подошел"
    if "петлица" in lower:
        return "петлица", "понравилась", "подошла"
    if "нашив" in lower:
        return "нашивка", "понравилась", "подошла"
    return "товар", "понравился", "подошел"


def _stable_variant(item: dict[str, Any], variants_count: int) -> int:
    key = f"{item.get('source_id') or item.get('id') or ''}|{item.get('offer_id') or ''}|{item.get('product_title') or ''}"
    return sum(ord(char) for char in key) % variants_count if variants_count else 0


def draft_review_reply(item: dict[str, Any]) -> str:
    title = str(item.get("product_title") or "")
    text = str(item.get("text") or "").strip()
    rating = _rating_number(item.get("rating"))
    word, liked, matched = _product_phrase(title)
    has_media = bool(item.get("has_media"))
    photos_count = _int_value(item.get("photos_count"))
    videos_count = _int_value(item.get("videos_count"))
    lower = text.lower()

    if rating in {1, 2, 3} and not text:
        return (
            "Спасибо за оценку. Нам жаль, что товар не оправдал ожиданий. "
            "Если вы уточните, что именно вас не устроило, мы учтем замечание."
        )
    if re.search(r"долго (?:ждать|ехал|достав)", lower):
        return (
            "Спасибо за обратную связь. Сожалеем, что доставка заняла больше времени, чем вы ожидали. "
            "Срок доставки рассчитывает и показывает маркетплейс."
        )
    if re.search(r"размер не тот|размер не подош|не подош.*размер", lower):
        return (
            "Спасибо за обратную связь. Сожалеем, что размер не подошел. "
            "Проверим соответствие размеров в карточке фактическому изделию."
        )
    if re.search(r"жаль.*(?:оливков|цвет)|не .*как на фото", lower):
        return (
            "Спасибо за отзыв и замечание по цвету. "
            "Проверим, насколько точно фотографии карточки передают оттенок изделия."
        )
    if re.search(r"липуч.*не .*прилип|вырезан неаккурат|кривоват|криво|детализац|брак|слом", lower):
        return (
            "Здравствуйте! Спасибо за обратную связь. Нам жаль, что качество изделия вас не устроило. "
            "Проверим эту позицию и текущую партию с учетом вашего замечания."
        )
    if re.search(r"не подош", lower):
        return "Спасибо за обратную связь. Жаль, что изделие вам не подошло. Учтем ваше замечание."
    if rating in {1, 2, 3} or _has_problem_text(text):
        return (
            "Здравствуйте! Спасибо за обратную связь. Нам жаль, что товар не подошел. "
            "Проверим карточку и партию по этой позиции. Если товар не подошел, "
            "возврат можно оформить через маркетплейс."
        )
    if has_media and not text:
        media_word = "фото и видео" if photos_count and videos_count else "видео" if videos_count else "фото"
        variants = [
            f"Спасибо за высокую оценку и прикрепленное {media_word}! Рады, что {word} вам {liked}.",
            f"Благодарим за оценку и {media_word}. Приятно видеть, что {word} {matched} вам.",
            f"Спасибо, что поделились {media_word}. Рады, что {word} оставил хорошее впечатление.",
        ]
        return variants[_stable_variant(item, len(variants))]
    if text:
        if has_media:
            media_word = "фото и видео" if photos_count and videos_count else "видео" if videos_count else "фото"
            return f"Спасибо за отзыв и {media_word}! Рады, что {word} вам {liked}."
        if "красив" in lower:
            return "Спасибо за отзыв! Рады, что вам понравилось исполнение и внешний вид изделия."
        if "хорош" in lower or "качеств" in lower:
            return f"Спасибо за отзыв! Приятно, что вы отметили качество изготовления."
        if "огонь" in lower:
            return f"Спасибо! Рады, что {word} вам {liked}. Носите с удовольствием."
        variants = [
            f"Спасибо за отзыв! Рады, что {word} вам {liked} и {matched} по качеству.",
            f"Благодарим за обратную связь. Рады, что {word} {matched} вам.",
            f"Спасибо за оценку и отзыв! Приятно, что {word} вам {liked} и {matched}.",
        ]
        return variants[_stable_variant(item, len(variants))]
    variants = [
        f"Спасибо за высокую оценку! Рады, что {word} вам {liked}.",
        f"Благодарим за оценку. Рады, что {word} {matched} вам.",
        f"Спасибо за 5 звезд! Приятно, что {word} вам {liked} и {matched}.",
    ]
    return variants[_stable_variant(item, len(variants))]


def _passport_product_size(item: dict[str, Any]) -> str:
    identity_values = {
        str(item.get("offer_id") or "").strip(),
        str(item.get("sku") or "").strip(),
    }
    identity_values.discard("")
    internal_skus = set(identity_values)
    mapping = _safe_read_json(PROJECT_ROOT / "data" / "catalog" / "unified" / "products.json")
    rows = mapping if isinstance(mapping, list) else mapping.get("products", []) if isinstance(mapping, dict) else []
    for row in rows:
        if not isinstance(row, dict):
            continue
        row_ids = {
            str(row.get("internal_sku") or "").strip(),
            str(row.get("ozon_offer_id") or "").strip(),
            str(row.get("ozon_sku") or "").strip(),
            str(row.get("wb_vendor_code") or "").strip(),
            str(row.get("wb_nm_id") or "").strip(),
        }
        if identity_values.intersection(row_ids):
            internal_sku = str(row.get("internal_sku") or "").strip()
            if internal_sku:
                internal_skus.add(internal_sku)
    for internal_sku in internal_skus:
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", internal_sku):
            continue
        passport = _safe_read_json(PROJECT_ROOT / "data" / "catalog" / "master_passport" / "approved" / f"{internal_sku}.json")
        physical = passport.get("physical") if isinstance(passport, dict) and isinstance(passport.get("physical"), dict) else {}
        product_size = str(physical.get("product_size_mm") or "").strip()
        if product_size:
            return product_size
    return ""


def draft_question_reply(item: dict[str, Any]) -> str:
    title = str(item.get("product_title") or "")
    text = str(item.get("text") or "").strip()
    all_text = f"{title}\n{text}".lower()

    if re.search(r"налич|сколько|количеств|остат|шт|штук|парт", text, re.IGNORECASE):
        return ""
    if re.search(r"лев(?:ую|ой)|прав(?:ую|ой)|какую сторону", text, re.IGNORECASE):
        return (
            "Здравствуйте! Этот нагрудный шеврон можно разместить с любой стороны - "
            "ориентируйтесь на расположение ответной части липучки на форме или экипировке."
        )
    if re.search(r"что будет.*нос|закон|правомер|можно ли нос", text, re.IGNORECASE):
        return (
            "Здравствуйте! Технически шеврон можно закрепить на рюкзаке при наличии ответной части липучки. "
            "Мы не консультируем по правовым последствиям использования ведомственной символики, "
            "поэтому перед ношением рекомендуем проверить требования законодательства."
        )
    if re.search(r"вместо|убрать|изменить надпись|индивидуальн|свой дизайн|на заказ", text, re.IGNORECASE):
        return (
            "Здравствуйте! К сожалению, индивидуальные изменения надписи и дизайна не выполняем. "
            "В карточке доступен только показанный вариант изделия."
        )
    if re.search(r"размер", text, re.IGNORECASE):
        product_size = _passport_product_size(item)
        if product_size:
            sizes = re.findall(r"\d+\s*[*xхX]\s*\d+\s*мм", product_size, flags=re.IGNORECASE)
            if len(sizes) >= 2 and "комплект" in title.lower():
                return (
                    f"Здравствуйте! В комплекте два шеврона: нагрудный размером {sizes[0]} "
                    f"и шеврон на кепку размером {sizes[1]}."
                )
            return f"Здравствуйте! Размер изделия: {product_size}."
        return ""
    if re.search(r"позывн", text, re.IGNORECASE):
        return ""
    if "липуч" in all_text or "велкро" in all_text:
        return (
            "Здравствуйте! Да, шеврон крепится на липучку. "
            "Жесткая часть липучки находится на обратной стороне изделия."
        )
    return ""


def build_actions(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    for item in items:
        status = classify_item(item)
        base = {
            "platform": item.get("platform", ""),
            "source_type": item.get("source_type", ""),
            "source_id": item.get("id", ""),
            "offer_id": item.get("offer_id", ""),
            "sku": item.get("sku", ""),
            "rating": item.get("rating", ""),
            "product_title": item.get("product_title", ""),
            "source_text": item.get("text", ""),
            "has_media": bool(item.get("has_media")),
            "photos_count": item.get("photos_count", 0),
            "videos_count": item.get("videos_count", 0),
            "media_urls": item.get("media_urls", []),
            "processing_status": status,
        }
        if status == "can_mark_viewed_after_owner_confirmation":
            actions.append(
                {
                    **base,
                    "action_type": "mark_review_viewed",
                    "state": "pending_owner_confirmation",
                    "risk": "low",
                    "draft_text": "",
                    "notes": "Ozon empty review/rating. Mark viewed only after owner confirmation.",
                }
            )
            continue
        if item.get("source_type") == "question":
            draft = draft_question_reply(item)
            actions.append(
                {
                    **base,
                    "action_type": "question_answer" if draft else "manual_question_review",
                    "state": "pending_owner_confirmation" if draft else "needs_owner_input",
                    "risk": "normal",
                    "draft_text": draft,
                    "notes": "Question requires stock/product-context check before publication." if not draft else "",
                }
            )
            continue
        if status in {"needs_owner_review_for_public_reply", "priority_problem_review", "needs_media_review_for_public_reply"}:
            actions.append(
                {
                    **base,
                    "action_type": "public_review_reply",
                    "state": "pending_owner_confirmation",
                    "risk": "high" if status == "priority_problem_review" else "normal",
                    "draft_text": draft_review_reply(item),
                    "notes": "Review attached media before approval." if status == "needs_media_review_for_public_reply" else "Owner must approve before publication.",
                }
            )
    return actions


def _extract_ozon_reviews(response: dict[str, Any]) -> list[dict[str, Any]]:
    result = response.get("result") if isinstance(response, dict) else None
    candidates: Any
    if isinstance(result, dict):
        candidates = result.get("reviews") or result.get("items") or result.get("data") or []
    else:
        candidates = result if isinstance(result, list) else response.get("reviews") if isinstance(response, dict) else []
    if not isinstance(candidates, list):
        return []
    rows: list[dict[str, Any]] = []
    for item in candidates:
        if not isinstance(item, dict):
            continue
        rows.append(
            {
                "platform": "ozon",
                "source_type": "review",
                "id": item.get("id") or item.get("review_id") or "",
                "published_at": item.get("published_at") or "",
                "rating": item.get("rating") or "",
                "offer_id": item.get("offer_id") or "",
                "sku": str(item.get("sku") or ""),
                "product_title": item.get("product_name") or item.get("product_title") or "",
                "text": str(item.get("text") or "").strip(),
                **_review_media_fields(item),
                "needs_public_reply": True,
                "can_mark_viewed": False,
            }
        )
    return rows


def _run_ozon_official_probe(
    *,
    credentials: AppCredentials,
    run_dir: Path,
    limit: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    raw_dir = ensure_dir(run_dir / "raw" / "ozon_api")
    if not credentials.ozon_seller:
        return [], {"status": "missing_credentials", "source": "Ozon Seller API"}

    adapter = OzonSellerAdapter(credentials.ozon_seller)
    result: dict[str, Any] = {"status": "unknown", "source": "Ozon Seller API", "methods": {}}
    reviews: list[dict[str, Any]] = []

    for name, path, payload in [
        ("review_count", "/v1/review/count", {}),
        ("review_list", "/v1/review/list", {"limit": min(max(limit, 20), 100), "sort_dir": "DESC", "status": "UNPROCESSED"}),
    ]:
        try:
            data = adapter.post(path, payload)
            write_json(raw_dir / f"{name}.json", data)
            result["methods"][name] = {"status": "ok", "path": path}
            if name == "review_list":
                reviews = _extract_ozon_reviews(data)
        except Exception as exc:  # noqa: BLE001
            result["methods"][name] = {"status": "error", "path": path, "error": _safe_error(exc)}

    result["status"] = "ok" if any(item.get("status") == "ok" for item in result["methods"].values()) else "error"
    result["reviews_count"] = len(reviews)
    return reviews, result


def _run_ozon_lk_fallback(*, run_dir: Path, limit: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    script = PROJECT_ROOT / "scripts" / "reviews" / "ozon_reviews_questions_readonly_cdp.js"
    completed = subprocess.run(
        ["node", str(script), "--run-dir", str(run_dir), "--limit", str(limit)],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        timeout=180,
        check=False,
    )
    summary = _safe_read_json(run_dir / "raw" / "ozon_lk_summary.json")
    if not isinstance(summary, dict):
        summary = {
            "source": "ozon_lk_cdp_internal_api",
            "ok": False,
            "blocker": (completed.stderr or completed.stdout or "").strip()[:1000],
        }
    summary["returncode"] = completed.returncode

    reviews_raw = _safe_read_json(run_dir / "processed" / "ozon_reviews.json")
    questions_raw = _safe_read_json(run_dir / "processed" / "ozon_questions.json")
    reviews = [_normalize_ozon_item(item) for item in reviews_raw] if isinstance(reviews_raw, list) else []
    questions = [_normalize_ozon_item(item) for item in questions_raw] if isinstance(questions_raw, list) else []
    raw_reviews_fetch = _safe_read_json(run_dir / "raw" / "ozon_lk" / "reviews_fetch.json")
    raw_questions_fetch = _safe_read_json(run_dir / "raw" / "ozon_lk" / "questions_fetch.json")
    if isinstance(raw_reviews_fetch, dict):
        not_viewed = (((raw_reviews_fetch.get("counter") or {}).get("json") or {}).get("items") or {}).get("NOT_VIEWED")
        if not_viewed is not None:
            summary.setdefault("outputs", {})["review_counter_not_viewed"] = int(not_viewed or 0)
    if isinstance(raw_questions_fetch, dict):
        new_questions = (((raw_questions_fetch.get("counter") or {}).get("json") or {}).get("count"))
        if new_questions is not None:
            try:
                summary.setdefault("outputs", {})["question_counter_new"] = int(new_questions or 0)
            except (TypeError, ValueError):
                summary.setdefault("outputs", {})["question_counter_new"] = str(new_questions)
    return reviews, questions, summary


def _run_wb_api(
    *,
    credentials: AppCredentials,
    run_dir: Path,
    limit: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    raw_dir = ensure_dir(run_dir / "raw" / "wb_api")
    if not credentials.wb:
        return [], [], {
            "status": "missing_credentials",
            "source": "WB Feedbacks API",
            "error": "WB API token is not available. Check WB_API_TOKEN or VITAL_SHEVRON_WB_TOKEN_FILE.",
        }

    adapter = WbCommunicationsAdapter(credentials.wb)
    summary: dict[str, Any] = {"status": "ok", "source": "WB Feedbacks API", "methods": {}}
    feedbacks: list[dict[str, Any]] = []
    questions: list[dict[str, Any]] = []

    calls = [
        ("feedbacks_count", lambda: adapter.fetch_unanswered_feedbacks_count()),
        ("questions_count", lambda: adapter.fetch_unanswered_questions_count()),
        ("feedbacks_list", lambda: adapter.fetch_feedbacks(is_answered=False, take=limit, skip=0, order="dateDesc")),
        ("questions_list", lambda: adapter.fetch_questions(is_answered=False, take=limit, skip=0, order="dateDesc")),
    ]
    for name, fn in calls:
        try:
            data = fn()
            write_json(raw_dir / f"{name}.json", data)
            summary["methods"][name] = {"status": "ok"}
            unwrapped = _unwrap_wb_data(data)
            if name == "feedbacks_list":
                raw_feedbacks = unwrapped.get("feedbacks") if isinstance(unwrapped, dict) else []
                feedbacks = [normalize_wb_feedback(item) for item in raw_feedbacks if isinstance(item, dict)]
            elif name == "questions_list":
                raw_questions = unwrapped.get("questions") if isinstance(unwrapped, dict) else []
                questions = [normalize_wb_question(item) for item in raw_questions if isinstance(item, dict)]
        except Exception as exc:  # noqa: BLE001
            summary["status"] = "error"
            summary["methods"][name] = {"status": "error", "error": _safe_error(exc)}

    summary["feedbacks_count"] = len(feedbacks)
    summary["questions_count"] = len(questions)
    return feedbacks, questions, summary


def _write_pending_package(
    *,
    data_dir: Path,
    run_id: str,
    started_at: datetime,
    actions: list[dict[str, Any]],
    report_text: str,
) -> dict[str, str]:
    pending_id = f"{run_id}_pending"
    pending_dir = ensure_dir(data_dir / "pending" / pending_id)
    fields = [
        "platform",
        "source_type",
        "action_type",
        "state",
        "risk",
        "source_id",
        "offer_id",
        "sku",
        "rating",
        "product_title",
        "source_text",
        "has_media",
        "photos_count",
        "videos_count",
        "media_urls",
        "draft_text",
        "processing_status",
        "notes",
    ]
    manifest = {
        "pending_id": pending_id,
        "run_id": run_id,
        "status": "pending_owner_review",
        "created_at": started_at.isoformat(timespec="seconds"),
        "actions_count": len(actions),
        "write_operations": False,
        "approval_required": True,
    }
    write_json(pending_dir / "manifest.json", manifest)
    write_json(pending_dir / "draft_answers.json", {"run_id": run_id, "actions": actions})
    _write_csv(pending_dir / "draft_answers.csv", actions, fields)
    (pending_dir / "APPROVAL_REQUIRED.md").write_text(report_text, encoding="utf-8")
    return {
        "pending_dir": str(pending_dir),
        "manifest": str(pending_dir / "manifest.json"),
        "draft_answers_json": str(pending_dir / "draft_answers.json"),
        "draft_answers_csv": str(pending_dir / "draft_answers.csv"),
        "approval_report": str(pending_dir / "APPROVAL_REQUIRED.md"),
    }


def _is_approvable_action(action: dict[str, Any]) -> bool:
    action_type = action.get("action_type")
    platform = action.get("platform")
    if action_type == "public_review_reply" and platform in {"ozon", "wb"}:
        return bool(str(action.get("draft_text") or "").strip())
    if action_type == "question_answer" and platform in {"ozon", "wb"}:
        return bool(str(action.get("draft_text") or "").strip())
    if action_type == "mark_review_viewed" and platform == "ozon":
        return True
    return False


def _action_matches_approval_mode(action: dict[str, Any], mode: str) -> bool:
    if mode == "all":
        return True
    if mode == "replies-only":
        return action.get("action_type") in {"public_review_reply", "question_answer"}
    if mode == "mark-viewed-only":
        return action.get("action_type") == "mark_review_viewed"
    raise ValueError(f"Unsupported reviews/questions approval mode: {mode}")


def _approved_action(action: dict[str, Any], *, approved_by: str, approved_at: str) -> dict[str, Any]:
    return {
        **action,
        "approved": True,
        "state": "approved",
        "approved_by": approved_by,
        "approved_at": approved_at,
    }


def _write_reviews_questions_approved_report(
    path: Path,
    *,
    package: dict[str, Any],
    skipped_actions: list[dict[str, Any]],
    artifacts: dict[str, str],
) -> None:
    action_counts = Counter(str(action.get("action_type") or "") for action in package["actions"])
    skipped_counts = Counter(str(action.get("action_type") or "") for action in skipped_actions)
    lines = [
        "# Reviews And Questions Approved Package",
        "",
        f"Approved ID: `{package['approved_id']}`",
        f"Pending ID: `{package['pending_id']}`",
        f"Source run: `{package['source_run_id']}`",
        f"Mode: `{package['mode']}`",
        f"Created at: `{package['created_at']}`",
        f"Approved by: `{package['approved_by']}`",
        "",
        "## Summary",
        "",
        f"- selected actions: `{package['selected_actions_count']}`",
        f"- skipped actions: `{len(skipped_actions)}`",
        f"- actions checksum: `{package['actions_checksum']}`",
        "",
        "## Selected Action Counts",
        "",
    ]
    if not action_counts:
        lines.append("- none")
    for action_type, count in sorted(action_counts.items()):
        lines.append(f"- `{action_type}`: `{count}`")
    lines.extend(["", "## Skipped Action Counts", ""])
    if not skipped_counts:
        lines.append("- none")
    for action_type, count in sorted(skipped_counts.items()):
        lines.append(f"- `{action_type}`: `{count}`")
    lines.extend(["", "## Safety", ""])
    lines.append("- Apply still requires `--confirmed-by-user`.")
    lines.append("- Apply will verify `actions_checksum` before marketplace write operations.")
    lines.append("- Apply idempotency guard blocks repeated use of the same approved package.")
    lines.extend(["", "## Artifacts", ""])
    for key, value in sorted(artifacts.items()):
        lines.append(f"- `{key}`: `{value}`")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def run_reviews_questions_prepare_approved(
    *,
    data_dir: Path = Path("data"),
    source_pending: str,
    mode: str = "all",
    approved_id: str | None = None,
    approved_by: str = "owner",
    overwrite: bool = False,
) -> dict[str, Any]:
    pending_dir = data_dir / "pending" / source_pending
    pending_manifest_path = pending_dir / "manifest.json"
    draft_answers_path = pending_dir / "draft_answers.json"
    if not pending_manifest_path.exists():
        raise FileNotFoundError(f"Pending manifest not found: {pending_manifest_path}")
    if not draft_answers_path.exists():
        raise FileNotFoundError(f"Pending draft answers not found: {draft_answers_path}")

    pending_manifest = _safe_read_json(pending_manifest_path)
    draft_answers = _safe_read_json(draft_answers_path)
    if not isinstance(pending_manifest, dict):
        raise RuntimeError(f"Pending manifest is not a JSON object: {pending_manifest_path}")
    if not isinstance(draft_answers, dict) or not isinstance(draft_answers.get("actions"), list):
        raise RuntimeError(f"Pending draft answers must contain actions list: {draft_answers_path}")

    source_run_id = str(pending_manifest.get("run_id") or draft_answers.get("run_id") or "")
    if not source_run_id:
        raise RuntimeError(f"Pending package has no source run id: {source_pending}")
    raw_actions = [action for action in draft_answers["actions"] if isinstance(action, dict)]
    approved_at = _now().isoformat(timespec="seconds")
    selected_actions: list[dict[str, Any]] = []
    skipped_actions: list[dict[str, Any]] = []
    for action in raw_actions:
        if _is_approvable_action(action) and _action_matches_approval_mode(action, mode):
            selected_actions.append(_approved_action(action, approved_by=approved_by, approved_at=approved_at))
        else:
            skipped_actions.append(action)
    if not selected_actions:
        raise RuntimeError(f"No approvable reviews/questions actions selected for mode: {mode}")

    approved_id = approved_id or f"{source_pending}_approved"
    approved_dir = data_dir / "approved" / approved_id
    if approved_dir.exists() and not overwrite:
        raise FileExistsError(f"Approved package already exists: {approved_dir}")
    ensure_dir(approved_dir)

    created_at = _now().isoformat(timespec="seconds")
    actions_checksum = action_rows_checksum(selected_actions)
    package = {
        "schema_version": "approval-package/v1",
        "package_type": "reviews_questions",
        "status": "approved",
        "approved_id": approved_id,
        "pending_id": source_pending,
        "source_run_id": source_run_id,
        "source_pending_manifest": str(pending_manifest_path),
        "created_at": created_at,
        "approved_by": approved_by,
        "mode": mode,
        "source_actions_count": len(raw_actions),
        "selected_actions_count": len(selected_actions),
        "skipped_actions_count": len(raw_actions) - len(selected_actions),
        "actions_checksum": actions_checksum,
        "actions": selected_actions,
    }
    package_path = approved_dir / "approved_apply_plan.json"
    skipped_path = approved_dir / "skipped_actions.json"
    report_path = approved_dir / "APPROVED_PACKAGE.md"
    artifacts = {
        "approved_dir": str(approved_dir),
        "approved_package": str(package_path),
        "skipped_actions": str(skipped_path),
        "report": str(report_path),
        "source_pending_manifest": str(pending_manifest_path),
        "source_draft_answers": str(draft_answers_path),
    }
    write_json(package_path, package)
    write_json(skipped_path, skipped_actions)
    _write_reviews_questions_approved_report(report_path, package=package, skipped_actions=skipped_actions, artifacts=artifacts)

    return {
        "approved_id": approved_id,
        "pending_id": source_pending,
        "source_run_id": source_run_id,
        "mode": mode,
        "selected_actions_count": len(selected_actions),
        "skipped_actions_count": len(raw_actions) - len(selected_actions),
        "actions_checksum": actions_checksum,
        "artifacts": artifacts,
    }


def _build_report(
    *,
    run_id: str,
    started_at: datetime,
    items: list[dict[str, Any]],
    actions: list[dict[str, Any]],
    sources: dict[str, Any],
    artifacts: dict[str, str],
) -> str:
    item_counts = Counter(f"{item.get('platform')}:{item.get('source_type')}" for item in items)
    action_counts = Counter(str(action.get("action_type") or "") for action in actions)
    status_counts = Counter(str(action.get("processing_status") or "") for action in actions)
    rating_counts = Counter(str(action.get("rating") or "н/д") for action in actions)
    blockers: list[str] = []
    for name, source in sources.items():
        if isinstance(source, dict):
            if source.get("status") in {"error", "missing_credentials"}:
                blockers.append(f"{name}: {source.get('error') or source.get('blocker') or source.get('status')}")
            for method_name, method in (source.get("methods") or {}).items():
                if isinstance(method, dict) and method.get("status") == "error":
                    blockers.append(f"{name}/{method_name}: {method.get('error')}")
            if source.get("ok") is False and source.get("blocker"):
                blockers.append(f"{name}: {source.get('blocker')}")

    lines = [
        "# Отзывы и вопросы: read-only dry-run",
        "",
        f"Run ID: `{run_id}`",
        f"Started at: `{started_at.isoformat(timespec='seconds')}`",
        "",
        "Операция read-only: ответы не опубликованы, вопросы не закрыты, отзывы не отмечены просмотренными.",
        "",
        "## Итог",
        "",
        f"- Найдено обращений: `{len(items)}`",
        f"- Действий в pending для согласования: `{len(actions)}`",
    ]
    for key, count in sorted(item_counts.items()):
        lines.append(f"- `{key}`: `{count}`")
    lines.extend(["", "## Действия", ""])
    if not action_counts:
        lines.append("- нет действий")
    for key, count in sorted(action_counts.items()):
        lines.append(f"- `{key}`: `{count}`")
    lines.extend(["", "## Статусы обработки", ""])
    if not status_counts:
        lines.append("- нет статусов")
    for key, count in sorted(status_counts.items()):
        lines.append(f"- `{key}`: `{count}`")

    lines.extend(["", "## Оценки", ""])
    if not rating_counts:
        lines.append("- оценок нет")
    for rating, count in sorted(rating_counts.items(), key=lambda item: _rating_sort_key(item[0]), reverse=True):
        lines.append(f"- `{rating}`: `{count}`")

    lines.extend(["", "## Черновики для согласования", ""])
    draft_actions = [action for action in actions if action.get("draft_text")]
    if not draft_actions:
        lines.append("- черновиков нет")
    for index, action in enumerate(draft_actions[:100], 1):
        lines.extend(
            [
                f"### {index}. {action['platform']} / {action['source_type']} / {action['offer_id'] or action['sku'] or action['source_id']}",
                "",
                f"- Товар: {action['product_title']}",
                f"- Оценка: `{action['rating'] or 'н/д'}`",
                f"- Риск: `{action['risk']}`",
                f"- Текст покупателя: {action['source_text'] or 'без текста'}",
                f"- Медиа: фото `{action.get('photos_count') or 0}`, видео `{action.get('videos_count') or 0}`",
                "",
                f"Черновик ответа: {action['draft_text']}",
                "",
            ]
        )

    viewed_actions = [action for action in actions if action.get("action_type") == "mark_review_viewed"]
    lines.extend(["", "## Отзывы без текста и без медиа к отметке просмотренными", ""])
    if not viewed_actions:
        lines.append("- нет")
    for index, action in enumerate(viewed_actions[:100], 1):
        lines.append(
            f"{index}. `{action.get('platform')}` / `{action.get('offer_id') or action.get('sku') or action.get('source_id')}` "
            f"/ оценка `{action.get('rating') or 'н/д'}` - {action.get('product_title') or 'без названия'}"
        )

    lines.extend(["", "## Блокеры и ограничения", ""])
    if not blockers:
        lines.append("- явных блокеров нет")
    else:
        for blocker in blockers:
            lines.append(f"- {blocker}")

    lines.extend(
        [
            "",
            "## Источники и проверка",
            "",
            "- WB: официальный раздел Customer Communication API, методы `/api/v1/feedbacks`, `/api/v1/questions`, `/api/v1/feedbacks/answer`, `PATCH /api/v1/questions`.",
            "- Ozon: официальный Review API проверен read-only запросами `/v1/review/count` и `/v1/review/list`; текущий ключ вернул `HTTP 403: not available with existing subscription`, поэтому для Ozon использован fallback через ЛК/CDP.",
            "- Apply запрещен до явного подтверждения владельца по pending-пакету.",
            "",
            "## Артефакты",
            "",
        ]
    )
    for key, value in sorted(artifacts.items()):
        lines.append(f"- `{key}`: `{value}`")
    lines.append("")
    return "\n".join(lines)


def run_reviews_questions(
    *,
    credentials: AppCredentials,
    data_dir: Path = Path("data"),
    run_id: str | None = None,
    marketplace: str = "all",
    limit: int = 100,
) -> dict[str, Any]:
    started_at = _now()
    run_id = run_id or f"reviews_questions_{started_at.strftime('%Y%m%dT%H%M%S')}"
    run_day = started_at.date().isoformat()
    run_dir = ensure_dir(data_dir / "runs" / run_day / run_id)
    processed_dir = ensure_dir(run_dir / "processed")

    sources: dict[str, Any] = {}
    items: list[dict[str, Any]] = []

    if marketplace in {"all", "ozon"}:
        official_reviews, official_summary = _run_ozon_official_probe(
            credentials=credentials,
            run_dir=run_dir,
            limit=limit,
        )
        sources["ozon_api"] = official_summary
        ozon_reviews, ozon_questions, lk_summary = _run_ozon_lk_fallback(run_dir=run_dir, limit=limit)
        sources["ozon_lk"] = lk_summary
        review_list_ok = (official_summary.get("methods") or {}).get("review_list", {}).get("status") == "ok"
        items.extend(official_reviews if review_list_ok else ozon_reviews)
        items.extend(ozon_questions)

    if marketplace in {"all", "wb"}:
        wb_feedbacks, wb_questions, wb_summary = _run_wb_api(credentials=credentials, run_dir=run_dir, limit=limit)
        sources["wb_api"] = wb_summary
        items.extend(wb_feedbacks)
        items.extend(wb_questions)

    normalized_items = [{**item, "processing_status": classify_item(item)} for item in items]
    actions = build_actions(normalized_items)

    processed_items_path = processed_dir / "reviews_questions_items.json"
    actions_path = processed_dir / "reviews_questions_actions.json"
    write_json(processed_items_path, normalized_items)
    write_json(actions_path, {"run_id": run_id, "actions": actions})

    artifacts: dict[str, str] = {
        "run_dir": str(run_dir),
        "processed_items": str(processed_items_path),
        "actions": str(actions_path),
        "run_manifest": str(run_dir / "manifest.json"),
    }

    report_text = _build_report(
        run_id=run_id,
        started_at=started_at,
        items=normalized_items,
        actions=actions,
        sources=sources,
        artifacts=artifacts,
    )
    report_path = run_dir / "reviews_questions_dry_run.md"
    report_path.write_text(report_text, encoding="utf-8")
    artifacts["report"] = str(report_path)
    artifacts.update(
        _write_pending_package(
            data_dir=data_dir,
            run_id=run_id,
            started_at=started_at,
            actions=actions,
            report_text=report_text,
        )
    )

    source_errors = []
    ozon_lk_ok = isinstance(sources.get("ozon_lk"), dict) and sources["ozon_lk"].get("ok") is True
    for source_name, source in sources.items():
        if not isinstance(source, dict) or source.get("status") not in {"error", "missing_credentials"}:
            continue
        if source_name == "ozon_api" and ozon_lk_ok:
            continue
        source_errors.append(source)
    overall_status = "ok"
    if ozon_lk_ok and isinstance(sources.get("ozon_api"), dict) and sources["ozon_api"].get("status") == "error":
        overall_status = "warning"
    if source_errors and normalized_items:
        overall_status = "warning"
    elif source_errors and not normalized_items:
        overall_status = "blocked"

    verification_warnings: list[dict[str, Any]] = []
    if marketplace in {"all", "ozon"} and ozon_lk_ok:
        lk_outputs = sources.get("ozon_lk", {}).get("outputs") if isinstance(sources.get("ozon_lk"), dict) else {}
        not_viewed = int((lk_outputs or {}).get("review_counter_not_viewed") or 0)
        if not_viewed > 0 and not any(action.get("platform") == "ozon" and action.get("source_type") == "review" for action in actions):
            verification_warnings.append(
                {
                    "source": "ozon_lk",
                    "type": "not_viewed_counter_without_actions",
                    "review_counter_not_viewed": not_viewed,
                    "message": "Ozon LK counter has NOT_VIEWED reviews, but dry-run produced no Ozon review actions.",
                }
            )
            overall_status = "warning"

    summary = {
        "run_id": run_id,
        "started_at": started_at.isoformat(timespec="seconds"),
        "overall_status": overall_status,
        "mode": "read_only_dry_run",
        "marketplace": marketplace,
        "pending_id": f"{run_id}_pending",
        "items_count": len(normalized_items),
        "actions_count": len(actions),
        "sources": sources,
        "verification_warnings": verification_warnings,
        "artifacts": artifacts,
    }
    summary_path = run_dir / "summary.json"
    write_json(summary_path, summary)
    summary["artifacts"]["summary"] = str(summary_path)
    write_run_manifest(
        data_dir=data_dir,
        run_dir=run_dir,
        manifest=manifest_from_summary(
            summary=summary,
            task="reviews-questions",
            mode="dry_run",
            risk="low",
            marketplaces=[marketplace] if marketplace != "all" else ["ozon", "wb"],
            inputs={"marketplace": marketplace, "limit": limit},
        ),
    )
    return summary


def _approved_actions(approved_path: Path) -> list[dict[str, Any]]:
    data = _safe_read_json(approved_path)
    if not isinstance(data, dict):
        raise RuntimeError(f"Approved plan is not valid JSON object: {approved_path}")
    if data.get("status") != "approved":
        raise RuntimeError(f"Approved plan status must be 'approved': {approved_path}")
    actions = data.get("actions")
    if not isinstance(actions, list):
        raise RuntimeError(f"Approved plan has no actions list: {approved_path}")
    approved_actions = [action for action in actions if isinstance(action, dict)]
    verify_action_rows_checksum(
        actions=approved_actions,
        expected_checksum=str(data.get("actions_checksum") or ""),
    )
    return approved_actions


def _build_apply_report(
    *,
    run_id: str,
    started_at: datetime,
    approved_path: Path,
    wb_result: dict[str, Any],
    ozon_result: dict[str, Any],
    artifacts: dict[str, str],
) -> str:
    wb_sent = wb_result.get("sent") if isinstance(wb_result.get("sent"), list) else []
    wb_questions = wb_result.get("questions_answered") if isinstance(wb_result.get("questions_answered"), list) else []
    ozon_sent = ozon_result.get("sent") if isinstance(ozon_result.get("sent"), list) else []
    ozon_marked = ozon_result.get("marked_viewed") if isinstance(ozon_result.get("marked_viewed"), list) else []
    wb_ok = sum(1 for item in wb_sent if item.get("ok"))
    wb_questions_ok = sum(1 for item in wb_questions if item.get("ok"))
    ozon_ok = sum(1 for item in ozon_sent if item.get("ok"))
    ozon_marked_ok = sum(1 for item in ozon_marked if item.get("ok"))
    lines = [
        "# Отзывы и вопросы: apply result",
        "",
        f"Run ID: `{run_id}`",
        f"Started at: `{started_at.isoformat(timespec='seconds')}`",
        f"Approved plan: `{approved_path}`",
        "",
        "## Итог",
        "",
        f"- WB отправлено: `{wb_ok}` из `{len(wb_sent)}`",
        f"- WB вопросов закрыто ответом: `{wb_questions_ok}` из `{len(wb_questions)}`",
        f"- Ozon отправлено: `{ozon_ok}` из `{len(ozon_sent)}`",
        f"- Ozon отмечено просмотренными: `{ozon_marked_ok}` из `{len(ozon_marked)}`",
        f"- WB status: `{wb_result.get('status') or ('ok' if wb_result.get('ok') else 'error')}`",
        f"- Ozon status: `{ozon_result.get('status') or ('ok' if ozon_result.get('ok') else 'error')}`",
        "",
        "## WB",
        "",
    ]
    if not wb_sent:
        lines.append("- нет отправленных WB-ответов")
    for item in wb_sent:
        lines.append(
            f"- `{item.get('offer_id')}` / `{item.get('source_id')}` / оценка `{item.get('rating')}`: "
            f"{'ok' if item.get('ok') else 'error'}"
        )
    lines.extend(["", "### WB вопросы", ""])
    if not wb_questions:
        lines.append("- нет отправленных WB-ответов на вопросы")
    for item in wb_questions:
        lines.append(
            f"- `{item.get('offer_id')}` / `{item.get('source_id')}`: "
            f"{'ok' if item.get('ok') else 'error'}"
        )
    lines.extend(["", "## Ozon", ""])
    if not ozon_sent:
        lines.append("- нет отправленных Ozon-ответов")
    for item in ozon_sent:
        lines.append(
            f"- `{item.get('offer_id')}` / `{item.get('source_id')}` / оценка `{item.get('rating')}`: "
            f"{'ok' if item.get('ok') else 'error'}"
        )
    lines.extend(["", "### Отмечено просмотренными", ""])
    if not ozon_marked:
        lines.append("- нет Ozon-отзывов, отмеченных просмотренными")
    for item in ozon_marked[:100]:
        lines.append(
            f"- `{item.get('offer_id')}` / `{item.get('source_id')}` / оценка `{item.get('rating')}`: "
            f"{'ok' if item.get('ok') else 'error'}"
        )
    if len(ozon_marked) > 100:
        lines.append(f"- ... еще `{len(ozon_marked) - 100}`")
    lines.extend(["", "## Ограничения", ""])
    if wb_result.get("blocker"):
        lines.append(f"- WB blocker: {wb_result['blocker']}")
    if ozon_result.get("blocker"):
        lines.append(f"- Ozon blocker: {ozon_result['blocker']}")
    if not wb_result.get("blocker") and not ozon_result.get("blocker"):
        lines.append("- явных блокеров нет")
    lines.extend(["", "## Артефакты", ""])
    for key, value in sorted(artifacts.items()):
        lines.append(f"- `{key}`: `{value}`")
    lines.append("")
    return "\n".join(lines)


def _apply_wb_public_replies(
    *,
    credentials: AppCredentials,
    actions: list[dict[str, Any]],
    run_dir: Path,
) -> dict[str, Any]:
    raw_dir = ensure_dir(run_dir / "raw" / "wb_apply")
    result: dict[str, Any] = {
        "source": "WB Feedbacks API",
        "ok": False,
        "sent": [],
        "questions_answered": [],
        "blocker": "",
    }
    feedback_actions = [
        action
        for action in actions
        if action.get("platform") == "wb"
        and action.get("source_type") == "review"
        and action.get("action_type") == "public_review_reply"
        and action.get("approved") is True
        and str(action.get("draft_text") or "").strip()
    ]
    question_actions = [
        action
        for action in actions
        if action.get("platform") == "wb"
        and action.get("source_type") == "question"
        and action.get("action_type") == "question_answer"
        and action.get("approved") is True
        and str(action.get("draft_text") or "").strip()
    ]
    if not feedback_actions and not question_actions:
        result["ok"] = True
        write_json(raw_dir / "wb_apply_result.json", result)
        return result
    if not credentials.wb:
        result["blocker"] = "WB API token is not available"
        write_json(raw_dir / "wb_apply_result.json", result)
        return result

    adapter = WbCommunicationsAdapter(credentials.wb)
    try:
        before_count = adapter.fetch_unanswered_feedbacks_count()
        write_json(raw_dir / "feedbacks_count_before.json", before_count)
    except Exception as exc:  # noqa: BLE001
        result["before_count_error"] = _safe_error(exc)
    try:
        before_questions_count = adapter.fetch_unanswered_questions_count()
        write_json(raw_dir / "questions_count_before.json", before_questions_count)
    except Exception as exc:  # noqa: BLE001
        result["before_questions_count_error"] = _safe_error(exc)

    for action in feedback_actions:
        row = {
            "source_id": action.get("source_id", ""),
            "offer_id": action.get("offer_id", ""),
            "sku": action.get("sku", ""),
            "rating": action.get("rating", ""),
            "ok": False,
            "error": "",
        }
        try:
            response = adapter.answer_feedback(
                feedback_id=str(action.get("source_id") or ""),
                text=str(action.get("draft_text") or ""),
            )
            row["ok"] = True
            row["response"] = response
        except Exception as exc:  # noqa: BLE001
            row["error"] = _safe_error(exc)
        result["sent"].append(row)
        if not row["ok"]:
            result["blocker"] = f"WB reply failed for {row['offer_id']}/{row['source_id']}: {row['error']}"
            break

    if not result["blocker"]:
        for action in question_actions:
            row = {
                "source_id": action.get("source_id", ""),
                "offer_id": action.get("offer_id", ""),
                "sku": action.get("sku", ""),
                "ok": False,
                "error": "",
            }
            try:
                response = adapter.answer_question(
                    question_id=str(action.get("source_id") or ""),
                    text=str(action.get("draft_text") or ""),
                )
                row["ok"] = True
                row["response"] = response
            except Exception as exc:  # noqa: BLE001
                row["error"] = _safe_error(exc)
            result["questions_answered"].append(row)
            if not row["ok"]:
                result["blocker"] = f"WB question answer failed for {row['offer_id']}/{row['source_id']}: {row['error']}"
                break

    try:
        after_count = adapter.fetch_unanswered_feedbacks_count()
        after_list = adapter.fetch_feedbacks(is_answered=False, take=100, skip=0, order="dateDesc")
        write_json(raw_dir / "feedbacks_count_after.json", after_count)
        write_json(raw_dir / "feedbacks_unanswered_after.json", after_list)
    except Exception as exc:  # noqa: BLE001
        result["after_verify_error"] = _safe_error(exc)
    try:
        after_questions_count = adapter.fetch_unanswered_questions_count()
        after_questions_list = adapter.fetch_questions(is_answered=False, take=100, skip=0, order="dateDesc")
        write_json(raw_dir / "questions_count_after.json", after_questions_count)
        write_json(raw_dir / "questions_unanswered_after.json", after_questions_list)
    except Exception as exc:  # noqa: BLE001
        result["after_questions_verify_error"] = _safe_error(exc)

    result["ok"] = (
        (bool(result["sent"]) or bool(result["questions_answered"]))
        and all(item.get("ok") for item in result["sent"])
        and all(item.get("ok") for item in result["questions_answered"])
        and not result["blocker"]
    )
    write_json(raw_dir / "wb_apply_result.json", result)
    return result


def _apply_ozon_public_replies(
    *,
    approved_path: Path,
    run_dir: Path,
) -> dict[str, Any]:
    raw_dir = ensure_dir(run_dir / "raw" / "ozon_apply")
    script = PROJECT_ROOT / "scripts" / "reviews" / "ozon_apply_reviews_questions_cdp.js"
    completed = subprocess.run(
        ["node", str(script), "--approved-path", str(approved_path), "--run-dir", str(run_dir)],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        timeout=180,
        check=False,
    )
    result = _safe_read_json(raw_dir / "ozon_apply_result.json")
    if not isinstance(result, dict):
        result = {
            "source": "ozon_lk_cdp_internal_api",
            "ok": False,
            "blocker": (completed.stderr or completed.stdout or "").strip()[:1000],
            "sent": [],
        }
        write_json(raw_dir / "ozon_apply_result.json", result)
    result["returncode"] = completed.returncode
    return result


def _action_identity(action: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(action.get("platform") or ""),
        str(action.get("source_type") or ""),
        str(action.get("source_id") or ""),
        str(action.get("action_type") or ""),
    )


def _marketplace_for_actions(actions: list[dict[str, Any]]) -> str:
    platforms = {str(action.get("platform") or "") for action in actions}
    platforms.discard("")
    if platforms == {"ozon"}:
        return "ozon"
    if platforms == {"wb"}:
        return "wb"
    return "all"


def _collect_reviews_questions_verify_state(
    *,
    credentials: AppCredentials,
    run_dir: Path,
    marketplace: str,
    limit: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    sources: dict[str, Any] = {}
    items: list[dict[str, Any]] = []
    if marketplace in {"all", "ozon"}:
        official_reviews, official_summary = _run_ozon_official_probe(
            credentials=credentials,
            run_dir=run_dir,
            limit=limit,
        )
        sources["ozon_api"] = official_summary
        ozon_reviews, ozon_questions, lk_summary = _run_ozon_lk_fallback(run_dir=run_dir, limit=limit)
        sources["ozon_lk"] = lk_summary
        review_list_ok = (official_summary.get("methods") or {}).get("review_list", {}).get("status") == "ok"
        items.extend(official_reviews if review_list_ok else ozon_reviews)
        items.extend(ozon_questions)
    if marketplace in {"all", "wb"}:
        wb_feedbacks, wb_questions, wb_summary = _run_wb_api(credentials=credentials, run_dir=run_dir, limit=limit)
        sources["wb_api"] = wb_summary
        items.extend(wb_feedbacks)
        items.extend(wb_questions)
    normalized_items = [{**item, "processing_status": classify_item(item)} for item in items]
    return build_actions(normalized_items), sources


def run_reviews_questions_verify(
    *,
    credentials: AppCredentials,
    approved_path: Path,
    data_dir: Path = Path("data"),
    run_id: str | None = None,
    limit: int = 300,
) -> dict[str, Any]:
    started_at = _now()
    run_id = run_id or f"reviews_questions_verify_{started_at.strftime('%Y%m%dT%H%M%S')}"
    run_dir = ensure_dir(data_dir / "runs" / started_at.date().isoformat() / run_id)
    processed_dir = ensure_dir(run_dir / "processed")

    approved_id = approval_identity_from_path(approved_path)
    approved_plan = _safe_read_json(approved_path)
    source_run_id = str(approved_plan.get("source_run_id") or "") if isinstance(approved_plan, dict) else ""
    approved_actions = _approved_actions(approved_path)
    marketplace = _marketplace_for_actions(approved_actions)
    effective_limit = max(limit, len(approved_actions) * 2, 100)
    current_actions, sources = _collect_reviews_questions_verify_state(
        credentials=credentials,
        run_dir=run_dir,
        marketplace=marketplace,
        limit=effective_limit,
    )

    current_by_identity = {_action_identity(action): action for action in current_actions}
    rows: list[dict[str, Any]] = []
    still_pending = 0
    for action in approved_actions:
        identity = _action_identity(action)
        current = current_by_identity.get(identity)
        status = "still_pending" if current else "verified_absent_from_pending"
        if current:
            still_pending += 1
        rows.append(
            {
                "platform": identity[0],
                "source_type": identity[1],
                "source_id": identity[2],
                "action_type": identity[3],
                "offer_id": action.get("offer_id") or "",
                "sku": action.get("sku") or "",
                "status": status,
                "current_processing_status": (current or {}).get("processing_status") or "",
            }
        )

    source_errors = [
        source
        for source in sources.values()
        if isinstance(source, dict) and source.get("status") in {"error", "missing_credentials"}
    ]
    overall_status = "ok"
    if still_pending:
        overall_status = "blocked" if still_pending == len(approved_actions) else "warning"
    elif source_errors:
        overall_status = "warning"

    write_json(processed_dir / "approved_actions.json", approved_actions)
    write_json(processed_dir / "current_actions.json", current_actions)
    _write_csv(
        processed_dir / "verify_rows.csv",
        rows,
        ["platform", "source_type", "source_id", "action_type", "offer_id", "sku", "status", "current_processing_status"],
    )
    artifacts = {
        "run_dir": str(run_dir),
        "summary": str(run_dir / "summary.json"),
        "approved_actions": str(processed_dir / "approved_actions.json"),
        "current_actions": str(processed_dir / "current_actions.json"),
        "verify_rows": str(processed_dir / "verify_rows.csv"),
        "run_manifest": str(run_dir / "manifest.json"),
    }
    summary = {
        "run_id": run_id,
        "started_at": started_at.isoformat(timespec="seconds"),
        "overall_status": overall_status,
        "mode": "verify",
        "approved_id": approved_id,
        "approved_path": str(approved_path),
        "source_run_id": source_run_id,
        "marketplace": marketplace,
        "approved_actions_count": len(approved_actions),
        "still_pending_count": still_pending,
        "verified_absent_count": len(approved_actions) - still_pending,
        "source_errors_count": len(source_errors),
        "sources": sources,
        "artifacts": artifacts,
    }
    write_json(run_dir / "summary.json", summary)
    write_summary_run_manifest(
        data_dir=data_dir,
        run_dir=run_dir,
        summary=summary,
        task="reviews-questions-verify",
        mode="verify",
        risk="low",
        marketplaces=[marketplace] if marketplace != "all" else ["ozon", "wb"],
        inputs={"approved_path": str(approved_path), "limit": effective_limit},
        approved_id=approved_id,
    )
    return summary


def run_reviews_questions_apply(
    *,
    credentials: AppCredentials,
    approved_path: Path,
    data_dir: Path = Path("data"),
    run_id: str | None = None,
    confirmed_by_user: bool = False,
) -> dict[str, Any]:
    started_at = _now()
    run_id = run_id or f"reviews_questions_apply_{started_at.strftime('%Y%m%dT%H%M%S')}"
    run_day = started_at.date().isoformat()
    run_dir = ensure_dir(data_dir / "runs" / run_day / run_id)

    approved_id = approval_identity_from_path(approved_path)
    artifacts: dict[str, str] = {
        "run_dir": str(run_dir),
        "approved_path": str(approved_path),
        "run_manifest": str(run_dir / "manifest.json"),
        "apply_marker": str(apply_marker_for(data_dir=data_dir, approved_id=approved_id)),
    }
    if not confirmed_by_user:
        summary = {
            "run_id": run_id,
            "started_at": started_at.isoformat(timespec="seconds"),
            "overall_status": "blocked",
            "mode": "apply",
            "blocker": "Apply requires --confirmed-by-user",
            "approved_id": approved_id,
            "artifacts": artifacts,
        }
        write_json(run_dir / "summary.json", summary)
        write_summary_run_manifest(
            data_dir=data_dir,
            run_dir=run_dir,
            summary=summary,
            task="reviews-questions-apply",
            mode="apply",
            risk="low",
            marketplaces=["ozon", "wb"],
            inputs={"approved_path": str(approved_path), "confirmed_by_user": confirmed_by_user},
            approved_id=approved_id,
        )
        return summary

    approved_plan = _safe_read_json(approved_path)
    assert_apply_not_repeated(data_dir=data_dir, approved_id=approved_id)
    pending_id = str(approved_plan.get("pending_id") or "") if isinstance(approved_plan, dict) else ""
    source_run_id = str(approved_plan.get("source_run_id") or "") if isinstance(approved_plan, dict) else ""
    actions = _approved_actions(approved_path)
    disallowed = [
        action
        for action in actions
        if not (
            (action.get("platform") in {"ozon", "wb"} and action.get("action_type") == "public_review_reply")
            or (action.get("platform") == "ozon" and action.get("action_type") == "question_answer")
            or (action.get("platform") == "wb" and action.get("action_type") == "question_answer")
            or (action.get("platform") == "ozon" and action.get("action_type") == "mark_review_viewed")
        )
    ]
    if disallowed:
        raise RuntimeError("Approved plan contains unsupported action types for this apply")

    wb_result = _apply_wb_public_replies(credentials=credentials, actions=actions, run_dir=run_dir)
    ozon_result = _apply_ozon_public_replies(approved_path=approved_path, run_dir=run_dir)
    artifacts["wb_result"] = str(run_dir / "raw" / "wb_apply" / "wb_apply_result.json")
    artifacts["ozon_result"] = str(run_dir / "raw" / "ozon_apply" / "ozon_apply_result.json")

    overall_status = "ok" if wb_result.get("ok") and ozon_result.get("ok") else "blocked"
    report_text = _build_apply_report(
        run_id=run_id,
        started_at=started_at,
        approved_path=approved_path,
        wb_result=wb_result,
        ozon_result=ozon_result,
        artifacts=artifacts,
    )
    report_path = run_dir / "reviews_questions_apply_result.md"
    report_path.write_text(report_text, encoding="utf-8")
    artifacts["report"] = str(report_path)

    summary = {
        "run_id": run_id,
        "started_at": started_at.isoformat(timespec="seconds"),
        "overall_status": overall_status,
        "mode": "apply",
        "pending_id": pending_id,
        "source_run_id": source_run_id,
        "approved_id": approved_id,
        "approved_path": str(approved_path),
        "applied_counts": {
            "wb_public_review_replies": len(wb_result.get("sent") or []),
            "wb_question_answers": len(wb_result.get("questions_answered") or []),
            "ozon_public_review_replies": len(ozon_result.get("sent") or []),
            "ozon_question_answers": len(ozon_result.get("questions_answered") or []),
            "ozon_marked_viewed": len(ozon_result.get("marked_viewed") or []),
        },
        "wb": wb_result,
        "ozon": ozon_result,
        "artifacts": artifacts,
    }
    summary_path = run_dir / "summary.json"
    write_json(summary_path, summary)
    summary["artifacts"]["summary"] = str(summary_path)
    write_json(summary_path, summary)
    manifest_paths = write_summary_run_manifest(
        data_dir=data_dir,
        run_dir=run_dir,
        summary=summary,
        task="reviews-questions-apply",
        mode="apply",
        risk="low",
        marketplaces=["ozon", "wb"],
        inputs={"approved_path": str(approved_path), "confirmed_by_user": confirmed_by_user},
        approved_id=approved_id,
    )
    mark_approved_applied(
        data_dir=data_dir,
        approved_id=approved_id,
        apply_run_id=run_id,
        task="reviews-questions-apply",
        status=overall_status,
        run_manifest_path=manifest_paths["manifest"],
        checksum=canonical_checksum({"approved_id": approved_id, "actions": actions}),
    )
    return summary
