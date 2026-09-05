import json
from types import SimpleNamespace

import httpx
import pytest

from backend.app import llm
from backend.app.llm import LLMError, _extract_json


def test_json_array_is_wrapped_for_safe_caller_handling():
    assert _extract_json('[{"index": 0, "supported": true}]') == {
        "items": [{"index": 0, "supported": True}]
    }


class _FakeClient:
    def __init__(self, bodies):
        self.bodies = iter(bodies)
        self.payloads = []

    def post(self, endpoint, *, headers, json, timeout):
        self.payloads.append(__import__("copy").deepcopy(json))
        return httpx.Response(
            200,
            request=httpx.Request("POST", endpoint),
            json=next(self.bodies),
        )


def _settings(*, retries=1):
    return SimpleNamespace(
        llm_provider="gemini",
        gemini_api_key="test-key",
        llm_base_url="https://example.invalid/v1beta",
        llm_model="test-model",
        llm_request_timeout_seconds=8.0,
        llm_total_budget_seconds=10.0,
        llm_max_retries=retries,
        llm_circuit_breaker_failures=2,
        llm_circuit_breaker_cooldown_seconds=30.0,
    )


def _body(text, *, finish_reason="STOP", tokens=20):
    return {
        "candidates": [
            {
                "finishReason": finish_reason,
                "content": {"parts": [{"text": text}]},
            }
        ],
        "usageMetadata": {
            "promptTokenCount": 10,
            "candidatesTokenCount": tokens,
            "totalTokenCount": 10 + tokens,
        },
    }


def _reset_circuit():
    llm._CONSECUTIVE_FAILURES = 0
    llm._CIRCUIT_OPEN_UNTIL = 0.0


def test_max_tokens_gets_one_larger_compact_retry(monkeypatch):
    schema = {
        "type": "object",
        "properties": {"value": {"type": "string"}},
        "required": ["value"],
        "additionalProperties": False,
    }
    client = _FakeClient(
        [
            _body('{"value":', finish_reason="MAX_TOKENS", tokens=100),
            _body(json.dumps({"value": "complete"})),
        ]
    )
    monkeypatch.setattr(llm, "get_settings", lambda: _settings(retries=1))
    monkeypatch.setattr(llm, "_client", lambda: client)
    _reset_circuit()
    metrics = []

    result = llm.generate_json(
        system_prompt="Return data.",
        user_prompt="Question",
        max_output_tokens=100,
        response_schema=schema,
        request_metrics=metrics,
    )

    assert result == {"value": "complete"}
    assert len(client.payloads) == 2
    assert client.payloads[1]["generationConfig"]["maxOutputTokens"] == 1_400
    assert metrics[0]["finish_reason"] == "MAX_TOKENS"
    assert metrics[0]["truncated"] is True


def test_invalid_json_does_not_open_provider_circuit(monkeypatch):
    client = _FakeClient([_body('{"value":not-json}')])
    monkeypatch.setattr(llm, "get_settings", lambda: _settings(retries=0))
    monkeypatch.setattr(llm, "_client", lambda: client)
    _reset_circuit()

    with pytest.raises(LLMError, match="invalid JSON"):
        llm.generate_json(system_prompt="Return data.", user_prompt="Question")

    assert llm._CONSECUTIVE_FAILURES == 0
    assert llm._CIRCUIT_OPEN_UNTIL == 0.0


def test_thought_parts_are_not_concatenated_with_json(monkeypatch):
    body = _body(json.dumps({"value": "final"}))
    body["candidates"][0]["content"]["parts"].insert(
        0,
        {"thought": True, "text": "internal non-JSON reasoning"},
    )
    client = _FakeClient([body])
    monkeypatch.setattr(llm, "get_settings", lambda: _settings(retries=0))
    monkeypatch.setattr(llm, "_client", lambda: client)
    _reset_circuit()

    assert llm.generate_json(system_prompt="Return data.", user_prompt="Question") == {
        "value": "final"
    }


def test_provider_schema_omits_max_items_but_local_validation_keeps_the_limit(monkeypatch):
    schema = {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "maxItems": 1,
                "items": {"type": "string"},
            }
        },
        "required": ["items"],
        "additionalProperties": False,
    }
    client = _FakeClient([_body(json.dumps({"items": ["one"]}))])
    monkeypatch.setattr(llm, "get_settings", lambda: _settings(retries=0))
    monkeypatch.setattr(llm, "_client", lambda: client)
    _reset_circuit()

    result = llm.generate_json(
        system_prompt="Return data.",
        user_prompt="Question",
        response_schema=schema,
    )

    provider_schema = client.payloads[0]["generationConfig"]["responseJsonSchema"]
    assert "maxItems" not in provider_schema["properties"]["items"]
    assert result == {"items": ["one"]}

    with pytest.raises(LLMError, match="too many items"):
        llm._validate_schema_value({"items": ["one", "two"]}, schema)
