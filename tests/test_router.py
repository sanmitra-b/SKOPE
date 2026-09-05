from backend.app.orchestrator import route_query


def test_contract_penalty_routes_supplier_analytics_and_documents():
    route = route_query("Which suppliers were late and what contract penalty applies?")
    assert "supplier" in route.agents
    assert "analytics" in route.agents
    assert route.requires_documents
    assert route.requires_sql


def test_container_routes_logistics():
    route = route_query("Where is container MSKU1234567?")
    assert "logistics" in route.agents


def test_contract_late_delivery_credit_does_not_trigger_logistics_without_logistics_entity():
    route = route_query("What late-delivery credit applies in contract CON-P2-0101?")
    assert route.agents == ["supplier"]
    assert route.requires_documents
    assert not route.requires_sql


def test_risk_is_not_prediction():
    route = route_query("Summarize current supplier risk and mitigations")
    assert "risk" in route.agents
    assert route.intent == "multi_source"


def test_sales_query_is_structured_only():
    route = route_query("What is the sales for last quarter?")
    assert route.agents == ["analytics"]
    assert route.requires_sql
    assert not route.requires_documents
    assert route.intent == "structured"
