from seller_agent.catalog.loader import build_master_catalog, normalize_sku


def test_normalize_sku_keeps_significant_zeroes() -> None:
    assert normalize_sku(" 00123 ") == "00123"


def test_build_master_catalog_matches_by_exact_internal_sku() -> None:
    rows, summary = build_master_catalog(
        product_list=[{"offer_id": "abc", "product_id": 1}],
        product_info=[{"offer_id": "abc", "id": 1, "name": "Ozon title"}],
        wb_cards=[{"vendorCode": "abc", "nmID": 2, "title": "WB title"}],
    )

    assert len(rows) == 1
    assert rows[0].match_status == "matched"
    assert summary["matched_rows"] == 1

