#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import datetime as dt
import html
import json
import mimetypes
import os
import re
import subprocess
from pathlib import Path
from typing import Any


NON_UNIFORM_NR_THEMES = {
    "bpla",
    "svo",
    "prikol",
    "chvk",
    "fan",
    "sht",
    "raz",
    "pz",
}


def esc(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def public_text(value: Any) -> str:
    text = "" if value is None else str(value)
    return re.sub(r"\bпо текущим данным карточек:\s*", "", text, flags=re.I)


def text_html(value: Any) -> str:
    return "<br>".join(esc(public_text(value)).split("\n"))


def data_uri(path: Path) -> str:
    media_type = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    return f"data:{media_type};base64," + base64.b64encode(path.read_bytes()).decode("ascii")


def resolve_asset_path(raw_path: str | None, audit_dir: Path) -> Path | None:
    if not raw_path:
        return None
    path = Path(raw_path)
    candidates = [path] if path.is_absolute() else [audit_dir / path, Path.cwd() / path]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def read_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, data: dict[str, Any]) -> None:
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def internal_theme(internal_sku: str) -> str:
    parts = internal_sku.split("_")
    if len(parts) >= 3:
        return parts[2]
    return ""


def is_non_uniform_nr(internal_sku: str) -> bool:
    parts = internal_sku.split("_")
    return len(parts) >= 3 and parts[1] == "nr" and parts[2] in NON_UNIFORM_NR_THEMES


def strip_sleeve_seo(text: str) -> str:
    text = re.sub(r"\s+на\s+рукав(?:е|ах)?", "", text, flags=re.I)
    text = re.sub(r"\s+нарукавн(?:ый|ые|ая|ое|ого|ому|ым|ом)", "", text, flags=re.I)
    return re.sub(r"\s{2,}", " ", text).strip(" ,;")


def normalize_hashtag_value(value: Any) -> str:
    if isinstance(value, list):
        return ", ".join(str(item).strip() for item in value if str(item).strip())
    return str(value or "")


def filter_hashtags(value: Any, *, non_uniform_nr: bool) -> str:
    if isinstance(value, list):
        tags = [str(tag).strip() for tag in value if str(tag).strip()]
    else:
        raw = normalize_hashtag_value(value)
        if "," in raw or ";" in raw:
            tags = [tag.strip() for tag in re.split(r"[,;]+", raw) if tag.strip()]
        else:
            tags = [tag.strip() for tag in raw.split() if tag.strip()]
    if non_uniform_nr:
        tags = [tag for tag in tags if not re.search(r"(рукав|нарукав)", tag, re.I)]
    seen: set[str] = set()
    out: list[str] = []
    for tag in tags:
        if tag not in seen:
            seen.add(tag)
            out.append(tag)
    return ", ".join(out[:30])


def recommended_hashtags(data: dict[str, Any]) -> Any:
    for rec in data.get("recommendations", []):
        rec_field = str(rec.get("field") or rec.get("title") or rec.get("id") or "")
        if "хештег" in rec_field.lower() or "hashtag" in rec_field.lower():
            value = rec.get("recommended")
            if value:
                return value
    for item in data.get("target_editor_fields", []):
        item_field = str(item.get("field") or "")
        if "хештег" in item_field.lower() or "hashtag" in item_field.lower():
            value = item.get("recommended")
            if value and not str(value).startswith("см."):
                return value
    return None


def current_ozon_attr(data: dict[str, Any], attr_name: str) -> str:
    for attr in data.get("current_state", {}).get("ozon", {}).get("attributes", []):
        if attr.get("attribute_name") == attr_name:
            return "; ".join(map(str, attr.get("values", [])))
    return ""


