from __future__ import annotations

import json

import pytest

from data_pipeline_diagnostics.cli import providers
from data_pipeline_diagnostics.cli.providers import (
    DEFAULT_OUTPUT_TOKENS,
    REQUEST_TIMEOUT,
    FakeTransport,
    GeminiAdapter,
    NormalizedGeneration,
    OpenRouterAdapter,
    ProviderError,
    Usage,
    sanitize_error,
)

FAKE_OR_KEY = "sk-or-v1-FAKEKEY123456"
FAKE_GEMINI_KEY = "AIzaFAKEKEY1234567890"


def or_payload(content="{}", finish="stop", model="openrouter/free", usage=None):
    payload = {
        "id": "gen-1",
        "model": model,
        "choices": [
            {"message": {"role": "assistant", "content": content}, "finish_reason": finish}
        ],
    }
    if usage is not None:
        payload["usage"] = usage
    return payload


def gemini_payload(text="{}", finish="STOP", usage=None, model_version=None):
    payload = {
        "candidates": [
            {
                "content": {"parts": [{"text": text}], "role": "model"},
                "finishReason": finish,
            }
        ]
    }
    if usage is not None:
        payload["usageMetadata"] = usage
    if model_version is not None:
        payload["modelVersion"] = model_version
    return payload


def or_adapter(script, model="some-model"):
    fake = FakeTransport(script)
    return OpenRouterAdapter(model=model, api_key=FAKE_OR_KEY, transport=fake), fake


def gemini_adapter(script, model="gemini-2.0-flash"):
    fake = FakeTransport(script)
    return GeminiAdapter(model=model, api_key=FAKE_GEMINI_KEY, transport=fake), fake


@pytest.mark.parametrize(
    ("finish_reason", "expected"),
    [
        ("stop", "stop"),
        ("length", "length"),
        ("content_filter", "refusal"),
        ("tool_calls", "error"),
        ("function_call", "error"),
        (None, "error"),
        ("bogus", "error"),
    ],
)
def test_openrouter_finish_mapping(finish_reason, expected):
    adapter, _ = or_adapter([or_payload("{}", finish_reason)])
    result = adapter.generate(system="s", user="u")
    assert isinstance(result, NormalizedGeneration)
    assert (result.text, result.finish) == ("{}", expected)


def test_openrouter_refusal_field():
    payload = or_payload("ignored", "stop")
    payload["choices"][0]["message"] = {"role": "assistant", "refusal": "nope"}
    adapter, _ = or_adapter([payload])
    assert adapter.generate(system="s", user="u").finish == "refusal"


@pytest.mark.parametrize(
    ("reason", "expected"),
    [
        ("STOP", "stop"),
        ("MAX_TOKENS", "length"),
        ("SAFETY", "refusal"),
        ("RECITATION", "refusal"),
        ("OTHER", "error"),
        ("BLOCKED", "error"),
        ("bogus", "error"),
    ],
)
def test_gemini_finish_mapping(reason, expected):
    adapter, _ = gemini_adapter([gemini_payload("{}", reason)])
    result = adapter.generate(system="s", user="u")
    assert (result.text, result.finish) == ("{}", expected)


def test_gemini_multi_part_text_concatenated():
    payload = gemini_payload()
    payload["candidates"][0]["content"]["parts"] = [{"text": '{"a":'}, {"text": "1}"}]
    adapter, _ = gemini_adapter([payload])
    assert adapter.generate(system="s", user="u").text == '{"a":1}'


def test_empty_stop_candidate_preserved():
    adapter, _ = or_adapter([or_payload("", "stop")])
    assert adapter.generate(system="s", user="u").text == ""


def test_usage_passthrough_openrouter():
    usage = {
        "prompt_tokens": 10,
        "completion_tokens": 20,
        "completion_tokens_details": {"reasoning_tokens": 5},
    }
    adapter, _ = or_adapter([or_payload(usage=usage)])
    assert adapter.generate(system="s", user="u").usage == Usage(10, 20, 5)


def test_usage_passthrough_gemini():
    usage = {"promptTokenCount": 7, "candidatesTokenCount": 9, "thoughtsTokenCount": 3}
    adapter, _ = gemini_adapter([gemini_payload(usage=usage)])
    assert adapter.generate(system="s", user="u").usage == Usage(7, 9, 3)


def test_missing_usage_is_none():
    adapter, _ = or_adapter([or_payload()])
    assert adapter.generate(system="s", user="u").usage is None
    gadapter, _ = gemini_adapter([gemini_payload()])
    assert gadapter.generate(system="s", user="u").usage is None


def test_model_identity_openrouter_router_model():
    adapter, fake = or_adapter([or_payload(model="other/actual-model")], model="openrouter/free")
    result = adapter.generate(system="s", user="u")
    assert result.model_identity == "other/actual-model"
    assert fake.calls[0]["provider"] == "openrouter"


def test_model_identity_gemini_version():
    adapter, _ = gemini_adapter([gemini_payload(model_version="gemini-2.0-flash-001")])
    assert adapter.generate(system="s", user="u").model_identity == "gemini-2.0-flash-001"
    adapter2, _ = gemini_adapter([gemini_payload()])
    assert adapter2.generate(system="s", user="u").model_identity is None


