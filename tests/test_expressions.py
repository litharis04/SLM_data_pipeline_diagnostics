"""Tests for structured expressions and conditions (SCENARIO_SPEC §10)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from data_pipeline_diagnostics.scenario.base import ContractModel
from data_pipeline_diagnostics.scenario.expressions import (
    AnyCondition,
    BinaryExpression,
    BooleanCondition,
    CoalesceExpression,
    ColumnExpression,
    ComparisonCondition,
    Condition,
    DatePartExpression,
    Expression,
    InCondition,
    LiteralExpression,
    NotCondition,
    NullCondition,
)


class WrapExpr(ContractModel):
    expr: Expression


class WrapCond(ContractModel):
    cond: Condition


# ---------------------------------------------------------------------------
# Expression tests
# ---------------------------------------------------------------------------


def test_column_expression():
    assert ColumnExpression(column="my_col").column == "my_col"
    with pytest.raises(ValidationError):
        ColumnExpression(column="Invalid")


def test_literal_expression():
    assert LiteralExpression(value="hello").value == "hello"
    assert LiteralExpression(value=1).value == 1
    assert LiteralExpression(value=1.5).value == 1.5
    assert LiteralExpression(value=True).value is True


def test_binary_expression():
    expr = BinaryExpression(
        operator="add",
        left={"kind": "column", "column": "a"},
        right={"kind": "literal", "value": 1},
    )
    assert expr.operator == "add"
    # nested
    nested = BinaryExpression(
        operator="multiply",
        left={"kind": "column", "column": "a"},
        right={
            "kind": "binary",
            "operator": "add",
            "left": {"kind": "column", "column": "b"},
            "right": {"kind": "literal", "value": 2},
        },
    )
    assert nested.right.kind == "binary"
    with pytest.raises(ValidationError):
        BinaryExpression(
            operator="invalid",
            left={"kind": "column", "column": "a"},
            right={"kind": "literal", "value": 1},
        )  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        BinaryExpression(
            operator="add",
            left={"kind": "column", "column": "a"},
            right={"kind": "literal", "value": float("inf")},
        )


def test_date_part_expression():
    assert DatePartExpression(part="year", value={"kind": "column", "column": "a"}).part == "year"
    for part in ["year", "quarter", "month", "day", "day_of_week"]:
        assert DatePartExpression(part=part, value={"kind": "column", "column": "a"}).part == part
    with pytest.raises(ValidationError):
        DatePartExpression(part="invalid", value={"kind": "column", "column": "a"})  # type: ignore[arg-type]


def test_coalesce_expression():
    expr = CoalesceExpression(
        values=({"kind": "column", "column": "a"}, {"kind": "literal", "value": "x"})
    )
    assert len(expr.values) == 2
    with pytest.raises(ValidationError):
        CoalesceExpression(values=({"kind": "column", "column": "a"},))
    with pytest.raises(ValidationError):
        CoalesceExpression(values=())


def test_expression_discriminated_union():
    assert WrapExpr(expr={"kind": "column", "column": "a"}).expr.kind == "column"
    assert WrapExpr(expr={"kind": "literal", "value": 1}).expr.kind == "literal"
    with pytest.raises(ValidationError, match="union_tag_invalid"):
        WrapExpr(expr={"kind": "unknown", "column": "a"})  # type: ignore[arg-type]
    with pytest.raises(ValidationError, match="union_tag_not_found"):
        WrapExpr(expr={"column": "a"})  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        WrapExpr(expr={"kind": "column", "column": "a", "extra": "x"})  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Condition tests
# ---------------------------------------------------------------------------


def test_comparison_condition():
    c = ComparisonCondition(
        operator="eq", left={"kind": "column", "column": "a"}, right={"kind": "literal", "value": 1}
    )
    assert c.operator == "eq"
    for op in ["eq", "ne", "lt", "lte", "gt", "gte"]:
        assert (
            ComparisonCondition(
                operator=op,
                left={"kind": "column", "column": "a"},
                right={"kind": "literal", "value": 1},
            ).operator
            == op
        )
    with pytest.raises(ValidationError):
        ComparisonCondition(
            operator="invalid",
            left={"kind": "column", "column": "a"},
            right={"kind": "literal", "value": 1},
        )  # type: ignore[arg-type]


def test_in_condition():
    c = InCondition(value={"kind": "column", "column": "a"}, options=("a", "b"))
    assert c.options == ("a", "b")
    assert c.negated is False
    assert (
        InCondition(value={"kind": "column", "column": "a"}, options=("a",), negated=True).negated
        is True
    )
    with pytest.raises(ValidationError):
        InCondition(value={"kind": "column", "column": "a"}, options=())
    with pytest.raises(ValidationError):
        InCondition(value={"kind": "column", "column": "a"}, options=("a",), negated="true")  # type: ignore[arg-type]


def test_null_condition():
    c = NullCondition(value={"kind": "column", "column": "a"})
    assert c.negated is False
    assert NullCondition(value={"kind": "column", "column": "a"}, negated=True).negated is True


def test_boolean_conditions():
    conds = (
        {
            "kind": "comparison",
            "operator": "eq",
            "left": {"kind": "column", "column": "a"},
            "right": {"kind": "literal", "value": 1},
        },
        {"kind": "is_null", "value": {"kind": "column", "column": "b"}},
    )
    assert BooleanCondition(conditions=conds).kind == "all"
    assert AnyCondition(conditions=conds).kind == "any"
    with pytest.raises(ValidationError):
        BooleanCondition(
            conditions=({"kind": "is_null", "value": {"kind": "column", "column": "a"}},)
        )
    with pytest.raises(ValidationError):
        AnyCondition(conditions=())


def test_not_condition():
    c = NotCondition(
        condition={
            "kind": "comparison",
            "operator": "eq",
            "left": {"kind": "column", "column": "a"},
            "right": {"kind": "literal", "value": 1},
        }
    )
    assert c.kind == "not"
    with pytest.raises(ValidationError):
        NotCondition(condition={"kind": "unknown", "column": "a"})  # type: ignore[arg-type]


def test_condition_discriminated_union():
    assert (
        WrapCond(
            cond={
                "kind": "comparison",
                "operator": "eq",
                "left": {"kind": "column", "column": "a"},
                "right": {"kind": "literal", "value": 1},
            }
        ).cond.kind
        == "comparison"
    )
    with pytest.raises(ValidationError, match="union_tag_invalid"):
        WrapCond(cond={"kind": "unknown", "value": {"kind": "column", "column": "a"}})  # type: ignore[arg-type]
