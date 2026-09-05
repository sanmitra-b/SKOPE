from fastapi.testclient import TestClient

from backend.app.config import get_settings
from backend.app.db import connection
from backend.app.main import app


client = TestClient(app)


def auth_headers():
    return {"Authorization": f"Bearer {get_settings().dev_auth_token}"}


def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] in {"ok", "degraded"}


def test_fastapi_serves_the_browser_application():
    page = client.get("/")
    assert page.status_code == 200
    assert 'src="/static/js/main.js"' in page.text

    config = client.get("/config.js")
    assert config.status_code == 200
    assert config.headers["content-type"].startswith("application/javascript")
    assert "window.SKOPE_CONFIG" in config.text
    assert get_settings().dev_auth_token not in config.text

    script = client.get("/static/js/main.js")
    assert script.status_code == 200
    assert script.headers["content-type"].startswith(("text/javascript", "application/javascript"))
    assert script.headers["cache-control"] == "no-cache"

    reviews = client.get("/static/js/pages/reviews.js")
    assert reviews.status_code == 200
    assert "Response review queue" in reviews.text


def test_auth_required():
    assert client.get("/api/me").status_code == 401


def test_me_and_index_status():
    me = client.get("/api/me", headers=auth_headers())
    assert me.status_code == 200
    assert "ADMINISTRATOR" in me.json()["roles"]
    index = client.get("/api/index/status", headers=auth_headers())
    assert index.status_code == 200
    assert index.json()["production_documents"] == 16200


def test_supplier_report_and_csv_export():
    report = client.get("/api/reports/suppliers", headers=auth_headers())
    assert report.status_code == 200
    assert isinstance(report.json(), list)
    export = client.get("/api/reports/suppliers.csv", headers=auth_headers())
    assert export.status_code == 200
    assert export.headers["content-type"].startswith("text/csv")
    assert "supplier_name" in export.text
    pdf_export = client.get("/api/reports/suppliers.pdf", headers=auth_headers())
    assert pdf_export.status_code == 200
    assert pdf_export.headers["content-type"] == "application/pdf"
    assert b"%PDF" in pdf_export.content


def test_admin_flag_list_is_protected():
    assert client.get("/api/admin/flags").status_code == 401
    assert client.get("/api/admin/flags", headers=auth_headers()).status_code == 200


def test_admin_metrics():
    res = client.get("/api/admin/metrics", headers=auth_headers())
    assert res.status_code == 200
    data = res.json()
    assert "conversations" in data
    assert "answers" in data
    assert "open_flags" in data
    assert "average_latency_ms" in data
    assert "ingestion_runs" in data
    assert "latest_evaluation" in data
    assert "evaluation_history" in data
    assert isinstance(data["evaluation_history"], list)
    assert "latest_benchmark" in data
    assert "benchmark_history" in data
    assert isinstance(data["benchmark_history"], list)
    assert "agent_volumes" in data
    assert isinstance(data["agent_volumes"], list)


def test_harmonized_email_source_file_is_available():
    with connection(readonly=True) as conn:
        row = conn.execute(
            """
            SELECT document_id
            FROM skope.document_index
            WHERE document_type = 'EMAIL_THREAD_JSON' AND rag_index_allowed
            ORDER BY document_id
            LIMIT 1
            """
        ).fetchone()
    assert row is not None
    response = client.get(
        f"/api/documents/{row['document_id']}/file",
        headers=auth_headers(),
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
