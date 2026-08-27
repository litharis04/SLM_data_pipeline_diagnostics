"""Contract tests for HealthyAssertion (SCENARIO_SPEC §15)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from data_pipeline_diagnostics.scenario.assertions import (
    AcceptedValuesAssertion,
    ColumnRangeAssertion,
    HealthyAssertion,
    NotNullAssertion,
    RelationshipsAssertion,
    RowCountAssertion,
    UniqueAssertion,
)
from data_pipeline_diagnostics.scenario.base import ContractModel


class Wrap(ContractModel):
    a: HealthyAssertion


# ---------------------------------------------------------------------------
# Positive – one per type
# ---------------------------------------------------------------------------


def test_not_null():
    a = NotNullAssertion(name="nn1", model="m1", columns=("col_a",))
    assert a.type == "not_null"
    assert Wrap(a=a).a.type == "not_null"


def test_unique():
    a = UniqueAssertion(name="uq1", model="m1", columns=("col_a", "col_b"))
    assert a.type == "unique"
    assert a.columns == ("col_a", "col_b")


def test_accepted_values():
    a = AcceptedValuesAssertion(name="av1", model="m1", column="col_a", values=("a", "b"))
    assert a.type == "accepted_values"
    assert a.values == ("a", "b")


def test_accepted_values_mixed_types_distinct():
    # true != 1 per spec – distinct by type
    a = AcceptedValuesAssertion(name="av2", model="m1", column="col_a", values=(True, 1))
    assert a.values == (True, 1)


def test_relationships():
    a = RelationshipsAssertion(
        name="rel1", model="m1", columns=("col_a",), to_model="m2", to_columns=("col_b",)
    )
    assert a.type == "relationships"


def test_relationships_composite():
    a = RelationshipsAssertion(
        name="rel2", model="m1", columns=("a", "b"), to_model="m2", to_columns=("c", "d")
    )
    assert len(a.columns) == 2


def test_row_count_min_only():
    a = RowCountAssertion(name="rc1", model="m1", min=5)
    assert a.min == 5
    assert a.max is None


def test_row_count_max_only():
    a = RowCountAssertion(name="rc2", model="m1", max=10)
    assert a.max == 10


def test_row_count_both():
    a = RowCountAssertion(name="rc3", model="m1", min=5, max=10)
    assert a.min == 5 and a.max == 10


def test_row_count_equal_bounds():
    a = RowCountAssertion(name="rc4", model="m1", min=5, max=5)
    assert a.min == a.max


def test_column_range_min_only():
    a = ColumnRangeAssertion(name="cr1", model="m1", column="col_a", min=1)
    assert a.min == 1


def test_column_range_max_only():
    a = ColumnRangeAssertion(name="cr2", model="m1", column="col_a", max=100)
    assert a.max == 100


def test_column_range_both():
    a = ColumnRangeAssertion(name="cr3", model="m1", column="col_a", min=1, max=10)
    assert a.min == 1 and a.max == 10


def test_column_range_inclusive():
    a = ColumnRangeAssertion(name="cr4", model="m1", column="col_a", min=1, inclusive=False)
    assert a.inclusive is False
    a2 = ColumnRangeAssertion(name="cr5", model="m1", column="col_a", min=1)
    assert a2.inclusive is True


def test_assertion_with_description():
    a = NotNullAssertion(name="nn2", model="m1", columns=("a",), description="must not be null")
    assert a.description == "must not be null"


def test_union_parsing():
    assert (
        Wrap(a={"name": "a", "model": "m", "type": "not_null", "columns": ("col",)}).a.type
        == "not_null"
    )
    assert (
        Wrap(a={"name": "a", "model": "m", "type": "unique", "columns": ("col",)}).a.type
        == "unique"
    )
    assert (
        Wrap(
            a={
                "name": "a",
                "model": "m",
                "type": "accepted_values",
                "column": "c",
                "values": ("a",),
            }
        ).a.type
        == "accepted_values"
    )
    assert (
        Wrap(
            a={
                "name": "a",
                "model": "m",
                "type": "relationships",
                "columns": ("c",),
                "to_model": "m2",
                "to_columns": ("d",),
            }
        ).a.type
        == "relationships"
    )
    assert Wrap(a={"name": "a", "model": "m", "type": "row_count", "min": 1}).a.type == "row_count"
    assert (
        Wrap(a={"name": "a", "model": "m", "type": "column_range", "column": "c", "min": 1}).a.type
        == "column_range"
    )


# ---------------------------------------------------------------------------
# Negative – local validators
# ---------------------------------------------------------------------------


def test_empty_columns_rejected():
    with pytest.raises(ValidationError):
        NotNullAssertion(name="a", model="m", columns=())
    with pytest.raises(ValidationError):
        UniqueAssertion(name="a", model="m", columns=())
    with pytest.raises(ValidationError):
        RelationshipsAssertion(name="a", model="m", columns=(), to_model="m2", to_columns=("col",))


def test_duplicate_columns_rejected():
    with pytest.raises(ValidationError, match="must not contain duplicates"):
        NotNullAssertion(name="a", model="m", columns=("col", "col"))
    with pytest.raises(ValidationError, match="must not contain duplicates"):
        UniqueAssertion(name="a", model="m", columns=("col", "col"))


def test_accepted_values_duplicate_rejected():
    with pytest.raises(ValidationError, match="unique by value and type"):
        AcceptedValuesAssertion(name="a", model="m", column="c", values=("a", "a"))
    # same value different type is allowed, same type+value not
    with pytest.raises(ValidationError, match="unique by value and type"):
        AcceptedValuesAssertion(name="a", model="m", column="c", values=(1, 1))
    with pytest.raises(ValidationError, match="unique by value and type"):
        AcceptedValuesAssertion(name="a", model="m", column="c", values=(True, True))


def test_accepted_values_empty_rejected():
    with pytest.raises(ValidationError):
        AcceptedValuesAssertion(name="a", model="m", column="c", values=())


def test_relationships_arity_mismatch():
    with pytest.raises(ValidationError, match="equal arity"):
        RelationshipsAssertion(
            name="a", model="m", columns=("a",), to_model="m2", to_columns=("b", "c")
        )
    with pytest.raises(ValidationError, match="equal arity"):
        RelationshipsAssertion(
            name="a", model="m", columns=("a", "b"), to_model="m2", to_columns=("c",)
        )


def test_relationships_empty_to_columns():
    with pytest.raises(ValidationError):
        RelationshipsAssertion(name="a", model="m", columns=("a",), to_model="m2", to_columns=())


def test_row_count_without_bounds():
    with pytest.raises(ValidationError, match="at least one"):
        RowCountAssertion(name="a", model="m")


def test_row_count_min_gt_max():
    with pytest.raises(ValidationError, match="min must be <="):
        RowCountAssertion(name="a", model="m", min=10, max=5)


def test_row_count_negative():
    with pytest.raises(ValidationError):
        RowCountAssertion(name="a", model="m", min=-1)
    with pytest.raises(ValidationError):
        RowCountAssertion(name="a", model="m", max=-1)


def test_row_count_strict_int():
    with pytest.raises(ValidationError):
        RowCountAssertion(name="a", model="m", min="1")  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        RowCountAssertion(name="a", model="m", min=1.0)  # type: ignore[arg-type]


def test_column_range_without_bounds():
    with pytest.raises(ValidationError, match="at least one"):
        ColumnRangeAssertion(name="a", model="m", column="c")


def test_column_range_with_both_none_explicit():
    with pytest.raises(ValidationError, match="at least one"):
        ColumnRangeAssertion(name="a", model="m", column="c", min=None, max=None)


def test_unknown_type_rejected():
    with pytest.raises(ValidationError, match="union_tag_invalid"):
        Wrap.model_validate(
            {"a": {"name": "a", "model": "m", "type": "unknown", "columns": ("col",)}}
        )
    with pytest.raises(ValidationError, match="union_tag_invalid"):
        Wrap.model_validate(
            {"a": {"name": "a", "model": "m", "type": "check", "columns": ("col",)}}
        )


def test_missing_discriminator_rejected():
    with pytest.raises(ValidationError, match="union_tag_not_found"):
        Wrap.model_validate({"a": {"name": "a", "model": "m", "columns": ("col",)}})


def test_extra_field_rejected():
    with pytest.raises(ValidationError, match="extra_forbidden"):
        NotNullAssertion.model_validate(
            {"name": "a", "model": "m", "type": "not_null", "columns": ("col",), "extra": "x"}
        )
    with pytest.raises(ValidationError, match="extra_forbidden"):
        RowCountAssertion.model_validate(
            {"name": "a", "model": "m", "type": "row_count", "min": 1, "severity": "warning"}
        )
    with pytest.raises(ValidationError, match="extra_forbidden"):
        ColumnRangeAssertion.model_validate(
            {
                "name": "a",
                "model": "m",
                "type": "column_range",
                "column": "c",
                "min": 1,
                "extra": "x",
            }
        )


def test_coercion_rejected():
    with pytest.raises(ValidationError):
        NotNullAssertion.model_validate(
            {"name": 123, "model": "m", "type": "not_null", "columns": ("col",)}
        )
    with pytest.raises(ValidationError):
        RowCountAssertion.model_validate(
            {"name": "a", "model": "m", "type": "row_count", "min": "1"}
        )
    with pytest.raises(ValidationError):
        ColumnRangeAssertion.model_validate(
            {"name": "a", "model": "m", "type": "column_range", "column": 123, "min": 1}
        )


def test_inclusive_strict_bool():
    with pytest.raises(ValidationError):
        ColumnRangeAssertion.model_validate(
            {
                "name": "a",
                "model": "m",
                "type": "column_range",
                "column": "c",
                "min": 1,
                "inclusive": "true",
            }
        )
    with pytest.raises(ValidationError):
        ColumnRangeAssertion.model_validate(
            {
                "name": "a",
                "model": "m",
                "type": "column_range",
                "column": "c",
                "min": 1,
                "inclusive": 1,
            }
        )
