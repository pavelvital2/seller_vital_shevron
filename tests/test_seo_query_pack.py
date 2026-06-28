from __future__ import annotations

import csv
import json
from pathlib import Path

from seller_agent.tasks.seo_query_pack import build_seo_query_pack
from seller_agent.tasks.registry import get_task_definition


def test_seo_query_pack_builds_exact_theme_cluster(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    content_path = data_dir / "catalog" / "content" / "content_master.csv"
    query_path = data_dir / "queries.json"
    _write_csv(
        content_path,
        [
            {
                "internal_product_id": "p1",
                "internal_sku": "chev_back_fsb_pict0001",
                "product_group": "chev",
                "product_name": 'Шеврон "ФСБ", на спину, на липучке',
                "marketplace_presence": "ozon_wb",
                "mapping_status": "confirmed",
            }
        ],
    )
    _write_json(
        query_path,
        [
            {"marketplace": "ozon", "query": "шеврон фсб", "popularity": 268, "seed_query": "шеврон фсб"},
            {"marketplace": "wb", "query": "шеврон на липучке фсб", "frequency": 71, "seed_query": "шеврон фсб"},
            {"marketplace": "ozon", "query": "шеврон на спину", "popularity": 74, "seed_query": "шеврон"},
        ],
    )

    result = build_seo_query_pack(
        data_dir=data_dir,
        content_master_path=Path("catalog/content/content_master.csv"),
        query_source_paths=[Path("queries.json")],
        output_dir=data_dir / "catalog/content/seo_query_pack",
        run_id="seo_pack_test",
    )

    rows = _read_csv(Path(result["artifacts"]["card_seo_targets_csv"]))
    target_json = json.loads(Path(result["artifacts"]["card_seo_targets_json"]).read_text(encoding="utf-8"))[0]
    assert result["status_counts"] == {"ready": 1}
    assert rows[0]["primary_target"] == "шеврон ФСБ"
    assert rows[0]["secondary_targets"] == "шеврон на липучке ФСБ"
    assert rows[0]["placement_terms"] == "шеврон на спину; шеврон ФСБ на спину"
    assert target_json["confirmed_query_rows"] == [
        {
            "marketplace": "ozon",
            "query": "шеврон фсб",
            "matched_term": "шеврон ФСБ",
            "role": "primary_target",
            "seed_query": "шеврон фсб",
            "rank": "",
            "frequency": "",
            "popularity": 268,
            "period": "",
            "source": "",
            "collected_at": "",
        },
        {
            "marketplace": "ozon",
            "query": "шеврон на спину",
            "matched_term": "шеврон на спину",
            "role": "placement_terms",
            "seed_query": "шеврон",
            "rank": "",
            "frequency": "",
            "popularity": 74,
            "period": "",
            "source": "",
            "collected_at": "",
        },
        {
            "marketplace": "wb",
            "query": "шеврон на липучке фсб",
            "matched_term": "шеврон на липучке ФСБ",
            "role": "secondary_targets",
            "seed_query": "шеврон фсб",
            "rank": "",
            "frequency": 71,
            "popularity": "",
            "period": "",
            "source": "",
            "collected_at": "",
        },
    ]


def test_seo_query_pack_suppresses_sleeve_seo_for_thematic_nr_chevron(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    content_path = data_dir / "catalog" / "content" / "content_master.csv"
    query_path = data_dir / "queries.json"
    _write_csv(
        content_path,
        [
            {
                "internal_product_id": "p1",
                "internal_sku": "chev_nr_bpla_pict0028",
                "product_group": "chev",
                "product_name": "Шеврон БПЛА Смерть сходящая с небес",
                "marketplace_presence": "ozon_only",
                "mapping_status": "ozon_only",
            }
        ],
    )
    _write_json(
        query_path,
        [
            {"marketplace": "ozon", "query": "шеврон бпла", "popularity": 268, "seed_query": "шеврон бпла"},
            {"marketplace": "ozon", "query": "шеврон бпла на рукав", "popularity": 111, "seed_query": "шеврон"},
        ],
    )

    result = build_seo_query_pack(
        data_dir=data_dir,
        content_master_path=Path("catalog/content/content_master.csv"),
        query_source_paths=[Path("queries.json")],
        output_dir=data_dir / "catalog/content/seo_query_pack",
        run_id="seo_pack_test_bpla_nr",
    )

    rows = _read_csv(Path(result["artifacts"]["card_seo_targets_csv"]))
    target_json = json.loads(Path(result["artifacts"]["card_seo_targets_json"]).read_text(encoding="utf-8"))[0]
    assert result["status_counts"] == {"ready": 1}
    assert rows[0]["primary_target"] == "шеврон БПЛА"
    assert rows[0]["placement_code"] == "nr"
    assert rows[0]["placement_label"] == "на рукав"
    assert rows[0]["placement_terms"] == ""
    assert "шеврон на рукав" in rows[0]["excluded_terms"]
    assert target_json["target_query_clusters"]["placement"] == []
    assert all("рукав" not in row["query"] for row in target_json["confirmed_query_rows"])


def test_seo_query_pack_keeps_sleeve_seo_for_formal_nr_chevron(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    content_path = data_dir / "catalog" / "content" / "content_master.csv"
    query_path = data_dir / "queries.json"
    _write_csv(
        content_path,
        [
            {
                "internal_product_id": "p1",
                "internal_sku": "chev_nr_mvd_pict0001",
                "product_group": "chev",
                "product_name": "Шеврон МВД нарукавный",
                "marketplace_presence": "ozon_wb",
                "mapping_status": "confirmed",
            }
        ],
    )
    _write_json(
        query_path,
        [
            {"marketplace": "ozon", "query": "шеврон мвд", "popularity": 268, "seed_query": "шеврон мвд"},
            {"marketplace": "ozon", "query": "шеврон мвд на рукав", "popularity": 111, "seed_query": "шеврон"},
        ],
    )

    result = build_seo_query_pack(
        data_dir=data_dir,
        content_master_path=Path("catalog/content/content_master.csv"),
        query_source_paths=[Path("queries.json")],
        output_dir=data_dir / "catalog/content/seo_query_pack",
        run_id="seo_pack_test_mvd_nr",
    )

    rows = _read_csv(Path(result["artifacts"]["card_seo_targets_csv"]))
    assert result["status_counts"] == {"ready": 1}
    assert rows[0]["placement_terms"] == "шеврон на рукав; шеврон МВД на рукав"


def test_seo_query_pack_excludes_non_patch_assortment(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_csv(
        data_dir / "catalog" / "content" / "content_master.csv",
        [
            {
                "internal_product_id": "p2",
                "internal_sku": "",
                "product_group": "wb_only",
                "product_name": "Подсумок тактический для сброса магазинов, олива",
                "marketplace_presence": "wb_only",
                "mapping_status": "wb_only",
            }
        ],
    )
    _write_json(data_dir / "queries.json", [{"marketplace": "wb", "query": "шеврон", "frequency": 1000}])

    result = build_seo_query_pack(
        data_dir=data_dir,
        content_master_path=Path("catalog/content/content_master.csv"),
        query_source_paths=[Path("queries.json")],
        output_dir=data_dir / "catalog/content/seo_query_pack",
        run_id="seo_pack_test_excluded",
    )

    rows = _read_csv(Path(result["artifacts"]["card_seo_targets_csv"]))
    assert result["status_counts"] == {"excluded_non_patch_assortment": 1}
    assert rows[0]["product_kind"] == "other"
    assert rows[0]["manual_review_reason"] == "non_patch_assortment_or_unclassified_product"


def test_seo_query_pack_marks_unidentified_shevron_for_manual_review(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_csv(
        data_dir / "catalog" / "content" / "content_master.csv",
        [
            {
                "internal_product_id": "p3",
                "internal_sku": "",
                "product_group": "ozon_only",
                "product_name": "Шеврон 3109063147",
                "marketplace_presence": "ozon_only",
                "mapping_status": "ozon_only",
            }
        ],
    )
    _write_json(data_dir / "queries.json", [{"marketplace": "ozon", "query": "шеврон", "popularity": 5000}])

    result = build_seo_query_pack(
        data_dir=data_dir,
        content_master_path=Path("catalog/content/content_master.csv"),
        query_source_paths=[Path("queries.json")],
        output_dir=data_dir / "catalog/content/seo_query_pack",
        run_id="seo_pack_test_manual",
    )

    rows = _read_csv(Path(result["artifacts"]["card_seo_targets_csv"]))
    assert result["status_counts"] == {"needs_manual_review": 1}
    assert rows[0]["primary_target"] == "шеврон"
    assert "theme_not_detected" in rows[0]["manual_review_reason"]
    assert "placement_not_detected" in rows[0]["manual_review_reason"]
    assert "internal_sku_missing" in rows[0]["manual_review_reason"]


def test_task_registry_contains_seo_query_pack() -> None:
    task = get_task_definition("seo-query-pack")

    assert task["command"] == "seo-query-pack"
    assert task["mode"] == "read_only"
    assert task["requires_mapping"] is True
    assert task["runbook_path"] == "data/planning/search_queries_runbook.md"


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = sorted({key for row in rows for key in row})
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _write_json(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
