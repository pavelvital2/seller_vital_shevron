from scripts.analytics.ozon_cpc_post_apply_review import bid_drift_metrics


def test_bid_drift_metrics_confirms_exact_current_bids() -> None:
    summary, rows = bid_drift_metrics(
        [
            {"sku": "100", "target_bid": "3.40"},
            {"sku": "200", "target_bid": "1.80"},
        ],
        [
            {"sku": "100", "bid": "3400000"},
            {"sku": "200", "bid": "1800000"},
        ],
    )

    assert summary == {
        "status": "ok",
        "expected_products": 2,
        "matched_products": 2,
        "missing_products": 0,
        "drifted_products": 0,
        "ambiguous_products": 0,
        "status_counts": {"match": 2},
    }
    assert [row["status"] for row in rows] == ["match", "match"]


def test_bid_drift_metrics_reports_missing_drift_and_ambiguity() -> None:
    summary, rows = bid_drift_metrics(
        [
            {"sku": "100", "target_bid": "3.40"},
            {"sku": "200", "target_bid": "1.80"},
            {"sku": "300", "target_bid": "2.00"},
        ],
        [
            {"sku": "100", "bid": "3000000"},
            {"sku": "200", "bid": "1800000"},
            {"sku": "200", "bid": "1900000"},
        ],
    )

    assert summary["status"] == "warning"
    assert summary["drifted_products"] == 1
    assert summary["ambiguous_products"] == 1
    assert summary["missing_products"] == 1
    assert [row["status"] for row in rows] == ["drift", "ambiguous", "missing"]
