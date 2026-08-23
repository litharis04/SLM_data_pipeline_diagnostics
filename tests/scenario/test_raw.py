"""Contract tests for RawColumn and RawTable (SCENARIO_SPEC §7.2–7.3)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from data_pipeline_diagnostics.scenario.raw import RawColumn, RawTable

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_INT_GEN = {"kind": "integer_range", "min": 1, "max": 10}
_STR_GEN = {"kind": "formatted_id", "digits": 5}
_FLOAT_GEN = {"kind": "float_range", "min": 1.0, "max": 2.0}


# ---------------------------------------------------------------------------
# Positive – minimal and nominal
# ---------------------------------------------------------------------------


def test_raw_column_minimal():
    col = RawColumn(name="col_a", type="integer", generator=_INT_GEN)
    assert col.name == "col_a"
    assert col.type == "integer"
    assert col.nullable is False
    assert col.null_probability == 0.0
    assert col.unique is False
    assert col.description is None


def test_raw_column_nullable_with_probability():
    col = RawColumn(
        name="col_a", type="string", nullable=True, null_probability=0.2, generator=_STR_GEN
    )
    assert col.nullable is True
    assert col.null_probability == 0.2


def test_raw_column_nullable_false_probability_zero_explicit():
    col = RawColumn(
        name="col_a", type="string", nullable=False, null_probability=0.0, generator=_STR_GEN
    )
    assert col.null_probability == 0.0


def test_raw_column_unique_and_description():
    col = RawColumn(
        name="col_a", type="float", unique=True, description="a metric", generator=_FLOAT_GEN
    )
    assert col.unique is True
    assert col.description == "a metric"


def test_raw_table_minimal_one_column_no_pk():
    tbl = RawTable(
        name="my_table",
        rows={"min": 1, "max": 10},
        columns=(RawColumn(name="id", type="integer", generator=_INT_GEN),),
    )
    assert tbl.name == "my_table"
    assert tbl.primary_key == ()
    assert len(tbl.columns) == 1


def test_raw_table_with_single_pk():
    col = RawColumn(name="id", type="integer", generator=_INT_GEN)
    tbl = RawTable(name="t", rows={"min": 5, "max": 5}, columns=(col,), primary_key=("id",))
    assert tbl.primary_key == ("id",)


def test_raw_table_with_composite_pk():
    cols = (
        RawColumn(name="id", type="integer", generator=_INT_GEN),
        RawColumn(name="seq", type="integer", generator=_INT_GEN),
    )
    tbl = RawTable(name="t", rows={"min": 1, "max": 3}, columns=cols, primary_key=("id", "seq"))
    assert tbl.primary_key == ("id", "seq")


def test_raw_column_json_type_key():
    # field is named exactly ``type`` – JSON round-trip must preserve it
    col = RawColumn.model_validate({"name": "col_a", "type": "string", "generator": _STR_GEN})
    assert col.model_dump()["type"] == "string"
    assert col.model_dump(mode="json")["type"] == "string"


def test_raw_table_description():
    col = RawColumn(name="id", type="integer", generator=_INT_GEN)
    tbl = RawTable(name="t", rows={"min": 1, "max": 1}, columns=(col,), description="raw source")
    assert tbl.description == "raw source"


# ---------------------------------------------------------------------------
# Negative – local validators
# ---------------------------------------------------------------------------


def test_raw_column_null_probability_requires_nullable():
    with pytest.raises(ValidationError, match="null_probability must be 0.0"):
        RawColumn(
            name="col_a", type="string", nullable=False, null_probability=0.1, generator=_STR_GEN
        )
    # also when nullable not set (defaults to False)
    with pytest.raises(ValidationError, match="null_probability must be 0.0"):
        RawColumn(name="col_a", type="string", null_probability=0.1, generator=_STR_GEN)
    with pytest.raises(ValidationError, match="null_probability must be 0.0"):
        RawColumn(
            name="col_a", type="string", nullable=False, null_probability=1.0, generator=_STR_GEN
        )


def test_raw_column_strict_bool_no_coercion():
    with pytest.raises(ValidationError):
        RawColumn.model_validate(
            {"name": "col_a", "type": "string", "nullable": "false", "generator": _STR_GEN}
        )
    with pytest.raises(ValidationError):
        RawColumn.model_validate(
            {"name": "col_a", "type": "string", "unique": "true", "generator": _STR_GEN}
        )
    with pytest.raises(ValidationError):
        RawColumn.model_validate(
            {"name": "col_a", "type": "string", "nullable": 1, "generator": _STR_GEN}
        )


def test_raw_column_null_probability_strict_float():
    # Probability is StrictProbabilityFloat – int must be rejected
    with pytest.raises(ValidationError):
        RawColumn(
            name="col_a", type="string", nullable=True, null_probability=1, generator=_STR_GEN
        )  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        RawColumn(
            name="col_a", type="string", nullable=True, null_probability="0.1", generator=_STR_GEN
        )  # type: ignore[arg-type]


def test_raw_column_extra_field_rejected():
    with pytest.raises(ValidationError, match="extra_forbidden"):
        RawColumn.model_validate(
            {"name": "col_a", "type": "string", "generator": _STR_GEN, "extra": "x"}
        )
    with pytest.raises(ValidationError, match="extra_forbidden"):
        RawColumn(name="col_a", type="string", generator=_STR_GEN, extra="x")  # type: ignore[call-arg]


def test_raw_table_extra_field_rejected():
    col = {"name": "id", "type": "integer", "generator": _INT_GEN}
    with pytest.raises(ValidationError, match="extra_forbidden"):
        RawTable.model_validate(
            {"name": "t", "rows": {"min": 1, "max": 1}, "columns": (col,), "extra": "x"}
        )


def test_raw_table_duplicate_column_names():
    cols = (
        {"name": "id", "type": "integer", "generator": _INT_GEN},
        {"name": "id", "type": "string", "generator": _STR_GEN},
    )
    with pytest.raises(ValidationError, match="column names must be unique"):
        RawTable.model_validate({"name": "t", "rows": {"min": 1, "max": 1}, "columns": cols})


def test_raw_table_duplicate_primary_key_entries():
    col = {"name": "id", "type": "integer", "generator": _INT_GEN}
    with pytest.raises(ValidationError, match="primary_key entries must be unique"):
        RawTable.model_validate(
            {
                "name": "t",
                "rows": {"min": 1, "max": 1},
                "columns": (col,),
                "primary_key": ("id", "id"),
            }
        )


def test_raw_table_empty_columns_rejected():
    with pytest.raises(ValidationError):
        RawTable.model_validate({"name": "t", "rows": {"min": 1, "max": 1}, "columns": ()})


def test_raw_table_strict_no_coercion():
    col = {"name": "id", "type": "integer", "generator": _INT_GEN}
    # rows min as string should be rejected
    with pytest.raises(ValidationError):
        RawTable.model_validate({"name": "t", "rows": {"min": "1", "max": 1}, "columns": (col,)})
    # primary_key entries as non-identifier?
    with pytest.raises(ValidationError):
        RawTable.model_validate(
            {
                "name": "t",
                "rows": {"min": 1, "max": 1},
                "columns": (col,),
                "primary_key": ("Invalid",),
            }
        )


def test_raw_column_invalid_identifier():
    with pytest.raises(ValidationError):
        RawColumn(name="Invalid", type="string", generator=_STR_GEN)
    with pytest.raises(ValidationError):
        RawColumn(name="a-b", type="string", generator=_STR_GEN)


def test_raw_table_invalid_name():
    col = {"name": "id", "type": "integer", "generator": _INT_GEN}
    with pytest.raises(ValidationError):
        RawTable.model_validate(
            {"name": "Invalid", "rows": {"min": 1, "max": 1}, "columns": (col,)}
        )


def test_raw_column_missing_required_fields():
    with pytest.raises(ValidationError):
        RawColumn.model_validate({"name": "col_a"})  # missing type, generator
    with pytest.raises(ValidationError):
        RawColumn.model_validate({"name": "col_a", "type": "string"})  # missing generator
