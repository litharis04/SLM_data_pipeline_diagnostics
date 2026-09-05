"""Contract tests for StagingModel and operations (SCENARIO_SPEC §11)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from data_pipeline_diagnostics.scenario.base import ContractModel
from data_pipeline_diagnostics.scenario.staging import (
    StagingColumn,
    StagingColumnOperation,
    StagingModel,
    StagingRowOperation,
)


class WrapColOp(ContractModel):
    op: StagingColumnOperation


class WrapRowOp(ContractModel):
    op: StagingRowOperation


# ---------------------------------------------------------------------------
# Positive – minimal and chains
# ---------------------------------------------------------------------------


def test_staging_minimal_one_column_no_ops():
    m = StagingModel(
        name="stg_a",
        source="raw_a",
        columns=(StagingColumn(source="col_a", target="col_a"),),
        grain=("col_a",),
    )
    assert m.name == "stg_a"
    assert m.columns[0].operations == ()


def test_staging_chain_trim_lower_cast():
    m = StagingModel(
        name="stg_a",
        source="raw_a",
        columns=(
            StagingColumn(
                source="col_a",
                target="col_a",
                operations=(
                    {"op": "trim"},
                    {"op": "lower"},
                    {"op": "cast", "type": "string"},
                ),
            ),
        ),
        grain=("col_a",),
    )
    assert len(m.columns[0].operations) == 3
    assert m.columns[0].operations[2].op == "cast"


def test_staging_cast_to_date_with_format():
    m = StagingModel(
        name="stg_a",
        source="raw_a",
        columns=(
            StagingColumn(
                source="col_a",
                target="col_a",
                operations=({"op": "cast", "type": "date", "format": "%Y-%m-%d"},),
            ),
        ),
        grain=("col_a",),
    )
    assert m.columns[0].operations[0].type == "date"


def test_staging_filter_with_condition():
    m = StagingModel(
        name="stg_a",
        source="raw_a",
        columns=(StagingColumn(source="col_a", target="col_a"),),
        row_operations=(
            {
                "op": "filter",
                "condition": {
                    "kind": "comparison",
                    "operator": "gt",
                    "left": {"kind": "column", "column": "col_a"},
                    "right": {"kind": "literal", "value": 1},
                },
            },
        ),
        grain=("col_a",),
    )
    assert m.row_operations[0].op == "filter"


def test_staging_deduplicate_with_sortkey():
    m = StagingModel(
        name="stg_a",
        source="raw_a",
        columns=(StagingColumn(source="col_a", target="col_a"),),
        row_operations=(
            {
                "op": "deduplicate",
                "keys": ("col_a",),
                "order_by": ({"column": "col_a", "direction": "desc"},),
            },
        ),
        grain=("col_a",),
    )
    assert m.row_operations[0].op == "deduplicate"


def test_staging_replace():
    m = StagingModel(
        name="stg_a",
        source="raw_a",
        columns=(
            StagingColumn(
                source="col_a",
                target="col_a",
                operations=({"op": "replace", "old": "a", "new": "b"},),
            ),
        ),
        grain=("col_a",),
    )
    assert m.columns[0].operations[0].old == "a"


def test_staging_map_values():
    m = StagingModel(
        name="stg_a",
        source="raw_a",
        columns=(
            StagingColumn(
                source="col_a",
                target="col_a",
                operations=({"op": "map_values", "mapping": {"a": "b"}, "on_unmapped": "keep"},),
            ),
        ),
        grain=("col_a",),
    )
    assert m.columns[0].operations[0].mapping == {"a": "b"}


def test_staging_null_if_and_coalesce():
    m = StagingModel(
        name="stg_a",
        source="raw_a",
        columns=(
            StagingColumn(
                source="col_a",
                target="col_a",
                operations=(
                    {"op": "null_if", "values": ("a",)},
                    {"op": "coalesce", "value": "b"},
                ),
            ),
        ),
        grain=("col_a",),
    )
    assert m.columns[0].operations[0].op == "null_if"
    assert m.columns[0].operations[1].op == "coalesce"


def test_staging_column_operations_union():
    assert WrapColOp(op={"op": "trim"}).op.op == "trim"
    assert WrapColOp(op={"op": "lower"}).op.op == "lower"
    assert WrapColOp(op={"op": "upper"}).op.op == "upper"
    assert WrapColOp(op={"op": "replace", "old": "a", "new": "b"}).op.op == "replace"
    assert WrapColOp(op={"op": "map_values", "mapping": {"a": "b"}}).op.op == "map_values"
    assert WrapColOp(op={"op": "null_if", "values": ("a",)}).op.op == "null_if"
    assert WrapColOp(op={"op": "coalesce", "value": "a"}).op.op == "coalesce"
    assert WrapColOp(op={"op": "cast", "type": "string"}).op.op == "cast"


def test_staging_row_operations_union():
    assert (
        WrapRowOp(
            op={
                "op": "filter",
                "condition": {
                    "kind": "comparison",
                    "operator": "eq",
                    "left": {"kind": "column", "column": "a"},
                    "right": {"kind": "literal", "value": 1},
                },
            }
        ).op.op
        == "filter"
    )
    assert (
        WrapRowOp(op={"op": "deduplicate", "keys": ("a",), "order_by": ({"column": "a"},)}).op.op
        == "deduplicate"
    )


# ---------------------------------------------------------------------------
# Negative – local invariants
# ---------------------------------------------------------------------------


def test_duplicate_source_rejected():
    with pytest.raises(ValidationError, match="source values must be unique"):
        StagingModel(
            name="stg_a",
            source="raw_a",
            columns=(
                StagingColumn(source="col_a", target="x"),
                StagingColumn(source="col_a", target="y"),
            ),
            grain=("x",),
        )


def test_duplicate_target_rejected():
    with pytest.raises(ValidationError, match="target values must be unique"):
        StagingModel(
            name="stg_a",
            source="raw_a",
            columns=(
                StagingColumn(source="col_a", target="x"),
                StagingColumn(source="col_b", target="x"),
            ),
            grain=("x",),
        )


def test_duplicate_grain_rejected():
    with pytest.raises(ValidationError, match="grain must not contain duplicates"):
        StagingModel(
            name="stg_a",
            source="raw_a",
            columns=(StagingColumn(source="col_a", target="col_a"),),
            grain=("col_a", "col_a"),
        )


def test_replace_empty_old_rejected():
    with pytest.raises(ValidationError):
        WrapColOp(op={"op": "replace", "old": "", "new": "b"})


def test_map_values_empty_rejected():
    with pytest.raises(ValidationError):
        WrapColOp(op={"op": "map_values", "mapping": {}})


def test_deduplicate_without_keys_rejected():
    with pytest.raises(ValidationError):
        WrapRowOp(op={"op": "deduplicate", "keys": (), "order_by": ({"column": "a"},)})
    with pytest.raises(ValidationError):
        WrapRowOp(op={"op": "deduplicate", "keys": ("a",), "order_by": ()})


def test_unknown_op_rejected():
    with pytest.raises(ValidationError, match="union_tag_invalid"):
        WrapColOp(op={"op": "unknown"})
    with pytest.raises(ValidationError, match="union_tag_invalid"):
        WrapRowOp(op={"op": "unknown", "keys": ("a",), "order_by": ({"column": "a"},)})


def test_extra_field_rejected():
    with pytest.raises(ValidationError, match="extra_forbidden"):
        StagingColumn.model_validate({"source": "a", "target": "a", "extra": "x"})
    with pytest.raises(ValidationError, match="extra_forbidden"):
        StagingModel.model_validate(
            {
                "name": "stg_a",
                "source": "raw_a",
                "columns": ({"source": "a", "target": "a"},),
                "grain": ("a",),
                "extra": "x",
            }
        )


def test_cast_with_forbidden_format():
    with pytest.raises(ValidationError, match="format is allowed only for date"):
        WrapColOp(op={"op": "cast", "type": "string", "format": "%Y-%m-%d"})
    with pytest.raises(ValidationError, match="format is allowed only for date"):
        WrapColOp(op={"op": "cast", "type": "integer", "format": "%Y-%m-%d"})


def test_cast_format_empty_rejected():
    with pytest.raises(ValidationError, match="must not be empty"):
        WrapColOp(op={"op": "cast", "type": "date", "format": ""})


def test_empty_columns_rejected():
    with pytest.raises(ValidationError):
        StagingModel(name="stg_a", source="raw_a", columns=(), grain=("a",))


def test_coercion_rejected():
    with pytest.raises(ValidationError):
        StagingColumn.model_validate({"source": 123, "target": "a"})
    with pytest.raises(ValidationError):
        StagingModel.model_validate(
            {
                "name": 123,
                "source": "raw_a",
                "columns": ({"source": "a", "target": "a"},),
                "grain": ("a",),
            }
        )


# ---------------------------------------------------------------------------
# Boundary – non-existent references must parse (semantic in T10)
# ---------------------------------------------------------------------------


def test_nonexistent_source_and_grain_parse():
    m = StagingModel(
        name="stg_ghost",
        source="ghost_raw",
        columns=(StagingColumn(source="ghost_col", target="ghost_col"),),
        grain=("ghost_col",),
    )
    assert m.source == "ghost_raw"
    # also via model_validate with ghost grain that doesn't exist as column target? Still parses locally – grain uniqueness only, not existence
    m2 = StagingModel.model_validate(
        {
            "name": "stg_a",
            "source": "ghost_raw",
            "columns": ({"source": "col_a", "target": "col_a"},),
            "grain": ("nonexistent",),
        }
    )
    assert m2.grain == ("nonexistent",)