def normalize_review_data(data: dict[str, Any], audit_dir: Path) -> dict[str, Any]:
    sku = data.get("identity", {}).get("internal_sku") or data.get("proposed_final_card", {}).get("canonical_title") or audit_dir.name
    non_uniform = is_non_uniform_nr(str(sku))
    proposed = data.setdefault("proposed_final_card", {})

    title = (
        proposed.get("canonical_title")
        or proposed.get("ozon_title")
        or data.get("current_state", {}).get("ozon", {}).get("title")
        or str(sku)
    )
    if non_uniform:
        title = strip_sleeve_seo(title)
    proposed["canonical_title"] = title
    proposed["ozon_title"] = title
    proposed["wb_title"] = title
    proposed["title_length"] = len(title)

    hashtags = proposed.get("ozon_hashtags") or recommended_hashtags(data) or current_ozon_attr(data, "#Хештеги")
    proposed["ozon_hashtags"] = filter_hashtags(hashtags, non_uniform_nr=non_uniform)

    blocks = proposed.get("description_blocks")
    if not isinstance(blocks, list) or len(blocks) < 3:
        desc = proposed.get("canonical_description") or data.get("current_state", {}).get("current_description_plaintext", "")
        blocks = [part.strip() for part in re.split(r"\n\s*\n", str(desc)) if part.strip()][:3]
    while len(blocks) < 3:
        blocks.append("")
    blocks = [
        re.sub(r"\bпо текущим данным карточек:\s*", "", str(block), flags=re.I)
        for block in blocks[:3]
    ]
    desc = "\n\n".join(blocks[:3])
    proposed["description_blocks"] = blocks[:3]
    proposed["canonical_description"] = desc
    proposed["ozon_description"] = desc
    proposed["wb_description"] = desc

    if non_uniform:
        owner = data.setdefault("owner_review", {})
        corrections = owner.setdefault("corrections", [])
        note = "fast_owner_review: removed sleeve terms from title/hashtags for non-uniform nr theme"
        if not any(c.get("reason") == note for c in corrections if isinstance(c, dict)):
            corrections.append(
                {
                    "at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
                    "by": "prepare_owner_review.py",
                    "reason": note,
                    "internal_sku": sku,
                }
            )
    return data


def field_rows(fields: list[dict[str, Any]], marketplace: str) -> str:
    rows = []
    for item in fields:
        item_marketplace = item.get("marketplace") or item.get("mp")
        if item_marketplace != marketplace:
            continue
        why = item.get("why") or item.get("reason")
        rows.append(
            "<tr>"
            f"<td>{esc(item.get('field'))}</td>"
            f"<td>{text_html(item.get('current'))}</td>"
            f"<td>{text_html(item.get('recommended'))}</td>"
            f"<td>{esc(item.get('status'))}</td>"
            f"<td>{esc(why)}</td>"
            "</tr>"
        )
    return "\n".join(rows)


