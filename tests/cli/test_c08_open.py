from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from data_pipeline_diagnostics.cli import app, instances
from data_pipeline_diagnostics.cli.cmd_open import run_open
from data_pipeline_diagnostics.cli.features import (
    AuthoringRequest,
    extract_features,
    normalize_request,
)
from data_pipeline_diagnostics.cli.instances import _cell
from data_pipeline_diagnostics.generator.cache import identity_digest
from data_pipeline_diagnostics.scenario import (
    parse_scenario_json,
    scenario_content_hash,
    validate_semantics,
)

REPO = Path(__file__).resolve().parents[2]
MINI_ID = "minimal_fixture"
MINI_BYTES = (REPO / "tests/scenario/fixtures/valid/minimal.json").read_bytes()
FAILING_ID = "failing_row_count"
FAILING_BYTES = (REPO / "tests/generator/fixtures/failing_row_count.json").read_bytes()

MINI_VALIDATED = validate_semantics(parse_scenario_json(MINI_BYTES))


def stage(root: Path, scenario_id: str = MINI_ID, raw: bytes = MINI_BYTES) -> Path:
    from data_pipeline_diagnostics.cli.workspace import init_workspace

    assert init_workspace(root, [(scenario_id, raw)]) is True
    return root


def run(root: Path, scenario_id: str, cache: Path, **kwargs):
    return run_open(
        root, app.build_parser().parse_args(["open", scenario_id]), cache_root=cache, **kwargs
    )


@pytest.fixture(scope="module")
def shared_cache(tmp_path_factory):
    return tmp_path_factory.mktemp("cli-open-cache")


@pytest.fixture(scope="module")
def ws_opened(tmp_path_factory):
    ws = tmp_path_factory.mktemp("cli-open-ws")
    return stage(ws)


def test_first_open_builds_and_summarizes(ws_opened, shared_cache, capsys):
    code = run(ws_opened, MINI_ID, shared_cache)
    out, err = capsys.readouterr()
    assert code == 0
    assert err == ""
    assert f"scenario: {MINI_ID}" in out
    assert "domain: testdomain" in out
    assert "seed: 0" in out
    assert "state: prepared" in out
    assert "models: staging=3 intermediate=3 output=2" in out
    assert "name layer rows" in out
    for raw in MINI_VALIDATED.scenario.raw_tables:
        assert f"{raw.name} raw " in out
    for model in (
        *MINI_VALIDATED.scenario.staging_models,
        *MINI_VALIDATED.scenario.intermediate_models,
        *MINI_VALIDATED.scenario.output_models,
    ):
        assert f"{model.name} " in out
    assert "largest raw table:" in out
    assert "dbt build: success" in out
    assert "working copy created:" in out
    scenario_file = ws_opened / "scenarios" / MINI_ID / "scenario.json"
    workdir = ws_opened / "scenarios" / MINI_ID / "work"
    assert f"scenario: {scenario_file.resolve()}" in out
    assert f"workdir: {workdir.resolve()}" in out
    assert "pipeline.duckdb" in out
    assert f"dbt: {workdir.resolve() / 'dbt'}" in out
    entry = json.loads((ws_opened / "scenarios" / MINI_ID / "entry.json").read_text())
    assert entry["seed"] == 0
    assert entry["instance"]["path"] == str(workdir.resolve())
    assert entry["instance"]["digest"] == identity_digest(MINI_VALIDATED, 0)
    assert entry["scenario_hash"] == scenario_content_hash(MINI_VALIDATED.scenario)
    assert (workdir / "pipeline.duckdb").is_file()
    assert (workdir / "instance_record.json").is_file()


