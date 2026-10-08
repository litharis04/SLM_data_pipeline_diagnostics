"""C16: live-authoring evaluation (separately enabled, never in CI).

Enable with ``PLGEN_LIVE=1`` plus ``OPENROUTER_API_KEY`` and
``GEMINI_API_KEY`` in the environment. Both adapters run the same small
diverse request set through the real ``create`` path (real transport, real
builds). Models default to ``openrouter/free`` and ``gemini-3.8-flash`` and
can be overridden with ``PLGEN_LIVE_OR_MODEL`` / ``PLGEN_LIVE_GEMINI_MODEL``
(free models only; the harness never enables billing or switches providers).
Results land in ``artifacts/cli_live_<date>.md``/``.json``; the report test
refuses to write files containing secret material.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import re
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from data_pipeline_diagnostics.cli import app
from data_pipeline_diagnostics.cli.cmd_connect import run_connect
from data_pipeline_diagnostics.cli.create import run_create

REPO = Path(__file__).resolve().parents[2]

REQUIRED_KEYS = ("OPENROUTER_API_KEY", "GEMINI_API_KEY")

PROVIDERS = (
    {
        "provider": "openrouter",
        "model_env": "PLGEN_LIVE_OR_MODEL",
        "default_model": "openrouter/free",
        "key_env": "OPENROUTER_API_KEY",
    },
    {
        "provider": "gemini",
        "model_env": "PLGEN_LIVE_GEMINI_MODEL",
        "default_model": "gemini-3.8-flash",
        "key_env": "GEMINI_API_KEY",
    },
)

REQUESTS = (
    {
        "key": "plain",
        "argv": ["create", "--domain", "bakery", "--id", "live_plain_001"],
    },
    {
        "key": "composite",
        "argv": [
            "create",
            "--domain",
            "joinery",
            "--id",
            "live_composite_001",
            "--composite-keys",
            "required",
            "--staging",
            "trim",
        ],
    },
    {
        "key": "combined",
        "argv": [
            "create",
            "--domain",
            "repairs",
            "--id",
            "live_combined_001",
            "--joins",
            "mixed",
            "--metrics",
            "sum,avg",
            "--intermediate",
            "derive",
        ],
    },
)

SECRET_PATTERNS = (
    re.compile(r"sk-or-v1-[A-Za-z0-9]+"),
    re.compile(r"AIza[A-Za-z0-9_-]+"),
    re.compile(r"(?i)(bearer\s+)\S+"),
    re.compile(r"(?i)(x-goog-api-key\s*[:=]\s*)\S+"),
)

pytestmark = pytest.mark.skipif(
    os.environ.get("PLGEN_LIVE") != "1"
    or any(not os.environ.get(name, "").strip() for name in REQUIRED_KEYS),
    reason=(
        "live evaluation: set PLGEN_LIVE=1 with OPENROUTER_API_KEY and "
        "GEMINI_API_KEY present (never part of the default suite)"
    ),
)

RESULTS: list[dict] = []


def parse(argv: list[str]):
    return app.build_parser().parse_args(argv)


def connect_profile(root: Path, provider: str, model: str) -> int:
    return run_connect(root, parse(["connect", "--provider", provider, "--model", model]))


def summarize_attempts(run_dir: Path) -> tuple[int, dict[str, int | None], bool]:
    """Count attempts, sum reported token usage, note whether any was reported."""
    usage = {"input_tokens": 0, "output_tokens": 0, "reasoning_tokens": 0}
    reported = False
    files = sorted(run_dir.glob("attempt_*.json"))
    for path in files:
        record = json.loads(path.read_text(encoding="utf-8"))
        entry = record.get("usage") or {}
        for field in usage:
            value = entry.get(field)
            if isinstance(value, int):
                usage[field] += value
                reported = True
    return len(files), usage, reported


def classify_exit(code: int, run_dir: Path, stderr: str) -> tuple[str, dict | None]:
    """Map the run outcome onto report categories (infra kept separate)."""
    if code == 0:
        return "accepted", None
    failure_file = run_dir / "failure_record.json"
    final = None
    if failure_file.is_file():
        record = json.loads(failure_file.read_text(encoding="utf-8"))
        final = record.get("final")
    if code == 3:
        if "refus" in stderr:
            return "refusal", final
        return "provider", final
    if code == 4:
        return "exhausted", final
    return "infra", final


def evaluate_request(root: Path, provider: str, model: str, request: dict) -> dict:
    buffer = io.StringIO()
    started = time.perf_counter()
    with contextlib.redirect_stderr(buffer):
        code = run_create(root, parse(request["argv"]))
    elapsed = time.perf_counter() - started
    scenario_id = request["argv"][request["argv"].index("--id") + 1]
    run_dir = root / "authoring" / scenario_id
    attempts, usage, reported = summarize_attempts(run_dir)
    outcome, final = classify_exit(code, run_dir, buffer.getvalue())
    entry_path = root / "scenarios" / scenario_id / "entry.json"
    accepted: dict | None = None
    if code == 0 and entry_path.is_file():
        entry = json.loads(entry_path.read_text(encoding="utf-8"))
        accepted = {
            "scenario_hash": entry.get("scenario_hash"),
            "realized": (entry.get("checks") or {}).get("realized"),
            "model_identity": (entry.get("provenance") or {}).get("model_identity"),
        }
    return {
        "provider": provider,
        "model": model,
        "request": request["key"],
        "scenario_id": scenario_id,
        "outcome": outcome,
        "exit": code,
        "attempts_used": attempts,
        "elapsed_s": round(elapsed, 1),
        "usage": usage,
        "usage_reported": reported,
        "final": final,
        "accepted": accepted,
    }


def evaluate_provider(root: Path, spec: dict) -> list[dict]:
    model = os.environ.get(spec["model_env"], spec["default_model"])
    connect_code = connect_profile(root, spec["provider"], model)
    if connect_code != 0:
        return [
            {
                "provider": spec["provider"],
                "model": model,
                "request": request["key"],
                "scenario_id": None,
                "outcome": "unavailable",
                "exit": connect_code,
                "attempts_used": 0,
                "elapsed_s": 0.0,
                "usage": {"input_tokens": 0, "output_tokens": 0, "reasoning_tokens": 0},
                "usage_reported": False,
                "final": {"code": "connect-failed", "path": "config", "message": "see stderr"},
                "accepted": None,
            }
            for request in REQUESTS
        ]
    return [evaluate_request(root, spec["provider"], model, request) for request in REQUESTS]


def test_live_openrouter(tmp_path):
    RESULTS.extend(evaluate_provider(tmp_path / "ws-or", PROVIDERS[0]))


def test_live_gemini(tmp_path):
    RESULTS.extend(evaluate_provider(tmp_path / "ws-gemini", PROVIDERS[1]))


def test_write_live_report():
    assert RESULTS, "live evaluations must run before the report (do not select with -k)"
    date = datetime.now(UTC).date().isoformat()
    report = {
        "date": date,
        "suite": "tests/cli/test_c16_live.py (PLGEN_LIVE=1)",
        "models": {
            spec["provider"]: os.environ.get(spec["model_env"], spec["default_model"])
            for spec in PROVIDERS
        },
        "notes": [
            "Five attempts is a budget, not a guaranteed success rate.",
            "API unavailability (connect failures, rate limits, timeouts) is recorded "
            "as unavailable/infra/provider outcomes, separate from authoring quality.",
            "No corpus-wide generation, no paid fallback, no model/provider switching, "
            "no retried 429s; the example corpus was not touched.",
        ],
        "results": RESULTS,
    }
    payload = json.dumps(report, indent=2, sort_keys=True) + "\n"
    for value in [os.environ.get(name, "") for name in REQUIRED_KEYS]:
        assert value and value not in payload, "secret material must never reach the report"
    for pattern in SECRET_PATTERNS:
        assert not pattern.search(payload), f"secret pattern in report: {pattern.pattern}"
    json_path = REPO / "artifacts" / f"cli_live_{date}.json"
    json_path.write_text(payload, encoding="utf-8")
    lines = [
        f"# CLI live-authoring evaluation ({date})",
        "",
        "| provider | model | request | outcome | exit | attempts | elapsed (s) | input/output/reasoning |",
        "| --- | --- | --- | --- | ---: | ---: | ---: | --- |",
    ]
    for result in RESULTS:
        usage = result["usage"]
        lines.append(
            f"| {result['provider']} | {result['model']} | {result['request']} | "
            f"{result['outcome']} | {result['exit']} | {result['attempts_used']} | "
            f"{result['elapsed_s']} | "
            f"{usage['input_tokens']}/{usage['output_tokens']}/{usage['reasoning_tokens']} |"
        )
    md_path = REPO / "artifacts" / f"cli_live_{date}.md"
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    for path in (json_path, md_path):
        text = path.read_text(encoding="utf-8")
        for value in [os.environ.get(name, "") for name in REQUIRED_KEYS]:
            assert value and value not in text