def build_html(data: dict[str, Any], audit_dir: Path, output: Path) -> None:
    identity = data.get("identity", {})
    current = data.get("current_state", {})
    ozon = current.get("ozon", {})
    wb = current.get("wb", {})
    proposed = data.get("proposed_final_card", {})
    sku = identity.get("internal_sku") or audit_dir.name
    media = data.get("media", {})
    if is_non_uniform_nr(str(sku)):
        summary_guardrail = "Для non-uniform `nr` карточек автоматически снят рукавный SEO-фокус."
    else:
        summary_guardrail = "Карточка проверена по применимым правилам типа изделия; нерелевантные guardrail-правила не применялись."

    photo_mode = media.get("owner_review_photo_mode") or "collage"
    collage = ""
    collage_raw = (
        media.get("collage")
        or media.get("photo_collage")
        or media.get("local_contact_sheet")
        or "photos/collage.jpg"
    )
    collage_path = resolve_asset_path(str(collage_raw), audit_dir)
    if photo_mode == "collage" and collage_path:
        collage = data_uri(collage_path)

    photo_audit_cards = []
    photo_items = (
        media.get("photo_audit")
        or media.get("owner_review_photo_audit")
        or media.get("photos")
        or data.get("photo_audit")
        or []
    )
    for item in photo_items:
        finding = (
            item.get("finding")
            or item.get("audit")
            or item.get("observed")
            or item.get("observation")
            or item.get("shows")
            or ""
        )
        confirms = item.get("confirms") or item.get("confirmed") or []
        note = item.get("missing_or_note") or item.get("missing")
        if isinstance(note, list):
            note = "; ".join(map(str, note))
        marketplace_raw = str(item.get("marketplace") or item.get("mp") or "")
        marketplace = {"ozon": "Ozon", "wb": "WB"}.get(marketplace_raw.lower(), marketplace_raw.upper())
        heading = f'{marketplace} {esc(item.get("position"))}'.strip()
        photo_audit_cards.append(
            f'<article class="photo-card"><h3>{heading}</h3>'
            f'<p>{esc(finding)}</p><p class="muted"><b>Подтверждено:</b> {esc("; ".join(map(str, confirms)))}</p>'
            + (f'<small class="note">{esc(note)}</small>' if note else "")
            + "</article>"
        )

    target_photo_set = media.get("target_marketplace_photo_set") or media.get("target_photo_set") or []
    target_photo_rows = []
    target_photo_list_items = []
    for item in target_photo_set:
        if isinstance(item, dict):
            target_photo_list_items.append(
                f"<p>{esc(item.get('position'))} — {esc(item.get('role') or item.get('purpose'))} — источник {esc(item.get('source'))}</p>"
            )
            target_photo_rows.append(
                f"<tr><td>{esc(item.get('position'))}</td><td>{esc(item.get('role') or item.get('purpose'))}</td>"
                f"<td>{esc(item.get('source'))}</td><td>{esc(item.get('note'))}</td></tr>"
            )
        else:
            target_photo_list_items.append(f"<p>{esc(item)}</p>")
            target_photo_rows.append(f"<tr><td colspan=\"4\">{esc(item)}</td></tr>")

    photo_counts = {
        "ozon": sum(1 for item in photo_items if str(item.get("marketplace") or item.get("mp") or "").lower() == "ozon"),
        "wb": sum(1 for item in photo_items if str(item.get("marketplace") or item.get("mp") or "").lower() == "wb"),
    }
    photo_display_note = media.get("owner_review_photo_display_note") or (
        f"На телефоне коллаж можно прокручивать горизонтально. Просмотрено {len(photo_items)} изображений: "
        f"Ozon {photo_counts['ozon']} и WB {photo_counts['wb']}."
    )

    rec_cards = []
    for rec in data.get("recommendations", []):
        current_value = rec.get("current")
        recommended_value = rec.get("recommended")
        if isinstance(current_value, (dict, list)):
            current_value = json.dumps(current_value, ensure_ascii=False, indent=2)
        if isinstance(recommended_value, (dict, list)):
            recommended_value = json.dumps(recommended_value, ensure_ascii=False, indent=2)
        rec_title = rec.get("title") or rec.get("field") or rec.get("id")
        why = rec.get("why") or rec.get("reason")
        rec_cards.append(
            f'<article class="rec {esc(rec.get("severity", "medium"))}">'
            f'<div class="rec-head"><h3>{esc(rec_title)}</h3><span>{esc(rec.get("status"))}</span></div>'
            f'<div class="flow"><div><b>Сейчас</b><p>{text_html(current_value)}</p></div>'
            f'<div><b>Рекомендую</b><p>{text_html(recommended_value)}</p></div></div>'
            f'<p class="why"><b>Почему:</b> {esc(why)}</p></article>'
        )

    seo_rows = []
    for row in data.get("seo", {}).get("confirmed_query_rows", []):
        decision = row.get("use_decision") or "используем"
        if is_non_uniform_nr(str(sku)) and re.search(r"(рукав|нарукав)", row.get("query", ""), re.I):
            decision = "не используем в title/hashtag"
        elif decision == "not_for_title_or_hashtags":
            decision = "не используем в title/hashtag"
        seo_rows.append(
            f"<tr><td>{esc(row.get('marketplace'))}</td><td>{esc(row.get('query'))}</td>"
            f"<td>{esc(row.get('frequency') or row.get('popularity'))}</td><td>{esc(row.get('role'))}</td><td>{esc(decision)}</td></tr>"
        )

    ozon_param_rows = []
    for attr in ozon.get("attributes", []):
        name = attr.get("attribute_name") or f"attribute_id {attr.get('attribute_id')}"
        values = "; ".join(map(str, attr.get("values", [])))
        ozon_param_rows.append(f"<tr><td>{esc(name)}</td><td>{esc(values)}</td><td>{esc(attr.get('attribute_id'))}</td></tr>")
    dims = ozon.get("dimensions") or {}
    if dims:
        ozon_param_rows.append(
            f"<tr><td>Габариты/вес упаковки</td><td>{esc(dims.get('width'))}*{esc(dims.get('depth'))}*{esc(dims.get('height'))} мм; {esc(dims.get('weight'))} г</td><td>dimensions</td></tr>"
        )
    model = ozon.get("model_info") or {}
    if model:
        ozon_param_rows.append(
            f"<tr><td>Группировка Ozon</td><td>model_id={esc(model.get('model_id'))}; count={esc(model.get('count'))}</td><td>model_info</td></tr>"
        )

    desc_blocks = proposed.get("description_blocks", ["", "", ""])
    physical = proposed.get("target_physical_parameters") if isinstance(proposed.get("target_physical_parameters"), dict) else {}
    color_value = proposed.get("colors") or proposed.get("color") or proposed.get("colour") or physical.get("color") or ""
    if isinstance(color_value, list):
        color_value = ", ".join(map(str, color_value))
    color_name = proposed.get("color_name") or physical.get("color_name") or ""

    wb_identity = (
        wb.get("vendor_code")
        or wb.get("vendorCode")
        or identity.get("wb", {}).get("vendorCode")
        or identity.get("wb", {}).get("vendor_code")
        or "карточки нет / не подтверждена"
    )

    html_text = f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Аудит карточки {esc(sku)}</title>
