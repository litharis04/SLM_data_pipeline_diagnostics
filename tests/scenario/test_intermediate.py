"""Contract tests for intermediate layer (SCENARIO_SPEC §12)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from data_pipeline_diagnostics.scenario.base import ContractModel
from data_pipeline_diagnostics.scenario.intermediate import (
    AggregateIntermediateModel,
    DeduplicateIntermediateModel,
    IntermediateModel,
    JoinIntermediateModel,
    JoinProjectionColumn,
    JoinSpec,
    ProjectionColumn,
    TransformIntermediateModel,
)


class Wrap(ContractModel):
    m: IntermediateModel


# ---------------------------------------------------------------------------
# Positive – one per operation
# ---------------------------------------------------------------------------


def test_transform_minimal():
    m = TransformIntermediateModel(
        name="trans_a",
        source="stg_a",
        columns=(ProjectionColumn(source="col_a", target="col_a"),),
        grain=("col_a",),
    )
    assert m.operation == "transform"
    assert Wrap(m=m).m.operation == "transform"


def test_transform_with_derived_and_filters():
    m = TransformIntermediateModel(
        name="trans_b",
        source="stg_a",
        columns=(ProjectionColumn(source="a", target="a"),),
        derived_columns=(
            {
                "name": "b",
                "type": "integer",
                "expression": {"kind": "column", "column": "a"},
            },
        ),
        filters=(
            {
                "kind": "comparison",
                "operator": "gt",
                "left": {"kind": "column", "column": "a"},
                "right": {"kind": "literal", "value": 1},
            },
        ),
        grain=("a",),
    )
    assert m.derived_columns[0].name == "b"
    assert m.filters[0].kind == "comparison"


def test_join_minimal():
    m = JoinIntermediateModel(
        name="join_a",
        left="stg_a",
        right="stg_b",
        join={"type": "inner", "on": ({"left": "id", "right": "a_id"},)},
        columns=(JoinProjectionColumn(side="left", source="col_a", target="col_a"),),
        grain=("col_a",),
    )
    assert m.operation == "join"
    assert m.join.type == "inner"


def test_join_composite_keys():
    m = JoinIntermediateModel(
        name="join_comp",
        left="j1",
        right="j2",
        join={
            "type": "left",
            "on": ({"left": "id", "right": "a_id"}, {"left": "seq", "right": "a_seq"}),
        },
        columns=(
            JoinProjectionColumn(side="left", source="a", target="a"),
            JoinProjectionColumn(side="right", source="b", target="b"),
        ),
        grain=("a",),
    )
    assert len(m.join.on) == 2


def test_join_with_derived():
    m = JoinIntermediateModel(
        name="join_derived",
        left="s1",
        right="s2",
        join={"type": "inner", "on": ({"left": "id", "right": "id"},)},
        columns=(JoinProjectionColumn(side="left", source="a", target="a"),),
        derived_columns=(
            {
                "name": "calc",
                "type": "integer",
                "expression": {
                    "kind": "binary",
                    "operator": "add",
                    "left": {"kind": "column", "column": "a"},
                    "right": {"kind": "literal", "value": 1},
                },
            },
        ),
        grain=("a",),
    )
    assert m.derived_columns[0].name == "calc"


def test_aggregate_minimal():
    m = AggregateIntermediateModel(
        name="agg_a",
        source="stg_a",
        group_by=(ProjectionColumn(source="cat", target="cat"),),
        metrics=({"name": "cnt", "function": "count_rows"},),
        grain=("cat",),
    )
    assert m.operation == "aggregate"
    assert m.metrics[0].name == "cnt"


def test_aggregate_with_metrics_and_filters():
    m = AggregateIntermediateModel(
        name="agg_b",
        source="trans_a",
        filters=(
            {
                "kind": "comparison",
                "operator": "gt",
                "left": {"kind": "column", "column": "a"},
                "right": {"kind": "literal", "value": 10},
            },
        ),
        group_by=(ProjectionColumn(source="cat", target="cat"),),
        metrics=(
            {"name": "total", "function": "sum", "column": "amount"},
            {"name": "cnt", "function": "count", "column": "id"},
        ),
        grain=("cat",),
    )
    assert len(m.metrics) == 2


def test_deduplicate_minimal():
    m = DeduplicateIntermediateModel(
        name="dedup_a",
        source="stg_a",
        keys=("id",),
        order_by=({"column": "ts", "direction": "desc"},),
        grain=("id",),
    )
    assert m.operation == "deduplicate"
    assert m.keys == ("id",)
    assert m.grain == ("id",)


def test_deduplicate_composite_keys():
    m = DeduplicateIntermediateModel(
        name="dedup_comp",
        source="trans_a",
        keys=("id", "seq"),
        order_by=({"column": "ts"}, {"column": "id", "direction": "asc"}),
        grain=("seq", "id"),
    )
    # order of grain vs keys as set must be equal – different order is ok
    assert set(m.grain) == set(m.keys)


def test_join_spec_direct():
    js = JoinSpec(type="inner", on=({"left": "id", "right": "a_id"},))
    assert js.type == "inner"
    js2 = JoinSpec(
        type="left", on=({"left": "id", "right": "a_id"}, {"left": "seq", "right": "a_seq"})
    )
    assert len(js2.on) == 2


# ---------------------------------------------------------------------------
# Discriminated union via Wrap
# ---------------------------------------------------------------------------


def test_union_parsing():
    assert (
        Wrap(
            m={
                "operation": "transform",
                "name": "t1",
                "source": "s1",
                "columns": ({"source": "a", "target": "a"},),
                "grain": ("a",),
            }
        ).m.operation
        == "transform"
    )
    assert (
        Wrap(
            m={
                "operation": "join",
                "name": "j1",
                "left": "s1",
                "right": "s2",
                "join": {"type": "inner", "on": ({"left": "id", "right": "id"},)},
                "columns": ({"side": "left", "source": "a", "target": "a"},),
                "grain": ("a",),
            }
        ).m.operation
        == "join"
    )
    assert (
        Wrap(
            m={
                "operation": "aggregate",
                "name": "a1",
                "source": "s1",
                "group_by": ({"source": "cat", "target": "cat"},),
                "metrics": ({"name": "cnt", "function": "count_rows"},),
                "grain": ("cat",),
            }
        ).m.operation
        == "aggregate"
    )
    assert (
        Wrap(
            m={
                "operation": "deduplicate",
                "name": "d1",
                "source": "s1",
                "keys": ("id",),
                "order_by": ({"column": "ts"},),
                "grain": ("id",),
            }
        ).m.operation
        == "deduplicate"
    )


# ---------------------------------------------------------------------------
# Negative – unknown operation, empty, duplicates, extra
# ---------------------------------------------------------------------------


def test_unknown_operation_rejected():
    with pytest.raises(ValidationError, match="union_tag_invalid"):
        Wrap.model_validate(
            {
                "m": {
                    "operation": "unknown",
                    "name": "x",
                    "source": "s",
                    "columns": ({"source": "a", "target": "a"},),
                    "grain": ("a",),
                }
            }
        )
    with pytest.raises(ValidationError, match="union_tag_invalid"):
        Wrap.model_validate(
            {
                "m": {
                    "operation": "union",
                    "name": "x",
                    "source": "s",
                    "keys": ("id",),
                    "order_by": ({"column": "ts"},),
                    "grain": ("id",),
                }
            }
        )


def test_missing_discriminator_rejected():
    with pytest.raises(ValidationError, match="union_tag_not_found"):
        Wrap.model_validate(
            {
                "m": {
                    "name": "x",
                    "source": "s",
                    "columns": ({"source": "a", "target": "a"},),
                    "grain": ("a",),
                }
            }
        )


def test_duplicate_output_names_transform():
    with pytest.raises(ValidationError, match="output names must be unique"):
        TransformIntermediateModel(
            name="t",
            source="s",
            columns=(ProjectionColumn(source="a", target="x"),),
            derived_columns=(
                {"name": "x", "type": "integer", "expression": {"kind": "column", "column": "a"}},
            ),
            grain=("x",),
        )
    with pytest.raises(ValidationError, match="output names must be unique"):
        TransformIntermediateModel(
            name="t",
            source="s",
            columns=(
                ProjectionColumn(source="a", target="x"),
                ProjectionColumn(source="b", target="x"),
            ),
            grain=("x",),
        )


def test_duplicate_output_names_join():
    with pytest.raises(ValidationError, match="output names must be unique"):
        JoinIntermediateModel(
            name="j",
            left="s1",
            right="s2",
            join={"type": "inner", "on": ({"left": "id", "right": "id"},)},
            columns=(
                JoinProjectionColumn(side="left", source="a", target="x"),
                JoinProjectionColumn(side="right", source="b", target="x"),
            ),
            grain=("x",),
        )


def test_join_duplicate_key_pairs():
    with pytest.raises(ValidationError, match="join key pairs must be unique"):
        JoinSpec(
            type="inner", on=({"left": "id", "right": "a_id"}, {"left": "id", "right": "a_id"})
        )


def test_empty_on_rejected():
    with pytest.raises(ValidationError):
        JoinSpec(type="inner", on=())


def test_empty_columns_rejected():
    with pytest.raises(ValidationError):
        TransformIntermediateModel(name="t", source="s", columns=(), grain=("a",))
    with pytest.raises(ValidationError):
        JoinIntermediateModel(
            name="j",
            left="s1",
            right="s2",
            join={"type": "inner", "on": ({"left": "id", "right": "id"},)},
            columns=(),
            grain=("a",),
        )
    with pytest.raises(ValidationError):
        AggregateIntermediateModel(
            name="a",
            source="s",
            group_by=(),
            metrics=({"name": "cnt", "function": "count_rows"},),
            grain=("a",),
        )
    with pytest.raises(ValidationError):
        AggregateIntermediateModel(
            name="a",
            source="s",
            group_by=(ProjectionColumn(source="cat", target="cat"),),
            metrics=(),
            grain=("a",),
        )


def test_empty_grain_rejected():
    with pytest.raises(ValidationError):
        TransformIntermediateModel(
            name="t", source="s", columns=(ProjectionColumn(source="a", target="a"),), grain=()
        )
    with pytest.raises(ValidationError):
        DeduplicateIntermediateModel(
            name="d", source="s", keys=("id",), order_by=({"column": "ts"},), grain=()
        )


def test_duplicate_keys_rejected():
    with pytest.raises(ValidationError, match="entries must be unique"):
        DeduplicateIntermediateModel(
            name="d",
            source="s",
            keys=("id", "id"),
            order_by=({"column": "ts"},),
            grain=("id", "id"),
        )
    with pytest.raises(ValidationError):
        TransformIntermediateModel(
            name="t",
            source="s",
            columns=(ProjectionColumn(source="a", target="a"),),
            grain=("a", "a"),
        )


def test_deduplicate_grain_must_equal_keys():
    with pytest.raises(ValidationError, match="grain must equal keys as a set"):
        DeduplicateIntermediateModel(
            name="d", source="s", keys=("id",), order_by=({"column": "ts"},), grain=("other",)
        )
    with pytest.raises(ValidationError, match="grain must equal keys as a set"):
        DeduplicateIntermediateModel(
            name="d", source="s", keys=("id", "seq"), order_by=({"column": "ts"},), grain=("id",)
        )


def test_aggregate_duplicate_group_by_targets():
    with pytest.raises(ValidationError, match="group_by targets must be unique"):
        AggregateIntermediateModel(
            name="a",
            source="s",
            group_by=(
                ProjectionColumn(source="a", target="x"),
                ProjectionColumn(source="b", target="x"),
            ),
            metrics=({"name": "cnt", "function": "count_rows"},),
            grain=("x",),
        )


def test_aggregate_duplicate_metric_names():
    with pytest.raises(ValidationError, match="metric names must be unique"):
        AggregateIntermediateModel(
            name="a",
            source="s",
            group_by=(ProjectionColumn(source="cat", target="cat"),),
            metrics=(
                {"name": "cnt", "function": "count_rows"},
                {"name": "cnt", "function": "count", "column": "id"},
            ),
            grain=("cat",),
        )


def test_aggregate_metric_collides_with_group_by():
    with pytest.raises(ValidationError, match="must not collide"):
        AggregateIntermediateModel(
            name="a",
            source="s",
            group_by=(ProjectionColumn(source="cat", target="cat"),),
            metrics=({"name": "cat", "function": "count_rows"},),
            grain=("cat",),
        )


def test_extra_field_rejected():
    with pytest.raises(ValidationError, match="extra_forbidden"):
        TransformIntermediateModel.model_validate(
            {
                "operation": "transform",
                "name": "t",
                "source": "s",
                "columns": ({"source": "a", "target": "a"},),
                "grain": ("a",),
                "extra": "x",
            }
        )
    with pytest.raises(ValidationError, match="extra_forbidden"):
        JoinSpec.model_validate(
            {"type": "inner", "on": ({"left": "id", "right": "id"},), "extra": "x"}
        )
    with pytest.raises(ValidationError, match="extra_forbidden"):
        ProjectionColumn.model_validate({"source": "a", "target": "a", "extra": "x"})


def test_join_type_out_of_scope():
    with pytest.raises(ValidationError):
        JoinSpec.model_validate({"type": "right", "on": ({"left": "id", "right": "id"},)})
    with pytest.raises(ValidationError):
        JoinSpec.model_validate({"type": "full", "on": ({"left": "id", "right": "id"},)})


def test_coercion_rejected():
    with pytest.raises(ValidationError):
        TransformIntermediateModel.model_validate(
            {
                "operation": "transform",
                "name": 123,
                "source": "s",
                "columns": ({"source": "a", "target": "a"},),
                "grain": ("a",),
            }
        )
    with pytest.raises(ValidationError):
        ProjectionColumn.model_validate({"source": 123, "target": "a"})
