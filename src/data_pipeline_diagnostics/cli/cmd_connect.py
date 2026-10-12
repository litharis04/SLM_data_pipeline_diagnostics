"""``plgen connect`` — env-backed provider profiles with verification (C07).

One profile per provider (model + output-token budget + credential env name),
verified through non-generation requests before anything is persisted. API
keys live only in process environment variables and never reach ``config.json``,
logs, prompts, saved files, or child-process environments.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from data_pipeline_diagnostics.cli.providers import (
    DEFAULT_OUTPUT_TOKENS,
    GEMINI_BASE_URL,
    OPENROUTER_BASE_URL,
    ProviderError,
    Sender,
    get_json,
    sanitize_error,
)
from data_pipeline_diagnostics.cli.workspace import (
    atomic_write_json,
    config_path,
    ensure_workspace,
)

PROVIDERS = ("openrouter", "gemini")
DEFAULT_KEY_ENV = {"openrouter": "OPENROUTER_API_KEY", "gemini": "GEMINI_API_KEY"}
OPENROUTER_FREE_ROUTER = "openrouter/free"

_ENV_NAME_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


@dataclass(frozen=True)
class ProviderProfile:
    """Non-secret provider profile as stored in ``config.json`` (no key ever)."""

    provider: str
    model: str
    key_env: str
    max_output_tokens: int

    def to_dict(self) -> dict[str, object]:
        return {
            "provider": self.provider,
            "model": self.model,
            "key_env": self.key_env,
            "max_output_tokens": self.max_output_tokens,
        }

    @classmethod
    def from_dict(cls, data: object) -> ProviderProfile:
        if not isinstance(data, dict):
            raise ValueError("profile must be a JSON object")
        provider = data.get("provider")
        model = data.get("model")
        key_env = data.get("key_env")
        budget = data.get("max_output_tokens")
        if provider not in PROVIDERS:
            raise ValueError(f"unknown provider {provider!r}")
        if not isinstance(model, str) or not model.strip():
            raise ValueError("profile model must be a non-empty string")
        if not isinstance(key_env, str) or _ENV_NAME_PATTERN.fullmatch(key_env) is None:
            raise ValueError(f"invalid key_env {key_env!r}")
        if not isinstance(budget, int) or isinstance(budget, bool) or budget <= 0:
            raise ValueError(f"invalid max_output_tokens {budget!r}")
        return cls(provider=provider, model=model, key_env=key_env, max_output_tokens=budget)


def configured_credential_envs(config: Mapping[str, object]) -> set[str]:
    """Credential env var names referenced by stored profiles (for scrubbing)."""
    profiles = config.get("profiles")
    if not isinstance(profiles, dict):
        return set()
    envs = set()
    for profile in profiles.values():
        if isinstance(profile, dict):
            name = profile.get("key_env")
            if isinstance(name, str) and name:
                envs.add(name)
    return envs


def child_environ(
    config: Mapping[str, object], base: Mapping[str, str] | None = None
) -> dict[str, str]:
    """Child-process environment with configured credential vars excluded."""
    environ = dict(os.environ if base is None else base)
    for name in configured_credential_envs(config):
        environ.pop(name, None)
    return environ


def verify_openrouter(*, model: str, key: str, sender: Sender | None) -> None:
    """Key metadata + model catalog; both non-generation. Raises ``ProviderError``."""
    headers = {"Authorization": f"Bearer {key}"}
    get_json("openrouter", f"{OPENROUTER_BASE_URL}/key", headers=headers, sender=sender)
    if model == OPENROUTER_FREE_ROUTER:
        # Documented router alias: not a catalog entry, key check above suffices.
        return
    catalog = get_json(
        "openrouter", f"{OPENROUTER_BASE_URL}/models", headers=headers, sender=sender
    )
    entries = catalog.get("data")
    if not isinstance(entries, list):
        raise ProviderError("openrouter", "invalid model catalog response")
    known = {entry.get("id") for entry in entries if isinstance(entry, dict)}
    if model not in known:
        raise ProviderError("openrouter", f"unknown model {model!r}", code="unknown-model")


def verify_gemini(*, model: str, key: str, sender: Sender | None) -> None:
    """Model metadata lookup; non-generation. Raises ``ProviderError``."""
    name = model[len("models/") :] if model.startswith("models/") else model
    try:
        get_json(
            "gemini",
            f"{GEMINI_BASE_URL}/models/{quote(name, safe='')}",
            headers={"x-goog-api-key": key},
            sender=sender,
        )
    except ProviderError as exc:
        if exc.status == 404:
            raise ProviderError("gemini", f"unknown model {model!r}", code="unknown-model") from exc
        raise


def _load_config(root: Path) -> dict[str, object] | None:
    try:
        config = json.loads(config_path(root).read_text(encoding="utf-8"))
    except OSError, ValueError:
        return None
    if not isinstance(config, dict) or not isinstance(config.get("profiles"), dict):
        return None
    return config


def run_connect(
    workspace: Path,
    args: argparse.Namespace,
    *,
    sender: Sender | None = None,
    env: Mapping[str, str] | None = None,
) -> int:
    """Configure one provider profile after non-generation verification."""
    root = Path(workspace)
    ensure_workspace(root)
    provider = args.provider
    if provider not in PROVIDERS:
        print(
            f"plgen connect: unsupported provider {provider!r}: expected one of {list(PROVIDERS)}",
            file=sys.stderr,
        )
        return 2
    model = args.model
    if not isinstance(model, str) or not model.strip():
        print("plgen connect: --model must be a non-empty string", file=sys.stderr)
        return 2
    config = _load_config(root)
    if config is None:
        print(f"plgen connect: unreadable config {config_path(root)}", file=sys.stderr)
        return 5
    existing: ProviderProfile | None = None
    stored = config["profiles"].get(provider)
    if stored is not None:
        try:
            existing = ProviderProfile.from_dict(stored)
        except ValueError:
            existing = None
    key_env = args.key_env or (existing.key_env if existing else None) or DEFAULT_KEY_ENV[provider]
    if _ENV_NAME_PATTERN.fullmatch(key_env) is None:
        print(f"plgen connect: invalid --key-env {key_env!r}", file=sys.stderr)
        return 2
    budget = args.max_output_tokens
    if budget is None:
        budget = existing.max_output_tokens if existing else DEFAULT_OUTPUT_TOKENS
    if not isinstance(budget, int) or isinstance(budget, bool) or budget <= 0:
        print(f"plgen connect: invalid --max-output-tokens {budget!r}", file=sys.stderr)
        return 2
    environ = os.environ if env is None else env
    key = environ.get(key_env)
    if key is None or not key.strip():
        print(
            f"plgen connect: missing credential: env var {key_env} is empty or missing",
            file=sys.stderr,
        )
        return 3
    try:
        if provider == "openrouter":
            verify_openrouter(model=model, key=key, sender=sender)
        else:
            verify_gemini(model=model, key=key, sender=sender)
    except ProviderError as exc:
        print(f"plgen connect: verification failed: {sanitize_error(exc)}", file=sys.stderr)
        return 3
    profiles = dict(config["profiles"])
    profiles[provider] = ProviderProfile(
        provider=provider, model=model, key_env=key_env, max_output_tokens=budget
    ).to_dict()
    atomic_write_json(config_path(root), {"profiles": profiles, "active_provider": provider})
    print(f"connected {provider} model={model} key_env={key_env}")
    return 0