def test_output_previews_bounded_and_grain_ordered(ws_opened, shared_cache, capsys):
    import duckdb

    code = run(ws_opened, MINI_ID, shared_cache)
    out, _ = capsys.readouterr()
    assert code == 0
    workdir = ws_opened / "scenarios" / MINI_ID / "work"
    connection = duckdb.connect(str(workdir / "pipeline.duckdb"), read_only=True)
    try:
        for model in MINI_VALIDATED.scenario.output_models:
            name = str(model.name)
            grain = [str(column) for column in model.grain]
            header = f"output {name} (ordered by grain: {', '.join(grain)}; at most 5 rows):"
            assert header in out
            lines = out.splitlines()
            pipe = []
            for line in lines[lines.index(header) + 1 :]:
                if " | " in line:
                    pipe.append(line)
                else:
                    break
            data = pipe[1:]
            total = connection.execute(f'SELECT COUNT(*) FROM main."{name}"').fetchone()[0]
            assert 1 <= len(data) <= 5
            assert len(data) == min(5, total)
            order = ", ".join(f'"{column}"' for column in grain)
            expected = [
                [_cell(value) for value in row]
                for row in connection.execute(
                    f'SELECT * FROM main."{name}" ORDER BY {order} LIMIT 5'
                ).fetchall()
            ]
            assert [line.split(" | ") for line in data] == expected
    finally:
        connection.close()


def test_second_open_reuses_baseline_and_replaces_copy(ws_opened, shared_cache, capsys):
    seen = {}
    real_prepare = instances.prepare_clean_instance

    def spy(validated, seed, cache_root):
        instance = real_prepare(validated, seed, cache_root)
        seen["hit"] = instance.cache_hit
        seen["digest"] = instance.instance_digest
        return instance

    workdir = ws_opened / "scenarios" / MINI_ID / "work"
    assert run(ws_opened, MINI_ID, shared_cache, prepare=spy) == 0
    capsys.readouterr()
    assert run(ws_opened, MINI_ID, shared_cache, prepare=spy) == 0
    out, _ = capsys.readouterr()
    assert seen["hit"] is True
    assert "working copy replaced:" in out
    success = shared_cache / "v1" / seen["digest"] / "SUCCESS"
    before = success.stat().st_mtime_ns
    marker = workdir / "tamper_probe.txt"
    marker.write_text("x", encoding="utf-8")
    assert run(ws_opened, MINI_ID, shared_cache, prepare=spy) == 0
    out, _ = capsys.readouterr()
    assert "working copy replaced:" in out
    assert success.stat().st_mtime_ns == before
    assert not marker.exists()


def test_tampered_copy_restored_pristine(ws_opened, shared_cache, capsys):
    workdir = ws_opened / "scenarios" / MINI_ID / "work"
    target = workdir / "scenario.json"
    target.write_bytes(b"tampered")
    code = run(ws_opened, MINI_ID, shared_cache)
    out, _ = capsys.readouterr()
    assert code == 0
    assert "working copy replaced:" in out
    entry = json.loads((ws_opened / "scenarios" / MINI_ID / "entry.json").read_text())
    baseline = shared_cache / "v1" / entry["instance"]["digest"] / "scenario.json"
    assert target.read_bytes() == baseline.read_bytes()


def test_unknown_id_and_corrupt_stored_json(tmp_path, shared_cache, capsys):
    root = stage(tmp_path / "ws")
    assert run(root, "nope_001", shared_cache) == 2
    _, err = capsys.readouterr()
    assert "unknown scenario" in err
    (root / "scenarios" / MINI_ID / "scenario.json").write_bytes(b"{oops")
    assert run(root, MINI_ID, shared_cache) == 2
    _, err = capsys.readouterr()
    assert "Traceback" not in err


def test_corrupt_entry_is_integrity_failure(tmp_path, shared_cache, capsys):
    root = stage(tmp_path / "ws")
    (root / "scenarios" / MINI_ID / "entry.json").write_bytes(b"{oops")
    assert run(root, MINI_ID, shared_cache) == 5
    _, err = capsys.readouterr()
    assert "Traceback" not in err


def _authored_entry(root: Path, requirements: dict) -> None:
    entry_file = root / "scenarios" / MINI_ID / "entry.json"
    entry = json.loads(entry_file.read_text(encoding="utf-8"))
    entry["origin"] = "authored"
    entry["requirements"] = requirements
    entry_file.write_text(json.dumps(entry), encoding="utf-8")


