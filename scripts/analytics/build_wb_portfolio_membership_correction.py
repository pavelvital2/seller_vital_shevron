#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
from decimal import Decimal
from html import escape
import json
from pathlib import Path
from typing import Any


LAUNCH_SEGMENTS = {"launch_priority", "launch_discovery"}


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str] | None = None) -> None:
    if fields is None:
        fields = list(rows[0]) if rows else ["nmID"]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def decimal_value(value: Any) -> Decimal:
    return Decimal(str(value).replace(",", ".")).quantize(Decimal("0.01"))


def campaign_memberships(payload: Any) -> dict[str, list[dict[str, Any]]]:
    campaigns = payload.get("adverts", []) if isinstance(payload, dict) else payload
    result: dict[str, list[dict[str, Any]]] = {}
    for campaign in campaigns or []:
        advert_id = str(campaign.get("id") or campaign.get("advertId") or "")
        name = str((campaign.get("settings") or {}).get("name") or campaign.get("name") or "")
        for item in campaign.get("nm_settings") or campaign.get("nmSettings") or []:
            nm_id = str(item.get("nm_id") or item.get("nmId") or "")
            bids = item.get("bids_kopecks") or item.get("bids") or {}
            search_kopecks = int(bids.get("search") or 0)
            result.setdefault(nm_id, []).append(
                {
                    "advert_id": advert_id,
                    "campaign_name": name,
                    "current_bid": Decimal(search_kopecks) / Decimal(100),
                }
            )
    return result


def build_correction(
    rows: list[dict[str, str]], memberships: dict[str, list[dict[str, Any]]]
) -> tuple[list[dict[str, str]], list[dict[str, str]], list[dict[str, str]], list[dict[str, str]]]:
    correction: list[dict[str, str]] = []
    no_ops: list[dict[str, str]] = []
    missing: list[dict[str, str]] = []
    ambiguous: list[dict[str, str]] = []
    for source in rows:
        if source.get("Первая волна") != "да" or source.get("Сегмент") not in LAUNCH_SEGMENTS:
            continue
        plan = str(source.get("План ставок") or "")
        if not plan.startswith("новая:"):
            continue
        target = decimal_value(plan.split(":", 1)[1])
        found = memberships.get(str(source.get("nmID") or ""), [])
        if not found:
            missing.append(dict(source))
            continue
        if len(found) != 1:
            row = dict(source)
            row["Найденные кампании"] = "; ".join(item["advert_id"] for item in found)
            ambiguous.append(row)
            continue
        membership = found[0]
        current = decimal_value(membership["current_bid"])
        row = dict(source)
        row["Кампании"] = membership["advert_id"]
        row["Текущая кампания"] = membership["campaign_name"]
        if current == target:
            row["План ставок"] = f"{membership['advert_id']}:{current}->{target}"
            no_ops.append(row)
        elif current < target:
            row["План ставок"] = f"{membership['advert_id']}:{current}->{target}"
            correction.append(row)
        else:
            row["Найденные кампании"] = f"current {current} > target {target}"
            ambiguous.append(row)
    return correction, no_ops, missing, ambiguous


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-plan", type=Path, required=True)
    parser.add_argument("--campaigns-json", type=Path, required=True)
    parser.add_argument("--expected-campaign-count", type=int, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    source_rows = read_csv(args.source_plan)
    campaigns_payload = json.loads(args.campaigns_json.read_text(encoding="utf-8"))
    campaigns = campaigns_payload.get("adverts", []) if isinstance(campaigns_payload, dict) else campaigns_payload
    if len(campaigns or []) != args.expected_campaign_count:
        raise RuntimeError(
            "campaign membership snapshot is partial: "
            f"expected {args.expected_campaign_count}, got {len(campaigns or [])}"
        )
    memberships = campaign_memberships(campaigns_payload)
    correction, no_ops, missing, ambiguous = build_correction(source_rows, memberships)
    fields = list(source_rows[0]) + ["Текущая кампания", "Найденные кампании"]
    write_csv(args.output_dir / "wb_portfolio_growth_plan_wave1.csv", correction, fields)
    write_csv(args.output_dir / "no_op_existing_memberships.csv", no_ops, fields)
    write_csv(args.output_dir / "missing_memberships.csv", missing, fields)
    write_csv(args.output_dir / "ambiguous_memberships.csv", ambiguous, fields)

    rows_html = "".join(
        "<tr>"
        f"<td>{escape(row['nmID'])}</td><td>{escape(row['Товар'])}</td>"
        f"<td>{escape(row.get('Текущая кампания', ''))}</td><td>{escape(row['План ставок'])}</td>"
        "</tr>"
        for row in correction
    )
    html = f"""<!doctype html><html lang=\"ru\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><title>WB correction</title>
<style>body{{font:14px Arial,sans-serif;letter-spacing:0;margin:20px;color:#17212b}}table{{border-collapse:collapse;width:100%}}th,td{{border:1px solid #d8dee4;padding:8px;text-align:left}}th{{background:#edf2f5}}code{{background:#edf2f5;padding:2px 4px}}</style></head><body>
<h1>WB: коррекция товаров, ошибочно отмеченных как новые</h1><p>Точных повышений: <b>{len(correction)}</b>; уже на цели: <b>{len(no_ops)}</b>; действительно отсутствуют: <b>{len(missing)}</b>; неоднозначных привязок: <b>{len(ambiguous)}</b>.</p>
<table><thead><tr><th>nmID</th><th>Товар</th><th>Текущая кампания</th><th>Изменение</th></tr></thead><tbody>{rows_html}</tbody></table>
</body></html>"""
    (args.output_dir / "report.html").write_text(html, encoding="utf-8")
    summary = {
        "correction_rows": len(correction),
        "no_op_rows": len(no_ops),
        "missing_rows": len(missing),
        "ambiguous_rows": len(ambiguous),
        "source_plan": str(args.source_plan),
        "campaigns_json": str(args.campaigns_json),
    }
    (args.output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if not ambiguous else 2


if __name__ == "__main__":
    raise SystemExit(main())
