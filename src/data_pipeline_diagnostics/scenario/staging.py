"""Staging layer — StagingColumn, operations and StagingModel.

Implements ``docs/SCENARIO_SPEC.md`` §11.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, StringConstraints, field_validator, model_validator

from data_pipeline_diagnostics.scenario.base import ContractModel
from data_pipeline_diagnostics.scenario.expressions import Condition
from data_pipeline_diagnostics.scenario.types import (
    DataType,
    Description,
    Identifier,
    ScalarValue,
    SortKey,
)

# ---------------------------------------------------------------------------
# Staging column operations (§11.3) – discriminated by ``op``
# ---------------------------------------------------------------------------


class CastOperation(ContractModel):
    op: Literal["cast"] = "cast"
    type: Annotated[DataType, Field(strict=False)]  # noqa: A003 – spec field name ``type``
    format: str | None = None

    @model_validator(mode="after")
    def _check_format(self) -> CastOperation:
        # ``format`` is allowed only when parsing a string as date or timestamp
        # Locally we can enforce: if type is date/timestamp, format may be present;
        # if type is not date/timestamp, format must be None.
        # Full source-type compatibility is semantic (depends on preceding ops).
        if self.type not in (DataType.date, DataType.timestamp) and self.format is not None:
            msg = "format is allowed only for date or timestamp cast"
            raise ValueError(msg)
        # Also forbid empty string format if present
        if self.format is not None and self.format == "":
            msg = "format must not be empty"
            raise ValueError(msg)
        return self


class TrimOperation(ContractModel):
    op: Literal["trim"] = "trim"


class LowerOperation(ContractModel):
    op: Literal["lower"] = "lower"


class UpperOperation(ContractModel):
    op: Literal["upper"] = "upper"


class ReplaceOperation(ContractModel):
    op: Literal["replace"] = "replace"
    old: Annotated[str, StringConstraints(min_length=1, strict=True)]
    new: str  # strict string, may be empty

    @field_validator("new")
    @classmethod
    def _strict_new(cls, v: str) -> str:
        if not isinstance(v, str):
            msg = "new must be a string"
            raise ValueError(msg)
        return v


class MapValuesOperation(ContractModel):
    op: Literal["map_values"] = "map_values"
    mapping: Annotated[dict[str, str], Field(min_length=1)]
    on_unmapped: Literal["keep", "null", "error"] = "keep"

    @field_validator("mapping")
    @classmethod
    def _check_mapping(cls, v: dict[str, str]) -> dict[str, str]:
        if len(v) < 1:
            msg = "mapping must not be empty"
            raise ValueError(msg)
        for k, val in v.items():
            if not isinstance(k, str) or not isinstance(val, str):
                msg = "mapping keys and values must be strings"
                raise ValueError(msg)
            if k == "" or val == "":
                # empty string keys/values are allowed? Spec says non-empty mapping, but not about empty strings inside
                # We allow empty values but not empty keys? Keep permissive – ruff will handle
                pass
        return v


class NullIfOperation(ContractModel):
    op: Literal["null_if"] = "null_if"
    values: Annotated[tuple[ScalarValue, ...], Field(min_length=1)]


class CoalesceOperation(ContractModel):
    op: Literal["coalesce"] = "coalesce"
    value: ScalarValue


StagingColumnOperation = Annotated[
    CastOperation
    | TrimOperation
    | LowerOperation
    | UpperOperation
    | ReplaceOperation
    | MapValuesOperation
    | NullIfOperation
    | CoalesceOperation,
    Field(discriminator="op"),
]

# ---------------------------------------------------------------------------
# Staging row operations (§11.4)
# ---------------------------------------------------------------------------


class FilterRowsOperation(ContractModel):
    op: Literal["filter"] = "filter"
    condition: Condition


class DeduplicateRowsOperation(ContractModel):
    op: Literal["deduplicate"] = "deduplicate"
    keys: Annotated[tuple[Identifier, ...], Field(min_length=1)]
    order_by: Annotated[tuple[SortKey, ...], Field(min_length=1)]

    @field_validator("keys")
    @classmethod
    def _unique_keys(cls, v: tuple[Identifier, ...]) -> tuple[Identifier, ...]:
        if len(v) != len(set(v)):
            msg = "keys must not contain duplicates"
            raise ValueError(msg)
        return v


StagingRowOperation = Annotated[
    FilterRowsOperation | DeduplicateRowsOperation,
    Field(discriminator="op"),
]

# ---------------------------------------------------------------------------
# StagingColumn and StagingModel
# ---------------------------------------------------------------------------


class StagingColumn(ContractModel):
    source: Identifier
    target: Identifier
    operations: tuple[StagingColumnOperation, ...] = ()
    description: Description | None = None


class StagingModel(ContractModel):
    name: Identifier
    source: Identifier
    columns: Annotated[tuple[StagingColumn, ...], Field(min_length=1)]
    row_operations: tuple[StagingRowOperation, ...] = ()
    grain: Annotated[tuple[Identifier, ...], Field(min_length=1)]
    description: Description | None = None

    @field_validator("columns")
    @classmethod
    def _unique_sources_targets(cls, v: tuple[StagingColumn, ...]) -> tuple[StagingColumn, ...]:
        sources = [c.source for c in v]
        if len(sources) != len(set(sources)):
            msg = "source values must be unique within columns"
            raise ValueError(msg)
        targets = [c.target for c in v]
        if len(targets) != len(set(targets)):
            msg = "target values must be unique within columns"
            raise ValueError(msg)
        return v

    @field_validator("grain")
    @classmethod
    def _unique_grain(cls, v: tuple[Identifier, ...]) -> tuple[Identifier, ...]:
        if len(v) != len(set(v)):
            msg = "grain must not contain duplicates"
            raise ValueError(msg)
        return v
