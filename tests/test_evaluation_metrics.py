import json

from backend.app.services.evaluation_metrics import load_benchmark_history


def test_benchmark_history_uses_latest_representative_run(tmp_path):
    focused = {
        "version": "focused",
        "completed_at": "2026-08-27T12:00:00Z",
        "question_count": 1,
        "metrics": {"answer_success_rate": 1.0},
    }
    full_old = {
        "version": "full-old",
        "completed_at": "2026-08-26T12:00:00Z",
        "question_count": 30,
        "metrics": {"answer_success_rate": 0.8},
    }
    full_new = {
        "version": "full-new",
        "completed_at": "2026-08-27T11:00:00Z",
        "question_count": 30,
        "metrics": {"answer_success_rate": 0.93},
    }
    for name, payload in (
        ("end_to_end_1.json", focused),
        ("end_to_end_30_old.json", full_old),
        ("end_to_end_30_new.json", full_new),
    ):
        (tmp_path / name).write_text(json.dumps(payload), encoding="utf-8")

    history = load_benchmark_history(tmp_path)

    assert [item["version"] for item in history] == ["full-new", "full-old"]
    assert history[0]["question_count"] == 30
    assert history[0]["source"] == "benchmark_artifact"
