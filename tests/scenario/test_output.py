"""Contract tests for OutputModel and MetricSpec (SCENARIO_SPEC §13–§14)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from data_pipeline_diagnostics.scenario.base import ContractModel
from data_pipeline_diagnostics.scenario.output import (
    AverageMetric,
    ConditionalCountMetric,
    ConditionalSumMetric,
    CountDistinctMetric,
    CountMetric,
    CountRowsMetric,
    MaximumMetric,
    MetricSpec,
    MinimumMetric,
    OutputModel,
    SumMetric,
)


class WrapMetric(ContractModel):
    m: MetricSpec


class WrapOutput(ContractModel):
    o: OutputModel


# ---------------------------------------------------------------------------
# Positive – one per function
# ---------------------------------------------------------------------------


def test_count_rows():
    m = CountRowsMetric(name="cnt")
    assert m.function == "count_rows"
    assert WrapMetric(m=m).m.function == "count_rows"


def test_count():
    m = CountMetric(name="cnt", column="id")
    assert m.column == "id"
    assert WrapMetric(m={"name": "c", "function": "count", "column": "id"}).m.function == "count"


def test_count_distinct():
    assert CountDistinctMetric(name="cd", column="id").function == "count_distinct"
    assert (
        WrapMetric(m={"name": "cd", "function": "count_distinct", "column": "id"}).m.function
        == "count_distinct"
    )


def test_sum():
    assert SumMetric(name="s", column="amount").function == "sum"


def test_avg():
    assert AverageMetric(name="a", column="amount").function == "avg"


def test_min():
    assert MinimumMetric(name="mn", column="ts").function == "min"


def test_max():
    assert MaximumMetric(name="mx", column="ts").function == "max"


def test_conditional_count():
    cond = {
        "kind": "comparison",
        "operator": "gt",
        "left": {"kind": "column", "column": "a"},
        "right": {"kind": "literal", "value": 1},
    }
    m = ConditionalCountMetric(name="cc", condition=cond)
    assert m.function == "conditional_count"
    assert (
        WrapMetric(m={"name": "cc", "function": "conditional_count", "condition": cond}).m.function
        == "conditional_count"
    )


def test_conditional_sum():
    cond = {
        "kind": "comparison",
        "operator": "gt",
        "left": {"kind": "column", "column": "a"},
        "right": {"kind": "literal", "value": 1},
    }
    m = ConditionalSumMetric(name="cs", column="amount", condition=cond)
    assert m.function == "conditional_sum"
    assert (
        WrapMetric(
            m={"name": "cs", "function": "conditional_sum", "column": "amount", "condition": cond}
        ).m.function
        == "conditional_sum"
    )


def test_metric_with_description():
    m = CountMetric(name="c", column="id", description="count")
    assert m.description == "count"


def test_output_minimal():
    o = OutputModel(
        name="out_a",
        source="agg_a",
        group_by=({"source": "cat", "target": "cat"},),
        grain=("cat",),
        metrics=({"name": "cnt", "function": "count_rows"},),
    )
    assert o.name == "out_a"
    assert WrapOutput(o=o).o.name == "out_a"


def test_output_with_dimensions_and_filters():
    o = OutputModel(
        name="out_b",
        source="agg_a",
        filters=(
            {
                "kind": "comparison",
                "operator": "gt",
                "left": {"kind": "column", "column": "cat"},
                "right": {"kind": "literal", "value": "a"},
            },
        ),
        group_by=({"source": "cat", "target": "cat"},),
        grain=("cat",),
        dimensions=("cat",),
        metrics=({"name": "total", "function": "sum", "column": "amount"},),
        description="test output",
    )
    assert o.dimensions == ("cat",)
    assert o.description == "test output"


def test_output_with_conditional_metrics():
    o = OutputModel(
        name="out_c",
        source="agg_a",
        group_by=({"source": "cat", "target": "cat"},),
        grain=("cat",),
        metrics=(
            {
                "name": "cc",
                "function": "conditional_count",
                "condition": {
                    "kind": "comparison",
                    "operator": "eq",
                    "left": {"kind": "column", "column": "a"},
                    "right": {"kind": "literal", "value": 1},
                },
            },
            {
                "name": "cs",
                "function": "conditional_sum",
                "column": "amount",
                "condition": {"kind": "is_null", "value": {"kind": "column", "column": "a"}},
            },
        ),
    )
    assert len(o.metrics) == 2


# ---------------------------------------------------------------------------
# Discriminated union via Wrap
# ---------------------------------------------------------------------------


def test_metric_union_parsing():
    assert WrapMetric(m={"name": "c", "function": "count_rows"}).m.function == "count_rows"
    assert WrapMetric(m={"name": "c", "function": "count", "column": "id"}).m.function == "count"
    assert WrapMetric(m={"name": "c", "function": "sum", "column": "a"}).m.function == "sum"


# ---------------------------------------------------------------------------
# Negative – unknown function, missing discriminator, duplicates
# ---------------------------------------------------------------------------


def test_unknown_function_rejected():
    with pytest.raises(ValidationError, match="union_tag_invalid"):
        WrapMetric.model_validate({"m": {"name": "c", "function": "unknown"}})
    with pytest.raises(ValidationError, match="union_tag_invalid"):
        WrapMetric.model_validate({"m": {"name": "c", "function": "ratio", "column": "a"}})


def test_missing_discriminator_rejected():
    with pytest.raises(ValidationError, match="union_tag_not_found"):
        WrapMetric.model_validate({"m": {"name": "c", "column": "a"}})
    with pytest.raises(ValidationError, match="union_tag_not_found"):
        WrapMetric.model_validate({"m": {"name": "c"}})


def test_duplicate_metric_names_rejected():
    with pytest.raises(ValidationError, match="metric names must be unique"):
        OutputModel(
            name="out",
            source="a",
            group_by=({"source": "cat", "target": "cat"},),
            grain=("cat",),
            metrics=(
                {"name": "cnt", "function": "count_rows"},
                {"name": "cnt", "function": "count", "column": "id"},
            ),
        )


def test_group_by_duplicate_target_rejected():
    with pytest.raises(ValidationError, match="group_by targets must be unique"):
        OutputModel(
            name="out",
            source="a",
            group_by=({"source": "a", "target": "x"}, {"source": "b", "target": "x"}),
            grain=("x",),
            metrics=({"name": "cnt", "function": "count_rows"},),
        )


def test_group_by_duplicate_source_rejected():
    with pytest.raises(ValidationError, match="group_by sources must be unique"):
        OutputModel(
            name="out",
            source="a",
            group_by=({"source": "a", "target": "x"}, {"source": "a", "target": "y"}),
            grain=("x",),
            metrics=({"name": "cnt", "function": "count_rows"},),
        )


def test_grain_duplicate_rejected():
    with pytest.raises(ValidationError, match="grain must not contain duplicates"):
        OutputModel(
            name="out",
            source="a",
            group_by=({"source": "cat", "target": "cat"},),
            grain=("cat", "cat"),
            metrics=({"name": "cnt", "function": "count_rows"},),
        )


def test_output_name_collision_rejected():
    with pytest.raises(ValidationError, match="must not collide"):
        OutputModel(
            name="out",
            source="a",
            group_by=({"source": "cat", "target": "cat"},),
            grain=("cat",),
            metrics=({"name": "cat", "function": "count_rows"},),
        )


def test_extra_field_rejected():
    with pytest.raises(ValidationError, match="extra_forbidden"):
        CountMetric.model_validate({"name": "c", "function": "count", "column": "id", "extra": "x"})
    with pytest.raises(ValidationError, match="extra_forbidden"):
        OutputModel.model_validate(
            {
                "name": "out",
                "source": "a",
                "group_by": ({"source": "cat", "target": "cat"},),
                "grain": ("cat",),
                "metrics": ({"name": "cnt", "function": "count_rows"},),
                "extra": "x",
            }
        )


def test_empty_group_by_rejected():
    with pytest.raises(ValidationError):
        OutputModel(
            name="out",
            source="a",
            group_by=(),
            grain=("cat",),
            metrics=({"name": "cnt", "function": "count_rows"},),
        )


def test_empty_metrics_rejected():
    with pytest.raises(ValidationError):
        OutputModel(
            name="out",
            source="a",
            group_by=({"source": "cat", "target": "cat"},),
            grain=("cat",),
            metrics=(),
        )


def test_coercion_rejected():
    with pytest.raises(ValidationError):
        CountMetric.model_validate({"name": 123, "function": "count", "column": "id"})
    with pytest.raises(ValidationError):
        OutputModel.model_validate(
            {
                "name": 123,
                "source": "a",
                "group_by": ({"source": "cat", "target": "cat"},),
                "grain": ("cat",),
                "metrics": ({"name": "cnt", "function": "count_rows"},),
            }
        )


# ---------------------------------------------------------------------------
# Boundary – non-existent source must parse
# ---------------------------------------------------------------------------


def test_nonexistent_source_parses():
    o = OutputModel(
        name="out",
        source="ghost_intermediate",
        group_by=({"source": "cat", "target": "cat"},),
        grain=("cat",),
        metrics=({"name": "cnt", "function": "count_rows"},),
    )
    assert o.source == "ghost_intermediate"
    assert (
        WrapOutput(
            o={
                "name": "out",
                "source": "ghost",
                "group_by": ({"source": "cat", "target": "cat"},),
                "grain": ("cat",),
                "metrics": ({"name": "cnt", "function": "count_rows"},),
            }
        ).o.source
        == "ghost"
    )
