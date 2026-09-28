"""G18 tests: instance reproducibility passport (§18).

Exact field sets follow GENERATOR_SPEC §18.2; the three strict sections
(``raw_tables[]``, ``dbt``, ``artifacts[]``) carry no extras. One module-
scoped green build feeds all tests; digest stability rewrites the record
in place twice.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from data_pipeline_diagnostics.generator.clean import build_clean_instance
from data_pipeline_diagnostics.generator.records import (
    identity_digest,
    identity_object,
    write_instance_record,
)
from data_pipeline_diagnostics.scenario.parsing import parse_scenario_file
from data_pipeline_diagnostics.scenario.semantic import validate_semantics

REPO = Path(__file__).resolve().parents[2]
MINIMAL = REPO / "tests" / "scenario" / "fixtures" / "valid" / "minimal.json"

DBT_COMMAND = ["dbt", "build", "--profiles-dir", ".", "--target", "clean", "--threads", "1"]


def _validated():
    return validate_semantics(parse_scenario_file(MINIMAL))


@pytest.fixture(scope="module")
def instance_dir(tmp_path_factory):
    instance = tmp_path_factory.mktemp("record") / "instance"
    result = build_clean_instance(validated=_validated(), data_seed=11, instance_dir=instance)
    assert result.success
    return instance


@pytest.fixture(scope="module")
def record(instance_dir):
    return write_instance_record(
        instance_dir=instance_dir,
        validated=_validated(),
        data_seed=11,
        dbt_command=DBT_COMMAND,
        dbt_exit_status=0,
    )


def test_schema_exactness(record):
    assert set(record) == {
        "record_version",
        "status",
        "identity",
        "versions",
        "randomness",
        "raw_tables",
        "dbt",
        "artifacts",
        "crosswalks",
    }
    assert record["record_version"] == "1.0"
    assert record["status"] == "success"
    assert set(record["identity"]) == {
        "instance_digest",
        "scenario_id",
        "scenario_schema_version",
        "scenario_sha256",
        "data_seed",
    }
    assert set(record["versions"]) == {
        "generator_contract",
        "raw_generator",
        "dbt_renderer",
        "python",
        "python_full",
        "duckdb",
        "dbt_core",
        "dbt_duckdb",
        "faker",
        "faker_locale",
    }
    assert record["versions"]["faker_locale"] == "de_DE"
    assert set(record["randomness"]) == {"stream_scheme", "streams"}
    assert record["randomness"]["stream_scheme"] == "dpd-rng-v1"
    assert set(record["dbt"]) == {
        "command",
        "exit_status",
        "project_dir",
        "log_path",
        "manifest_path",
        "run_results_path",
    }
    for entry in record["raw_tables"]:
        assert set(entry) == {
            "name",
            "row_count",
            "duckdb_relation",
            "parquet_path",
            "columns",
            "sha256",
        }
        for column in entry["columns"]:
            assert set(column) == {"name", "duckdb_type"}
    for entry in record["artifacts"]:
        assert set(entry) == {"path", "kind", "size_bytes", "sha256"}
        assert entry["kind"] in {
            "scenario",
            "parquet",
            "duckdb",
            "dbt_project",
            "dbt_target",
            "log",
            "record",
            "marker",
        }


def test_python_full_excluded_from_digest(record):
    validated = _validated()
    identity = identity_object(validated, 11)
    assert "python_full" not in identity
    assert record["identity"]["instance_digest"] == identity_digest(validated, 11)
    assert record["versions"]["python_full"].startswith(record["versions"]["python"] + ".")
    assert record["versions"]["python"] != record["versions"]["python_full"]


def test_digest_stable_across_rewrites(instance_dir, record):
    second = write_instance_record(
        instance_dir=instance_dir,
        validated=_validated(),
        data_seed=11,
        dbt_command=DBT_COMMAND,
        dbt_exit_status=0,
    )
    assert second["identity"]["instance_digest"] == record["identity"]["instance_digest"]
    assert second == record


def test_negative_no_rows_secrets_or_fault(instance_dir, record):
    text = json.dumps(record)
    assert "fault" not in text.lower()
    for secret in ("password", "passwd", "token", "secret"):
        assert secret not in text.lower()
    payload = json.loads((instance_dir / "instance_record.json").read_text())
    assert payload == record
    _scan_forbidden(record)


def _scan_forbidden(value, *, _path="record"):
    if isinstance(value, dict):
        for key, item in value.items():
            assert "fault" not in str(key).lower(), _path
            _scan_forbidden(item, _path=f"{_path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _scan_forbidden(item, _path=f"{_path}[{index}]")


def test_success_written_after_record_with_fake_publisher(instance_dir, record):
    operations: list[str] = []

    class FakePublisher:
        def publish(self, directory: Path) -> None:
            write_instance_record(
                instance_dir=directory,
                validated=_validated(),
                data_seed=11,
                dbt_command=DBT_COMMAND,
                dbt_exit_status=0,
            )
            operations.append("record")
            (directory / "SUCCESS").write_text("success\n", encoding="utf-8")
            operations.append("success")

    import shutil

    probe = instance_dir.parent / "probe"
    shutil.copytree(instance_dir, probe, ignore=shutil.ignore_patterns("instance_record.json"))
    FakePublisher().publish(probe)
    assert operations == ["record", "success"]
    assert (probe / "SUCCESS").stat().st_mtime_ns >= (
        probe / "instance_record.json"
    ).stat().st_mtime_ns
