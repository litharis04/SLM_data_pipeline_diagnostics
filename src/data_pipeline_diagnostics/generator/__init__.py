"""Generator package — validated-scenario compiler boundary (GENERATOR_SPEC §§3–4).

Public API surface with ``ValidatedScenario``-only entry points; every entry
point enforces the boundary and the ``data_seed`` contract. ``RawPlan`` and
``build_raw_plan`` live in :mod:`raw_plan` (G08), ``RenderedDbtProject`` and
``render_dbt_project`` in :mod:`dbt_render` (G11–G16), and the cache-aware
``CleanInstance``/``prepare_clean_instance`` in :mod:`cache` (G19).
"""

from __future__ import annotations

from pathlib import Path

from data_pipeline_diagnostics.generator.cache import (
    CleanInstance,
    prepare_clean_instance,
)
from data_pipeline_diagnostics.generator.dbt_render import (
    RenderedDbtProject,
    render_dbt_project,
)
from data_pipeline_diagnostics.generator.raw_plan import RawPlan, build_raw_plan
from data_pipeline_diagnostics.scenario.parsing import (
    parse_scenario_file,
    parse_scenario_json,
)
from data_pipeline_diagnostics.scenario.semantic import validate_semantics

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
