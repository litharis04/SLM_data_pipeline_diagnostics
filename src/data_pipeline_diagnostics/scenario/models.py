"""Root Scenario model — contract composition (§6)."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from data_pipeline_diagnostics.scenario.assertions import HealthyAssertion
from data_pipeline_diagnostics.scenario.base import ContractModel
from data_pipeline_diagnostics.scenario.intermediate import IntermediateModel
from data_pipeline_diagnostics.scenario.output import OutputModel
from data_pipeline_diagnostics.scenario.raw import RawTable
from data_pipeline_diagnostics.scenario.relationships import RelationshipSpec
from data_pipeline_diagnostics.scenario.staging import StagingModel
from data_pipeline_diagnostics.scenario.types import Description, DomainName, ScenarioId


class Scenario(ContractModel):
    schema_version: Literal["1.0"]
    scenario_id: ScenarioId
    domain: DomainName
    description: Description | None = None
    raw_tables: Annotated[tuple[RawTable, ...], Field(min_length=3, max_length=4)]
    relationships: Annotated[tuple[RelationshipSpec, ...], Field(min_length=1)]
    staging_models: Annotated[tuple[StagingModel, ...], Field(min_length=3, max_length=4)]
    intermediate_models: Annotated[tuple[IntermediateModel, ...], Field(min_length=2, max_length=3)]
    output_models: Annotated[tuple[OutputModel, ...], Field(min_length=1, max_length=2)]
    tests: tuple[HealthyAssertion, ...] = ()
