"""One grounded synthesis pass shared by SKOPE's specialist capabilities."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Sequence

from .llm import generate_json
from .models import AgentResult, Claim, Evidence


AGENT_INSTRUCTIONS = {
    "supplier": "Supplier performance, obligations, contracts, penalties, and supplier actions.",
    "logistics": "Shipments, containers, routes, carriers, delays, customs, and delivery status.",
    "inventory": "Stock, receipts, shortages, warehouse operations, and SOP-grounded actions.",
    "analytics": "Read-only ERP results, units, dates, nulls, scope, KPIs, and aggregations.",
    "risk": "Current evidence-supported exposure, controls, and mitigations; never predictions or causality.",
    "executive_reporting": "Concise management summaries and evidence-supported recommended actions.",
}

NO_SUPPORTED_ANSWER = (
    "I could not produce a sufficiently supported answer from the available document and structured "
    "evidence. Try narrowing the entity, identifier, supplier, product, or date range."
)
NO_EVIDENCE_ANSWER = (
    "No document or structured evidence was retrieved for this question. "
    "Try specifying an identifier, supplier, product, warehouse, or date range."
)
UNVERIFIED_CLAIMS_ANSWER = (
    "Evidence was retrieved, but the proposed answer contained values or identifiers that could not "
    "be verified against its citations. No factual answer was returned."
)
PROVIDER_UNAVAILABLE_ANSWER = (
    "Evidence retrieval completed, but the reasoning provider was unavailable, so SKOPE did not "
    "generate an unverified answer."
)

SYNTHESIS_SCHEMA = {
    "type": "object",
    "properties": {
        "claims": {
            "type": "array",
            "description": "At most six concise, atomic, evidence-grounded claims.",
            "maxItems": 6,
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "One concise factual sentence."},
                    "evidence_ids": {"type": "array", "items": {"type": "string"}},
                    "agents": {
                        "type": "array",
                        "description": "Routed specialist capabilities supported by this claim.",
                        "items": {"type": "string", "enum": list(AGENT_INSTRUCTIONS)},
                    },
                },
                "required": ["text", "evidence_ids", "agents"],
                "additionalProperties": False,
            },
        },
        "warnings": {"type": "array", "maxItems": 3, "items": {"type": "string"}},
    },
    "required": ["claims", "warnings"],
    "additionalProperties": False,
}

_NUMBER = re.compile(r"(?<![A-Za-z0-9])[-+]?\d+(?:,\d{3})*(?:\.\d+)?%?(?![A-Za-z0-9])")
_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_SEPARATED_BUSINESS_ID = re.compile(
    r"\b(?=[A-Z0-9._/-]*[A-Z])(?=[A-Z0-9._/-]*\d)"
    r"[A-Z][A-Z0-9]{1,15}(?:[-_/][A-Z0-9.]{1,24})+\b",
    re.I,
)
_COMPACT_BUSINESS_ID = re.compile(
    r"\b(?=[A-Z0-9]{6,32}\b)(?=[A-Z0-9]*[A-Z])(?=[A-Z0-9]*\d)"
    r"[A-Z][A-Z0-9]{5,31}\b",
    re.I,
)


@dataclass
class SynthesisResult:
    answer: str
    claims: list[Claim]
    agent_results: list[AgentResult]
    warnings: list[str] = field(default_factory=list)
    provider_metrics: list[dict[str, Any]] = field(default_factory=list)


def _last_complete_quarter(snapshot_value: Any) -> tuple[date, date, int, int]:
    snapshot = date.fromisoformat(str(snapshot_value).split()[0])
    current_quarter_month = ((snapshot.month - 1) // 3) * 3 + 1
    current_quarter_start = date(snapshot.year, current_quarter_month, 1)
    quarter_end = current_quarter_start - timedelta(days=1)
    quarter_start_month = ((quarter_end.month - 1) // 3) * 3 + 1
    quarter_start = date(quarter_end.year, quarter_start_month, 1)
    quarter_number = ((quarter_start.month - 1) // 3) + 1
    return quarter_start, quarter_end, quarter_start.year, quarter_number


def _format_amount(value: Any) -> str:
    return f"{Decimal(str(value or 0)):,.2f}"


def _sales_quarter_summary(rows: Sequence[dict[str, Any]]) -> str:
    snapshot = max(str(row["maximum_data_as_of"]) for row in rows)
    quarter_start, quarter_end, quarter_year, quarter_number = _last_complete_quarter(snapshot)
    amounts: list[str] = []
    for row in rows:
        currency = str(row.get("currency_code") or "SOURCE_UNSPECIFIED")
        amount = _format_amount(row.get("sum_net_sales"))
        order_count = int(row.get("distinct_sales_order_count") or 0)
        order_label = "order" if order_count == 1 else "orders"
        if currency == "SOURCE_UNSPECIFIED":
            amounts.append(
                f"records with unspecified source currency totalled {amount} "
                f"across {order_count:,} distinct {order_label}"
            )
        else:
            amounts.append(
                f"{currency} {amount} across {order_count:,} distinct {order_label}"
            )
    amount_text = "; ".join(amounts)
    currency_note = (
        " Currency groups are reported separately; no currency conversion was applied."
        if len(rows) > 1
        else ""
    )
    return (
        f"Using the dataset snapshot dated {snapshot}, the last completed calendar quarter is "
        f"{quarter_year} Q{quarter_number} ({quarter_start} to {quarter_end}). "
        f"Net sales were {amount_text}.{currency_note}"
    )


def _deterministic_structured_result(
    selected_agents: Sequence[str],
    evidence: Sequence[Evidence],
) -> SynthesisResult | None:
    """Render completeness-sensitive tables locally instead of sampling them through Gemini."""
    structured = next(
        (
            item
            for item in evidence
            if item.evidence_type == "sql" and item.metadata.get("deterministic_render")
        ),
        None,
    )
    if structured is None:
        return None

    rows = structured.rows or []
    total_rows = int(structured.metadata.get("total_rows", len(rows)))
    returned_rows = int(structured.metadata.get("returned_rows", len(rows)))
    truncated = bool(structured.metadata.get("truncated"))
    plan = structured.metadata.get("query_plan") or {}
    dimensions = set(plan.get("dimensions") or [])
    measures = set(plan.get("measures") or [])

    if (
        plan.get("dataset") == "sales_orders"
        and "sum_net_sales" in measures
        and rows
    ):
        summary = _sales_quarter_summary(rows)
    elif (
        plan.get("dataset") == "purchase_order_lines"
        and dimensions == {"supplier_id", "supplier_name"}
        and "distinct_purchase_order_count" in measures
    ):
        if truncated:
            summary = f"{total_rows} suppliers have open purchase orders."
        else:
            open_orders = sum(
                int(row.get("distinct_purchase_order_count") or 0) for row in rows
            )
            summary = (
                f"{total_rows} suppliers have {open_orders} distinct open purchase orders."
            )
    elif (
        plan.get("dataset") == "purchase_order_lines"
        and "payment_terms" in dimensions
        and "distinct_purchase_order_count" in measures
    ):
        if truncated:
            summary = (
                f"The open-purchase-order payment-term breakdown contains {total_rows} rows."
            )
        else:
            open_orders = sum(
                int(row.get("distinct_purchase_order_count") or 0) for row in rows
            )
            summary = (
                "The open-purchase-order payment-term breakdown contains "
                f"{total_rows} rows covering {open_orders} distinct purchase orders."
            )
    elif total_rows == 0:
        summary = "No matching structured records were found."
    else:
        title = str(structured.metadata.get("result_title") or "Structured lookup")
        summary = f"{title} returned {total_rows} result rows."

    if truncated:
        summary += f" The table shows the first {returned_rows} rows."
    else:
        summary += " The complete result is shown in the table."

    claim = Claim(
        text=summary,
        evidence_ids=[structured.evidence_id],
        supported=True,
    )
    warnings = (
        [
            f"Showing {returned_rows} of {total_rows} rows; use pagination or export for the remainder."
        ]
        if truncated
        else []
    )
    return SynthesisResult(
        answer=summary,
        claims=[claim],
        agent_results=[
            AgentResult(
                agent=agent,
                status="completed",
                answer_fragment=summary,
                claims=[claim],
            )
            for agent in selected_agents
        ],
        warnings=warnings,
        provider_metrics=[],
    )


def render_evidence(evidence: Sequence[Evidence]) -> str:
    """Serialize a bounded evidence bundle; never send the full corpus to Gemini."""
    items = [
        {
            "evidence_id": item.evidence_id,
            "type": item.evidence_type,
            "title": item.title,
            "page": item.page,
            "section": item.section,
            "data_as_of": item.data_as_of,
            "excerpt": (item.excerpt or "")[:1_800],
            "rows": (item.rows or [])[:15],
        }
        for item in evidence[:10]
    ]
    return json.dumps(items, ensure_ascii=False, default=str, separators=(",", ":"))


def _support_text(item: Evidence) -> str:
    return " ".join(
        (
            item.title,
            item.excerpt or "",
            json.dumps((item.rows or [])[:30], ensure_ascii=False, default=str),
        )
    ).upper()


def _business_id_matches(text: str) -> list[re.Match[str]]:
    matches = [
        *list(_SEPARATED_BUSINESS_ID.finditer(text)),
        *list(_COMPACT_BUSINESS_ID.finditer(text)),
    ]
    return sorted(matches, key=lambda match: match.start())


def _business_ids(text: str) -> set[str]:
    """Extract identifier-shaped tokens containing both letters and digits."""
    return {match.group(0).upper() for match in _business_id_matches(text)}


def _decimal(token: str) -> Decimal | None:
    normalized = token.replace(",", "").removesuffix("%")
    try:
        return Decimal(normalized)
    except InvalidOperation:
        return None


def _numbers(text: str) -> set[Decimal]:
    """Extract standalone numbers, never numeric fragments inside business IDs."""
    identifier_spans = [match.span() for match in _business_id_matches(text)]
    values: set[Decimal] = set()
    for match in _NUMBER.finditer(text):
        if any(match.start() < end and match.end() > start for start, end in identifier_spans):
            continue
        value = _decimal(match.group(0))
        if value is not None:
            values.add(value)
    return values


def _iter_scalars(value: Any):
    if isinstance(value, dict):
        for child in value.values():
            yield from _iter_scalars(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield from _iter_scalars(child)
    elif value is not None:
        yield value


def _evidence_token_sets(evidence: Sequence[Evidence]) -> tuple[set[str], set[Decimal], set[str]]:
    identifiers: set[str] = set()
    numbers: set[Decimal] = set()
    dates: set[str] = set()
    for item in evidence:
        support = _support_text(item)
        identifiers.update(_business_ids(support))
        numbers.update(_numbers(support))
        dates.update(match.group(0) for match in _DATE.finditer(support))
        # Preserve typed SQL values so formatting differences such as 100.00
        # versus 100.00% do not create false rejections.
        for scalar in _iter_scalars(item.rows or []):
            if isinstance(scalar, (int, float, Decimal)) and not isinstance(scalar, bool):
                value = _decimal(str(scalar))
                if value is not None:
                    numbers.add(value)
            elif isinstance(scalar, str):
                identifiers.update(_business_ids(scalar))
                numbers.update(_numbers(scalar))
                dates.update(match.group(0) for match in _DATE.finditer(scalar))
    return identifiers, numbers, dates


def _check_verifiable_tokens(claim_text: str, cited_evidence: Sequence[Evidence]) -> bool:
    """Match IDs, dates, and normalized numeric values against cited evidence."""
    evidence_ids, evidence_numbers, evidence_dates = _evidence_token_sets(cited_evidence)
    claim_ids = _business_ids(claim_text)
    claim_numbers = _numbers(claim_text)
    claim_dates = {match.group(0) for match in _DATE.finditer(claim_text)}
    return (
        claim_ids.issubset(evidence_ids)
        and claim_numbers.issubset(evidence_numbers)
        and claim_dates.issubset(evidence_dates)
    )


def validate_claims(payload: dict[str, Any], evidence: Sequence[Evidence]) -> list[Claim]:
    """Validate citations and exact values locally; unsupported claims remain visible but withheld."""
    evidence_by_id = {item.evidence_id: item for item in evidence}
    claims: list[Claim] = []
    for raw in payload.get("claims", []):
        if not isinstance(raw, dict):
            continue
        text = str(raw.get("text", "")).strip()
        raw_ids = [str(value) for value in raw.get("evidence_ids", [])]
        if not text:
            continue
        ids_are_valid = bool(raw_ids) and all(value in evidence_by_id for value in raw_ids)
        cited_evidence = [evidence_by_id[value] for value in raw_ids if value in evidence_by_id]
        supported = ids_are_valid and _check_verifiable_tokens(text, cited_evidence)
        claims.append(Claim(text=text, evidence_ids=raw_ids, supported=supported))
    return claims


def render_supported_claims(
    claims: Sequence[Claim],
    *,
    abstention_answer: str = NO_SUPPORTED_ANSWER,
) -> str:
    """Render only locally accepted claims so unsupported model prose cannot leak into the answer."""
    supported = [claim.text.strip() for claim in claims if claim.supported and claim.text.strip()]
    return "\n\n".join(supported) if supported else abstention_answer


def _insufficient_evidence_answer(query: str) -> str:
    concise_query = " ".join(query.split())[:180]
    return (
        f'The retrieved evidence did not establish enough facts to answer “{concise_query}”. '
        "Try adding a specific identifier, entity, or date range."
    )


def _agent_results(
    selected_agents: Sequence[str],
    payload: dict[str, Any],
    claims: Sequence[Claim],
    evidence: Sequence[Evidence],
) -> list[AgentResult]:
    available_ids = {item.evidence_id for item in evidence}
    raw_claims = {
        str(item.get("text", "")).strip(): item
        for item in payload.get("claims", [])
        if isinstance(item, dict) and str(item.get("text", "")).strip()
    }
    results: list[AgentResult] = []
    for agent in selected_agents:
        related_claims = [
            claim
            for claim in claims
            if agent in raw_claims.get(claim.text, {}).get("agents", [])
        ]
        supported_claims = [claim for claim in related_claims if claim.supported]
        evidence_ids = [
            evidence_id
            for claim in supported_claims
            for evidence_id in claim.evidence_ids
            if evidence_id in available_ids
        ]
        results.append(
            AgentResult(
                agent=agent,
                status="completed" if supported_claims else "partial",
                answer_fragment=" ".join(claim.text for claim in supported_claims[:2]),
                claims=related_claims,
                warnings=[] if supported_claims else ["No supported agent-specific claim was returned."],
            )
        )
    return results


def synthesize_answer(
    query: str,
    selected_agents: Sequence[str],
    evidence: Sequence[Evidence],
    *,
    provider_deadline: float | None = None,
) -> SynthesisResult:
    """Run one schema-constrained synthesis for all routed specialist capabilities."""
    if not evidence:
        return SynthesisResult(
            answer=NO_EVIDENCE_ANSWER,
            claims=[],
            agent_results=[
                AgentResult(
                    agent=agent,
                    status="partial",
                    warnings=["No usable evidence was available."],
                )
                for agent in selected_agents
            ],
            warnings=["No usable evidence was available for synthesis."],
        )

    deterministic = _deterministic_structured_result(selected_agents, evidence)
    if deterministic is not None:
        return deterministic

    capabilities = "\n".join(
        f"- {agent}: {AGENT_INSTRUCTIONS[agent]}" for agent in selected_agents
    )
    system_prompt = f"""You are SKOPE's shared evidence synthesis engine.
