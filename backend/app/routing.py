"""Deterministic query routing for SKOPE's six specialist agents."""

from __future__ import annotations

from .models import RoutingDecision


AGENTS = (
    "supplier",
    "logistics",
    "inventory",
    "analytics",
    "risk",
    "executive_reporting",
)

DOMAIN_KEYWORDS = {
    "supplier": (
        "supplier",
        "vendor",
        "contract",
        "clause",
        "penalty",
        "purchase order",
        " po ",
        "procure",
    ),
    "logistics": (
        "shipment",
        "container",
        "carrier",
        "freight",
        "delivery",
        "route",
        "customs",
        "port",
        "tracking",
        "waybill",
        "bill of lading",
        "manifest",
    ),
    "inventory": (
        "inventory",
        "stock",
        "warehouse",
        "shortage",
        "receipt",
        "received",
        "damaged",
        "on hand",
        "on-hand",
        "sop",
        "invoice",
        "quality-control",
    ),
    "risk": (
        "risk",
        "exposure",
        "mitigation",
        "breach",
        "noncompliance",
        "exception",
        "red flag",
        "safety",
    ),
    "executive_reporting": (
        "report",
        "scorecard",
        "board",
        "executive",
        "summary",
        "summarize",
        "dashboard",
        "brief",
    ),
}

ANALYTICAL_KEYWORDS = (
    "how many",
    "top ",
    "most ",
    "least ",
    "lowest",
    "greatest",
    " versus ",
    "immediate action",
    "average",
    "percentage",
    "count",
    "total",
    "trend",
    "compare",
    "current",
    "open ",
    "late ",
    "sales",
    "revenue",
    "turnover",
)

DOCUMENT_KEYWORDS = ("contract", "clause", "email", "sop", "report", "why")
SQL_ENRICHMENT_KEYWORDS = ("penalty", "breach", "risk", "exposure", "scorecard")
LOGISTICS_ENTITY_KEYWORDS = (
    "shipment",
    "container",
    "carrier",
    "freight",
    "route",
    "customs",
    "port",
    "tracking",
    "waybill",
    "bill of lading",
    "manifest",
)


def _domain_agents(text: str) -> list[str]:
    padded = f" {text} "
    return [
        agent
        for agent, keywords in DOMAIN_KEYWORDS.items()
        if any(keyword in padded for keyword in keywords)
    ]


def _remove_false_logistics_match(text: str, selected: list[str]) -> None:
    """Avoid treating contractual 'late delivery credit' as a shipment query."""
    contract_context = any(
        word in text
        for word in ("contract", "clause", "supplier", "vendor", "penalty")
    )
    logistics_context = any(word in text for word in LOGISTICS_ENTITY_KEYWORDS)
    if contract_context and not logistics_context and "logistics" in selected:
        selected.remove("logistics")


def route_query(query: str) -> RoutingDecision:
    """Select agents and evidence tools using inspectable business rules."""
    text = query.lower()
    selected = _domain_agents(text)
    _remove_false_logistics_match(text, selected)

    requires_sql = any(keyword in text for keyword in ANALYTICAL_KEYWORDS)
    if any(keyword in text for keyword in ("sales", "revenue", "turnover")):
        selected.append("analytics")
    if requires_sql and "analytics" not in selected:
        selected.append("analytics")

    if not selected:
        selected = ["analytics", "executive_reporting"]
        requires_sql = True

    if any(keyword in text for keyword in SQL_ENRICHMENT_KEYWORDS):
        requires_sql = True
        if "analytics" not in selected:
            selected.append("analytics")

    if "purchase order" in text:
        requires_sql = True
        if "analytics" not in selected:
            selected.append("analytics")

    requires_documents = any(agent != "analytics" for agent in selected) or any(
        keyword in text for keyword in DOCUMENT_KEYWORDS
    )
    ordered_agents = [agent for agent in AGENTS if agent in selected]

    if requires_documents and requires_sql:
        intent = "multi_source"
    elif requires_documents:
        intent = "document"
    else:
        intent = "structured"

    return RoutingDecision(
        intent=intent,
        agents=ordered_agents,
        requires_documents=requires_documents,
        requires_sql=requires_sql,
        reason="Deterministic business-domain and tool-requirement routing.",
    )
