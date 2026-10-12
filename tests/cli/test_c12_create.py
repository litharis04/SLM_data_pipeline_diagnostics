from __future__ import annotations

import argparse
import json
import tempfile
from dataclasses import dataclass
from pathlib import Path

import pytest

from data_pipeline_diagnostics.cli import app, create
from data_pipeline_diagnostics.cli.create import run_create
from data_pipeline_diagnostics.cli.providers import FakeTransport
from data_pipeline_diagnostics.cli.workspace import init_workspace
from data_pipeline_diagnostics.generator.raw_constraints import GenerationFailure
from data_pipeline_diagnostics.scenario import (
    canonical_json,
    parse_scenario_json,
    validate_semantics,
)

REPO = Path(__file__).resolve().parents[2]
COOP = (REPO / "scenarios" / "agriculture_coop_001.json").read_bytes()
HARVESTS = (REPO / "scenarios" / "agriculture_harvests_002.json").read_bytes()
COOP_ID = "agriculture_coop_001"
OR_ENV = "OPENROUTER_API_KEY"
OR_KEY = "sk-or-v1-TESTKEY007"

COOP_VALIDATED = validate_semantics(parse_scenario_json(COOP))
HARVESTS_VALIDATED = validate_semantics(parse_scenario_json(HARVESTS))
BUNDLED = [COOP_VALIDATED, HARVESTS_VALIDATED]


@dataclass(frozen=True)
class FakeInstance:
    instance_dir: Path
    instance_digest: str
    cache_hit: bool = False


class FakeBuilder:
    """Scripted builder: ("ok", counts, digest) or ("fail", reason, detail)."""

    def __init__(self, parent: Path, script: list[tuple]):
        self.parent = parent
        self.script = list(script)
        self.calls: list[tuple[str, int]] = []

    def __call__(self, validated, seed: int, cache_root):
        scenario_id = str(validated.scenario.scenario_id)
        self.calls.append((scenario_id, seed))
        if not self.script:
            raise AssertionError("FakeBuilder script exhausted")
        item = self.script.pop(0)
        if item[0] == "ok":
            _, counts, digest = item
            target = Path(tempfile.mkdtemp(dir=str(self.parent)))
            record = {
                "raw_tables": [{"name": f"t{i}", "row_count": c} for i, c in enumerate(counts)]
            }
            (target / "instance_record.json").write_text(json.dumps(record), encoding="utf-8")
            (target / "pipeline.duckdb").write_bytes(b"fake")
            return FakeInstance(target, digest)
        _, reason, detail = item
        target = Path(tempfile.mkdtemp(dir=str(self.parent)))
        (target / "failure_record.json").write_text(
            json.dumps({"category": reason, "message": detail}), encoding="utf-8"
        )
        exc = GenerationFailure(table="*", column=None, reason=reason, detail=detail)
        exc.workspace = target
        raise exc


def reply(text: str, finish: str = "stop", model: str = "m") -> dict:
    return {
        "model": model,
        "choices": [{"message": {"content": text}, "finish_reason": finish}],
        "usage": {"prompt_tokens": 11, "completion_tokens": 22},
    }


def broken_source(index: int) -> str:
    document = json.loads(COOP.decode("utf-8"))
    document["staging_models"][0]["source"] = f"nope_table_{index}"
    return json.dumps(document)


def stage(root: Path, with_profile: bool = True) -> Path:
    assert init_workspace(root, []) is True
    if with_profile:
        (root / "config.json").write_text(
            json.dumps(
                {
                    "profiles": {
                        "openrouter": {
                            "provider": "openrouter",
                            "model": "m",
                            "key_env": OR_ENV,
                            "max_output_tokens": 4096,
                        }
                    },
                    "active_provider": "openrouter",
                }
            ),
            encoding="utf-8",
        )
    return root


def parse(argv: list[str]) -> argparse.Namespace:
    return app.build_parser().parse_args(argv)


