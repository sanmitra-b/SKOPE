from backend.app.models import Evidence
from backend.app.retrieval import exact_identifiers_satisfied, extract_identifiers


def test_identifier_extraction_handles_business_ids_and_containers():
    assert extract_identifiers("Check PO-P2-00123 and container MSKU1234567") == [
        "MSKU1234567",
        "PO-P2-00123",
    ]


def test_identifier_extraction_ignores_ordinary_numbers():
    assert extract_identifiers("Show the top 10 suppliers for quarter 3") == []


def test_exact_fast_path_requires_a_hit_for_every_identifier():
    evidence = [
        Evidence(
            evidence_id="DOC-1",
            evidence_type="document",
            title="PURCHASE_ORDER: PO-P2-00123",
            excerpt="Purchase order PO-P2-00123 is open.",
        )
    ]
    assert exact_identifiers_satisfied("Show PO-P2-00123", evidence)
    assert not exact_identifiers_satisfied("Compare PO-P2-00123 with PO-P2-00999", evidence)
