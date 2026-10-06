"""G22 tests: correct dbt table materialization.

``materialization: table`` is not a valid dbt configuration key (the valid
key is ``materialized``), so every generated model silently built as a VIEW.
These tests pin the valid key in the project YAML and all five model header
emitters (staging, transform, join, deduplicate, aggregate — output shares
the aggregate renderer), guard pre-publication manifest validation in
``clean.py``, and verify the renderer version bump in instance records.
End-to-end DuckDB relation-type checks extend the existing G17 green
fixture; the prepare/cache identity check here reuses one tiny build.
"""

from __future__ import annotations

import json
from pathlib import Path

from data_pipeline_diagnostics.generator import render_dbt_project
from data_pipeline_diagnostics.generator.cache import prepare_clean_instance
from data_pipeline_diagnostics.generator.clean import _validate_build
from data_pipeline_diagnostics.generator.dbt_render import (
    model_output_columns,
    render_aggregate_sql,
    render_deduplicate_sql,
    render_join_sql,
    render_output_sql,
    render_staging_sql,
    render_transform_sql,
)
from data_pipeline_diagnostics.scenario.parsing import (
    parse_scenario_file,
    parse_scenario_json,
)
from data_pipeline_diagnostics.scenario.semantic import validate_semantics

REPO = Path(__file__).resolve().parents[2]
COMMAND = ("dbt", "build", "--profiles-dir", ".", "--target", "clean", "--threads", "1")


def _validated_file(name: str):
    return validate_semantics(parse_scenario_file(REPO / "scenarios" / name))


def _validated_json(text: str):
    return validate_semantics(parse_scenario_json(text))


def _write_build(tmp_path: Path, nodes: dict, results: list[dict]) -> Path:
    target = tmp_path / "dbt" / "target"
    target.mkdir(parents=True, exist_ok=True)
    (target / "manifest.json").write_text(
        json.dumps({"metadata": {"project_name": "dpd_pipeline"}, "nodes": nodes}),
        encoding="utf-8",
    )
    (target / "run_results.json").write_text(json.dumps({"results": results}), encoding="utf-8")
    return tmp_path / "dbt"


def _model_node(config: object) -> dict:
    node: dict[str, object] = {"resource_type": "model"}
    if config is not None:
        node["config"] = config
    return node


def _model_result(name: str, status: str = "success") -> dict:
    return {"unique_id": f"model.dpd_pipeline.{name}", "status": status}


def _passing_build(tmp_path: Path, mutate: dict[str, object] | None = None) -> Path:
    nodes = {
        "model.dpd_pipeline.m1": _model_node({"materialized": "table"}),
        "model.dpd_pipeline.m2": _model_node({"materialized": "table"}),
        "test.dpd_pipeline.t1": _model_node({"materialized": "view"}),
        "source.dpd_pipeline.raw.r1": {"resource_type": "source"},
    }
    if mutate:
        for key, config in mutate.items():
            nodes[key] = _model_node(config)
    results = [
        _model_result("m1"),
        _model_result("m2"),
        {"unique_id": "test.dpd_pipeline.t1", "status": "pass"},
    ]
    return _write_build(tmp_path, nodes, results)


def test_valid_materialization_passes(tmp_path):
    result = _validate_build(_passing_build(tmp_path), COMMAND, 0)
    assert result.success
    assert result.category is None


def test_view_materialization_rejected(tmp_path):
    project = _passing_build(tmp_path, {"model.dpd_pipeline.m2": {"materialized": "view"}})
    result = _validate_build(project, COMMAND, 0)
    assert not result.success
    assert result.category == "manifest-mismatch"


def test_missing_config_rejected(tmp_path):
    target = tmp_path / "dbt" / "target"
    target.mkdir(parents=True)
    (target / "manifest.json").write_text(
        json.dumps(
            {
                "metadata": {"project_name": "dpd_pipeline"},
                "nodes": {"model.dpd_pipeline.m1": {"resource_type": "model"}},
            }
        ),
        encoding="utf-8",
    )
    (target / "run_results.json").write_text(
        json.dumps({"results": [_model_result("m1")]}), encoding="utf-8"
    )
    result = _validate_build(tmp_path / "dbt", COMMAND, 0)
    assert not result.success
    assert result.category == "manifest-mismatch"


def test_missing_key_rejected(tmp_path):
    project = _passing_build(tmp_path, {"model.dpd_pipeline.m1": {"alias": "m1"}})
    result = _validate_build(project, COMMAND, 0)
    assert not result.success
    assert result.category == "manifest-mismatch"


def test_malformed_config_rejected_without_incidental_error(tmp_path):
    for bad in ("table", ["table"], None, 42):
        project = _passing_build(tmp_path, {"model.dpd_pipeline.m1": bad})
        result = _validate_build(project, COMMAND, 0)
        assert not result.success
        assert result.category == "manifest-mismatch"


