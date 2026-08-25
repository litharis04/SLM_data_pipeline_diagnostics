"""Relationship models — RelationshipSpec and bridge.

Implements ``docs/SCENARIO_SPEC.md`` §5.5, §9.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from data_pipeline_diagnostics.scenario.base import ContractModel
from data_pipeline_diagnostics.scenario.types import Description, Identifier, RelationshipEndpoint


class BridgeReference(ContractModel):
    """Bridge table reference for many-to-many (§9.3)."""

    table: Identifier
    left_columns: Annotated[tuple[Identifier, ...], Field(min_length=1)]
    right_columns: Annotated[tuple[Identifier, ...], Field(min_length=1)]

    @field_validator("left_columns", "right_columns")
    @classmethod
    def _unique_within_side(cls, v: tuple[Identifier, ...]) -> tuple[Identifier, ...]:
        if len(v) != len(set(v)):
            msg = "bridge columns must be unique within side"
            raise ValueError(msg)
        return v

    @model_validator(mode="after")
    def _disjoint_columns(self) -> BridgeReference:
        if set(self.left_columns) & set(self.right_columns):
            msg = "left_columns and right_columns must be disjoint"
            raise ValueError(msg)
        return self


class OneToOneRelationship(ContractModel):
    name: Identifier
    cardinality: Literal["one_to_one"] = "one_to_one"
    left: RelationshipEndpoint
    right: RelationshipEndpoint
    description: Description | None = None


class OneToManyRelationship(ContractModel):
    name: Identifier
    cardinality: Literal["one_to_many"] = "one_to_many"
    left: RelationshipEndpoint
    right: RelationshipEndpoint
    description: Description | None = None


class ManyToOneRelationship(ContractModel):
    name: Identifier
    cardinality: Literal["many_to_one"] = "many_to_one"
    left: RelationshipEndpoint
    right: RelationshipEndpoint
    description: Description | None = None


class ManyToManyRelationship(ContractModel):
    name: Identifier
    cardinality: Literal["many_to_many"] = "many_to_many"
    left: RelationshipEndpoint
    right: RelationshipEndpoint
    bridge: BridgeReference
    description: Description | None = None


RelationshipSpec = Annotated[
    OneToOneRelationship | OneToManyRelationship | ManyToOneRelationship | ManyToManyRelationship,
    Field(discriminator="cardinality"),
]
