from backend.app import orchestrator


def test_retrieval_time_does_not_reduce_the_llm_request_budget(monkeypatch):
    captured = {}

    class FakeGraph:
        def invoke(self, state):
            captured.update(state)
            return {"timings_ms": {}}

    monkeypatch.setattr(orchestrator, "graph", lambda: FakeGraph())

    orchestrator.run_orchestrator("What do the SOPs say?")

    assert "provider_deadline" not in captured
