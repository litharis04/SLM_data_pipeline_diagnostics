"""Provider transport and adapters (C06, §§3.1/3.3).

One thin adapter per provider behind the shared ``NormalizedGeneration``
result, over stdlib ``urllib`` only. Single attempt per call: no retry loop
exists at this layer (stdlib installs no retry handler, and this module never
re-issues a request — a scripted ``429`` therefore yields exactly one call).
No scenario validation, no materialization, no key storage here. API keys
never enter URLs, logs, files, or exception text.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
OPENROUTER_COMPLETIONS_URL = f"{OPENROUTER_BASE_URL}/chat/completions"
GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"

DEFAULT_OUTPUT_TOKENS = 32_768
REQUEST_TIMEOUT = 180

Sender = Callable[[str, str, dict[str, str], bytes, int], tuple[int, dict[str, str], bytes]]


@dataclass(frozen=True)
class Usage:
    """Token counts as reported by the provider; every field optional."""

    input_tokens: int | None = None
    output_tokens: int | None = None
    reasoning_tokens: int | None = None


@dataclass(frozen=True)
class NormalizedGeneration:
    """Common internal authoring result both adapters normalize into."""

    text: str
    finish: Literal["stop", "length", "refusal", "error"]
    usage: Usage | None = None
    model_identity: str | None = None


class ProviderError(Exception):
    """Aborted provider call: auth/quota/model/network/timeout/refusal-infra."""

    def __init__(
        self,
        provider: str,
        message: str,
        *,
        status: int | None = None,
        code: str | None = None,
        retry_hint: str | None = None,
        timeout: bool = False,
    ) -> None:
        super().__init__(provider, message)
        self.provider = provider
        self.message = message
        self.status = status
        self.code = code
        self.retry_hint = retry_hint
        self.timeout = timeout

    @property
    def is_auth_failure(self) -> bool:
        return self.status in (401, 403)

    @property
    def is_rate_limited(self) -> bool:
        return self.status == 429

    @property
    def is_timeout(self) -> bool:
        return self.timeout

    def __str__(self) -> str:
        return _format_provider_error(self)


def _format_provider_error(exc: ProviderError) -> str:
    parts = [f"{exc.provider} request failed"]
    if exc.status is not None:
        parts.append(f"[http {exc.status}]")
    if exc.code:
        parts.append(f"({exc.code})")
    parts.append(_redact(exc.message))
    if exc.retry_hint:
        parts.append(f"hint: {_redact(exc.retry_hint)}")
    return " ".join(parts)


_REDACTIONS = (
    (re.compile(r"(?i)(bearer\s+)\S+"), r"\1[redacted]"),
    (re.compile(r"(?i)(x-goog-api-key\s*[:=]\s*)\S+"), r"\1[redacted]"),
    (re.compile(r"(?i)(api[_-]?key\s*[:=]\s*['\"]?)[^'\"\s]+"), r"\1[redacted]"),
    (re.compile(r"sk-or-v1-[A-Za-z0-9]+"), "[redacted]"),
    (re.compile(r"AIza[A-Za-z0-9_-]+"), "[redacted]"),
)


def _redact(text: str) -> str:
    for pattern, replacement in _REDACTIONS:
        text = pattern.sub(replacement, text)
    return text


def sanitize_error(exc: BaseException) -> str:
    """Render an exception for logs/terminal: no keys, tokens, or auth headers."""
    if isinstance(exc, ProviderError):
        return str(exc)
    return _redact(f"{type(exc).__name__}: {exc}")


def _send_urllib(
    provider: str, url: str, headers: dict[str, str], body: bytes, timeout: int
) -> tuple[int, dict[str, str], bytes]:
    """One POST; HTTP errors surface as data, never as retries."""
    del provider
    request = Request(
        url, data=body, headers={"Content-Type": "application/json", **headers}, method="POST"
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            return response.status, dict(response.headers), response.read()
    except HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read()


def _parse_response(
    provider: str, status: int, headers: dict[str, str], body: bytes
) -> dict[str, object]:
    if status == 200:
        try:
            payload = json.loads(body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise ProviderError(provider, f"invalid response body: {exc}") from exc
        if not isinstance(payload, dict):
            raise ProviderError(provider, "invalid response body: expected a JSON object")
        return payload
    envelope: object = None
    try:
        envelope = json.loads(body.decode("utf-8"))
    except ValueError, UnicodeDecodeError:
        envelope = None
    detail = envelope.get("error") if isinstance(envelope, dict) else None
    detail = detail if isinstance(detail, dict) else {}
    code = detail.get("code")
    message = detail.get("message")
    lowered = {str(key).lower(): value for key, value in headers.items()}
    retry_after = lowered.get("retry-after")
    retry_hint = f"retry after {retry_after}" if retry_after else None
    if not isinstance(message, str) or not message:
        message = f"http error {status}"
    raise ProviderError(
        provider,
        message,
        status=status,
        code=str(code) if code is not None else None,
        retry_hint=retry_hint,
    )


def post_json(
    provider: str,
    url: str,
    *,
    headers: dict[str, str],
    payload: dict[str, object],
    timeout: int = REQUEST_TIMEOUT,
    sender: Sender | None = None,
) -> dict[str, object]:
    """POST a JSON payload once; transport failures become ``ProviderError``."""
    body = json.dumps(payload).encode("utf-8")
    send = sender or _send_urllib
    try:
        status, response_headers, response_body = send(provider, url, headers, body, timeout)
    except TimeoutError as exc:
        raise ProviderError(provider, f"request timed out after {timeout}s", timeout=True) from exc
    except (URLError, OSError) as exc:
        raise ProviderError(provider, f"network failure: {exc}") from exc
    return _parse_response(provider, status, response_headers, response_body)


def _checked_budget(max_output_tokens: int) -> int:
    if (
        not isinstance(max_output_tokens, int)
        or isinstance(max_output_tokens, bool)
        or max_output_tokens <= 0
    ):
        raise ValueError(
            f"invalid output budget {max_output_tokens!r}: expected a positive integer"
        )
    return max_output_tokens


def _optional_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


class OpenRouterAdapter:
    """Thin adapter over OpenAI-compatible Chat Completions (Bearer auth).

    Finish mapping (``choices[0].finish_reason`` → ``finish``)::

        "stop"           → "stop"      (complete candidate in message.content)
        "length"         → "length"    (truncated by the output budget)
        "content_filter" → "refusal"  (moderated; message.refusal when present)
        null/other       → "error"     (tool_calls, function_call, unknown …)

    A non-null ``message.refusal`` always normalizes to ``"refusal"``.
    """

    provider = "openrouter"

    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        transport: Sender | None = None,
    ) -> None:
        if not model:
            raise ValueError("model is required")
        if not api_key:
            raise ValueError("api_key is required")
        self.model = model
        self._api_key = api_key
        self._transport = transport

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}"}

    def generate(
        self, *, system: str, user: str, max_output_tokens: int = DEFAULT_OUTPUT_TOKENS
    ) -> NormalizedGeneration:
        payload: dict[str, object] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "n": 1,
            "stream": False,
            "max_tokens": _checked_budget(max_output_tokens),
        }
        data = post_json(
            self.provider,
            OPENROUTER_COMPLETIONS_URL,
            headers=self._headers(),
            payload=payload,
            sender=self._transport,
        )
        return self._normalize(data)

    def _normalize(self, data: dict[str, object]) -> NormalizedGeneration:
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise ProviderError(self.provider, "invalid response body: missing choices[0]")
        first = choices[0]
        message = first.get("message")
        if not isinstance(message, dict):
            raise ProviderError(self.provider, "invalid response body: missing message")
        refusal = message.get("refusal")
        content = message.get("content")
        if isinstance(refusal, str) and refusal:
            return NormalizedGeneration(
                text=refusal,
                finish="refusal",
                usage=_usage_openrouter(data.get("usage")),
                model_identity=_optional_str(data.get("model")),
            )
        if isinstance(content, list):
            text = "".join(
                str(part.get("text", ""))
                for part in content
                if isinstance(part, dict) and isinstance(part.get("text"), str)
            )
        elif isinstance(content, str):
            text = content
        else:
            text = ""
        reason = first.get("finish_reason")
        if reason == "stop":
            finish: Literal["stop", "length", "refusal", "error"] = "stop"
        elif reason == "length":
            finish = "length"
        elif reason == "content_filter":
            finish = "refusal"
        else:
            finish = "error"
        return NormalizedGeneration(
            text=text,
            finish=finish,
            usage=_usage_openrouter(data.get("usage")),
            model_identity=_optional_str(data.get("model")),
        )


def _usage_openrouter(usage: object) -> Usage | None:
    if not isinstance(usage, dict):
        return None
    details = usage.get("completion_tokens_details")
    details = details if isinstance(details, dict) else {}
    return Usage(
        input_tokens=_optional_int(usage.get("prompt_tokens")),
        output_tokens=_optional_int(usage.get("completion_tokens")),
        reasoning_tokens=_optional_int(details.get("reasoning_tokens")),
    )


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) else None


class GeminiAdapter:
    """Thin adapter over ``models.generateContent`` (``x-goog-api-key`` header).

    The key travels only in the header, never in the URL. JSON MIME output
    is requested; nothing else (tools, grounding) is enabled.

    Finish mapping (``candidates[0].finishReason`` → ``finish``)::

        "STOP"                 → "stop"      (complete candidate in text parts)
        "MAX_TOKENS"           → "length"    (truncated by the output budget)
        "SAFETY"/"RECITATION"  → "refusal"   (blocked by the provider)
        other                  → "error"     (OTHER, BLOCKED, unknown …)
    """

    provider = "gemini"

    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        transport: Sender | None = None,
    ) -> None:
        if not model:
            raise ValueError("model is required")
        if not api_key:
            raise ValueError("api_key is required")
        self.model = model
        self._api_key = api_key
        self._transport = transport

    def _url(self) -> str:
        return f"{GEMINI_BASE_URL}/models/{quote(self.model, safe='')}:generateContent"

    def _headers(self) -> dict[str, str]:
        return {"x-goog-api-key": self._api_key}

    def generate(
        self, *, system: str, user: str, max_output_tokens: int = DEFAULT_OUTPUT_TOKENS
    ) -> NormalizedGeneration:
        payload: dict[str, object] = {
            "system_instruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {
                "candidateCount": 1,
                "maxOutputTokens": _checked_budget(max_output_tokens),
                "responseMimeType": "application/json",
            },
        }
        data = post_json(
            self.provider,
            self._url(),
            headers=self._headers(),
            payload=payload,
            sender=self._transport,
        )
        return self._normalize(data)

    def _normalize(self, data: dict[str, object]) -> NormalizedGeneration:
        candidates = data.get("candidates")
        if (
            not isinstance(candidates, list)
            or not candidates
            or not isinstance(candidates[0], dict)
        ):
            raise ProviderError(self.provider, "invalid response body: missing candidates[0]")
        first = candidates[0]
        content = first.get("content")
        parts = content.get("parts") if isinstance(content, dict) else None
        if not isinstance(parts, list):
            raise ProviderError(self.provider, "invalid response body: missing content parts")
        text = "".join(
            str(part.get("text", ""))
            for part in parts
            if isinstance(part, dict) and isinstance(part.get("text"), str)
        )
        reason = first.get("finishReason")
        if reason == "STOP":
            finish: Literal["stop", "length", "refusal", "error"] = "stop"
        elif reason == "MAX_TOKENS":
            finish = "length"
        elif reason in ("SAFETY", "RECITATION"):
            finish = "refusal"
        else:
            finish = "error"
        usage = data.get("usageMetadata")
        return NormalizedGeneration(
            text=text,
            finish=finish,
            usage=_usage_gemini(usage),
            model_identity=_optional_str(data.get("modelVersion")),
        )


def _usage_gemini(usage: object) -> Usage | None:
    if not isinstance(usage, dict):
        return None
    return Usage(
        input_tokens=_optional_int(usage.get("promptTokenCount")),
        output_tokens=_optional_int(usage.get("candidatesTokenCount")),
        reasoning_tokens=_optional_int(usage.get("thoughtsTokenCount")),
    )


class FakeTransport:
    """Scripted transport for offline tests: deterministic FIFO replies.

    Script items are either success payload dicts (served as HTTP 200) or
    failure tuples consumed once, in order::

        ("http", status, body, headers)  — HTTP failure with JSON body + headers
        ("timeout",)                     — socket timeout
        ("network", message)             — connection-level failure

    Every call is recorded in ``calls``; a scripted ``429`` therefore proves
    the single-attempt contract when exactly one call exists afterwards.
    """

    def __init__(self, script: list[object]) -> None:
        self._script = list(script)
        self.calls: list[dict[str, object]] = []

    def __call__(
        self, provider: str, url: str, headers: dict[str, str], body: bytes, timeout: int
    ) -> tuple[int, dict[str, str], bytes]:
        self.calls.append(
            {
                "provider": provider,
                "url": url,
                "headers": dict(headers),
                "body": body,
                "timeout": timeout,
            }
        )
        if not self._script:
            raise AssertionError("FakeTransport script exhausted")
        item = self._script.pop(0)
        if isinstance(item, dict):
            return 200, {}, json.dumps(item).encode("utf-8")
        if not isinstance(item, tuple) or not item:
            raise AssertionError(f"invalid FakeTransport script item: {item!r}")
        kind = item[0]
        if kind == "http":
            _, status, payload, response_headers = item
            return int(status), dict(response_headers), json.dumps(payload).encode("utf-8")
        if kind == "timeout":
            raise TimeoutError("timed out")
        if kind == "network":
            raise URLError(item[1] if len(item) > 1 else "connection refused")
        raise AssertionError(f"invalid FakeTransport script item: {item!r}")
