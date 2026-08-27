"""Intermediate layer — projections, joins and business transformations.

Implements ``docs/SCENARIO_SPEC.md`` §12.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from data_pipeline_diagnostics.scenario.base import ContractModel
from data_pipeline_diagnostics.scenario.expressions import Condition, Expression
from data_pipeline_diagnostics.scenario.types import DataType, Description, Identifier, SortKey

# ---------------------------------------------------------------------------
# Shared projection types (§12.1)
# ---------------------------------------------------------------------------


class ProjectionColumn(ContractModel):
    source: Identifier
    target: Identifier


class JoinProjectionColumn(ContractModel):
    side: Literal["left", "right"]
    source: Identifier
    target: Identifier


class DerivedColumn(ContractModel):
    name: Identifier
    type: Annotated[DataType, Field(strict=False)]  # noqa: A003 – spec field name is ``type``
    expression: Expression
    description: Description | None = None


# ---------------------------------------------------------------------------
# Join types (§12.2)
# ---------------------------------------------------------------------------


class JoinKeyPair(ContractModel):
    left: Identifier
    right: Identifier


class JoinSpec(ContractModel):
    type: Literal["inner", "left"]
    on: Annotated[tuple[JoinKeyPair, ...], Field(min_length=1)]

    @field_validator("on")
    @classmethod
    def _unique_key_pairs(cls, v: tuple[JoinKeyPair, ...]) -> tuple[JoinKeyPair, ...]:
        seen: set[tuple[str, str]] = set()
        for pair in v:
            key = (pair.left, pair.right)
            if key in seen:
                msg = "join key pairs must be unique"
                raise ValueError(msg)
            seen.add(key)
        return v


# ---------------------------------------------------------------------------
# MetricSpec for Aggregate (§13) – defined here to avoid circular import
# with output.py. Output will re-export it.
# ---------------------------------------------------------------------------


class CountRowsMetric(ContractModel):
    name: Identifier
    function: Literal["count_rows"] = "count_rows"
    description: Description | None = None


class CountMetric(ContractModel):
    name: Identifier
    function: Literal["count"] = "count"
    column: Identifier
    description: Description | None = None


class CountDistinctMetric(ContractModel):
    name: Identifier
    function: Literal["count_distinct"] = "count_distinct"
    column: Identifier
    description: Description | None = None


class SumMetric(ContractModel):
    name: Identifier
    function: Literal["sum"] = "sum"
    column: Identifier
    description: Description | None = None


class AverageMetric(ContractModel):
    name: Identifier
    function: Literal["avg"] = "avg"
    column: Identifier
    description: Description | None = None


class MinimumMetric(ContractModel):
    name: Identifier
    function: Literal["min"] = "min"
    column: Identifier
    description: Description | None = None


class MaximumMetric(ContractModel):
    name: Identifier
    function: Literal["max"] = "max"
    column: Identifier
    description: Description | None = None


class ConditionalCountMetric(ContractModel):
    name: Identifier
    function: Literal["conditional_count"] = "conditional_count"
    condition: Condition
    description: Description | None = None


class ConditionalSumMetric(ContractModel):
    name: Identifier
    function: Literal["conditional_sum"] = "conditional_sum"
    column: Identifier
    condition: Condition
    description: Description | None = None


MetricSpec = Annotated[
    CountRowsMetric
    | CountMetric
    | CountDistinctMetric
    | SumMetric
    | AverageMetric
    | MinimumMetric
    | MaximumMetric
    | ConditionalCountMetric
    | ConditionalSumMetric,
    Field(discriminator="function"),
]

# ---------------------------------------------------------------------------
# Intermediate models (§12.3)
# ---------------------------------------------------------------------------


class TransformIntermediateModel(ContractModel):
    operation: Literal["transform"] = "transform"
    name: Identifier
    source: Identifier
    columns: Annotated[tuple[ProjectionColumn, ...], Field(min_length=1)]
    derived_columns: tuple[DerivedColumn, ...] = ()
    filters: tuple[Condition, ...] = ()
    grain: Annotated[tuple[Identifier, ...], Field(min_length=1)]
    description: Description | None = None

    @field_validator("grain")
    @classmethod
    def _unique_grain(cls, v: tuple[Identifier, ...]) -> tuple[Identifier, ...]:
        if len(v) != len(set(v)):
            msg = "grain must not contain duplicates"
            raise ValueError(msg)
        return v

    @model_validator(mode="after")
    def _unique_output_names(self) -> TransformIntermediateModel:
        # projected targets + derived names must be unique
        seen: set[str] = set()
        for col in self.columns:
            if col.target in seen:
                msg = "output names must be unique (projected and derived)"
                raise ValueError(msg)
            seen.add(col.target)
        for dc in self.derived_columns:
            if dc.name in seen:
                msg = "output names must be unique (projected and derived)"
                raise ValueError(msg)
            seen.add(dc.name)
        return self


class JoinIntermediateModel(ContractModel):
    operation: Literal["join"] = "join"
    name: Identifier
    left: Identifier
    right: Identifier
    join: JoinSpec
    columns: Annotated[tuple[JoinProjectionColumn, ...], Field(min_length=1)]
    derived_columns: tuple[DerivedColumn, ...] = ()
    filters: tuple[Condition, ...] = ()
    grain: Annotated[tuple[Identifier, ...], Field(min_length=1)]
    description: Description | None = None

    @field_validator("grain")
    @classmethod
    def _unique_grain(cls, v: tuple[Identifier, ...]) -> tuple[Identifier, ...]:
        if len(v) != len(set(v)):
            msg = "grain must not contain duplicates"
            raise ValueError(msg)
        return v

    @model_validator(mode="after")
    def _unique_output_names(self) -> JoinIntermediateModel:
        seen: set[str] = set()
        for col in self.columns:
            if col.target in seen:
                msg = "output names must be unique"
                raise ValueError(msg)
            seen.add(col.target)
        for dc in self.derived_columns:
            if dc.name in seen:
                msg = "output names must be unique"
                raise ValueError(msg)
            seen.add(dc.name)
        return self


class AggregateIntermediateModel(ContractModel):
    operation: Literal["aggregate"] = "aggregate"
    name: Identifier
    source: Identifier
    filters: tuple[Condition, ...] = ()
    group_by: Annotated[tuple[ProjectionColumn, ...], Field(min_length=1)]
    metrics: Annotated[tuple[MetricSpec, ...], Field(min_length=1)]
    grain: Annotated[tuple[Identifier, ...], Field(min_length=1)]
    description: Description | None = None

    @field_validator("grain")
    @classmethod
    def _unique_grain(cls, v: tuple[Identifier, ...]) -> tuple[Identifier, ...]:
        if len(v) != len(set(v)):
            msg = "grain must not contain duplicates"
            raise ValueError(msg)
        return v

    @field_validator("group_by")
    @classmethod
    def _unique_group_by_targets(
        cls, v: tuple[ProjectionColumn, ...]
    ) -> tuple[ProjectionColumn, ...]:
        targets = [c.target for c in v]
        if len(targets) != len(set(targets)):
            msg = "group_by targets must be unique"
            raise ValueError(msg)
        sources = [c.source for c in v]
        if len(sources) != len(set(sources)):
            msg = "group_by sources must be unique"
            raise ValueError(msg)
        return v

    @model_validator(mode="after")
    def _check_uniqueness_and_grain(self) -> AggregateIntermediateModel:
        # metric names unique
        metric_names = [m.name for m in self.metrics]
        if len(metric_names) != len(set(metric_names)):
            msg = "metric names must be unique within container"
            raise ValueError(msg)
        # metric names must not collide with group_by targets
        group_targets = {c.target for c in self.group_by}
        overlap = group_targets & set(metric_names)
        if overlap:
            msg = "metric names must not collide with group_by targets"
            raise ValueError(msg)
        # local grain subset check is semantic, but we ensure grain non-empty unique already
        # Full subset validation deferred to semantic (T10)
        return self


class DeduplicateIntermediateModel(ContractModel):
    operation: Literal["deduplicate"] = "deduplicate"
    name: Identifier
    source: Identifier
    keys: Annotated[tuple[Identifier, ...], Field(min_length=1)]
    order_by: Annotated[tuple[SortKey, ...], Field(min_length=1)]
    grain: Annotated[tuple[Identifier, ...], Field(min_length=1)]
    description: Description | None = None

    @field_validator("keys", "grain")
    @classmethod
    def _unique_entries(cls, v: tuple[Identifier, ...]) -> tuple[Identifier, ...]:
        if len(v) != len(set(v)):
            msg = "entries must be unique"
            raise ValueError(msg)
        return v

    @model_validator(mode="after")
    def _grain_equals_keys(self) -> DeduplicateIntermediateModel:
        if set(self.grain) != set(self.keys):
            msg = "grain must equal keys as a set"
            raise ValueError(msg)
        return self


IntermediateModel = Annotated[
    TransformIntermediateModel
    | JoinIntermediateModel
    | AggregateIntermediateModel
    | DeduplicateIntermediateModel,
    Field(discriminator="operation"),
]
