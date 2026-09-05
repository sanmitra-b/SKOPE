from decimal import Decimal

from backend.app.agent_reasoning import (
    NO_EVIDENCE_ANSWER,
    SYNTHESIS_SCHEMA,
    UNVERIFIED_CLAIMS_ANSWER,
    render_supported_claims,
    synthesize_answer,
    validate_claims,
)
from backend.app.models import Evidence


def _evidence():
    return Evidence(
        evidence_id="SQL-test",
        evidence_type="sql",
        title="Structured ERP query",
        rows=[{"po_id": "PO-P2-00420", "gap": 12}],
    )


def test_claim_validation_accepts_cited_exact_values():
    claims = validate_claims(
        {
            "claims": [
                {
                    "text": "PO-P2-00420 has a gap of 12 units.",
                    "evidence_ids": ["SQL-test"],
                }
            ]
        },
        [_evidence()],
    )
    assert claims[0].supported
    assert "12 units" in render_supported_claims(claims)


def test_claim_validation_withholds_unknown_values_and_citations():
    claims = validate_claims(
        {
            "claims": [
                {"text": "PO-P2-00420 has a gap of 99 units.", "evidence_ids": ["SQL-test"]},
                {"text": "The order is open.", "evidence_ids": ["SQL-missing"]},
            ]
        },
        [_evidence()],
    )
    assert [claim.supported for claim in claims] == [False, False]


def test_synthesis_schema_is_compact_and_does_not_duplicate_the_answer():
    assert "answer" not in SYNTHESIS_SCHEMA["properties"]
    assert SYNTHESIS_SCHEMA["properties"]["claims"]["maxItems"] == 6
    claim_properties = SYNTHESIS_SCHEMA["properties"]["claims"]["items"]["properties"]
    assert "agents" in claim_properties


def test_percentage_and_hyphenated_language_use_typed_sql_values():
    evidence = Evidence(
        evidence_id="SQL-scorecard",
        evidence_type="sql",
        title="Structured ERP query",
        rows=[
            {
                "supplier_name": "Aurora Medical Supplies",
                "on_time_percentage": Decimal("100.00"),
                "open_purchase_order_count": 6,
            }
        ],
    )
    claims = validate_claims(
        {
            "claims": [
                {
                    "text": (
                        "Aurora Medical Supplies has an on-time percentage of 100.00% "
                        "and 6 open purchase orders."
                    ),
                    "evidence_ids": ["SQL-scorecard"],
                }
            ]
        },
        [evidence],
    )
    assert claims[0].supported


def test_compact_product_ids_and_on_hand_language_are_verified():
    evidence = Evidence(
        evidence_id="SQL-inventory",
        evidence_type="sql",
        title="Structured ERP query",
        rows=[
            {
                "source_product_id": "PROD001183",
                "product_name": "Comics V3",
                "quantity_on_hand": Decimal("10.0000"),
                "data_as_of": "2026-07-31",
            },
            {
                "source_product_id": "PROD001958",
                "product_name": "Lays Chips V6",
                "quantity_on_hand": Decimal("10.0000"),
                "data_as_of": "2026-07-31",
            },
        ],
    )
    claims = validate_claims(
        {
            "claims": [
                {
                    "text": (
                        "The lowest on-hand quantity is 10.0000 units for Comics V3 "
                        "(PROD001183) and Lays Chips V6 (PROD001958) on 2026-07-31."
                    ),
                    "evidence_ids": ["SQL-inventory"],
                }
            ]
        },
        [evidence],
    )
    assert claims[0].supported


def test_digits_inside_product_id_do_not_support_an_unrelated_quantity():
    evidence = Evidence(
        evidence_id="SQL-product",
        evidence_type="sql",
        title="Structured ERP query",
        rows=[{"source_product_id": "PROD001183"}],
    )
    claims = validate_claims(
        {
            "claims": [
                {
                    "text": "The quantity is 1183 units.",
                    "evidence_ids": ["SQL-product"],
                }
            ]
        },
        [evidence],
    )
    assert not claims[0].supported


def test_no_evidence_has_a_specific_abstention_reason():
    result = synthesize_answer("Which shipment is delayed?", ["logistics"], [])
    assert result.answer == NO_EVIDENCE_ANSWER
    assert "No document or structured evidence" in result.answer


def test_retrieved_but_insufficient_evidence_names_the_question(monkeypatch):
    monkeypatch.setattr(
        "backend.app.agent_reasoning.generate_json",
        lambda **kwargs: {"claims": [], "warnings": []},
    )
    result = synthesize_answer(
        "Find bills of lading for delayed ocean shipments.",
        ["logistics"],
        [_evidence()],
    )
    assert "did not establish enough facts" in result.answer
    assert "delayed ocean shipments" in result.answer


def test_rejected_claims_have_a_verification_specific_abstention(monkeypatch):
    monkeypatch.setattr(
        "backend.app.agent_reasoning.generate_json",
        lambda **kwargs: {
            "claims": [
                {
                    "text": "PO-P2-00420 has a gap of 99 units.",
                    "evidence_ids": ["SQL-test"],
                    "agents": ["inventory"],
                }
            ],
            "warnings": [],
        },
    )
    result = synthesize_answer("Show the gap.", ["inventory"], [_evidence()])
    assert result.answer == UNVERIFIED_CLAIMS_ANSWER
