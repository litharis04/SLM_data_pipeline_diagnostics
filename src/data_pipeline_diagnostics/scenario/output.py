"""Output layer and metrics.

Implements ``docs/SCENARIO_SPEC.md`` §13–§14.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from data_pipeline_diagnostics.scenario.base import ContractModel
from data_pipeline_diagnostics.scenario.expressions import Condition
from data_pipeline_diagnostics.scenario.intermediate import ProjectionColumn
from data_pipeline_diagnostics.scenario.types import Description, Identifier

# ---------------------------------------------------------------------------
# MetricSpec (§13) – discriminated by ``function``
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
# OutputModel (§14)
# ---------------------------------------------------------------------------


class OutputModel(ContractModel):
    name: Identifier
    source: Identifier
    filters: tuple[Condition, ...] = ()
    group_by: Annotated[tuple[ProjectionColumn, ...], Field(min_length=1)]
    grain: Annotated[tuple[Identifier, ...], Field(min_length=1)]
    dimensions: tuple[Identifier, ...] = ()
    metrics: Annotated[tuple[MetricSpec, ...], Field(min_length=1)]
    description: Description | None = None

    @field_validator("group_by")
    @classmethod
    def _unique_group_by(cls, v: tuple[ProjectionColumn, ...]) -> tuple[ProjectionColumn, ...]:
        targets = [c.target for c in v]
        if len(targets) != len(set(targets)):
            msg = "group_by targets must be unique"
            raise ValueError(msg)
        sources = [c.source for c in v]
        if len(sources) != len(set(sources)):
            msg = "group_by sources must be unique"
            raise ValueError(msg)
        return v

    @field_validator("grain")
    @classmethod
    def _unique_grain(cls, v: tuple[Identifier, ...]) -> tuple[Identifier, ...]:
        if len(v) != len(set(v)):
            msg = "grain must not contain duplicates"
            raise ValueError(msg)
        return v

    @field_validator("dimensions")
    @classmethod
    def _unique_dimensions(cls, v: tuple[Identifier, ...]) -> tuple[Identifier, ...]:
        if len(v) != len(set(v)):
            msg = "dimensions must not contain duplicates"
            raise ValueError(msg)
        return v

    @model_validator(mode="after")
    def _check_output_names(self) -> OutputModel:
        # metric names unique
        metric_names = [m.name for m in self.metrics]
        if len(metric_names) != len(set(metric_names)):
            msg = "metric names must be unique within container"
            raise ValueError(msg)
        group_targets = [c.target for c in self.group_by]
        # metric names must not collide with group_by targets
        overlap = set(group_targets) & set(metric_names)
        if overlap:
            msg = "metric names must not collide with group_by targets"
            raise ValueError(msg)
        # overall output names uniqueness: group_by targets ∪ metric names already checked via above two,
        # but also need to ensure group_by targets unique (already) and metric names unique, and no overlap
        # dimensions are not part of output names? They are analytical dimension names in output, but spec says
        # metrics names unique and output uniqueness (group_by targets ∪ metric names) – local. Dimensions are subset of group_by? Not needed for local.
        return self
