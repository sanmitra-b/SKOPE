from backend.app.auth import _normalized_roles


def test_normalized_roles_accepts_singular_firebase_claim() -> None:
    assert _normalized_roles({"role": "manager"}) == ["MANAGER"]


def test_normalized_roles_treats_string_as_one_role() -> None:
    assert _normalized_roles({"roles": "pii_operator"}) == ["PII_OPERATOR"]


def test_normalized_roles_defaults_for_invalid_claim() -> None:
    assert _normalized_roles({"roles": {"unexpected": True}}) == ["OPERATIONS_USER"]
