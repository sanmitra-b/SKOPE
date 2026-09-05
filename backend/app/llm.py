"""Pooled Gemini REST gateway with schemas, deadlines, and bounded retries."""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from functools import lru_cache
from typing import Any

import httpx

from .config import get_settings


logger = logging.getLogger("skope.llm")
_CIRCUIT_LOCK = threading.Lock()
_CONSECUTIVE_FAILURES = 0
_CIRCUIT_OPEN_UNTIL = 0.0


class LLMError(RuntimeError):
    pass


class LLMTruncatedError(LLMError):
    """Raised when Gemini stops because the configured output budget is exhausted."""


def _extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            raise LLMError("Model response did not contain a JSON object.")
        try:
            value = json.loads(text[start : end + 1])
        except json.JSONDecodeError as exc:
            raise LLMError("Model response contained invalid JSON.") from exc
    if isinstance(value, list):
        # Gemini occasionally returns the requested list rather than its
        # containing object. Preserve it in a stable wrapper so callers can
        # fail safely or explicitly consume it (the verifier does the latter).
        return {"items": value}
    if not isinstance(value, dict):
        raise LLMError("Model response must be a JSON object or array.")
    return value


def _candidate_text(candidate: dict[str, Any]) -> tuple[str, int]:
    """Return only final-answer parts, excluding Gemini thought-summary parts."""
    parts = candidate.get("content", {}).get("parts", [])
    final_parts = [part for part in parts if not part.get("thought")]
    selected = final_parts or parts
    text = "".join(str(part.get("text", "")) for part in selected)
    return text, len(parts) - len(final_parts)


def _validate_schema_value(value: Any, schema: dict[str, Any], *, path: str = "$") -> None:
    """Validate the small JSON-Schema subset used by SKOPE without another dependency."""
    allowed_types = schema.get("type")
    if isinstance(allowed_types, str):
        allowed_types = [allowed_types]
    if allowed_types:
        matches_type = {
            "object": lambda item: isinstance(item, dict),
            "array": lambda item: isinstance(item, list),
            "string": lambda item: isinstance(item, str),
            "integer": lambda item: isinstance(item, int) and not isinstance(item, bool),
            "number": lambda item: isinstance(item, (int, float)) and not isinstance(item, bool),
            "boolean": lambda item: isinstance(item, bool),
            "null": lambda item: item is None,
        }
        if not any(validator(value) for name in allowed_types if (validator := matches_type.get(name))):
            raise LLMError(f"Structured response field {path} has the wrong type.")

    if "enum" in schema and value not in schema["enum"]:
        raise LLMError(f"Structured response field {path} is outside its allowed values.")

    if isinstance(value, dict):
        properties = schema.get("properties", {})
        missing = [name for name in schema.get("required", []) if name not in value]
        if missing:
            raise LLMError(f"Structured response field {path} is missing: {', '.join(missing)}.")
        if schema.get("additionalProperties") is False:
            unexpected = sorted(set(value) - set(properties))
            if unexpected:
                raise LLMError(
                    f"Structured response field {path} has unexpected keys: {', '.join(unexpected)}."
                )
        for name, child in value.items():
            if name in properties:
                _validate_schema_value(child, properties[name], path=f"{path}.{name}")

    if isinstance(value, list):
        minimum = schema.get("minItems")
        maximum = schema.get("maxItems")
        if minimum is not None and len(value) < minimum:
            raise LLMError(f"Structured response field {path} has too few items.")
        if maximum is not None and len(value) > maximum:
            raise LLMError(f"Structured response field {path} has too many items.")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                _validate_schema_value(item, item_schema, path=f"{path}[{index}]")


