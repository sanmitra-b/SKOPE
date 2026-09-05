from backend.app.routes.documents import _resolve_source_file


def test_source_resolver_finds_harmonized_phase1_file(tmp_path):
    source = tmp_path / "harmonized_data" / "EmailJson2500" / "email.json"
    source.parent.mkdir(parents=True)
    source.write_text("{}", encoding="utf-8")

    assert _resolve_source_file(tmp_path, "EmailJson2500/email.json") == source


def test_source_resolver_prefers_dataset_root_and_rejects_traversal(tmp_path):
    source = tmp_path / "Documents" / "contract.pdf"
    source.parent.mkdir()
    source.write_bytes(b"%PDF")

    assert _resolve_source_file(tmp_path, "Documents/contract.pdf") == source
    assert _resolve_source_file(tmp_path, "../outside.json") is None
