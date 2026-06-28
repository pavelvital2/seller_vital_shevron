from __future__ import annotations

import csv
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
import re
from typing import Any

from seller_agent.catalog.loader import normalize_sku
from seller_agent.core.run_manifest import write_summary_run_manifest
from seller_agent.reports.writer import ensure_dir, write_json


DEFAULT_CONTENT_MASTER_PATH = Path("catalog/content/content_master.csv")
DEFAULT_OUTPUT_DIR = Path("catalog/content/seo_query_pack")

QUERY_FIELDS = [
    "marketplace",
    "seed_query",
    "rank",
    "query",
    "frequency",
    "popularity",
    "period",
    "source",
    "collected_at",
]

CARD_TARGET_FIELDS = [
    "target_rank",
    "internal_product_id",
    "internal_sku",
    "product_name",
    "marketplace_presence",
    "mapping_status",
    "product_kind",
    "product_kind_label",
    "attachment_label",
    "theme_code",
    "theme_label",
    "placement_code",
    "placement_label",
    "kit_qty",
    "primary_target",
    "secondary_targets",
    "broad_identity_terms",
    "placement_terms",
    "excluded_terms",
    "ozon_confirmed_queries",
    "wb_confirmed_queries",
    "ozon_frequency_sum",
    "wb_frequency_sum",
    "query_pack_status",
    "manual_review_reason",
    "notes",
]

TYPE_LABELS = {
    "chev": "шеврон",
    "nash": "нашивка",
    "loop": "петлица",
    "other": "другой товар",
}

THEME_LABELS = {
    "bpla": "БПЛА",
    "berserk": "Берсерк",
    "brig": "бригада",
    "chvk": "ЧВК",
    "fan": "фанатский",
    "form": "форменный",
    "fsb": "ФСБ",
    "fsin": "ФСИН",
    "fso": "ФСО",
    "fssp": "ФССП",
    "gv": "группировка войск",
    "mvd": "МВД",
    "oborg": "общественная организация",
    "prikol": "прикол",
    "pz": "позывной",
    "raz": "разные",
    "rg": "Росгвардия",
    "sht": "Шторм",
    "svo": "СВО",
    "voisk": "войсковой",
}

THEME_KEYWORDS = [
    ("fsb", ("фсб", "пограничная служба")),
    ("mvd", ("мвд", "полиция", "дпс", "гибдд", "омон", "собр", "кинолог")),
    ("rg", ("росгвард", "фсвнг")),
    ("fsin", ("фсин",)),
    ("fso", ("фсо",)),
    ("fssp", ("фссп", "пристав")),
    ("bpla", ("бпла", "дрон")),
    ("sht", ("шторм",)),
    ("svo", ("сво",)),
    ("chvk", ("чвк",)),
    ("form", ("security", "секьюрити", "staff", "охрана", "press", "пресс", "контролер", "кадет")),
    ("pz", ("позывной",)),
    ("voisk", ("вдв", "пво", "рвсн", "рхбз", "вкс", "вмф", "войск", "военная", "медицинская служба")),
    ("prikol", ("прикол", "дураков", "рыболовные")),
]

PLACEMENT_LABELS = {
    "back": "на спину",
    "kp": "на кепку",
    "ng": "нагрудный",
    "nr": "на рукав",
}

PLACEMENT_KEYWORDS = [
    ("back", ("на спину", "наспин")),
    ("kp", ("на кепку", "кепку")),
    ("ng", ("нагруд", "на груд")),
    ("nr", ("на рукав", "нарукав", "рукав")),
]

ROLE_NAMES = ("primary_target", "secondary_targets", "broad_identity_terms", "placement_terms")

SLEEVE_SEO_ALLOWED_THEMES = {"form", "fsb", "fsin", "fso", "fssp", "gv", "mvd", "rg", "voisk"}


@dataclass(frozen=True)
class QueryMatch:
    query: str
    marketplace: str
    frequency: float