def test_openrouter_request_shape():
    adapter, fake = or_adapter([or_payload()], model="openrouter/free")
    adapter.generate(system="sys", user="usr", max_output_tokens=1024)
    call = fake.calls[0]
    assert call["url"] == providers.OPENROUTER_COMPLETIONS_URL
    assert "?" not in call["url"]
    assert call["headers"]["Authorization"] == f"Bearer {FAKE_OR_KEY}"
    payload = json.loads(call["body"].decode("utf-8"))
    assert payload["model"] == "openrouter/free"
    assert payload["messages"] == [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "usr"},
    ]
    assert payload["n"] == 1
    assert payload["stream"] is False
    assert payload["max_tokens"] == 1024
    assert "tools" not in payload
    assert "response_format" not in payload
    assert FAKE_OR_KEY not in call["url"]
    assert call["timeout"] == REQUEST_TIMEOUT


def test_gemini_request_shape():
    adapter, fake = gemini_adapter([gemini_payload()], model="gemini-2.0-flash")
    adapter.generate(system="sys", user="usr")
    call = fake.calls[0]
    assert call["url"].startswith(providers.GEMINI_BASE_URL)
    assert call["url"].endswith("/models/gemini-2.0-flash:generateContent")
    assert "?" not in call["url"]
    assert FAKE_GEMINI_KEY not in call["url"]
    assert call["headers"]["x-goog-api-key"] == FAKE_GEMINI_KEY
    payload = json.loads(call["body"].decode("utf-8"))
    config = payload["generationConfig"]
    assert config["candidateCount"] == 1
    assert config["maxOutputTokens"] == DEFAULT_OUTPUT_TOKENS
    assert config["responseMimeType"] == "application/json"
    assert "tools" not in payload
    assert call["timeout"] == REQUEST_TIMEOUT


def test_default_budget_and_timeout_constants():
    assert DEFAULT_OUTPUT_TOKENS == 32_768
    assert REQUEST_TIMEOUT == 180


def test_budget_validation_rejects_non_positive():
    adapter, fake = or_adapter([or_payload()])
    with pytest.raises(ValueError, match="budget"):
        adapter.generate(system="s", user="u", max_output_tokens=0)
    assert fake.calls == []


def test_urllib_forwards_timeout(monkeypatch):
    seen = {}

    class _Response:
        status = 200
        headers = {}

        def read(self):
            return b"{}"

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def fake_urlopen(request, timeout=None):
        seen["timeout"] = timeout
        seen["url"] = request.full_url
        return _Response()

    monkeypatch.setattr(providers, "urlopen", fake_urlopen)
    status, _, body = providers._send_urllib("openrouter", "https://x", {"A": "b"}, b"{}", 180)
    assert (status, body) == (200, b"{}")
    assert seen == {"timeout": 180, "url": "https://x"}


def test_rate_limit_single_attempt_no_retry():
    script = [
        ("http", 429, {"error": {"code": 429, "message": "rate limited"}}, {"Retry-After": "30"})
    ]
    adapter, fake = or_adapter(script)
    with pytest.raises(ProviderError) as exc:
        adapter.generate(system="s", user="u")
    assert exc.value.is_rate_limited
    assert not exc.value.is_auth_failure
    assert exc.value.retry_hint == "retry after 30"
    assert len(fake.calls) == 1


def test_auth_failure_kind():
    adapter, fake = or_adapter(
        [("http", 401, {"error": {"code": "invalid_key", "message": "bad key"}}, {})]
    )
    with pytest.raises(ProviderError) as exc:
        adapter.generate(system="s", user="u")
    assert exc.value.is_auth_failure
    assert exc.value.code == "invalid_key"
    assert len(fake.calls) == 1


def test_timeout_and_network_kinds():
    adapter, fake = or_adapter([("timeout",)])
    with pytest.raises(ProviderError) as exc:
        adapter.generate(system="s", user="u")
    assert exc.value.is_timeout
    adapter2, fake2 = gemini_adapter([("network", "connection refused")])
    with pytest.raises(ProviderError) as exc2:
        adapter2.generate(system="s", user="u")
    assert not exc2.value.is_timeout
    assert not exc2.value.is_rate_limited


def test_malformed_success_body_is_provider_error():
    adapter, _ = or_adapter([{"no": "choices"}])
    with pytest.raises(ProviderError, match="choices"):
        adapter.generate(system="s", user="u")
    gadapter, _ = gemini_adapter([{"no": "candidates"}])
    with pytest.raises(ProviderError, match="candidates"):
        gadapter.generate(system="s", user="u")


def test_sanitize_removes_keys_keeps_status_and_hint():
    from urllib.error import URLError

    leaked = URLError(f"boom Bearer {FAKE_OR_KEY} end")
    cleaned = sanitize_error(leaked)
    assert FAKE_OR_KEY not in cleaned
    assert "[redacted]" in cleaned

    header_leak = f"Authorization: Bearer {FAKE_OR_KEY} / x-goog-api-key: {FAKE_GEMINI_KEY}"
    assert FAKE_GEMINI_KEY not in sanitize_error(RuntimeError(header_leak))

    err = ProviderError(
        "gemini",
        "rate limited",
        status=429,
        code="RESOURCE_EXHAUSTED",
        retry_hint="retry after 30",
    )
    text = sanitize_error(err)
    assert "429" in text and "RESOURCE_EXHAUSTED" in text and "retry after 30" in text


def test_adapters_require_model_and_key():
    with pytest.raises(ValueError, match="model"):
        OpenRouterAdapter(model="", api_key="k")
    with pytest.raises(ValueError, match="api_key"):
        GeminiAdapter(model="m", api_key="")
