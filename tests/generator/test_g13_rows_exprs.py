"""G13 tests: expressions, conditions, staging row operations (§§14.3–14.5).

Runtime behavior of these snapshots is verified once against DuckDB in G17,
not here. The full-model snapshot renders the real
``education_cohorts_001.stg_enrollments`` (filter + deduplicate, including
the ``MATERIALIZED`` column-phase barrier).
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from data_pipeline_diagnostics.generator.dbt_render import render_staging_sql
from data_pipeline_diagnostics.generator.sql_render import (
    render_condition,
    render_expression,
)
from data_pipeline_diagnostics.scenario.expressions import (
    AnyCondition,
    BinaryExpression,
    BooleanCondition,
    CoalesceExpression,
    ColumnExpression,
    ComparisonCondition,
    DatePartExpression,
    InCondition,
    LiteralExpression,
    NotCondition,
    NullCondition,
)
from data_pipeline_diagnostics.scenario.parsing import parse_scenario_json
from data_pipeline_diagnostics.scenario.semantic import validate_semantics
from data_pipeline_diagnostics.scenario.staging import (
    DeduplicateRowsOperation,
    FilterRowsOperation,
    StagingColumn,
    StagingModel,
)
from data_pipeline_diagnostics.scenario.types import SortKey

REPO = Path(__file__).resolve().parents[2]


def _col(name="a"):
    return ColumnExpression(column=name)


def _lit(value):
    return LiteralExpression(value=value)


def test_expression_snapshots():
    assert render_expression(_col()) == '"a"'
    assert render_expression(_lit("x")) == "'x'"
    assert render_expression(_lit(3)) == "3"
    assert render_expression(_lit(0.5)) == "CAST(0.5 AS DOUBLE)"
    assert render_expression(_lit(False)) == "FALSE"
    assert (
        render_expression(BinaryExpression(operator="add", left=_col(), right=_lit(1)))
        == '("a" + 1)'
    )
    assert (
        render_expression(BinaryExpression(operator="subtract", left=_col(), right=_lit(1)))
        == '("a" - 1)'
    )
    assert (
        render_expression(BinaryExpression(operator="multiply", left=_col(), right=_lit(2)))
        == '("a" * 2)'
    )
    nested = BinaryExpression(
        operator="multiply",
        left=BinaryExpression(operator="add", left=_col(), right=_lit(1)),
        right=_col("c"),
    )
    assert render_expression(nested) == '(("a" + 1) * "c")'
    assert (
        render_expression(CoalesceExpression(values=(_col(), _lit("x")))) == "coalesce(\"a\", 'x')"
    )


def test_safe_division_snapshot():
    rendered = render_expression(
        BinaryExpression(operator="divide", left=_col("n"), right=_col("d"))
    )
    assert rendered == (
        '(CASE WHEN "d" IS NULL OR "d" = 0 THEN NULL '
        'ELSE CAST("n" AS DOUBLE) / CAST("d" AS DOUBLE) END)'
    )


def test_date_part_snapshots():
    assert render_expression(DatePartExpression(part="year", value=_col("d"))) == (
        'extract(year from "d")'
    )
    assert render_expression(DatePartExpression(part="quarter", value=_col("d"))) == (
        'extract(quarter from "d")'
    )
    assert render_expression(DatePartExpression(part="month", value=_col("d"))) == (
        'extract(month from "d")'
    )
    assert render_expression(DatePartExpression(part="day", value=_col("d"))) == (
        'extract(day from "d")'
    )
    assert render_expression(DatePartExpression(part="day_of_week", value=_col("d"))) == (
        'extract(isodow from "d")'
    )


def test_condition_snapshots():
    assert (
        render_condition(ComparisonCondition(operator="eq", left=_col(), right=_lit(1)))
        == '("a" = 1)'
    )
    assert (
        render_condition(ComparisonCondition(operator="ne", left=_col(), right=_lit(1)))
        == '("a" <> 1)'
    )
    assert (
        render_condition(ComparisonCondition(operator="lt", left=_col(), right=_lit(1)))
        == '("a" < 1)'
    )
    assert (
        render_condition(ComparisonCondition(operator="lte", left=_col(), right=_lit(1)))
        == '("a" <= 1)'
    )
    assert (
        render_condition(ComparisonCondition(operator="gt", left=_col(), right=_lit(1)))
        == '("a" > 1)'
    )
    assert (
        render_condition(ComparisonCondition(operator="gte", left=_col(), right=_lit(1)))
        == '("a" >= 1)'
    )
    assert (
        render_condition(InCondition(value=_col("b"), options=("x", "y")))
        == "(\"b\" IN ('x', 'y'))"
    )
    assert (
        render_condition(InCondition(value=_col("b"), options=("x",), negated=True))
        == "(\"b\" NOT IN ('x'))"
    )
    assert render_condition(NullCondition(value=_col())) == '("a" IS NULL)'
    assert render_condition(NullCondition(value=_col(), negated=True)) == ('("a" IS NOT NULL)')


def test_filter_parenthesization_snapshot():
    rendered = render_condition(
        BooleanCondition(
            conditions=(
                NotCondition(condition=NullCondition(value=_col())),
                InCondition(value=_col("b"), options=("x", "y")),
            )
        )
    )
    assert rendered == "((NOT (\"a\" IS NULL)) AND (\"b\" IN ('x', 'y')))"
    rendered_or = render_condition(
        AnyCondition(
            conditions=(
                ComparisonCondition(operator="gt", left=_col(), right=_lit(0)),
                NullCondition(value=_col("c")),
            )
        )
    )
    assert rendered_or == '(("a" > 0) OR ("c" IS NULL))'


def test_unknown_variants_raise():
    with pytest.raises(ValueError):
        render_expression(SimpleNamespace(kind="bogus"))
    with pytest.raises(ValueError):
        render_condition(SimpleNamespace(kind="bogus"))
    with pytest.raises(ValueError):
        render_expression(
            SimpleNamespace(kind="binary", operator="modulo", left=_col(), right=_lit(1))
        )


def _dedup_model():
    return StagingModel(
        name="stg_d",
        source="raw_t",
        columns=(
            StagingColumn(source="a", target="a"),
            StagingColumn(source="b", target="b"),
        ),
        row_operations=(
            DeduplicateRowsOperation(
                keys=("a",),
                order_by=(
                    SortKey(column="b", direction="asc"),
                    SortKey(column="a", direction="desc"),
                ),
            ),
        ),
        grain=("a",),
    )


def test_deduplication_snapshot():
    rendered = render_staging_sql(_dedup_model())
    assert '"b" ASC NULLS LAST' in rendered
    assert '"a" DESC NULLS LAST' in rendered
    order_clause = rendered.split("ORDER BY", 1)[1].split(")", 1)[0]
    assert order_clause.count("NULLS LAST") == 2
    assert "_dpd_row_number" not in rendered.rsplit("SELECT", 1)[1]
    assert 'WHERE "_dpd_row_number" = 1' in rendered


def test_full_staging_model_snapshot():
    data = json.loads((REPO / "scenarios" / "education_cohorts_001.json").read_text())
    validated = validate_semantics(parse_scenario_json(json.dumps(data)))
    model = next(m for m in validated.scenario.staging_models if str(m.name) == "stg_enrollments")
    assert (
        render_staging_sql(model)
        == """{{ config(materialization='table') }}