def _project_path(data_dir: Path, path: Path) -> Path:
    return path if path.is_absolute() else data_dir / path


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        raise FileNotFoundError(str(path))
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [
            {key: (value or "").strip() for key, value in row.items()}
            for row in csv.DictReader(handle)
        ]


def _write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    ensure_dir(path.parent)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def _read_json_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, list) else []


def _float_value(value: Any) -> float:
    text = str(value or "").replace("\xa0", "").replace(" ", "").replace(",", ".").strip()
    if not text:
        return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0


def _int_text(value: Any) -> int:
    try:
        return int(float(str(value or "0").replace(",", ".")))
    except ValueError:
        return 0


def _norm_query(value: Any) -> str:
    text = str(value or "").lower().replace("ё", "е")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _unique(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        text = re.sub(r"\s+", " ", str(value or "")).strip()
        if text and text not in result:
            result.append(text)
    return result


def _sku_parts(row: dict[str, str]) -> list[str]:
    sku = normalize_sku(row.get("internal_sku") or row.get("internal_product_id"))
    return [part for part in sku.split("_") if part]


def _product_kind(row: dict[str, str], parts: list[str]) -> str:
    if parts and parts[0] in TYPE_LABELS:
        return parts[0]
    group = normalize_sku(row.get("product_group"))
    if group in TYPE_LABELS:
        return group
    name = _norm_query(row.get("product_name"))
    if "шеврон" in name or "шевроны" in name:
        return "chev"
    if "нашив" in name:
        return "nash"
    if "петлиц" in name:
        return "loop"
    non_patch_needles = (
        "головной убор",
        "подсумок",
        "патронташ",
        "фартук",
        "носовой платок",
        "коврик",
        "юбка",
        "подшива",
        "плащ",
        "панама",
        "бафф",
        "снуд",
        "балаклава",
        "фальшпогоны",
    )
    if any(needle in name for needle in non_patch_needles):
        return "other"
    if not parts and group in {"wb_only", "ozon_only"}:
        return "other"
    return "chev"


def _kit_qty(row: dict[str, str], parts: list[str]) -> int:
    for part in parts:
        match = re.fullmatch(r"kit(\d+)", part)
        if match:
            return int(match.group(1))
    pack_qty = _int_text(row.get("pack_qty"))
    name = _norm_query(row.get("product_name"))
    if "комплект" in name and pack_qty > 1:
        return pack_qty
    return 0


def _theme_code(row: dict[str, str], parts: list[str]) -> str:
    ignore = set(TYPE_LABELS) | set(PLACEMENT_LABELS) | {"kit2", "kit3", "kit4", "kit5", "text", "pict"}
    for part in parts:
        if re.fullmatch(r"kit\d+", part) or re.fullmatch(r"(text|pict)\d+", part):
            continue
        if part in ignore:
            continue
        if part in THEME_LABELS:
            return part
    name = _norm_query(row.get("product_name"))
    for code, needles in THEME_KEYWORDS:
        if any(needle in name for needle in needles):
            return code
    return ""


def _placement_code(row: dict[str, str], parts: list[str]) -> str:
    for part in parts:
        if part in PLACEMENT_LABELS:
            return part
    name = _norm_query(row.get("product_name"))
    for code, needles in PLACEMENT_KEYWORDS:
        if any(needle in name for needle in needles):
            return code
    return ""


def _attachment_label(product_kind: str) -> str:
    if product_kind == "nash":
        return "без липучки"
    if product_kind in {"chev", "loop"}:
        return "на липучке"
    return ""


def _allow_placement_seo(product_kind: str, placement_code: str, theme_code: str) -> bool:
    if placement_code != "nr":
        return True
    if product_kind != "chev":
        return True
    return theme_code in SLEEVE_SEO_ALLOWED_THEMES


def _base_type_query(product_kind: str, kit_qty: int) -> str:
    label = TYPE_LABELS.get(product_kind, "шеврон")
    if kit_qty > 1:
        if product_kind == "nash":
            return "комплект нашивок"
        if product_kind == "loop":
            return "комплект петлиц"
        return "комплект шевронов"
    return label


def _manual_review_reasons(row: dict[str, str], cluster: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    product_kind = cluster["product_kind"]
    if product_kind == "other":
        return ["non_patch_assortment_or_unclassified_product"]
    if not cluster["theme_code"]:
        reasons.append("theme_not_detected")
    if product_kind == "chev" and not int(cluster["kit_qty"] or 0) and not cluster["placement_code"]:
        reasons.append("placement_not_detected")
    if not row.get("internal_sku", "").strip():
        reasons.append("internal_sku_missing")
    return reasons


def _status_for_cluster(
    *,
    query_rows: list[dict[str, Any]],
    manual_review_reasons: list[str],
    exact_matches: list[QueryMatch],
    all_matches: list[QueryMatch],
) -> str:
    if not query_rows:
        return "blocked_no_query_tables"
    if "non_patch_assortment_or_unclassified_product" in manual_review_reasons:
        return "excluded_non_patch_assortment"
    if manual_review_reasons:
        return "needs_manual_review"
    if exact_matches:
        return "ready"
    if all_matches:
        return "ready_broad_only"
    return "target_cluster_without_frequency"


def _query_cluster(row: dict[str, str]) -> dict[str, Any]:
    parts = _sku_parts(row)
    product_kind = _product_kind(row, parts)
    kit_qty = _kit_qty(row, parts)
    theme_code = _theme_code(row, parts)
    placement_code = _placement_code(row, parts)
    theme_label = THEME_LABELS.get(theme_code, "")
    placement_label = PLACEMENT_LABELS.get(placement_code, "")
    attachment = _attachment_label(product_kind)
    base_type = _base_type_query(product_kind, kit_qty)
    single_type = TYPE_LABELS.get(product_kind, "шеврон")

    broad: list[str] = []
    if product_kind == "chev":
        broad = ["шеврон", "шеврон на липучке"]
        if kit_qty > 1:
            broad.insert(0, "комплект шевронов")
    elif product_kind == "nash":
        broad = ["нашивка", "нашивка без липучки", "патч"]
        if kit_qty > 1:
            broad.insert(0, "комплект нашивок")
    elif product_kind == "loop":
        broad = ["петлица", "петлицы"]
        if kit_qty > 1:
            broad.insert(0, "комплект петлиц")

    primary: list[str] = []
    if theme_label:
        primary.append(f"{base_type} {theme_label}")
        if product_kind == "chev" and kit_qty == 0:
            primary.append(f"шеврон {theme_label}")
    else:
        primary.append(base_type)

    secondary: list[str] = []
    if product_kind == "chev" and attachment == "на липучке" and theme_label:
        secondary.append(f"шеврон на липучке {theme_label}")
    if theme_code == "pz":
        secondary.extend(["шеврон позывной", "шеврон с позывным"])
        if kit_qty > 1:
            secondary.append("комплект шевронов позывной")
    if product_kind == "nash" and theme_label:
        secondary.append(f"нашивка {theme_label}")
    if product_kind == "loop" and theme_label:
        secondary.append(f"петлица {theme_label}")

    placement_terms: list[str] = []
    allow_placement_seo = _allow_placement_seo(product_kind, placement_code, theme_code)
    if placement_label and allow_placement_seo:
        placement_terms.append(f"{single_type} {placement_label}")
        if theme_label and product_kind == "chev":
            placement_terms.append(f"шеврон {theme_label} {placement_label}")

    excluded = []
    other_themes = ["ФСБ", "МВД", "Росгвардия", "ФСИН", "БПЛА", "СВО", "Шторм", "позывной"]
    for other in other_themes:
        if theme_label and other.lower() != theme_label.lower():
            excluded.append(f"шеврон {other}")
    for code, label in PLACEMENT_LABELS.items():
        if placement_code and code != placement_code:
            excluded.append(f"шеврон {label}")
    if placement_code == "nr" and not allow_placement_seo:
        excluded.append("шеврон на рукав")
        if theme_label and product_kind == "chev":
            excluded.append(f"шеврон {theme_label} на рукав")
    if product_kind != "nash":
        excluded.append("нашивка без липучки")
    if product_kind != "loop":
        excluded.append("петлица")

    return {
        "product_kind": product_kind,
        "product_kind_label": TYPE_LABELS.get(product_kind, "шеврон"),
        "attachment_label": attachment,
        "theme_code": theme_code,
        "theme_label": theme_label,
        "placement_code": placement_code,
        "placement_label": placement_label,
        "kit_qty": kit_qty,
        "primary_target": _unique(primary)[0] if primary else "",
        "secondary_targets": _unique(secondary),
        "broad_identity_terms": _unique(broad),
        "placement_terms": _unique(placement_terms),
        "excluded_terms": _unique(excluded),
    }


def _load_query_rows(paths: list[Path]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in paths:
        if not path.exists():
            continue
        if path.suffix.lower() == ".json":
            source_rows = _read_json_rows(path)
        else:
            source_rows = _read_csv(path)
        for row in source_rows:
            query = normalize_sku(row.get("query"))
            marketplace = normalize_sku(row.get("marketplace"))
            if not query or marketplace not in {"ozon", "wb"}:
                continue
            rows.append(row)
    return rows


def _query_index(query_rows: list[dict[str, Any]]) -> dict[tuple[str, str], QueryMatch]:
    result: dict[tuple[str, str], QueryMatch] = {}
    for row in query_rows:
        marketplace = normalize_sku(row.get("marketplace"))
        query = normalize_sku(row.get("query"))
        key = (marketplace, _norm_query(query))
        frequency = _float_value(row.get("frequency") or row.get("popularity"))
        current = result.get(key)
        if current is None or frequency > current.frequency:
            result[key] = QueryMatch(query=query, marketplace=marketplace, frequency=frequency)
    return result


def _query_row_index(query_rows: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    result: dict[tuple[str, str], dict[str, Any]] = {}
    for row in query_rows:
        marketplace = normalize_sku(row.get("marketplace"))
        query = normalize_sku(row.get("query"))
        key = (marketplace, _norm_query(query))
        frequency = _float_value(row.get("frequency") or row.get("popularity"))
        current = result.get(key)
        current_frequency = _float_value((current or {}).get("frequency") or (current or {}).get("popularity"))
        if current is None or frequency > current_frequency:
            result[key] = row
    return result


def _confirmed_terms(terms: list[str], marketplace: str, query_index: dict[tuple[str, str], QueryMatch]) -> list[QueryMatch]:
    matches: list[QueryMatch] = []
    for term in terms:
        match = query_index.get((marketplace, _norm_query(term)))
        if match and match.query not in {item.query for item in matches}:
            matches.append(match)
    return matches


def _number_for_json(value: Any) -> int | float | str:
    if not str(value or "").strip():
        return ""
    number = _float_value(value)
    return int(number) if number.is_integer() else number


def _confirmed_query_rows(
    terms_by_role: dict[str, list[str]],
    marketplace: str,
    query_row_index: dict[tuple[str, str], dict[str, Any]],
) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for role in ROLE_NAMES:
        for term in terms_by_role.get(role, []):
            row = query_row_index.get((marketplace, _norm_query(term)))
            if not row:
                continue
            query = normalize_sku(row.get("query"))
            key = (marketplace, _norm_query(query))
            if key in seen:
                continue
            seen.add(key)
            matches.append(
                {
                    "marketplace": marketplace,
                    "query": query,
                    "matched_term": term,
                    "role": role,
                    "seed_query": row.get("seed_query", ""),
                    "rank": _number_for_json(row.get("rank")),
                    "frequency": _number_for_json(row.get("frequency")),
                    "popularity": _number_for_json(row.get("popularity")),
                    "period": row.get("period", ""),
                    "source": row.get("source", ""),
                    "collected_at": row.get("collected_at", ""),
                }
            )
    return matches


def _group_confirmed_rows(rows: list[dict[str, Any]], field: str) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        key = normalize_sku(row.get(field)) or "unknown"
        result.setdefault(key, []).append(row)
    return result


def _join(values: list[Any]) -> str:
    return "; ".join(str(value) for value in values if str(value).strip())


def build_seo_query_pack(
    *,
    data_dir: Path,
    content_master_path: Path,
    query_source_paths: list[Path],
    output_dir: Path,
    run_id: str | None = None,
) -> dict[str, Any]:
    started_at = datetime.now().isoformat(timespec="seconds")
    run_id = run_id or f"seo_query_pack_{datetime.now().strftime('%Y%m%dT%H%M%S')}"
    run_dir = data_dir / "runs" / datetime.now().strftime("%Y-%m-%d") / run_id
    ensure_dir(run_dir)
    ensure_dir(output_dir)

    content_rows = _read_csv(_project_path(data_dir, content_master_path))
    query_paths = [_project_path(data_dir, path) for path in query_source_paths]
    query_rows = _load_query_rows(query_paths)
    query_index = _query_index(query_rows)
    query_row_index = _query_row_index(query_rows)

    combined_query_rows: list[dict[str, Any]] = []
    for row in query_rows:
        combined_query_rows.append({field: row.get(field, "") for field in QUERY_FIELDS})

    target_rows: list[dict[str, Any]] = []
    target_json: list[dict[str, Any]] = []
    status_counts: Counter[str] = Counter()
    for index, row in enumerate(content_rows, start=1):
        cluster = _query_cluster(row)
        terms_by_role = {
            "primary_target": [cluster["primary_target"]] if cluster["primary_target"] else [],
            "secondary_targets": cluster["secondary_targets"],
            "broad_identity_terms": cluster["broad_identity_terms"],
            "placement_terms": cluster["placement_terms"],
        }
        all_terms = _unique([term for role in ROLE_NAMES for term in terms_by_role[role]])
        exact_terms = _unique(
            terms_by_role["primary_target"]
            + terms_by_role["secondary_targets"]
            + terms_by_role["placement_terms"]
        )
        ozon_matches = _confirmed_terms(all_terms, "ozon", query_index)
        wb_matches = _confirmed_terms(all_terms, "wb", query_index)
        ozon_confirmed_rows = _confirmed_query_rows(terms_by_role, "ozon", query_row_index)
        wb_confirmed_rows = _confirmed_query_rows(terms_by_role, "wb", query_row_index)
        confirmed_rows = ozon_confirmed_rows + wb_confirmed_rows
        exact_matches = _confirmed_terms(exact_terms, "ozon", query_index) + _confirmed_terms(
            exact_terms,
            "wb",
            query_index,
        )
        manual_review_reasons = _manual_review_reasons(row, cluster)
        status = _status_for_cluster(
            query_rows=query_rows,
            manual_review_reasons=manual_review_reasons,
            exact_matches=exact_matches,
            all_matches=ozon_matches + wb_matches,
        )
        status_counts[status] += 1

        target_row = {
            "target_rank": index,
            "internal_product_id": row.get("internal_product_id", ""),
            "internal_sku": row.get("internal_sku", ""),
            "product_name": row.get("product_name", ""),
            "marketplace_presence": row.get("marketplace_presence", ""),
            "mapping_status": row.get("mapping_status", ""),
            "product_kind": cluster["product_kind"],
            "product_kind_label": cluster["product_kind_label"],
            "attachment_label": cluster["attachment_label"],
            "theme_code": cluster["theme_code"],
            "theme_label": cluster["theme_label"],
            "placement_code": cluster["placement_code"],
            "placement_label": cluster["placement_label"],
            "kit_qty": cluster["kit_qty"] or "",
            "primary_target": cluster["primary_target"],
            "secondary_targets": _join(cluster["secondary_targets"]),
            "broad_identity_terms": _join(cluster["broad_identity_terms"]),
            "placement_terms": _join(cluster["placement_terms"]),
            "excluded_terms": _join(cluster["excluded_terms"]),
            "ozon_confirmed_queries": _join([f"{item.query}:{int(item.frequency) if item.frequency.is_integer() else item.frequency}" for item in ozon_matches]),
            "wb_confirmed_queries": _join([f"{item.query}:{int(item.frequency) if item.frequency.is_integer() else item.frequency}" for item in wb_matches]),
            "ozon_frequency_sum": int(sum(item.frequency for item in ozon_matches)),
            "wb_frequency_sum": int(sum(item.frequency for item in wb_matches)),
            "query_pack_status": status,
            "manual_review_reason": _join(manual_review_reasons),
            "notes": "parser positions are baseline/monitoring, not mandatory for first-pass cluster charging",
        }
        target_rows.append(target_row)
        target_json.append(
            {
                **target_row,
                "target_query_clusters": {
                    "primary_target": terms_by_role["primary_target"],
                    "secondary_target": terms_by_role["secondary_targets"],
                    "broad_identity": terms_by_role["broad_identity_terms"],
                    "placement": terms_by_role["placement_terms"],
                    "exclude": cluster["excluded_terms"],
                },
                "confirmed_query_rows": confirmed_rows,
                "confirmed_query_rows_by_marketplace": {
                    "ozon": ozon_confirmed_rows,
                    "wb": wb_confirmed_rows,
                },
                "confirmed_query_rows_by_role": _group_confirmed_rows(confirmed_rows, "role"),
            }
        )

    artifacts = {
        "source_queries_csv": str(run_dir / "source_queries.csv"),
        "source_queries_json": str(run_dir / "source_queries.json"),
        "card_seo_targets_csv": str(run_dir / "card_seo_targets.csv"),
        "card_seo_targets_json": str(run_dir / "card_seo_targets.json"),
        "seo_query_pack_json": str(run_dir / "seo_query_pack.json"),
        "summary": str(run_dir / "summary.json"),
        "latest_card_seo_targets_csv": str(output_dir / "card_seo_targets.csv"),
        "latest_seo_query_pack_json": str(output_dir / "seo_query_pack.json"),
    }

    _write_csv(run_dir / "source_queries.csv", combined_query_rows, QUERY_FIELDS)
    write_json(run_dir / "source_queries.json", combined_query_rows)
    _write_csv(run_dir / "card_seo_targets.csv", target_rows, CARD_TARGET_FIELDS)
    write_json(run_dir / "card_seo_targets.json", target_json)

    seo_pack = {
        "run_id": run_id,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "purpose": "first_pass_card_cluster_charging",
        "parser_role": "optional_baseline_monitoring_after_changes",
        "query_source_paths": [str(path) for path in query_paths],
        "source_queries_count": len(combined_query_rows),
        "card_targets_count": len(target_rows),
        "status_counts": dict(status_counts),
        "source_queries": combined_query_rows,
        "card_targets": target_json,
    }
    write_json(run_dir / "seo_query_pack.json", seo_pack)

    _write_csv(output_dir / "card_seo_targets.csv", target_rows, CARD_TARGET_FIELDS)
    write_json(output_dir / "card_seo_targets.json", target_json)
    write_json(output_dir / "source_queries.json", combined_query_rows)
    write_json(output_dir / "seo_query_pack.json", seo_pack)

    summary = {
        "run_id": run_id,
        "started_at": started_at,
        "finished_at": datetime.now().isoformat(timespec="seconds"),
        "overall_status": "ok" if query_rows else "warning",
        "mode": "read_only",
        "content_rows": len(content_rows),
        "source_queries": len(combined_query_rows),
        "card_targets": len(target_rows),
        "status_counts": dict(status_counts),
        "artifacts": artifacts,
        "limitations": []
        if query_rows
        else ["No query source rows were loaded; card targets do not have demand frequency evidence."],
    }
    write_json(run_dir / "summary.json", summary)
    write_summary_run_manifest(
        data_dir=data_dir,
        run_dir=run_dir,
        summary=summary,
        task="seo-query-pack",
        mode="read_only",
        risk="low",
        marketplaces=["ozon", "wb"],
        inputs={
            "content_master_path": str(_project_path(data_dir, content_master_path)),
            "query_source_paths": [str(path) for path in query_paths],
            "output_dir": str(output_dir),
        },
    )
    return summary