The routed specialist capabilities are:
{capabilities}
Use only the supplied evidence. Never use outside knowledge or invent missing values.
Keep claims atomic. Preserve identifiers, quantities, units, dates, and qualifiers exactly.
Every factual claim must cite one or more exact evidence_id values.
Recommendations must be explicitly labelled and cite the facts that justify them.
Return at most six claims. Keep each claim to one concise sentence.
Assign every claim to its relevant routed specialists in the agents field; never add an unrouted specialist."""

    provider_metrics: list[dict[str, Any]] = []
    try:
        payload = generate_json(
            system_prompt=system_prompt,
            user_prompt=f"QUESTION:\n{query}\n\nEVIDENCE:\n{render_evidence(evidence)}",
            temperature=0.0,
            max_output_tokens=1_400,
            response_schema=SYNTHESIS_SCHEMA,
            request_metrics=provider_metrics,
            provider_deadline=provider_deadline,
        )
    except Exception as exc:
        return SynthesisResult(
            answer=PROVIDER_UNAVAILABLE_ANSWER,
            claims=[],
            agent_results=[
                AgentResult(
                    agent=agent,
                    status="failed",
                    warnings=["Shared reasoning provider was unavailable."],
                )
                for agent in selected_agents
            ],
            warnings=[f"Grounded synthesis failed: {exc}"],
            provider_metrics=provider_metrics,
        )

    claims = validate_claims(payload, evidence)
    rejected = sum(not claim.supported for claim in claims)
    warnings = [str(value) for value in payload.get("warnings", [])]
    if rejected:
        warnings.append(f"{rejected} claim(s) were withheld by deterministic citation/value checks.")
    abstention_answer = (
        UNVERIFIED_CLAIMS_ANSWER if claims else _insufficient_evidence_answer(query)
    )
    return SynthesisResult(
        answer=render_supported_claims(claims, abstention_answer=abstention_answer),
        claims=claims,
        agent_results=_agent_results(
            selected_agents,
            payload,
            claims,
            evidence,
        ),
        warnings=warnings,
        provider_metrics=provider_metrics,
    )


__all__ = [
    "AGENT_INSTRUCTIONS",
    "NO_SUPPORTED_ANSWER",
    "SynthesisResult",
    "render_evidence",
    "render_supported_claims",
    "synthesize_answer",
    "validate_claims",
]
