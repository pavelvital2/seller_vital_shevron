from scripts.supply.enrich_ozon_shipment_workbook import (
    build_live_offer_by_sku,
    build_summary,
    enrich_rows,
)


def test_enrich_rows_uses_live_offer_and_physical_quantity() -> None:
    source = [
        {
            "sheet": "Москва",
            "source_row": 1,
            "cluster": "Москва",
            "source_offer_id": "old",
            "ozon_sku": "100",
            "title": "Комплект",
            "planned_goods_qty": 8,
            "shipment_goods_qty": 7,
        }
    ]
    rows = enrich_rows(
        source,
        live_offer_by_sku={"100": "new"},
        pack_qty_by_sku={"100": 2},
    )
    assert rows[0]["current_offer_id"] == "new"
    assert rows[0]["seller_article_status"] == "updated"
    assert rows[0]["shipment_physical_qty"] == 14
    summary = build_summary(rows)
    assert summary["shipment_goods_qty"] == 7
    assert summary["shipment_physical_qty"] == 14
    assert summary["updated_article_mappings"] == [
        {
            "ozon_sku": "100",
            "source_offer_id": "old",
            "current_offer_id": "new",
            "title": "Комплект",
        }
    ]


def test_live_offer_mapping_rejects_ambiguous_sku() -> None:
    try:
        build_live_offer_by_sku(
            [
                {"sku": 100, "offer_id": "one"},
                {"sku": 100, "offer_id": "two"},
            ]
        )
    except RuntimeError as exc:
        assert "ambiguous" in str(exc)
    else:
        raise AssertionError("ambiguous SKU must fail")
