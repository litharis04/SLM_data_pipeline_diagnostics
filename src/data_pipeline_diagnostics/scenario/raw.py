"""Raw-data models — RawColumn and RawTable.

Implements ``docs/SCENARIO_SPEC.md`` §7.2–7.3.
All models inherit from :class:`ContractModel` (strict, frozen, extra=forbid).
"""

from __future__ import annotations

from typing import Annotated

from pydantic import Field, StrictBool, field_validator, model_validator

from data_pipeline_diagnostics.scenario.base import ContractModel
from data_pipeline_diagnostics.scenario.generators import GeneratorSpec
from data_pipeline_diagnostics.scenario.types import (
    DataType,
    Description,
    Identifier,
    Probability,
    RowCount,
)


class RawColumn(ContractModel):
    """Single column in a raw table (§7.2)."""

    name: Identifier
    type: Annotated[DataType, Field(strict=False)]  # noqa: A003 – spec requires field name exactly ``type``
    nullable: StrictBool = False
    null_probability: Probability = 0.0
    unique: StrictBool = False
    generator: GeneratorSpec
    description: Description | None = None

    @model_validator(mode="after")
    def _check_null_probability(self) -> RawColumn:
        if not self.nullable and self.null_probability != 0.0:
            msg = "null_probability must be 0.0 when nullable is false"
            raise ValueError(msg)
        return self


class RawTable(ContractModel):
    """Raw table declaration (§7.3)."""

    name: Identifier
    rows: RowCount
    columns: Annotated[tuple[RawColumn, ...], Field(min_length=1)]
    primary_key: tuple[Identifier, ...] = ()
    description: Description | None = None

    @field_validator("columns")
    @classmethod
    def _unique_column_names(cls, v: tuple[RawColumn, ...]) -> tuple[RawColumn, ...]:
        names = [c.name for c in v]
        if len(names) != len(set(names)):
            msg = "column names must be unique within the table"
            raise ValueError(msg)
        return v

    @field_validator("primary_key")
    @classmethod
    def _unique_pk_entries(cls, v: tuple[Identifier, ...]) -> tuple[Identifier, ...]:
        if len(v) != len(set(v)):
            msg = "primary_key entries must be unique"
            raise ValueError(msg)
        return v