BASE_ARGV = [
    "create",
    "--domain",
    "agriculture",
    "--id",
    COOP_ID,
    "--staging",
    "lower",
    "--joins",
    "inner",
    "--metrics",
    "sum",
    "--composite-keys",
    "forbidden",
]


def run(root: Path, argv: list[str], tmp_path: Path, **kwargs):
    kwargs.setdefault("spec_text", "SPEC")
    kwargs.setdefault("schema_json", "{}")
    kwargs.setdefault("bundled", BUNDLED)
    return run_create(root, parse(argv), **kwargs)


def users_of(fake: FakeTransport) -> list[str]:
    return [
        json.loads(call["body"].decode("utf-8"))["messages"][1]["content"] for call in fake.calls
    ]


def test_first_try_success_publishes(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv(OR_ENV, OR_KEY)
    root = stage(tmp_path / "ws")
    fake = FakeTransport([reply(COOP.decode("utf-8"), model="router-model")])
    builder = FakeBuilder(tmp_path, [("ok", [5, 50, 500], "digest-1")])
    code = run(root, BASE_ARGV, tmp_path, sender=fake, prepare=builder, env={OR_ENV: OR_KEY})
    out, err = capsys.readouterr()
    assert code == 0
    assert "plgen create: attempt 1/5: requesting candidate" in err
    assert "plgen create: attempt 1/5: validating candidate" in err
    assert "plgen create: attempt 1/5: building clean instance" in err
    assert "plgen create: attempt 1/5: checking realized size" in err
    assert f"created {COOP_ID} seed=0 size=small (500 rows)" in out
    scenario_file = root / "scenarios" / COOP_ID / "scenario.json"
    workdir = root / "scenarios" / COOP_ID / "work"
    assert f"scenario: {scenario_file.resolve()}" in out
    assert f"instance: {workdir.resolve()}" in out
    assert scenario_file.read_bytes() == canonical_json(COOP_VALIDATED.scenario)
    entry = json.loads((root / "scenarios" / COOP_ID / "entry.json").read_text())
    assert entry["origin"] == "authored"
    assert entry["seed"] == 0
    assert entry["instance"] == {"path": str(workdir.resolve()), "digest": "digest-1"}
    assert entry["requirements"]["scenario_id"] == COOP_ID
    assert entry["provenance"]["provider"] == "openrouter"
    assert entry["provenance"]["model_identity"] == "router-model"
    assert entry["provenance"]["usage"] == {
        "input_tokens": 11,
        "output_tokens": 22,
        "reasoning_tokens": None,
    }
    assert entry["checks"]["realized"] == {"max_raw_rows": 500, "category": "small"}
    assert entry["checks"]["requirement_issues"] == []
    catalog = json.loads((root / "catalog.json").read_text())
    assert catalog["scenario_ids"] == [COOP_ID]
    run_dir = root / "authoring" / COOP_ID
    assert (run_dir / "request.json").is_file()
    attempts = sorted((run_dir).glob("attempt_*.json"))
    assert len(attempts) == 1
    assert json.loads(attempts[0].read_text())["issues"] == []
    assert fake.calls and len(fake.calls) == 1
    assert builder.calls == [(COOP_ID, 0)]
    payload = json.loads(fake.calls[0]["body"].decode("utf-8"))
    assert payload["max_tokens"] == 4096
    assert OR_KEY not in out and OR_KEY not in json.dumps(entry)


def test_repair_accepted_on_fifth(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv(OR_ENV, OR_KEY)
    root = stage(tmp_path / "ws")
    texts = [broken_source(i) for i in range(1, 5)] + [COOP.decode("utf-8")]
    fake = FakeTransport([reply(text) for text in texts])
    builder = FakeBuilder(tmp_path, [("ok", [5, 50, 500], "digest-5")])
    code = run(root, BASE_ARGV, tmp_path, sender=fake, prepare=builder, env={OR_ENV: OR_KEY})
    out, _ = capsys.readouterr()
    assert code == 0
    assert len(fake.calls) == 5
    assert len(builder.calls) == 1
    assert f"created {COOP_ID}" in out


def test_frozen_requirements_latest_only_repairs(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv(OR_ENV, OR_KEY)
    root = stage(tmp_path / "ws")
    texts = [broken_source(i) for i in range(1, 5)] + [COOP.decode("utf-8")]
    fake = FakeTransport([reply(text) for text in texts])
    builder = FakeBuilder(tmp_path, [("ok", [5, 50, 500], "digest-5")])
    assert run(root, BASE_ARGV, tmp_path, sender=fake, prepare=builder, env={OR_ENV: OR_KEY}) == 0
    capsys.readouterr()
    users = users_of(fake)
    assert len(users) == 5
    fixed = f"scenario_id: {COOP_ID}"
    assert all(fixed in user for user in users)
    assert "Previous candidate" not in users[0]
    for index in range(1, 5):
        assert "Previous candidate" in users[index]
        assert texts[index - 1] in users[index]
        for older in texts[: index - 1]:
            assert older not in users[index]


def test_exhaustion_reports_and_registers_nothing(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv(OR_ENV, OR_KEY)
    root = stage(tmp_path / "ws")
    fake = FakeTransport([reply(broken_source(i)) for i in range(1, 6)])
    builder = FakeBuilder(tmp_path, [])
    code = run(root, BASE_ARGV, tmp_path, sender=fake, prepare=builder, env={OR_ENV: OR_KEY})
    _, err = capsys.readouterr()
    assert code == 4
    assert len(fake.calls) == 5
    assert builder.calls == []
    assert "failed after 5 attempts" in err
    assert "diagnostics:" in err
    run_dir = root / "authoring" / COOP_ID
    record = json.loads((run_dir / "failure_record.json").read_text())
    assert record["status"] == "attempts-exhausted"
    assert record["attempts"] == 5
    assert record["final"]["code"]
    assert len(sorted(run_dir.glob("attempt_*.json"))) == 5
    catalog = json.loads((root / "catalog.json").read_text())
    assert catalog["scenario_ids"] == []
    assert not (root / "scenarios" / COOP_ID).exists()


@pytest.mark.parametrize(
    ("script", "needle"),
    [
        ([("http", 429, {"error": {"code": 429, "message": "slow"}}, {})], "429"),
        ([("http", 401, {"error": {"code": "bad", "message": "nope"}}, {})], "nope"),
        ([("timeout",)], "timed out"),
    ],
)
def test_provider_aborts_untouched_budget(tmp_path, monkeypatch, capsys, script, needle):
    monkeypatch.setenv(OR_ENV, OR_KEY)
    root = stage(tmp_path / "ws")
    fake = FakeTransport(script)
    builder = FakeBuilder(tmp_path, [])
    code = run(root, BASE_ARGV, tmp_path, sender=fake, prepare=builder, env={OR_ENV: OR_KEY})
    _, err = capsys.readouterr()
    assert code == 3
    assert needle in err
    assert OR_KEY not in err
    assert len(fake.calls) == 1
    assert builder.calls == []
    assert json.loads((root / "catalog.json").read_text())["scenario_ids"] == []
    assert not (root / "scenarios" / COOP_ID).exists()


def test_refusal_is_distinct_not_infeasibility(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv(OR_ENV, OR_KEY)
    root = stage(tmp_path / "ws")
    fake = FakeTransport([reply("no", finish="content_filter")])
    builder = FakeBuilder(tmp_path, [])
    code = run(root, BASE_ARGV, tmp_path, sender=fake, prepare=builder, env={OR_ENV: OR_KEY})
    _, err = capsys.readouterr()
    assert code == 3
    assert "refus" in err
    assert "infeasible" not in err
    assert "not proof" in err
    assert len(fake.calls) == 1


def test_infra_failure_aborts_baseline_untouched(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv(OR_ENV, OR_KEY)
    root = stage(tmp_path / "ws")
    baseline = root / "cache" / "v1" / "baseline"
    baseline.mkdir(parents=True)
    (baseline / "data.bin").write_bytes(b"baseline")
    fake = FakeTransport([reply(COOP.decode("utf-8"))])
    builder = FakeBuilder(tmp_path, [("fail", "dbt-executable-not-found", "no dbt")])
    code = run(root, BASE_ARGV, tmp_path, sender=fake, prepare=builder, env={OR_ENV: OR_KEY})
    _, err = capsys.readouterr()
    assert code == 5
    assert "dbt-executable-not-found" in err
    assert "failure_record" in err
    assert (baseline / "data.bin").read_bytes() == b"baseline"
    assert json.loads((root / "catalog.json").read_text())["scenario_ids"] == []
    assert not (root / "scenarios" / COOP_ID).exists()


def test_data_dependent_failure_repairs_then_succeeds(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv(OR_ENV, OR_KEY)
    root = stage(tmp_path / "ws")
    valid = COOP.decode("utf-8")
    fake = FakeTransport([reply(valid), reply(valid)])
    builder = FakeBuilder(
        tmp_path, [("fail", "dbt-test-failure", "row count off"), ("ok", [5, 50, 500], "digest-2")]
    )
    code = run(root, BASE_ARGV, tmp_path, sender=fake, prepare=builder, env={OR_ENV: OR_KEY})
    out, _ = capsys.readouterr()
    assert code == 0
    assert len(fake.calls) == 2
    assert len(builder.calls) == 2
    users = users_of(fake)
    assert "build-failed" in users[1]
    assert "failure record" in users[1]
    assert f"created {COOP_ID}" in out


def test_realized_size_failure_repairs(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv(OR_ENV, OR_KEY)
    root = stage(tmp_path / "ws")
    valid = COOP.decode("utf-8")
    fake = FakeTransport([reply(valid), reply(valid)])
    builder = FakeBuilder(tmp_path, [("ok", [5, 2000], "d-big"), ("ok", [5, 50, 500], "d-ok")])
    code = run(root, BASE_ARGV, tmp_path, sender=fake, prepare=builder, env={OR_ENV: OR_KEY})
    out, _ = capsys.readouterr()
    assert code == 0
    assert "size=small (500 rows)" in out
    assert "raw-size-exceeded" in users_of(fake)[1]


def test_crash_before_publish_keeps_previous_usable(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv(OR_ENV, OR_KEY)
    root = stage(tmp_path / "ws")
    neighbor_dir = root / "scenarios" / "neighbor_001"
    neighbor_dir.mkdir(parents=True)
    (neighbor_dir / "scenario.json").write_bytes(b'{"n": 1}')
    catalog_file = root / "catalog.json"
    catalog = json.loads(catalog_file.read_text())
    catalog["scenario_ids"].append("neighbor_001")
    catalog_file.write_text(json.dumps(catalog), encoding="utf-8")

    def boom(*args, **kwargs):
        raise RuntimeError("simulated crash")

    monkeypatch.setattr(create, "publish_scenario", boom)
    builder = FakeBuilder(tmp_path, [("ok", [5, 50, 500], "digest-1")])
    argv = [
        "create",
        "--domain",
        "agriculture",
        "--id",
        "fresh_001",
        "--staging",
        "lower",
        "--joins",
        "inner",
        "--metrics",
        "sum",
        "--composite-keys",
        "forbidden",
    ]
    document = json.loads(COOP.decode("utf-8"))
    document["scenario_id"] = "fresh_001"
    fake2 = FakeTransport([reply(json.dumps(document))])
    with pytest.raises(RuntimeError, match="simulated crash"):
        run(root, argv, tmp_path, sender=fake2, prepare=builder, env={OR_ENV: OR_KEY})
    capsys.readouterr()
    assert json.loads(catalog_file.read_text())["scenario_ids"] == ["neighbor_001"]
    assert (neighbor_dir / "scenario.json").read_bytes() == b'{"n": 1}'
    assert not (root / "scenarios" / "fresh_001").exists()
    assert len(fake2.calls) == 1


def test_interrupt_publishes_nothing(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv(OR_ENV, OR_KEY)
    root = stage(tmp_path / "ws")
    calls: list[int] = []

    def flaky(provider, url, headers, body, timeout):
        calls.append(1)
        if len(calls) == 1:
            return 200, {}, json.dumps(reply(broken_source(1))).encode("utf-8")
        raise KeyboardInterrupt

    builder = FakeBuilder(tmp_path, [])
    with pytest.raises(KeyboardInterrupt):
        run(root, BASE_ARGV, tmp_path, sender=flaky, prepare=builder, env={OR_ENV: OR_KEY})
    capsys.readouterr()
    assert json.loads((root / "catalog.json").read_text())["scenario_ids"] == []
    assert not (root / "scenarios" / COOP_ID).exists()


def test_allocated_id_end_to_end(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv(OR_ENV, OR_KEY)
    root = stage(tmp_path / "ws")
    document = json.loads(COOP.decode("utf-8"))
    document["scenario_id"] = "demo_001"
    document["domain"] = "demo"
    candidate = json.dumps(document)
    assert validate_semantics(parse_scenario_json(candidate)).scenario.scenario_id == "demo_001"
    fake = FakeTransport([reply(candidate)])
    builder = FakeBuilder(tmp_path, [("ok", [5, 50, 500], "digest-9")])
    argv = [
        "create",
        "--domain",
        "demo",
        "--staging",
        "lower",
        "--joins",
        "inner",
        "--metrics",
        "sum",
        "--composite-keys",
        "forbidden",
    ]
    code = run(root, argv, tmp_path, sender=fake, prepare=builder, env={OR_ENV: OR_KEY})
    out, _ = capsys.readouterr()
    assert code == 0
    assert "created demo_001" in out
    assert (root / "scenarios" / "demo_001" / "scenario.json").is_file()


def test_local_and_credential_failures(tmp_path, monkeypatch, capsys):
    root = stage(tmp_path / "ws")
    assert (
        run(
            root,
            ["create", "--domain", "demo", "--staging", "bogus"],
            tmp_path,
            sender=FakeTransport([]),
            prepare=FakeBuilder(tmp_path, []),
            env={},
        )
        == 2
    )
    capsys.readouterr()
    assert list((root / "authoring").iterdir()) == []
    assert (
        run(
            root,
            BASE_ARGV,
            tmp_path,
            sender=FakeTransport([]),
            prepare=FakeBuilder(tmp_path, []),
            env={},
        )
        == 3
    )
    capsys.readouterr()
    root2 = stage(tmp_path / "ws2")
    (root2 / "config.json").write_text(json.dumps({"profiles": {}, "active_provider": None}))
    assert (
        run(
            root2,
            BASE_ARGV,
            tmp_path,
            sender=FakeTransport([]),
            prepare=FakeBuilder(tmp_path, []),
            env={OR_ENV: OR_KEY},
        )
        == 3
    )
    capsys.readouterr()


def test_id_collision_fails_locally(tmp_path, monkeypatch, capsys):
    root = stage(tmp_path / "ws")
    (root / "scenarios" / COOP_ID).mkdir(parents=True)
    catalog_file = root / "catalog.json"
    catalog = json.loads(catalog_file.read_text())
    catalog["scenario_ids"].append(COOP_ID)
    catalog_file.write_text(json.dumps(catalog), encoding="utf-8")
    fake = FakeTransport([])
    code = run(
        root,
        BASE_ARGV,
        tmp_path,
        sender=fake,
        prepare=FakeBuilder(tmp_path, []),
        env={OR_ENV: OR_KEY},
    )
    _, err = capsys.readouterr()
    assert code == 2
    assert "collision" in err
    assert fake.calls == []


def test_dispatch_wires_create(tmp_path, monkeypatch):
    seen = {}

    def recorder(workspace, parsed):
        seen["domain"] = parsed.domain
        return 0

    monkeypatch.setattr(app, "run_create", recorder)
    code = app.main(["--workspace", str(tmp_path / "ws"), "create", "--domain", "demo"])
    assert code == 0
    assert seen == {"domain": "demo"}
