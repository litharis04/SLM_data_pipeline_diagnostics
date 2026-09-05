"""Tests for shared scalar types and references (SCENARIO_SPEC §5, §7.1)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from data_pipeline_diagnostics.scenario.base import ContractModel
from data_pipeline_diagnostics.scenario.types import (
    DataType,
    Description,
    DomainName,
    Identifier,
    Probability,
    RelationshipEndpoint,
    RowCount,
    ScalarValue,
    ScenarioId,
    SortKey,
)


class MId(ContractModel):
    v: Identifier


class MScenarioId(ContractModel):
    v: ScenarioId


class MDesc(ContractModel):
    v: Description


class MProb(ContractModel):
    v: Probability


class MScalar(ContractModel):
    v: ScalarValue


def test_identifier_valid():
    assert MId(v="a").v == "a"
    assert MId(v="abc_123").v == "abc_123"
    assert MId(v="a" * 63).v == "a" * 63


def test_identifier_invalid():
    for bad in ["", "A", "1abc", "a-b", "a" * 64, "a b"]:
        with pytest.raises(ValidationError):
            MId(v=bad)
    with pytest.raises(ValidationError):
        MId(v=123)  # type: ignore[arg-type]


def test_scenario_id_length():
    assert MScenarioId(v="a" * 100).v == "a" * 100
    with pytest.raises(ValidationError):
        MScenarioId(v="a" * 101)


def test_domain_name_alias():
    # DomainName is Identifier
    class MDom(ContractModel):
        v: DomainName

    assert MDom(v="mydomain").v == "mydomain"
    with pytest.raises(ValidationError):
        MDom(v="BadDomain")


def test_description_constraints():
    assert MDesc(v="hello").v == "hello"
    assert MDesc(v="a" * 500).v == "a" * 500
    for bad in ["", "a" * 501, 123]:
        with pytest.raises(ValidationError):
            MDesc(v=bad)  # type: ignore[arg-type]


def test_datatype_enum():
    assert DataType("string") == DataType.string
    assert set(DataType) == {
        DataType.string,
        DataType.integer,
        DataType.float,
        DataType.boolean,
        DataType.date,
        DataType.timestamp,
    }

    # invalid value should fail when used as field
    class MDt(ContractModel):
        v: DataType

    with pytest.raises(ValidationError):
        MDt(v="invalid")  # type: ignore[arg-type]


def test_scalar_value_strict():
    assert MScalar(v="hello").v == "hello"
    assert MScalar(v=1).v == 1 and type(MScalar(v=1).v) is int
    assert MScalar(v=1.5).v == 1.5 and type(MScalar(v=1.5).v) is float
    assert MScalar(v=True).v is True
    assert MScalar(v=False).v is False
    with pytest.raises(ValidationError):
        MScalar(v=None)  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        MScalar(v=float("inf"))
    with pytest.raises(ValidationError):
        MScalar(v=float("nan"))

    # bool must not be accepted as int: explicit int field rejects bool
    class MInt(ContractModel):
        v: int

    with pytest.raises(ValidationError):
        MInt(v=True)


def test_probability_strict():
    assert MProb(v=0.0).v == 0.0
    assert MProb(v=0.5).v == 0.5
    assert MProb(v=1.0).v == 1.0
    for bad in [-0.1, 1.1, 1, True, "0.5", float("inf"), float("nan")]:
        with pytest.raises(ValidationError):
            MProb(v=bad)  # type: ignore[arg-type]


def test_relationship_endpoint():
    ep = RelationshipEndpoint(table="my_table", columns=("col_a", "col_b"))
    assert ep.table == "my_table"
    assert ep.columns == ("col_a", "col_b")
    with pytest.raises(ValidationError):
        RelationshipEndpoint(table="my_table", columns=())
    with pytest.raises(ValidationError):
        RelationshipEndpoint(table="my_table", columns=("a", "a"))
    with pytest.raises(ValidationError):
        RelationshipEndpoint(table="Invalid", columns=("a",))
    with pytest.raises(ValidationError):
        RelationshipEndpoint(table="my_table", columns=("a",), extra="x")  # type: ignore[call-arg]


def test_sort_key():
    assert SortKey(column="my_col").direction == "asc"
    assert SortKey(column="my_col", direction="desc").direction == "desc"
    with pytest.raises(ValidationError):
        SortKey(column="my_col", direction="invalid")  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        SortKey(column="Invalid")  # type: ignore[arg-type]


def test_row_count():
    assert RowCount(min=1, max=1).min == 1
    assert RowCount(min=1, max=10).max == 10
    with pytest.raises(ValidationError):
        RowCount(min=10, max=1)
    with pytest.raises(ValidationError):
        RowCount(min=0, max=1)
    with pytest.raises(ValidationError):
        RowCount(min=1.0, max=2)  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        RowCount(min=True, max=2)  # type: ignore[arg-type]