<style>
:root {{--bg:#f5f6f8;--panel:#fff;--text:#171a1f;--muted:#667085;--line:#d9dee7;--accent:#1769aa;--high:#b42318;--medium:#925a00;--low:#286c2f;--soft-high:#fff1f0;--soft-medium:#fff7e8;--soft-low:#eef9ef}}
*{{box-sizing:border-box}} html,body{{margin:0;max-width:100%;overflow-x:hidden}} body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial,sans-serif;background:var(--bg);color:var(--text);line-height:1.45}} header{{background:var(--panel);border-bottom:1px solid var(--line);padding:14px 16px}} main{{max-width:980px;margin:0 auto;padding:12px}} h1{{font-size:20px;margin:0 0 8px;overflow-wrap:anywhere}} h2{{font-size:18px;margin:0 0 12px}} h3{{font-size:15px;margin:0}} section,details{{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:14px;margin:12px 0}} .meta,.chips{{display:flex;flex-wrap:wrap;gap:8px}} .chip{{border:1px solid var(--line);border-radius:999px;padding:5px 9px;background:#fafbfc;font-size:13px}} .summary-box{{border-left:4px solid var(--accent)}} .ids,.field-grid,.flow,.photo-grid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}} .idbox,.field,.photo-card,.desc-block{{border:1px solid var(--line);border-radius:8px;padding:10px;background:#fafbfc;min-width:0}} .idbox b,.field span,.flow b{{display:block;color:var(--muted);font-size:12px;font-weight:600;margin-bottom:4px}} p,td,th,b,span,small{{overflow-wrap:anywhere}} .media-scroll{{max-width:100%;overflow-x:auto;border:1px solid var(--line);border-radius:8px;background:#fff}} .collage{{display:block;width:100%;min-width:720px;height:auto}} .photo-card img{{width:100%;height:auto;display:block;border-radius:6px;margin-bottom:8px}} .rec-grid,.desc-grid{{display:grid;gap:10px}} .rec{{border:1px solid var(--line);border-radius:10px;padding:12px}} .rec.high{{background:var(--soft-high);border-color:#ffc9c2}} .rec.medium{{background:var(--soft-medium);border-color:#ffdca8}} .rec.low{{background:var(--soft-low);border-color:#bfe8c4}} .rec-head{{display:flex;justify-content:space-between;align-items:flex-start;gap:10px;margin-bottom:10px}} .rec-head span{{border:1px solid currentColor;border-radius:999px;padding:3px 8px;font-size:12px;background:#fff;white-space:nowrap}} .flow div{{background:rgba(255,255,255,.72);border:1px solid rgba(0,0,0,.07);border-radius:8px;padding:9px;min-width:0}} .flow p,.why,.photo-card p{{margin:5px 0 0;font-size:14px;white-space:pre-wrap}} table{{width:100%;border-collapse:collapse;font-size:13px}} th,td{{border:1px solid var(--line);padding:8px;text-align:left;vertical-align:top}} th{{background:#edf2f4}} .table-wrap{{max-width:100%;overflow-x:auto}} .warn{{background:#fff7ed;border:1px solid #fed7aa;border-radius:8px;padding:10px}} .footer{{color:var(--muted);font-size:12px;padding:10px 2px 28px}} @media(max-width:760px){{main{{padding:8px}} section,details{{padding:12px;border-radius:8px;margin:10px 0}} .ids,.field-grid,.flow,.photo-grid{{grid-template-columns:1fr}} .rec-head{{display:block}} .rec-head span{{display:inline-block;margin-top:6px}}}}
</style></head><body><header><h1>Аудит карточки: {esc(sku)}</h1><div class="meta"><span class="chip">Layer 2</span><span class="chip">read-only</span><span class="chip">{esc(identity.get('marketplace_presence'))}</span></div></header><main>
<section class="summary-box"><h2>Краткий вывод</h2><p><b>Review-пакет подготовлен для согласования.</b> Проверены фото, текущие поля Ozon/WB, SEO-запросы, целевые параметры и будущий apply-scope. {esc(summary_guardrail)}</p></section>
<section><h2>Идентификаторы</h2><div class="ids"><div class="idbox"><b>Internal SKU</b><span>{esc(sku)}</span></div><div class="idbox"><b>Ozon</b><span>offer={esc(identity.get('ozon', {}).get('offer_id') or ozon.get('offer_id'))}; product={esc(identity.get('ozon', {}).get('product_id') or ozon.get('product_id'))}; sku={esc(identity.get('ozon', {}).get('sku') or ozon.get('sku'))}</span></div><div class="idbox"><b>WB</b><span>{esc(wb_identity)}</span></div><div class="idbox"><b>Товар</b><span>{esc(identity.get('product_name'))}</span></div></div></section>
<section><h2>Коллаж фото</h2>{f'<div class="media-scroll"><img class="collage" src="{collage}" alt="Коллаж всех фото карточки"></div>' if collage else '<p class="warn">Коллаж не найден, HTML не готов к отправке владельцу.</p>'}<p class="hint">{esc(photo_display_note)}</p></section>
<section><h2>Рекомендации: сейчас -> рекомендую -> почему</h2><div class="rec-grid">{''.join(rec_cards)}</div></section>
<section><h2>Фото-аудит</h2><div class="photo-grid">{''.join(photo_audit_cards)}</div>{f'<h3>Порядок фото для загрузки на Ozon и WB</h3><div class="photo-card">{"".join(target_photo_list_items)}</div><div class="table-wrap"><table><thead><tr><th>№</th><th>Что должно быть</th><th>Источник</th><th>Примечание</th></tr></thead><tbody>{"".join(target_photo_rows)}</tbody></table></div>' if target_photo_rows else ''}<p class="hint">{esc(media.get('target_marketplace_photo_set_note') or '')}</p></section>
<section><h2>Итоговый вариант</h2><div class="field-grid"><div class="field"><span>Название Ozon/WB</span><b>{esc(proposed.get('canonical_title'))}</b></div><div class="field"><span>Длина</span><b>{esc(proposed.get('title_length'))}</b></div><div class="field"><span>Цвет / название цвета</span><b>{esc(color_value)} / {esc(color_name)}</b></div><div class="field"><span>Ozon хештеги</span><b>{esc(proposed.get('ozon_hashtags'))}</b></div></div><h3>Описание, 3 блока</h3><div class="desc-grid">{''.join(f'<div class="desc-block"><b>Блок {i + 1}</b><p>{esc(block)}</p></div>' for i, block in enumerate(desc_blocks[:3]))}</div><p class="warn"><b>Правило:</b> упаковку, размеры упаковки и вес не пишем в продающем описании.</p></section>
<section><h2>SEO-запросы</h2><div class="table-wrap"><table><thead><tr><th>MP</th><th>Запрос</th><th>Частотность</th><th>Роль</th><th>Решение</th></tr></thead><tbody>{''.join(seo_rows)}</tbody></table></div></section>
<section><h2>Целевые поля Ozon</h2><div class="table-wrap"><table><thead><tr><th>Поле</th><th>Сейчас</th><th>Рекомендую</th><th>Статус</th><th>Почему</th></tr></thead><tbody>{field_rows(data.get('target_editor_fields', []), 'Ozon')}</tbody></table></div></section>
<section><h2>Целевые поля WB</h2><div class="table-wrap"><table><thead><tr><th>Поле</th><th>Сейчас</th><th>Рекомендую</th><th>Статус</th><th>Почему</th></tr></thead><tbody>{field_rows(data.get('target_editor_fields', []), 'WB')}</tbody></table></div></section>
<details open><summary>Все текущие параметры Ozon</summary><div class="table-wrap"><table><thead><tr><th>Поле</th><th>Сейчас</th><th>ID/Источник</th></tr></thead><tbody>{''.join(ozon_param_rows)}</tbody></table></div></details>
<section><h2>Решение владельца</h2><p>Варианты: <b>применяй</b>, <b>поменять ...</b>, <b>отложить</b>, <b>нет</b>.</p><p class="warn">Если после просмотра владелец пишет <b>применяй</b>, это approval только для действий, явно показанных в этом HTML и сохраненных в Layer 2/Layer 3 package.</p></section>
<p class="footer">Generated {esc(dt.datetime.now().astimezone().isoformat(timespec='seconds'))} from {esc(audit_dir / 'audit.json')}</p>
</main></body></html>"""
    output.write_text(html_text, encoding="utf-8")


def validate_html(path: Path) -> dict[str, Any]:
    script = f"""
const {{ chromium }} = require('playwright');
(async () => {{
  const browser = await chromium.launch({{headless:true}});
  const out = [];
  for (const [name, viewport] of Object.entries({{mobile:{{width:390,height:844}}, desktop:{{width:1366,height:1000}}}})) {{
    const page = await browser.newPage({{viewport}});
    await page.goto('file://{path.resolve()}', {{waitUntil:'load'}});
    const metrics = await page.evaluate(() => ({{
      scrollWidth: document.documentElement.scrollWidth,
      clientWidth: document.documentElement.clientWidth,
      imgCount: document.images.length,
      brokenImages: [...document.images].filter(img => !img.complete || img.naturalWidth === 0).length
    }}));
    out.push({{name, ...metrics, noHorizontalOverflow: metrics.scrollWidth === metrics.clientWidth}});
    await page.close();
  }}
  await browser.close();
  console.log(JSON.stringify(out));
}})();
"""
    env = os.environ.copy()
    env["NODE_PATH"] = "/home/Codex/agent-tools/node/node_modules"
    proc = subprocess.run(
        ["node", "-e", script],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    if proc.returncode != 0:
        raise SystemExit(proc.stderr.strip() or f"node validation failed with code {proc.returncode}")
    result = json.loads(proc.stdout)
    if not all(row["noHorizontalOverflow"] and row["brokenImages"] == 0 for row in result):
        raise SystemExit(f"HTML validation failed: {result}")
    return {"browser_checks": result}


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare a fast owner-review HTML from Layer 2 card audit.json.")
    parser.add_argument("audit_dir", type=Path)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--validate", action="store_true")
    parser.add_argument("--write-json", action="store_true", help="Persist guardrail corrections into audit.json.")
    args = parser.parse_args()

    audit_dir = args.audit_dir
    audit_path = audit_dir / "audit.json"
    data = normalize_review_data(read_json(audit_path), audit_dir)
    output = args.output or audit_dir / f"{data.get('identity', {}).get('internal_sku', audit_dir.name)}_owner_review_fast.html"
    build_html(data, audit_dir, output)
    validation: dict[str, Any] | None = None
    if args.validate:
        validation = validate_html(output)
        data.setdefault("validation", {})["fast_owner_review"] = {
            "html_path": str(output),
            "checked_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
            **validation,
        }
    if args.write_json or validation:
        write_json(audit_path, data)
    print(json.dumps({"html_path": str(output), "validation": validation}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