def test_authored_violations_abort_before_build(tmp_path, shared_cache, capsys):
    root = stage(tmp_path / "ws")

    def forbidden(validated, seed, cache_root):
        raise AssertionError("must not build")

    bad = normalize_request(
        scenario_id=MINI_ID,
        domain="other",
        size="small",
        composite_keys="forbidden",
        joins="inner",
    ).to_dict()
    _authored_entry(root, bad)
    code = run(root, MINI_ID, shared_cache, prepare=forbidden)
    _, err = capsys.readouterr()
    assert code == 4
    assert "domain-mismatch" in err
    entry = json.loads((root / "scenarios" / MINI_ID / "entry.json").read_text())
    assert entry["instance"] is None


def test_authored_matching_requirements_build(tmp_path, shared_cache, capsys):
    root = stage(tmp_path / "ws")
    features = extract_features(MINI_VALIDATED)
    good = normalize_request(
        scenario_id=MINI_ID,
        domain="testdomain",
        size="small",
        composite_keys="forbidden",
        staging=sorted(features.staging_ops),
        intermediate=sorted(features.intermediate_features),
        joins="inner" if features.join_types == {"inner"} else "auto",
        metrics=sorted(features.metric_functions),
    ).to_dict()
    _authored_entry(root, good)
    assert AuthoringRequest.from_dict(good).staging == features.staging_ops
    assert run(root, MINI_ID, shared_cache) == 0
    capsys.readouterr()


def test_preview_failure_stays_visible(tmp_path, shared_cache, monkeypatch, capsys):
    root = stage(tmp_path / "ws")

    def boom(db_path, model_name, grain):
        raise RuntimeError("db gone")

    monkeypatch.setattr(instances, "_preview_output", boom)
    code = run(root, MINI_ID, shared_cache)
    out, err = capsys.readouterr()
    assert code == 0
    assert "preview failed" in err
    assert "preview unavailable" in out
    assert "(no rows)" not in out


def test_credential_env_excluded_during_build(tmp_path, shared_cache, monkeypatch, capsys):
    monkeypatch.setenv("CRED_X", "secret-x")
    root = stage(tmp_path / "ws")
    (root / "config.json").write_text(
        json.dumps(
            {
                "profiles": {
                    "openrouter": {
                        "provider": "openrouter",
                        "model": "m",
                        "key_env": "CRED_X",
                        "max_output_tokens": 1,
                    }
                },
                "active_provider": "openrouter",
            }
        ),
        encoding="utf-8",
    )
    real_prepare = instances.prepare_clean_instance

    def spy(validated, seed, cache_root):
        assert "CRED_X" not in os.environ
        return real_prepare(validated, seed, cache_root)

    assert run(root, MINI_ID, shared_cache, prepare=spy) == 0
    capsys.readouterr()
    assert os.environ["CRED_X"] == "secret-x"


def test_clean_control_failure_reports_record(tmp_path, shared_cache, capsys):
    root = stage(tmp_path / "fws", FAILING_ID, FAILING_BYTES)
    code = run(root, FAILING_ID, shared_cache)
    _, err = capsys.readouterr()
    assert code == 5
    assert "failure_record" in err
    assert "Traceback" not in err
    entry = json.loads((root / "scenarios" / FAILING_ID / "entry.json").read_text())
    assert entry["instance"] is None
    assert not (root / "scenarios" / FAILING_ID / "work").exists()


def test_request_round_trip():
    request = normalize_request(
        scenario_id=MINI_ID,
        domain="testdomain",
        staging="trim,filter",
        intermediate="derive",
        joins="inner",
        metrics="sum",
        seed=3,
    )
    assert AuthoringRequest.from_dict(request.to_dict()) == request
    with pytest.raises(ValueError):
        AuthoringRequest.from_dict({"scenario_id": MINI_ID})
    with pytest.raises(ValueError):
        AuthoringRequest.from_dict({"scenario_id": MINI_ID, "domain": "x", "size": "huge"})


def test_dispatch_wires_open(tmp_path, monkeypatch):
    seen = {}

    def recorder(workspace, parsed):
        seen["id"] = parsed.scenario_id
        return 0

    monkeypatch.setattr(app, "run_open", recorder)
    code = app.main(["--workspace", str(tmp_path / "ws"), "open", MINI_ID])
    assert code == 0
    assert seen == {"id": MINI_ID}