def test_non_model_nodes_ignored(tmp_path):
    project = _passing_build(
        tmp_path,
        {
            "test.dpd_pipeline.t1": {"materialized": "ephemeral"},
            "macro.dpd_pipeline.mac": "not-a-dict",
        },
    )
    result = _validate_build(project, COMMAND, 0)
    assert result.success
    assert result.category is None


def test_failure_classification_priority_intact(tmp_path):
    project = _passing_build(tmp_path)
    results = [
        _model_result("m1"),
        _model_result("m2"),
        {"unique_id": "test.dpd_pipeline.t1", "status": "fail"},
    ]
    (project / "target" / "run_results.json").write_text(
        json.dumps({"results": results}), encoding="utf-8"
    )
    result = _validate_build(project, COMMAND, 1)
    assert not result.success
    assert result.category == "dbt-test-failure"
    results = [
        _model_result("m1"),
        _model_result("m2", "error"),
        {"unique_id": "test.dpd_pipeline.t1", "status": "pass"},
    ]
    (project / "target" / "run_results.json").write_text(
        json.dumps({"results": results}), encoding="utf-8"
    )
    result = _validate_build(project, COMMAND, 1)
    assert not result.success
    assert result.category == "dbt-model-error"


def test_rendered_project_and_models_use_valid_key(tmp_path):
    validated = _validated_file("agriculture_coop_001.json")
    project_dir = tmp_path / "dbt"
    render_dbt_project(validated, project_dir)
    project = (project_dir / "dbt_project.yml").read_text(encoding="utf-8")
    assert "+materialized: table" in project
    assert "materialization" not in project
    model_files = sorted(project_dir.glob("models/**/*.sql"))
    assert len(model_files) >= 5
    for path in model_files:
        sql = path.read_text(encoding="utf-8")
        assert sql.startswith("{{ config(materialized='table') }}\n")
        assert "materialization" not in sql


def _output_map(validated) -> dict[str, tuple[str, ...]]:
    by_name = {
        str(m.name): m
        for m in (
            *validated.scenario.staging_models,
            *validated.scenario.intermediate_models,
        )
    }
    memo: dict[str, tuple[str, ...]] = {}
    for model in (
        *validated.scenario.staging_models,
        *validated.scenario.intermediate_models,
    ):
        model_output_columns(model, by_name, memo)
    return memo


def _header(name: str, validated_name: str, kind: str) -> str:
    validated = _validated_file(validated_name)
    columns = _output_map(validated)
    if kind == "staging":
        model = next(m for m in validated.scenario.staging_models if str(m.name) == name)
        raw = next(t for t in validated.scenario.raw_tables if str(t.name) == str(model.source))
        return render_staging_sql(model, tuple(str(c.name) for c in raw.columns))
    if kind == "transform":
        model = next(m for m in validated.scenario.intermediate_models if str(m.name) == name)
        return render_transform_sql(model, columns[str(model.source)])
    if kind == "join":
        model = next(m for m in validated.scenario.intermediate_models if str(m.name) == name)
        return render_join_sql(model, columns[str(model.left)], columns[str(model.right)])
    if kind == "deduplicate":
        model = next(m for m in validated.scenario.intermediate_models if str(m.name) == name)
        return render_deduplicate_sql(model, columns[str(model.source)])
    if kind == "aggregate":
        model = next(m for m in validated.scenario.intermediate_models if str(m.name) == name)
        return render_aggregate_sql(model, columns[str(model.source)])
    if kind == "output":
        model = next(m for m in validated.scenario.output_models if str(m.name) == name)
        return render_output_sql(model, columns[str(model.source)])
    raise AssertionError(f"unknown kind {kind}")


def test_all_model_emitters_use_valid_key():
    cases = [
        ("stg_enrollments", "education_cohorts_001.json", "staging"),
        ("t_messungen", "energy_meters_002.json", "transform"),
        ("j_full", "education_cohorts_001.json", "join"),
        ("d_limits", "finance_limits_001.json", "deduplicate"),
        ("a_fahrzeug_stats", "transport_trips_002.json", "aggregate"),
        ("o_area_by_variety", "agriculture_coop_001.json", "output"),
    ]
    assert len(cases) == 6
    for name, scenario, kind in cases:
        rendered = _header(name, scenario, kind)
        assert rendered.startswith("{{ config(materialized='table') }}\n"), name
        assert "materialization" not in rendered, name


def test_prepare_records_renderer_version_and_reuses_cache(tmp_path):
    validated = validate_semantics(
        parse_scenario_file(REPO / "tests" / "scenario" / "fixtures" / "valid" / "minimal.json")
    )
    first = prepare_clean_instance(validated, 11, tmp_path / "cache")
    assert not first.cache_hit
    record = json.loads((first.instance_dir / "instance_record.json").read_text())
    assert record["versions"]["dbt_renderer"] == "1.0.1"
    assert record["identity"]["instance_digest"] == first.instance_digest
    second = prepare_clean_instance(validated, 11, tmp_path / "cache")
    assert second.cache_hit
    assert second.instance_digest == first.instance_digest
