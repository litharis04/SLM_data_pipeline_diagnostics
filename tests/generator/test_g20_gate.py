"""G20 Part A: minimum conformance gate (§21.0). Cheap enough for CI.

Covers every closed-union variant positively (generator kinds, staging
column/row operations, expressions, conditions, intermediate operations,
metrics, assertion types), one deliberately failing clean control per
custom generic-test family, fixed-seed repeatability, named-stream
isolation, a controlled stochastic fixture, and one end-to-end
``prepare_clean_instance`` with a verified cache hit on repeat. Heavy
behavioral coverage stays in G03–G19; this gate pins breadth. dbt builds:
four tiny failing controls plus one green prepare (~20s total).
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from data_pipeline_diagnostics.generator.cache import prepare_clean_instance
from data_pipeline_diagnostics.generator.clean import (
    build_clean_instance,
    validate_failure_record,
)
from data_pipeline_diagnostics.generator.dbt_render import (
    LogicalAssertion,
    render_aggregate_sql,
    render_deduplicate_sql,
    render_intermediate_sql,
    render_join_sql,
    render_metric,
    render_staging_sql,
    render_transform_sql,
)
from data_pipeline_diagnostics.generator.raw_values import generate_scalar
from data_pipeline_diagnostics.generator.rng import stream as make_stream
from data_pipeline_diagnostics.generator.sql_render import (
    render_condition,
    render_expression,
    render_staging_column,
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
from data_pipeline_diagnostics.scenario.generators import (
    BooleanGenerator,
    CategoricalGenerator,
    CityGenerator,
    CompanyNameGenerator,
    DateRangeGenerator,
    EmailGenerator,
    FloatRangeGenerator,
    ForeignKeyGenerator,
    FormattedIdGenerator,
    IntegerRangeGenerator,
    PersonNameGenerator,
    PhoneNumberGenerator,
    RandomStringGenerator,
    StreetAddressGenerator,
    TemplateStringGenerator,
    TimestampRangeGenerator,
)
from data_pipeline_diagnostics.scenario.intermediate import (
    AggregateIntermediateModel,
    DeduplicateIntermediateModel,
    JoinIntermediateModel,
    JoinKeyPair,
    JoinSpec,
    TransformIntermediateModel,
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
from data_pipeline_diagnostics.scenario.parsing import parse_scenario_file
from data_pipeline_diagnostics.scenario.semantic import validate_semantics
from data_pipeline_diagnostics.scenario.staging import (
    CastOperation,
    CoalesceOperation,
    DeduplicateRowsOperation,
    FilterRowsOperation,
    LowerOperation,
    MapValuesOperation,
    NullIfOperation,
    ReplaceOperation,
    StagingColumn,
    StagingModel,
    TrimOperation,
    UpperOperation,
)
from data_pipeline_diagnostics.scenario.types import SortKey

REPO = Path(__file__).resolve().parents[2]
MINIMAL = REPO / "tests" / "scenario" / "fixtures" / "valid" / "minimal.json"
FAILING = {
    "composite_unique": "failing_composite_unique.json",
    "composite_relationships": "failing_composite_relationships.json",
    "row_count_between": "failing_row_count.json",
    "column_range": "failing_column_range.json",
}


def _validated_minimal():
    from data_pipeline_diagnostics.scenario.parsing import parse_scenario_json

    return validate_semantics(parse_scenario_json(MINIMAL.read_bytes()))


def _stream(name: str, seed: int = 11):
    return make_stream("g20-gate", seed, f"values/t/{name}")


# ---------------------------------------------------------------------------
# Closed-union positives
# ---------------------------------------------------------------------------


def test_all_scalar_generator_kinds_execute():
    configs = [
        FormattedIdGenerator(prefix="U-", digits=3, start=7),
        IntegerRangeGenerator(min=1, max=5),
        FloatRangeGenerator(min=1.0, max=2.0, decimal_places=1),
        DateRangeGenerator(min=date(2024, 1, 1), max=date(2024, 1, 3)),
        TimestampRangeGenerator(
            min=datetime(2024, 1, 1, tzinfo=UTC),
            max=datetime(2024, 1, 2, tzinfo=UTC),
        ),
        CategoricalGenerator(values=("a", "b")),
        BooleanGenerator(true_probability=0.5),
        RandomStringGenerator(min_length=2, max_length=4, alphabet="ab"),
        TemplateStringGenerator(template="id-{x}"),
        PersonNameGenerator(),
        EmailGenerator(),
        CityGenerator(),
        StreetAddressGenerator(),
        CompanyNameGenerator(),
        PhoneNumberGenerator(),
    ]
    assert len(configs) == 15
    for cfg in configs:
        placeholders = {"x": 1} if cfg.kind == "template_string" else None
        value = generate_scalar(cfg, _stream(cfg.kind), 0, placeholders=placeholders)
        assert value is not None
    with pytest.raises(ValueError):
        generate_scalar(ForeignKeyGenerator(relationship="r", target_side="left"), _stream("fk"), 0)


def test_all_staging_column_operations_render():
    from data_pipeline_diagnostics.scenario.types import DataType as DT

    ops = [
        TrimOperation(),
        LowerOperation(),
        UpperOperation(),
        ReplaceOperation(old="x", new="y"),
        MapValuesOperation(mapping={"a": "A"}, on_unmapped="error"),
        NullIfOperation(values=("n",)),
        CoalesceOperation(value="d"),
        CastOperation(type=DT.integer),
    ]
    assert len(ops) == 8
    for op in ops:
        rendered = render_staging_column(
            '"c"',
            StagingColumn(source="c", target="c", operations=(op,)),
            model="stg_gate",
        )
        assert rendered.endswith('AS "c"')
        assert "TRY_CAST" not in rendered


def test_both_row_operations_render():
    model = StagingModel(
        name="stg_gate",
        source="raw_t",
        columns=(StagingColumn(source="a", target="a"),),
        row_operations=(
            FilterRowsOperation(
                condition=ComparisonCondition(
                    operator="gt",
                    left=ColumnExpression(column="a"),
                    right=LiteralExpression(value=0),
                )
            ),
            DeduplicateRowsOperation(keys=("a",), order_by=(SortKey(column="a", direction="asc"),)),
        ),
        grain=("a",),
    )
    rendered = render_staging_sql(model, ("a",))
    assert "WHERE" in rendered and "row_number()" in rendered


def test_all_expression_and_condition_variants_render():
    col = ColumnExpression(column="a")
    expressions = [
        col,
        LiteralExpression(value=1),
        BinaryExpression(operator="add", left=col, right=LiteralExpression(value=1)),
        BinaryExpression(operator="subtract", left=col, right=LiteralExpression(value=1)),
        BinaryExpression(operator="multiply", left=col, right=LiteralExpression(value=1)),
        BinaryExpression(operator="divide", left=col, right=LiteralExpression(value=1)),
        DatePartExpression(part="year", value=col),
        DatePartExpression(part="quarter", value=col),
        DatePartExpression(part="month", value=col),
        DatePartExpression(part="day", value=col),
        DatePartExpression(part="day_of_week", value=col),
        CoalesceExpression(values=(col, LiteralExpression(value=0))),
    ]
    assert len(expressions) == 12
    for expr in expressions:
        assert render_expression(expr)
    conditions = [
        ComparisonCondition(operator="eq", left=col, right=LiteralExpression(value=1)),
        ComparisonCondition(operator="ne", left=col, right=LiteralExpression(value=1)),
        ComparisonCondition(operator="lt", left=col, right=LiteralExpression(value=1)),
        ComparisonCondition(operator="lte", left=col, right=LiteralExpression(value=1)),
        ComparisonCondition(operator="gt", left=col, right=LiteralExpression(value=1)),
        ComparisonCondition(operator="gte", left=col, right=LiteralExpression(value=1)),
        InCondition(value=col, options=("x",)),
        InCondition(value=col, options=("x",), negated=True),
        NullCondition(value=col),
        NullCondition(value=col, negated=True),
        BooleanCondition(
            conditions=(
                ComparisonCondition(operator="gt", left=col, right=LiteralExpression(value=0)),
                NullCondition(value=col),
            )
        ),
        AnyCondition(
            conditions=(
                ComparisonCondition(operator="gt", left=col, right=LiteralExpression(value=0)),
                NullCondition(value=col),
            )
        ),
        NotCondition(condition=NullCondition(value=col)),
    ]
    assert len(conditions) == 13
    for cond in conditions:
        assert render_condition(cond).startswith("(")


def _projection():
    from data_pipeline_diagnostics.scenario.intermediate import ProjectionColumn

    return (ProjectionColumn(source="a", target="a"),)


def test_all_intermediate_operations_render():
    from data_pipeline_diagnostics.scenario.intermediate import JoinProjectionColumn

    transform = TransformIntermediateModel(
        name="t_gate",
        source="stg_x",
        columns=_projection(),
        derived_columns=(),
        filters=(),
        grain=("a",),
    )
    assert "ref('stg_x')" in render_transform_sql(transform, ("a",))
    join = JoinIntermediateModel(
        name="j_gate",
        left="stg_x",
        right="stg_y",
        join=JoinSpec(type="inner", on=(JoinKeyPair(left="a", right="a"),)),
        columns=(JoinProjectionColumn(side="left", source="a", target="a"),),
        derived_columns=(),
        filters=(),
        grain=("a",),
    )
    assert "INNER JOIN" in render_join_sql(join, ("a",), ("a",))
    dedup = DeduplicateIntermediateModel(
        name="d_gate",
        source="stg_x",
        keys=("a",),
        order_by=(SortKey(column="a", direction="asc"),),
        grain=("a",),
    )
    assert "row_number()" in render_deduplicate_sql(dedup, ("a",))
    aggregate = AggregateIntermediateModel(
        name="a_gate",
        source="stg_x",
        filters=(),
        group_by=_projection(),
        metrics=({"function": "count_rows", "name": "n"},),
        grain=("a",),
    )
    assert "GROUP BY" in render_aggregate_sql(aggregate, ("a",))
    outputs = {
        "stg_x": ("a",),
        "stg_y": ("a",),
        "t_gate": ("a",),
        "j_gate": ("a",),
        "d_gate": ("a",),
        "a_gate": ("a", "n"),
    }
    for model in (transform, join, dedup, aggregate):
        assert render_intermediate_sql(model, outputs)


def test_all_metric_functions_render():
    cond = ComparisonCondition(
        operator="gt",
        left=ColumnExpression(column="v"),
        right=LiteralExpression(value=0),
    )
    metrics = [
        CountRowsMetric(name="m"),
        CountMetric(name="m", column="v"),
        CountDistinctMetric(name="m", column="v"),
        SumMetric(name="m", column="v"),
        AverageMetric(name="m", column="v"),
        MinimumMetric(name="m", column="v"),
        MaximumMetric(name="m", column="v"),
        ConditionalCountMetric(name="m", condition=cond),
        ConditionalSumMetric(name="m", column="v", condition=cond),
    ]
    assert len(metrics) == 9
    for metric in metrics:
        assert render_metric(metric).endswith('AS "m"')


def test_all_assertion_types_lower():
    from data_pipeline_diagnostics.generator.dbt_render import render_assertions_yml

    assertions = [
        LogicalAssertion(name="a", origin="explicit", type="not_null", model="m", columns=("c",)),
        LogicalAssertion(name="b", origin="explicit", type="unique", model="m", columns=("c",)),
        LogicalAssertion(
            name="c",
            origin="explicit",
            type="accepted_values",
            model="m",
            column="c",
            values=("x",),
        ),
        LogicalAssertion(
            name="d",
            origin="explicit",
            type="relationships",
            model="m",
            columns=("c",),
            to_model="n",
            to_columns=("c",),
        ),
        LogicalAssertion(name="e", origin="explicit", type="row_count", model="m", min=1),
        LogicalAssertion(
            name="f", origin="explicit", type="column_range", model="m", column="c", min=0, max=5
        ),
    ]
    assert len(assertions) == 6
    text = render_assertions_yml(
        assertions, raw_tables=["n"], staging=["m"], intermediates=[], outputs=[]
    )
    for token in (
        "not_null:",
        "unique:",
        "accepted_values:",
        "relationships:",
        "row_count_between:",
        "column_range:",
    ):
        assert token in text


# ---------------------------------------------------------------------------
# Failing controls, determinism, end-to-end
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def failed_builds(tmp_path_factory):
    builds = {}
    for family, filename in FAILING.items():
        path = REPO / "tests" / "generator" / "fixtures" / filename
        validated = validate_semantics(parse_scenario_file(path))
        instance = tmp_path_factory.mktemp(family) / "instance"
        result = build_clean_instance(validated=validated, data_seed=11, instance_dir=instance)
        builds[family] = (instance, result)
    return builds


@pytest.mark.parametrize("family", sorted(FAILING))
def test_failing_control_per_custom_family(failed_builds, family):
    instance, result = failed_builds[family]
    assert not result.success
    assert not (instance / "SUCCESS").exists()
    record = json.loads((instance / "failure_record.json").read_text())
    assert validate_failure_record(record) == []
    assert record["stage"] == "dbt_build"
    assert record["category"] == "dbt-test-failure"


def test_repeatability_and_isolation_and_stochastic_seed():
    from data_pipeline_diagnostics.generator.raw_plan import generate_raw_data

    validated = _validated_minimal()
    assert generate_raw_data(validated, 11) == generate_raw_data(validated, 11)
    assert generate_raw_data(validated, 11) != generate_raw_data(validated, 12)
    first = make_stream("g20-gate", 11, "values/t/a").random()
    make_stream("g20-gate", 11, "values/t/b").random()
    assert make_stream("g20-gate", 11, "values/t/a").random() == first


def test_end_to_end_prepare_with_cache_hit(tmp_path):
    validated = _validated_minimal()
    first = prepare_clean_instance(validated, 11, tmp_path / "cache")
    assert not first.cache_hit
    assert (first.instance_dir / "SUCCESS").is_file()
    record = json.loads((first.instance_dir / "instance_record.json").read_text())
    assert record["status"] == "success"
    second = prepare_clean_instance(validated, 11, tmp_path / "cache")
    assert second.cache_hit
    assert second.instance_digest == first.instance_digest
