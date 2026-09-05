from backend.app.text_to_sql import template_sql


def test_exact_container_uses_trusted_template():
    generated = template_sql("Show container MSKU1234567 and its linked order")
    assert generated is not None
    assert generated.source == "template:container_exact"
    assert "analytics.container_status" in generated.sql


def test_late_supplier_query_uses_trusted_template():
    generated = template_sql("Which suppliers have late shipments?")
    assert generated is not None
    assert generated.source == "template:supplier_late_shipments"
    assert "late_shipment_count > 0" in generated.sql


def test_unfamiliar_analysis_keeps_llm_fallback_available():
    assert template_sql("Correlate unusual operational characteristics") is None


def test_route_arrival_variance_uses_trusted_template():
    generated = template_sql("Which routes have the greatest arrival variance?")
    assert generated is not None
    assert generated.source == "template:route_arrival_variance"
    assert "GROUP BY origin_city, destination_city" in generated.sql


def test_common_operational_questions_avoid_llm_sql():
    questions_and_sources = {
        "Which suppliers are in the high risk tier?": "template:high_risk_suppliers",
        "Compare purchase order value and received quantity by supplier.": "template:supplier_po_receipts",
        "What clauses govern late delivery credits?": "template:late_delivery_terms",
        "Which shipments arrived late and by how many days?": "template:late_shipments",
        "Show carrier performance by number of late deliveries.": "template:carrier_late_performance",
        "What is the current status of our containers?": "template:container_status",
        "Summarize goods received versus ordered quantities.": "template:ordered_received_quantities",
        "Which products have the lowest on-hand quantity?": "template:current_inventory",
        "Which purchase order lines remain partially received?": "template:partially_received_po_lines",
    }
    for question, source in questions_and_sources.items():
        generated = template_sql(question)
        assert generated is not None
        assert generated.source == source