def _gemini_response_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Return the subset of JSON Schema accepted by Gemini controlled output.

    Gemini rejects ``maxItems`` with HTTP 400 even though SKOPE uses it to
    bound planner and synthesis arrays. The original schema is still passed to
    :func:`_validate_schema_value` after generation, so removing this keyword
    from the provider payload does not weaken SKOPE's local validation.
    """

    def compatible(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                key: compatible(child)
                for key, child in value.items()
                if key != "maxItems"
            }
        if isinstance(value, list):
            return [compatible(child) for child in value]
        return value

    return compatible(schema)


@lru_cache(maxsize=1)
def _client() -> httpx.Client:
    """Reuse TCP/TLS connections across model requests and worker threads."""
    settings = get_settings()
    timeout = httpx.Timeout(
        settings.llm_request_timeout_seconds,
        connect=min(3.0, settings.llm_request_timeout_seconds),
    )
    limits = httpx.Limits(max_connections=20, max_keepalive_connections=10)
    return httpx.Client(timeout=timeout, limits=limits)


def close_client() -> None:
    """Close the shared transport during application shutdown or tests."""
    if _client.cache_info().currsize:
        _client().close()
        _client.cache_clear()


def _retry_delay(response: httpx.Response | None, attempt: int) -> float:
    if response is not None:
        value = response.headers.get("retry-after", "").strip()
        try:
            return min(max(float(value), 0.0), 2.0)
        except ValueError:
            pass
    return min(0.5 * (2**attempt), 2.0)


def _check_circuit() -> None:
    with _CIRCUIT_LOCK:
        if time.monotonic() < _CIRCUIT_OPEN_UNTIL:
            raise LLMError("Gemini circuit breaker is temporarily open after repeated failures.")


def _record_success() -> None:
    global _CONSECUTIVE_FAILURES, _CIRCUIT_OPEN_UNTIL
    with _CIRCUIT_LOCK:
        _CONSECUTIVE_FAILURES = 0
        _CIRCUIT_OPEN_UNTIL = 0.0


def _record_failure() -> None:
    global _CONSECUTIVE_FAILURES, _CIRCUIT_OPEN_UNTIL
    settings = get_settings()
    with _CIRCUIT_LOCK:
        _CONSECUTIVE_FAILURES += 1
        if _CONSECUTIVE_FAILURES >= settings.llm_circuit_breaker_failures:
            _CIRCUIT_OPEN_UNTIL = time.monotonic() + settings.llm_circuit_breaker_cooldown_seconds


def generate_json(
    *,
    system_prompt: str,
    user_prompt: str,
    temperature: float = 0.1,
    max_output_tokens: int = 4096,
    response_schema: dict[str, Any] | None = None,
    request_metrics: list[dict[str, Any]] | None = None,
    provider_deadline: float | None = None,
) -> dict[str, Any]:
    settings = get_settings()
    if settings.llm_provider.lower() != "gemini":
        raise LLMError(f"Unsupported LLM provider: {settings.llm_provider}")
    if not settings.gemini_api_key:
        raise LLMError("GEMINI_API_KEY is not configured.")
    _check_circuit()

    endpoint = f"{settings.llm_base_url.rstrip('/')}/models/{settings.llm_model}:generateContent"
    generation_config: dict[str, Any] = {
        "temperature": temperature,
        "maxOutputTokens": max_output_tokens,
        "responseMimeType": "application/json",
    }
    if response_schema:
        generation_config["responseJsonSchema"] = _gemini_response_schema(response_schema)

    payload = {
        "systemInstruction": {"parts": [{"text": system_prompt}]},
        "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
        "generationConfig": generation_config,
    }
    last_error: Exception | None = None
    transient_failure = False
    local_deadline = time.monotonic() + settings.llm_total_budget_seconds
    deadline = min(local_deadline, provider_deadline) if provider_deadline else local_deadline
    attempts = settings.llm_max_retries + 1

    for attempt in range(attempts):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            last_error = LLMError("Shared Gemini provider budget was exhausted.")
            break
        started = time.perf_counter()
        response: httpx.Response | None = None
        try:
            request_timeout = max(
                0.5,
                min(settings.llm_request_timeout_seconds, remaining),
            )
            response = _client().post(
                endpoint,
                headers={"x-goog-api-key": settings.gemini_api_key},
                json=payload,
                timeout=httpx.Timeout(request_timeout, connect=min(3.0, request_timeout)),
            )
            retryable = response.status_code == 429 or response.status_code in {
                500,
                502,
                503,
                504,
            }
            if request_metrics is not None:
                request_metrics.append(
                    {
                        "attempt": attempt + 1,
                        "status_code": response.status_code,
                        "latency_ms": int((time.perf_counter() - started) * 1_000),
                    }
                )
            if retryable and attempt + 1 < attempts:
                delay = _retry_delay(response, attempt)
                if time.monotonic() + delay < deadline:
                    time.sleep(delay)
                    continue

            response.raise_for_status()
            body = response.json()
            usage = body.get("usageMetadata") or {}
            candidates = body.get("candidates") or []
            if not candidates:
                raise LLMError("Gemini returned no candidate response.")
            candidate = candidates[0]
            finish_reason = str(candidate.get("finishReason", "UNKNOWN"))
            text, ignored_thought_parts = _candidate_text(candidate)
            if request_metrics is not None and request_metrics:
                request_metrics[-1].update(
                    {
                        "provider": "gemini",
                        "model": settings.llm_model,
                        "input_tokens": usage.get("promptTokenCount"),
                        "output_tokens": usage.get("candidatesTokenCount"),
                        "total_tokens": usage.get("totalTokenCount"),
                        "finish_reason": finish_reason,
                        "response_characters": len(text),
                        "ignored_thought_parts": ignored_thought_parts,
                    }
                )
            # A valid HTTP response proves provider availability even when the
            # model output is truncated or malformed. Such content errors must
            # never open the global availability circuit breaker.
            _record_success()
            if finish_reason == "MAX_TOKENS":
                last_error = LLMTruncatedError(
                    f"Gemini output reached the {generation_config['maxOutputTokens']}-token limit."
                )
                if request_metrics is not None and request_metrics:
                    request_metrics[-1]["truncated"] = True
                if attempt + 1 < attempts:
                    generation_config["maxOutputTokens"] = min(
                        max(generation_config["maxOutputTokens"] * 2, 1_400),
                        4_096,
                    )
                    payload["systemInstruction"]["parts"][0]["text"] = (
                        system_prompt
                        + "\nA prior response exceeded its output budget. Return a much more concise object "
                        "that still satisfies the schema; omit all nonessential prose."
                    )
                    continue
                break
            if finish_reason not in {"STOP", "UNKNOWN"}:
                raise LLMError(f"Gemini stopped with finish reason {finish_reason}.")
            result = _extract_json(text)
            if response_schema:
                _validate_schema_value(result, response_schema)
            return result
        except httpx.HTTPError as exc:
            last_error = exc
            status_code = response.status_code if response is not None else None
            transient_failure = response is None or status_code == 429 or status_code in {
                500,
                502,
                503,
                504,
            }
            if request_metrics is not None and response is None:
                request_metrics.append(
                    {
                        "attempt": attempt + 1,
                        "status_code": None,
                        "latency_ms": int((time.perf_counter() - started) * 1_000),
                        "error": type(exc).__name__,
                    }
                )
            if attempt + 1 < attempts:
                delay = _retry_delay(response, attempt)
                if time.monotonic() + delay < deadline:
                    time.sleep(delay)
                    continue
            break
        except (ValueError, LLMError) as exc:
            # Controlled-output or schema errors are content defects, not an
            # unavailable provider. Retrying the same completed response is
            # wasteful and must not trip the availability circuit breaker.
            last_error = exc
            break

    logger.warning("Gemini request exhausted its bounded provider budget: %s", last_error)
    if transient_failure:
        _record_failure()
    raise LLMError(f"Gemini request failed: {last_error}") from last_error
