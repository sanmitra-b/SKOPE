from workers.ingestion.extraction import normalize_text, split_text


def test_normalize_text_removes_control_spacing_without_flattening_paragraphs():
    assert normalize_text("First\u00a0 line  \n\n\nSecond\tline") == "First line\n\nSecond line"


def test_split_text_is_bounded_and_preserves_document_coverage():
    text = "First sentence. " * 40
    chunks = split_text(text, max_chars=120, overlap=20)

    assert len(chunks) > 1
    assert all(0 < len(chunk) <= 120 for chunk in chunks)
    assert chunks[0].startswith("First sentence.")
    assert chunks[-1].endswith("First sentence.")
