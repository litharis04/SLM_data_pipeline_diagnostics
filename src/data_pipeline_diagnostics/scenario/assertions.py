"""Healthy assertions — explicit checks (SCENARIO_SPEC §15)."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, StrictBool, StrictInt, field_validator, model_validator

from data_pipeline_diagnostics.scenario.base import ContractModel
from data_pipeline_diagnostics.scenario.types import Description, Identifier, ScalarValue


class NotNullAssertion(ContractModel):
    name: Identifier
    model: Identifier
    type: Literal["not_null"] = "not_null"
    columns: Annotated[tuple[Identifier, ...], Field(min_length=1)]
    description: Description | None = None

    @field_validator("columns")
    @classmethod
    def _unique_columns(cls, v: tuple[Identifier, ...]) -> tuple[Identifier, ...]:
        if len(v) != len(set(v)):
            msg = "columns must not contain duplicates"
            raise ValueError(msg)
        return v


class UniqueAssertion(ContractModel):
    name: Identifier
    model: Identifier
    type: Literal["unique"] = "unique"
    columns: Annotated[tuple[Identifier, ...], Field(min_length=1)]
    description: Description | None = None

    @field_validator("columns")
    @classmethod
    def _unique_columns(cls, v: tuple[Identifier, ...]) -> tuple[Identifier, ...]:
        if len(v) != len(set(v)):
            msg = "columns must not contain duplicates"
            raise ValueError(msg)
        return v


class AcceptedValuesAssertion(ContractModel):
    name: Identifier
    model: Identifier
    type: Literal["accepted_values"] = "accepted_values"
    column: Identifier
    values: Annotated[tuple[ScalarValue, ...], Field(min_length=1)]
    description: Description | None = None

    @field_validator("values")
    @classmethod
    def _unique_values(cls, v: tuple[ScalarValue, ...]) -> tuple[ScalarValue, ...]:
        seen: set[tuple[str, object]] = set()
        for item in v:
            key = (type(item).__name__, item)
            if key in seen:
                msg = "values must be unique by value and type"
                raise ValueError(msg)
            seen.add(key)
        return v


class RelationshipsAssertion(ContractModel):
    name: Identifier
    model: Identifier
    type: Literal["relationships"] = "relationships"
    columns: Annotated[tuple[Identifier, ...], Field(min_length=1)]
    to_model: Identifier
    to_columns: Annotated[tuple[Identifier, ...], Field(min_length=1)]
    description: Description | None = None

    @model_validator(mode="after")
    def _arity_equal(self) -> RelationshipsAssertion:
        if len(self.columns) != len(self.to_columns):
            msg = "columns and to_columns must have equal arity"
            raise ValueError(msg)
        return self


class RowCountAssertion(ContractModel):
    name: Identifier
    model: Identifier
    type: Literal["row_count"] = "row_count"
    min: Annotated[StrictInt, Field(ge=0)] | None = None
    max: Annotated[StrictInt, Field(ge=0)] | None = None
    description: Description | None = None

    @model_validator(mode="after")
    def _check_bounds(self) -> RowCountAssertion:
        if self.min is None and self.max is None:
            msg = "at least one of min or max must be set"
            raise ValueError(msg)
        if self.min is not None and self.max is not None and self.min > self.max:
            msg = "min must be <= max"
            raise ValueError(msg)
        return self


class ColumnRangeAssertion(ContractModel):
    name: Identifier
    model: Identifier
    type: Literal["column_range"] = "column_range"
    column: Identifier
    min: ScalarValue | None = None
    max: ScalarValue | None = None
    inclusive: StrictBool = True
    description: Description | None = None

    @model_validator(mode="after")
    def _check_bounds(self) -> ColumnRangeAssertion:
        if self.min is None and self.max is None:
            msg = "at least one of min or max must be set"
            raise ValueError(msg)
        return self


HealthyAssertion = Annotated[
    NotNullAssertion
    | UniqueAssertion
    | AcceptedValuesAssertion
    | RelationshipsAssertion
    | RowCountAssertion
    | ColumnRangeAssertion,
    Field(discriminator="type"),
]
