"""Generator package — validated-scenario compiler boundary (GENERATOR_SPEC §§3–4).

Public API surface with ``ValidatedScenario``-only entry points; every entry
point enforces the boundary and the ``data_seed`` contract. ``RawPlan`` and
``build_raw_plan`` are implemented in :mod:`raw_plan` (G08); the dbt and
clean-instance surfaces follow in later tasks.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from data_pipeline_diagnostics.generator.raw_plan import RawPlan, build_raw_plan
from data_pipeline_diagnostics.scenario.parsing import (
    parse_scenario_file,
    parse_scenario_json,
)
from data_pipeline_diagnostics.scenario.semantic import (
    ValidatedScenario,
    validate_semantics,
)

__all__ = [
    "CleanInstance",
    "MAX_DATA_SEED",
    "MIN_DATA_SEED",
    "RawPlan",
    "RenderedDbtProject",
    "build_raw_plan",
    "prepare_clean_instance",
    "prepare_clean_instance_from_json",
    "render_dbt_project",
]

MIN_DATA_SEED = 0
MAX_DATA_SEED = 2**63 - 1


@dataclass(frozen=True)
class RenderedDbtProject:
    """Placeholder grouping of rendered dbt artifact locations (grows in G11)."""

    scenario_id: str
    destination: Path


@dataclass(frozen=True)
class CleanInstance:
    """Placeholder clean-baseline handle (grows in G17)."""

    scenario_id: str
    data_seed: int
    cache_root: Path


def _require_validated(validated: object) -> ValidatedScenario:
    """Reject bare Scenario/dict/path; accept only ValidatedScenario."""
    if not isinstance(validated, ValidatedScenario):
        raise TypeError(
            "compiler input must be a ValidatedScenario "
            f"(got {type(validated).__name__}); "
            "use prepare_clean_instance_from_json for JSON/path input"
        )
    return validated


def _validate_seed(data_seed: object) -> int:
    """Strict data_seed check: int (excluding bool), 0 <= seed <= 2**63 - 1."""
    if type(data_seed) is not int:
        raise ValueError(f"data_seed must be a strict int, got {type(data_seed).__name__}")
    if not MIN_DATA_SEED <= data_seed <= MAX_DATA_SEED:
        raise ValueError(f"data_seed {data_seed} out of range [0, 2**63 - 1]")
    return data_seed


def render_dbt_project(validated: ValidatedScenario, destination: str | Path) -> RenderedDbtProject:
    """Render the dbt project skeleton (stub: G11 fills logic)."""
    scenario = _require_validated(validated).scenario
    return RenderedDbtProject(scenario_id=str(scenario.scenario_id), destination=Path(destination))


def prepare_clean_instance(
    validated: ValidatedScenario,
    data_seed: int,
    cache_root: str | Path,
) -> CleanInstance:
    """Prepare the clean baseline instance (stub: G17 fills logic)."""
    scenario = _require_validated(validated).scenario
    seed = _validate_seed(data_seed)
    return CleanInstance(
        scenario_id=str(scenario.scenario_id),
        data_seed=seed,
        cache_root=Path(cache_root),
    )


def prepare_clean_instance_from_json(
    source: str | Path | bytes,
    data_seed: int,
    cache_root: str | Path,
) -> CleanInstance:
    """Facade: parse JSON/path -> validate -> prepare_clean_instance.

    Accepts UTF-8 JSON bytes/str content or a filesystem path. Runs the exact
    chain ``parse_scenario_json -> validate_semantics ->
    prepare_clean_instance`` before any compiler code.
    """
    if isinstance(source, bytes):
        scenario = parse_scenario_json(source)
    elif isinstance(source, Path):
        scenario = parse_scenario_file(source)
    elif isinstance(source, str):
        candidate = Path(source)
        if candidate.exists() and candidate.is_file():
            scenario = parse_scenario_file(candidate)
        else:
            scenario = parse_scenario_json(source)
    else:
        raise TypeError(f"unsupported scenario source type: {type(source).__name__}")
    validated = validate_semantics(scenario)
    return prepare_clean_instance(validated, data_seed, cache_root)
