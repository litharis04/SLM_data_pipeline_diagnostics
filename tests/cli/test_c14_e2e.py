"""C14: real-pipeline end-to-end with substituted LLM transport.

Everything is real (validators, Parquet, DuckDB, dbt, cache) except the HTTP
socket layer, which a ``sitecustomize`` shim (test scaffolding, never shipped)
replays from a script file. At most 3 real dbt builds module-wide: green
create (seed 0), reseed (seed 7), red-create attempt 1. Every other build is a
verified-cache copy. Subprocess and in-process phases share ``ws_sub``.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import duckdb
import pytest

from data_pipeline_diagnostics.cli import app
from data_pipeline_diagnostics.cli.cmd_delete import run_delete
from data_pipeline_diagnostics.cli.cmd_open import run_open
from data_pipeline_diagnostics.cli.create import run_create
from data_pipeline_diagnostics.cli.instances import _cell
from data_pipeline_diagnostics.cli.providers import FakeTransport
from data_pipeline_diagnostics.cli.workspace import init_workspace
from data_pipeline_diagnostics.generator.cache import prepare_clean_instance
from data_pipeline_diagnostics.scenario import parse_scenario_json, validate_semantics

REPO = Path(__file__).resolve().parents[2]
COOP = json.loads((REPO / "scenarios" / "agriculture_coop_001.json").read_bytes())
HARVESTS = json.loads((REPO / "scenarios" / "agriculture_harvests_002.json").read_bytes())
GREEN_ID = "e2e_garden_001"
RED_ID = "e2e_red_001"
GREEN_SEED = 7
E2E_KEY = "sk-or-v1-E2EKEY"

SHIM_SOURCE = '''
"""Test-only transport shim (never shipped): replays scripted HTTP replies."""
import json as _json
import os as _os

_script_path = _os.environ.get("PLGEN_E2E_SCRIPT")
if _script_path:
    import data_pipeline_diagnostics.cli.providers as _providers

    with open(_script_path, encoding="utf-8") as _handle:
        _items = _json.load(_handle)["items"]
    _log_path = _os.environ.get("PLGEN_E2E_LOG")
    _state = {"n": 0}

    def _fake_send(provider, url, headers, body, timeout):
        item = _items[_state["n"]]
        _state["n"] += 1
        if "raise" in item:
            if item["raise"] == "timeout":
                raise TimeoutError("timed out")
            from urllib.error import URLError

            raise URLError(item.get("message", "boom"))
        if _log_path:
            logged = {"provider": provider, "url": url, "timeout": timeout}
            if body is not None:
                payload = _json.loads(body.decode("utf-8"))
                config = payload.get("generationConfig", {})
                logged["max_tokens"] = payload.get("max_tokens", config.get("maxOutputTokens"))
                logged["messages"] = len(payload.get("messages", payload.get("contents", [])))
            with open(_log_path, "a", encoding="utf-8") as _log_handle:
                _log_handle.write(_json.dumps(logged) + "\\n")
        return item["status"], dict(item.get("headers", {})), _json.dumps(item["body"]).encode()

    _providers._send_urllib = _fake_send
'''


def green_text() -> str:
    document = json.loads(json.dumps(COOP))
    document["scenario_id"] = GREEN_ID
    return json.dumps(document)


def red_text() -> str:
    document = json.loads(json.dumps(COOP))
    document["scenario_id"] = RED_ID
    for model in document["staging_models"]:
        for column in model["columns"]:
            if column["source"] == "seed_id":
                column["operations"] = [
                    {"op": "map_values", "mapping": {"ZZZ_NOPE": "x"}, "on_unmapped": "error"}
                ]
    return json.dumps(document)


def openrouter_reply(text: str) -> dict:
    return {
        "model": "m",
        "choices": [{"message": {"content": text}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 6},
    }


@pytest.fixture(scope="module")
def e2e_env(tmp_path_factory):
    work = tmp_path_factory.mktemp("c14")
    shim_dir = work / "shim"
    shim_dir.mkdir()
    (shim_dir / "sitecustomize.py").write_text(SHIM_SOURCE, encoding="utf-8")
    log = work / "calls.jsonl"
    env = dict(os.environ)
    pythonpath = str(shim_dir)
    if env.get("PYTHONPATH"):
        pythonpath += os.pathsep + env["PYTHONPATH"]
    env["PYTHONPATH"] = pythonpath
    env["PLGEN_E2E_LOG"] = str(log)
    env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env.get("PATH", "")
    env["OPENROUTER_API_KEY"] = E2E_KEY
    env.pop("GEMINI_API_KEY", None)
    return {"work": work, "ws": work / "ws", "env": env, "log": log}


def plgen(e2e_env, *argv, script=()) -> subprocess.CompletedProcess:
    """Run one real CLI subprocess; ``script`` holds exactly its HTTP replies."""
    handle, script_path = tempfile.mkstemp(
        prefix="script-", suffix=".json", dir=str(e2e_env["work"])
    )
    with open(handle, "w", encoding="utf-8") as stream:
        stream.write(json.dumps({"items": list(script)}))
    env = dict(e2e_env["env"])
    env["PLGEN_E2E_SCRIPT"] = script_path
    runner = "import sys; from data_pipeline_diagnostics.cli.app import main; sys.exit(main())"
    return subprocess.run(
        [sys.executable, "-c", runner, "--workspace", str(e2e_env["ws"]), *argv],
        capture_output=True,
        text=True,
        cwd=str(e2e_env["work"]),
        env=env,
        timeout=900,
    )


CONNECT_SCRIPT = [
    {"status": 200, "body": {"data": {"label": "t", "limit": 10}}},
    {"status": 200, "body": {"data": [{"id": "m"}]}},
]


def test_subprocess_create_list_open(e2e_env):
    connected = plgen(
        e2e_env,
        "connect",
        "--provider",
        "openrouter",
        "--model",
        "m",
        script=CONNECT_SCRIPT,
    )
    assert connected.returncode == 0, connected.stderr
    assert "connected openrouter model=m" in connected.stdout
    created = plgen(
        e2e_env,
        "create",
        "--domain",
        "agriculture",
        "--id",
        GREEN_ID,
        "--staging",
        "lower",
        "--joins",
        "inner",
        "--metrics",
        "sum",
        "--composite-keys",
        "forbidden",
        script=[{"status": 200, "body": openrouter_reply(green_text())}],
    )
    assert created.returncode == 0, created.stderr[-4000:]
    assert f"created {GREEN_ID} seed=0 size=small" in created.stdout
    assert "attempt 1/5" in created.stderr
    listed = plgen(e2e_env, "list")
    assert listed.returncode == 0, listed.stderr
    assert GREEN_ID in listed.stdout
    assert "prepared" in listed.stdout
    opened = plgen(e2e_env, "open", GREEN_ID)
    assert opened.returncode == 0, opened.stderr[-4000:]
    assert f"scenario: {GREEN_ID}" in opened.stdout
    assert "state: prepared" in opened.stdout
    assert "dbt build: success" in opened.stdout
    assert str(e2e_env["ws"].resolve()) in opened.stdout


def test_subprocess_reseed_and_reopen(e2e_env):
    before = (e2e_env["ws"] / "scenarios" / GREEN_ID / "scenario.json").read_bytes()
    reseeded = plgen(e2e_env, "seed", GREEN_ID, str(GREEN_SEED))
    assert reseeded.returncode == 0, reseeded.stderr[-4000:]
    assert f"seed: {GREEN_SEED}" in reseeded.stdout
    after = (e2e_env["ws"] / "scenarios" / GREEN_ID / "scenario.json").read_bytes()
    assert after == before
    entry = json.loads((e2e_env["ws"] / "scenarios" / GREEN_ID / "entry.json").read_text())
    assert entry["seed"] == GREEN_SEED
    reopened = plgen(e2e_env, "open", GREEN_ID)
    assert reopened.returncode == 0, reopened.stderr[-4000:]
    assert f"seed: {GREEN_SEED}" in reopened.stdout


def test_transport_log_counts_default_budget(e2e_env):
    calls = [json.loads(line) for line in (e2e_env["log"]).read_text(encoding="utf-8").splitlines()]
    assert len(calls) == 3
    assert [call["provider"] for call in calls] == ["openrouter"] * 3
    assert all(call["timeout"] == 180 for call in calls)
    assert calls[2]["max_tokens"] == 32768
    assert calls[2]["messages"] == 2


def test_inprocess_state_record_previews(e2e_env, capsys):
    ws = e2e_env["ws"]
    entry_before = json.loads((ws / "scenarios" / GREEN_ID / "entry.json").read_text())
    baseline = ws / "cache" / "v1" / entry_before["instance"]["digest"] / "SUCCESS"
    assert baseline.is_file()
    mtime_before = baseline.stat().st_mtime_ns
    code = run_open(ws, app.build_parser().parse_args(["open", GREEN_ID]))
    out, err = capsys.readouterr()
    assert code == 0
    assert err == ""
    assert baseline.stat().st_mtime_ns == mtime_before
    assert f"seed: {GREEN_SEED}" in out
    workdir = ws / "scenarios" / GREEN_ID / "work"
    record = json.loads((workdir / "instance_record.json").read_text(encoding="utf-8"))
    assert record["status"] == "success"
    assert record["identity"]["data_seed"] == GREEN_SEED
    run_results = json.loads((workdir / "dbt" / "target" / "run_results.json").read_text())
    for result in run_results["results"]:
        unique_id = str(result.get("unique_id", ""))
        if unique_id.startswith("model."):
            assert result.get("status") == "success", unique_id
        elif unique_id.startswith("test."):
            assert result.get("status") == "pass", unique_id
    assert (workdir / "dbt" / "target" / "manifest.json").is_file()
    green = validate_semantics(parse_scenario_json(green_text()))
    connection = duckdb.connect(str(workdir / "pipeline.duckdb"), read_only=True)
    try:
        model = green.scenario.output_models[0]
        name, grain = str(model.name), [str(column) for column in model.grain]
        order = ", ".join(f'"{column}"' for column in grain)
        expected = [
            [_cell(value) for value in row]
            for row in connection.execute(
                f'SELECT * FROM main."{name}" ORDER BY {order} LIMIT 5'
            ).fetchall()
        ]
    finally:
        connection.close()
    header = f"output {name} (ordered by grain: {', '.join(grain)}; at most 5 rows):"
    assert header in out
    lines = out.splitlines()
    pipe = []
    for line in lines[lines.index(header) + 1 :]:
        if " | " in line:
            pipe.append(line)
        else:
            break
    assert [line.split(" | ") for line in pipe[1:]] == expected
    assert str(workdir.resolve()) in out


def test_inprocess_delete_then_fresh_list(e2e_env, capsys):
    ws = e2e_env["ws"]
    assert run_delete(ws, app.build_parser().parse_args(["delete", GREEN_ID])) == 0
    capsys.readouterr()
    listed = plgen(e2e_env, "list")
    assert listed.returncode == 0, listed.stderr
    assert GREEN_ID not in listed.stdout


def test_create_red_exhaustion_keeps_nothing(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("OPENROUTER_API_KEY", E2E_KEY)
    root = tmp_path / "ws"
    assert init_workspace(root, []) is True
    (root / "config.json").write_text(
        json.dumps(
            {
                "profiles": {
                    "openrouter": {
                        "provider": "openrouter",
                        "model": "m",
                        "key_env": "OPENROUTER_API_KEY",
                        "max_output_tokens": 4096,
                    }
                },
                "active_provider": "openrouter",
            }
        ),
        encoding="utf-8",
    )
    red = red_text()
    validate_semantics(parse_scenario_json(red))
    script = [openrouter_reply(red)] + [
        {"model": "m", "choices": [{"message": {"content": "{oops"}, "finish_reason": "stop"}]}
    ] * 4
    fake = FakeTransport(script)
    argv = [
        "create",
        "--domain",
        "agriculture",
        "--id",
        RED_ID,
        "--staging",
        "lower,map_values",
        "--joins",
        "inner",
        "--metrics",
        "sum",
        "--composite-keys",
        "forbidden",
    ]
    code = run_create(
        root,
        app.build_parser().parse_args(argv),
        sender=fake,
        prepare=prepare_clean_instance,
        spec_text="SPEC",
        schema_json="{}",
        bundled=[
            validate_semantics(parse_scenario_json(json.dumps(COOP))),
            validate_semantics(parse_scenario_json(json.dumps(HARVESTS))),
        ],
        env={"OPENROUTER_API_KEY": E2E_KEY},
    )
    _, err = capsys.readouterr()
    assert code == 4
    assert len(fake.calls) == 5
    assert "failed after 5 attempts" in err
    assert "diagnostics:" in err
    run_dir = root / "authoring" / RED_ID
    record = json.loads((run_dir / "failure_record.json").read_text(encoding="utf-8"))
    assert record["status"] == "attempts-exhausted"
    assert record["attempts"] == 5
    assert record["final"]["code"] == "invalid-json"
    first = json.loads((run_dir / "attempt_01.json").read_text(encoding="utf-8"))
    assert first["issues"] and first["issues"][0]["code"] == "build-failed"
    assert first["failure_record"].endswith("failure_record.json")
    assert Path(first["failure_record"]).is_file()
    catalog = json.loads((root / "catalog.json").read_text(encoding="utf-8"))
    assert catalog["scenario_ids"] == []
    assert not (root / "scenarios" / RED_ID).exists()
