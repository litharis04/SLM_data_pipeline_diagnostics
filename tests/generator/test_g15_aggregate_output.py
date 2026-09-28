"""G15 tests: aggregate intermediates and output models (§14.8).

Snapshots render real corpus models (aggregate with pre-filter plus
conditional metrics from ``transport_trips_002``, output from
``agriculture_coop_001``) and all nine metric functions directly. Runtime
behavior is verified once against DuckDB in G17, not here.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from data_pipeline_diagnostics.generator.dbt_render import (
    model_output_columns,
    render_aggregate_sql,
    render_metric,
    render_output_sql,
)
from data_pipeline_diagnostics.scenario.expressions import (
    ColumnExpression,
    ComparisonCondition,
    LiteralExpression,
)
from data_pipeline_diagnostics.scenario.output import (
    AverageMetric,
    ConditionalCountMetric,
    ConditionalSumMetric,
    CountDistinctMetric,
    CountMetric,
    CountRowsMetric,
    MaximumMetric,
    MinimumMetric,
    SumMetric,
)
from data_pipeline_diagnostics.scenario.parsing import parse_scenario_json
from data_pipeline_diagnostics.scenario.semantic import validate_semantics

REPO = Path(__file__).resolve().parents[2]


def _validated(name: str):
    data = json.loads((REPO / "scenarios" / name).read_text())
    return validate_semantics(parse_scenario_json(json.dumps(data)))


def _output_map(validated):
    by_name = {
        str(m.name): m
        for m in (*validated.scenario.staging_models, *validated.scenario.intermediate_models)
    }
    memo: dict[str, tuple[str, ...]] = {}
    for model in (*validated.scenario.staging_models, *validated.scenario.intermediate_models):
        model_output_columns(model, by_name, memo)
    return memo


def _cond():
    return ComparisonCondition(
        operator="gt", left=ColumnExpression(column="v"), right=LiteralExpression(value=0)
    )


def test_metric_snapshots():
    assert render_metric(CountRowsMetric(name="n")) == 'COUNT(*) AS "n"'
    assert render_metric(CountMetric(name="n", column="v")) == 'COUNT("v") AS "n"'
    assert render_metric(CountDistinctMetric(name="n", column="v")) == (
        'COUNT(DISTINCT "v") AS "n"'
    )
    assert render_metric(SumMetric(name="s", column="v")) == ('CAST(SUM("v") AS DOUBLE) AS "s"')
    assert render_metric(AverageMetric(name="a", column="v")) == ('CAST(AVG("v") AS DOUBLE) AS "a"')
    assert render_metric(MinimumMetric(name="m", column="v")) == 'MIN("v") AS "m"'
    assert render_metric(MaximumMetric(name="m", column="v")) == 'MAX("v") AS "m"'
    assert render_metric(ConditionalCountMetric(name="c", condition=_cond())) == (
        'COUNT(*) FILTER (WHERE ("v" > 0)) AS "c"'
    )
    assert render_metric(ConditionalSumMetric(name="s", column="v", condition=_cond())) == (
        'CAST(SUM("v") FILTER (WHERE ("v" > 0)) AS DOUBLE) AS "s"'
    )
    with pytest.raises(ValueError):
        render_metric(SimpleNamespace(name="x", function="bogus"))


def test_aggregate_snapshot_with_filter_and_conditionals():
    validated = _validated("transport_trips_002.json")
    columns = _output_map(validated)
    model = next(
        m for m in validated.scenario.intermediate_models if str(m.name) == "a_fahrzeug_stats"
    )
    assert (
        render_aggregate_sql(model, columns[str(model.source)])
        == """{{ config(materialization='table') }}

WITH "source" AS (
    SELECT "fahrt_id", "status", "betrag", "fahrzeug_id", "depot_id" FROM {{ ref('j_fahrt_fahrzeug') }}
),
"filtered" AS (
    SELECT "fahrt_id", "status", "betrag", "fahrzeug_id", "depot_id" FROM "source"
    WHERE ("betrag" > CAST(0.0 AS DOUBLE))
)
SELECT
        "depot_id" AS "depot_id",
        "fahrzeug_id" AS "fahrzeug_id",
        COUNT(*) FILTER (WHERE ("status" = 'bezahlt')) AS "n_bezahlt",
        CAST(SUM("betrag") FILTER (WHERE ("status" = 'bezahlt')) AS DOUBLE) AS "umsatz",
        COUNT(*) AS "n"
    FROM "filtered"
    GROUP BY "depot_id", "fahrzeug_id"
"""
    )


def test_output_snapshot_group_targets_first():
    validated = _validated("agriculture_coop_001.json")
    columns = _output_map(validated)
    model = validated.scenario.output_models[0]
    rendered = render_output_sql(model, columns[str(model.source)])
    assert (
        rendered
        == """{{ config(materialization='table') }}

WITH "source" AS (
    SELECT "planting_id", "area_ha", "variety_name" FROM {{ ref('j_full') }}
)
SELECT
        "variety_name" AS "variety_name",
        CAST(SUM("area_ha") AS DOUBLE) AS "total_area",
        COUNT(*) AS "n_plantings"
    FROM "source"
    GROUP BY "variety_name"
"""
    )
    final_select = rendered.rsplit("SELECT", 1)[1]
    assert final_select.index('"variety_name" AS "variety_name"') < final_select.index(
        'CAST(SUM("area_ha")'
    )


def test_grain_only_difference_keeps_sql():
    validated = _validated("agriculture_coop_001.json")
    columns = _output_map(validated)
    model = validated.scenario.output_models[0]
    narrowed = model.model_copy(update={"grain": ("stadt_narrow",)})
    assert render_output_sql(narrowed, columns[str(model.source)]) == render_output_sql(
        model, columns[str(model.source)]
    )


def test_no_out_of_scope_sql():
    validated = _validated("transport_trips_002.json")
    columns = _output_map(validated)
    rendered = [
        render_aggregate_sql(m, columns[str(m.source)])
        for m in validated.scenario.intermediate_models
        if m.operation == "aggregate"
    ]
    rendered += [
        render_output_sql(m, columns[str(m.source)]) for m in validated.scenario.output_models
    ]
    for sql in rendered:
        assert "OVER" not in sql
        assert "PERCENT" not in sql.upper()
        assert "RATIO" not in sql.upper()
