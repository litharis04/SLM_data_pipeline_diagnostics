"""G17 tests: clean control execution and failure semantics (§§16–17).

The green fixture is ``tests/scenario/fixtures/valid/minimal.json`` ( §21.0
e2e core): tiny exact row counts for a fast hermetic ``dbt build``. The
failing fixture (``tests/generator/fixtures/failing_row_count.json``)
validates cleanly but violates a vendored ``row_count_between`` test at
build time. dbt builds are module-scoped (three invocations total).
"""

from __future__ import annotations

import json
from pathlib import Path

import duckdb
import pytest

from data_pipeline_diagnostics.generator.clean import (
    build_clean_instance,
    validate_failure_record,
)
from data_pipeline_diagnostics.scenario.parsing import (
    canonical_json,
    parse_scenario_file,
)
from data_pipeline_diagnostics.scenario.semantic import validate_semantics

REPO = Path(__file__).resolve().parents[2]
MINIMAL = REPO / "tests" / "scenario" / "fixtures" / "valid" / "minimal.json"
FAILING = REPO / "tests" / "generator" / "fixtures" / "failing_row_count.json"


def _validated(path: Path):
    return validate_semantics(parse_scenario_file(path))


@pytest.fixture(scope="module")
def green_dir(tmp_path_factory):
    instance = tmp_path_factory.mktemp("green") / "instance"
    result = build_clean_instance(
        validated=_validated(MINIMAL), data_seed=11, instance_dir=instance
    )
    assert result.success
    return instance


@pytest.fixture(scope="module")
def green_dir_again(tmp_path_factory):
    instance = tmp_path_factory.mktemp("green2") / "instance"
    result = build_clean_instance(
        validated=_validated(MINIMAL), data_seed=11, instance_dir=instance
    )
    assert result.success
    return instance


@pytest.fixture(scope="module")
def fail_dir(tmp_path_factory):
    instance = tmp_path_factory.mktemp("fail") / "instance"
    result = build_clean_instance(
        validated=_validated(FAILING), data_seed=11, instance_dir=instance
    )
    assert not result.success
    return instance


def test_green_builds_end_to_end(green_dir):
    assert (green_dir / "SUCCESS").is_file()
    assert not (green_dir / "failure_record.json").exists()
    assert (green_dir / "scenario.json").read_bytes() == canonical_json(
        _validated(MINIMAL).scenario
    )
    assert (green_dir / "pipeline.duckdb").is_file()
    manifest = json.loads((green_dir / "dbt" / "target" / "manifest.json").read_text())
    assert manifest["metadata"]["project_name"] == "dpd_pipeline"
    run_results = json.loads((green_dir / "dbt" / "target" / "run_results.json").read_text())
    assert run_results["results"]
    assert {r["status"] for r in run_results["results"]} <= {"success", "pass"}
    assert any(u.startswith("model.dpd_pipeline.out_") for u in manifest["nodes"])


def test_failing_build_records_no_success(fail_dir):
    assert not (fail_dir / "SUCCESS").exists()
    record = json.loads((fail_dir / "failure_record.json").read_text())
    assert validate_failure_record(record) == []
    assert set(record) == {
        "identity",
        "stage",
        "category",
        "message",
        "command",
        "exit_status",
        "paths",
    }
    assert set(record["identity"]) == {
        "scenario_sha256",
        "scenario_id",
        "data_seed",
        "generator_contract",
        "raw_generator",
        "dbt_renderer",
    }
    assert set(record["paths"]) == {"log", "manifest", "run_results"}
    assert record["stage"] == "dbt_build"
    assert record["command"][:3] == ["dbt", "build", "--profiles-dir"]
    assert record["exit_status"] == 1
    assert "fault" not in json.dumps(record)


def _logical_results(path: Path) -> dict:
    run_results = json.loads((path / "dbt" / "target" / "run_results.json").read_text())
    assert any(r.get("timing") for r in run_results["results"])
    return {r["unique_id"]: r["status"] for r in run_results["results"]}


def _raw_rows(instance: Path) -> dict:
    db = duckdb.connect(str(instance / "pipeline.duckdb"), read_only=True)
    try:
        db.execute("SET TimeZone = 'UTC'")
        tables = [
            row[0]
            for row in db.execute(
                "SELECT table_name FROM duckdb_tables() WHERE schema_name = 'raw' ORDER BY table_name"
            ).fetchall()
        ]
        return {
            table: sorted(db.execute(f'SELECT * FROM "raw"."{table}"').fetchall(), key=repr)
            for table in tables
        }
    finally:
        db.close()


def test_rebuild_compares_logical_artifacts_only(green_dir, green_dir_again):
    assert _logical_results(green_dir) == _logical_results(green_dir_again)
    assert _raw_rows(green_dir) == _raw_rows(green_dir_again)
    for relative in (
        "dbt/dbt_project.yml",
        "dbt/profiles.yml",
        "dbt/models/sources.yml",
        "dbt/models/assertions.yml",
        "dbt/macros/generated_assertions.sql",
    ):
        assert (green_dir / relative).read_bytes() == (green_dir_again / relative).read_bytes()
