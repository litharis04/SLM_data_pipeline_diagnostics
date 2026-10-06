"""G14 tests: transform/join/deduplicate intermediates (§§14.6–14.7, 14.9).

Snapshots render real corpus models (transform+derived+filter from
``energy_meters_002``, composite-key inner join from
``education_cohorts_001``, LEFT join from ``education_admissions_001``,
dedup from ``finance_limits_001``). Runtime behavior is verified once
against DuckDB in G17, not here.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from data_pipeline_diagnostics.generator.dbt_render import (
    model_output_columns,
    render_deduplicate_sql,
    render_intermediate_sql,
    render_join_sql,
    render_transform_sql,
)
from data_pipeline_diagnostics.scenario.expressions import ColumnExpression
from data_pipeline_diagnostics.scenario.intermediate import (
    DerivedColumn,
    ProjectionColumn,
    TransformIntermediateModel,
)
from data_pipeline_diagnostics.scenario.parsing import parse_scenario_json
from data_pipeline_diagnostics.scenario.semantic import validate_semantics
from data_pipeline_diagnostics.scenario.types import DataType

REPO = Path(__file__).resolve().parents[2]


def _validated(name: str):
    data = json.loads((REPO / "scenarios" / name).read_text())
    return validate_semantics(parse_scenario_json(json.dumps(data)))


def _intermediate(validated, name: str):
    return next(m for m in validated.scenario.intermediate_models if str(m.name) == name)


def _output_map(validated):
    by_name = {
        str(m.name): m
        for m in (*validated.scenario.staging_models, *validated.scenario.intermediate_models)
    }
    memo: dict[str, tuple[str, ...]] = {}
    for model in (*validated.scenario.staging_models, *validated.scenario.intermediate_models):
        model_output_columns(model, by_name, memo)
    return memo


def test_transform_with_derived_and_filter_snapshot():
    validated = _validated("energy_meters_002.json")
    model = _intermediate(validated, "t_messungen")
    columns = _output_map(validated)
    assert (
        render_transform_sql(model, columns["stg_messungen"])
        == """{{ config(materialized='table') }}

WITH "source" AS (
    SELECT "mess_id", "zaehler_id", "beginnt", "endet", "kwh" FROM {{ ref('stg_messungen') }}
),
"projected" AS MATERIALIZED (SELECT
        "mess_id" AS "mess_id",
        "zaehler_id" AS "zaehler_id",
        "beginnt" AS "beginnt",
        "endet" AS "endet",
        "kwh" AS "kwh"
    FROM "source"),
"derived" AS MATERIALIZED (SELECT "mess_id", "zaehler_id", "beginnt", "endet", "kwh",
        extract(day from "beginnt") AS "tag"
    FROM "projected"),
"filtered" AS (
    SELECT "mess_id", "zaehler_id", "beginnt", "endet", "kwh", "tag" FROM "derived"
    WHERE ("endet" > "beginnt")
)
SELECT "mess_id", "zaehler_id", "beginnt", "endet", "kwh", "tag" FROM "filtered"
"""
    )


def test_inner_join_composite_keys_exact():
    validated = _validated("education_cohorts_001.json")
    columns = _output_map(validated)
    model = _intermediate(validated, "j_full")
    rendered = render_join_sql(model, columns[str(model.left)], columns[str(model.right)])
    assert (
        'ON "left"."student_id" = "right"."student_id" AND "left"."course_id" = "right"."course_id"'
    ) in rendered
    assert rendered.count(" = ") == 2
    assert "INNER JOIN" in rendered
    assert "WHERE" not in rendered
    assert '"left"."student_id" AS "student_id"' in rendered
    assert '"right"."gpa" AS "gpa"' in rendered


def test_left_join_keyword():
    validated = _validated("education_admissions_001.json")
    columns = _output_map(validated)
    model = _intermediate(validated, "j_apps_reviewers")
    rendered = render_join_sql(model, columns[str(model.left)], columns[str(model.right)])
    assert "LEFT JOIN" in rendered
    assert "INNER JOIN" not in rendered
    assert "SELECT *" not in rendered and ".*" not in rendered


def test_deduplicate_model_snapshot():
    validated = _validated("finance_limits_001.json")
    full_by_name = {
        str(m.name): m
        for m in (*validated.scenario.staging_models, *validated.scenario.intermediate_models)
    }
    source_columns = model_output_columns(full_by_name["stg_limits"], full_by_name, {})
    assert source_columns == (
        "limit_id",
        "customer_id",
        "active_flag",
        "max_amount",
        "used",
        "reviewed",
    )
    assert (
        render_deduplicate_sql(_intermediate(validated, "d_limits"), source_columns)
        == """{{ config(materialized='table') }}

WITH "source" AS (
    SELECT "limit_id", "customer_id", "active_flag", "max_amount", "used", "reviewed" FROM {{ ref('stg_limits') }}
),
"ranked" AS MATERIALIZED (SELECT "limit_id", "customer_id", "active_flag", "max_amount", "used", "reviewed", row_number() OVER (PARTITION BY "limit_id" ORDER BY "reviewed" DESC NULLS LAST) AS "_dpd_row_number" FROM "source")
SELECT "limit_id", "customer_id", "active_flag", "max_amount", "used", "reviewed" FROM "ranked" WHERE "_dpd_row_number" = 1
"""
    )


def test_namespace_derived_sees_projected_only():
    model = TransformIntermediateModel(
        name="t_probe",
        source="stg_x",
        columns=(ProjectionColumn(source="a", target="a"),),
        derived_columns=(
            DerivedColumn(name="d", type=DataType.string, expression=ColumnExpression(column="a")),
        ),
        filters=(),
        grain=("a",),
    )
    rendered = render_transform_sql(model, ("a",))
    derived_block = rendered.split('"derived" AS MATERIALIZED (', 1)[1].split("FROM", 1)[0]
    assert '"a"' in derived_block
    assert '"secret"' not in rendered
    bad = model.model_copy(
        update={
            "derived_columns": (
                DerivedColumn(
                    name="d", type=DataType.string, expression=ColumnExpression(column="ghost")
                ),
            )
        }
    )
    with pytest.raises(ValueError):
        render_transform_sql(bad, ("a",))


def test_no_wildcard_natural_or_cross():
    validated = _validated("education_cohorts_001.json")
    columns = _output_map(validated)
    rendered = [render_intermediate_sql(m, columns) for m in validated.scenario.intermediate_models]
    for sql in rendered:
        assert "SELECT *" not in sql
        assert ".*" not in sql
        assert "NATURAL" not in sql
        assert "CROSS" not in sql


def test_unknown_operation_dispatch():
    with pytest.raises(ValueError):
        render_intermediate_sql(SimpleNamespace(operation="bogus", name="b"), {})


def test_model_output_columns_orders():
    validated = _validated("agriculture_coop_001.json")
    by_name = {
        str(m.name): m
        for m in (*validated.scenario.staging_models, *validated.scenario.intermediate_models)
    }
    assert model_output_columns(by_name["stg_seeds"], by_name, {}) == (
        "seed_id",
        "variety_code",
        "origin",
        "supplier",
    )
    assert model_output_columns(by_name["j_full"], by_name, {}) == (
        "planting_id",
        "area_ha",
        "variety_name",
    )