WITH "base" AS (
    SELECT * FROM {{ source('raw', 'raw_enrollments') }}
),
"columns" AS MATERIALIZED (SELECT
        "student_id" AS "student_id",
        "course_id" AS "course_id",
        "enrolled" AS "enrolled",
        "status" AS "status"
    FROM "base"),
"row_0" AS (
    SELECT * FROM "columns"
    WHERE ("status" <> 'dropped')
),
"row_1" AS (
    SELECT * FROM (SELECT "row_0".*, row_number() OVER (PARTITION BY "course_id", "student_id" ORDER BY "enrolled" DESC NULLS LAST) AS "_dpd_row_number" FROM "row_0")
    WHERE "_dpd_row_number" = 1
)
SELECT "student_id", "course_id", "enrolled", "status" FROM "row_1"
"""
    )


def test_unknown_row_operation_raises():
    model = _dedup_model().model_copy(update={"row_operations": (SimpleNamespace(op="bogus"),)})
    with pytest.raises(ValueError):
        render_staging_sql(model)


def test_filter_row_operation_snapshot():
    model = StagingModel(
        name="stg_f",
        source="raw_t",
        columns=(StagingColumn(source="a", target="a"),),
        row_operations=(
            FilterRowsOperation(
                condition=ComparisonCondition(operator="gt", left=_col(), right=_lit(0))
            ),
        ),
        grain=("a",),
    )
    rendered = render_staging_sql(model)
    assert 'WHERE ("a" > 0)' in rendered
    assert rendered.rsplit("SELECT", 1)[1].strip() == '"a" FROM "row_0"'
