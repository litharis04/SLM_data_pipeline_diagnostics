"""dbt project scaffolding: fixed shell, stub models (GENERATOR_SPEC §§6, 13).

Renders ``dbt_project.yml``, ``profiles.yml``, ``models/sources.yml``,
per-model ``.sql`` stubs, an ``assertions.yml`` placeholder and the
``macros/`` directory. Model SQL bodies arrive in G12–G15 and healthy tests
in G16; stubs carry the correct ``source(``/``ref(`` skeleton so the shell
is ``dbt parse``-able.

Conventions: fixed names from :mod:`physical`; every model materialized
``table`` in schema ``main`` with quoted identifiers; staging uses
``source()``, downstream models use ``ref()``; intermediates render in
``ValidatedScenario.topological_order``, staging/output in declaration
order; file enumeration deterministic. Dispatch is exhaustive over
discriminator fields — an unknown variant raises (never passthrough/omit).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from data_pipeline_diagnostics.generator.physical import (
    DBT_PROFILE_NAME,
    DBT_PROJECT_NAME,
    DBT_TARGET_NAME,
    MAIN_SCHEMA_NAME,
    RAW_SCHEMA_NAME,
    RAW_SOURCE_NAME,
    write_text_file,
)
from data_pipeline_diagnostics.scenario.semantic import ValidatedScenario

__all__ = [
    "RenderedDbtProject",
    "model_dependencies",
    "render_dbt_project",
]


@dataclass(frozen=True)
class RenderedDbtProject:
    """Passive grouping of rendered dbt artifact paths (relative, in order)."""

    scenario_id: str
    project_dir: Path
    files: tuple[str, ...] = ()


def _require_validated(validated: object) -> ValidatedScenario:
    if not isinstance(validated, ValidatedScenario):
        raise TypeError(
            "compiler input must be a ValidatedScenario "
            f"(got {type(validated).__name__}); "
            "use prepare_clean_instance_from_json for JSON/path input"
        )
    return validated


def model_dependencies(model: object) -> tuple[str, ...]:
    """Upstream model/source names for one staging/intermediate/output model.

    Exhaustive over the intermediate ``operation`` discriminator; anything
    else raises ``ValueError``.
    """
    name = getattr(model, "name", None)
    if hasattr(model, "operation"):
        match model.operation:
            case "transform" | "aggregate" | "deduplicate":
                return (str(model.source),)
            case "join":
                return (str(model.left), str(model.right))
            case _:
                raise ValueError(
                    f"model {name!r}: unknown intermediate operation {model.operation!r}"
                )
    if hasattr(model, "source"):
        return (str(model.source),)
    raise ValueError(f"model {name!r}: no source dependency (unknown model variant)")


def _dbt_project_yml() -> str:
    return (
        f"name: {DBT_PROJECT_NAME}\n"
        "version: 1.0.0\n"
        "config-version: 2\n"
        f"profile: {DBT_PROFILE_NAME}\n"
        'model-paths: ["models"]\n'
        'macro-paths: ["macros"]\n'
        "target-path: target\n"
        "log-path: logs\n"
        "models:\n"
        f"  {DBT_PROJECT_NAME}:\n"
        "    +materialization: table\n"
        f"    +schema: {MAIN_SCHEMA_NAME}\n"
    )


def _profiles_yml() -> str:
    return (
        f"{DBT_PROFILE_NAME}:\n"
        f"  target: {DBT_TARGET_NAME}\n"
        "  outputs:\n"
        f"    {DBT_TARGET_NAME}:\n"
        "      type: duckdb\n"
        "      path: ../pipeline.duckdb\n"
        "      threads: 1\n"
    )


def _sources_yml(raw_tables: tuple[str, ...]) -> str:
    lines = [
        "version: 2",
        "",
        "sources:",
        f"  - name: {RAW_SOURCE_NAME}",
        f"    schema: {RAW_SCHEMA_NAME}",
        "    tables:",
    ]
    for table in raw_tables:
        lines.append(f"      - name: {table}")
        lines.append(f"        identifier: {table}")
    return "\n".join(lines) + "\n"


def _assertions_placeholder() -> str:
    return "version: 2\n\nmodels: []\n"


def _staging_stub(model_name: str, raw_table: str) -> str:
    return (
        "{{ config(materialization='table') }}\n"
        "\n"
        f"-- STUB (G12 renders staging model {model_name})\n"
        "SELECT *\n"
        f"FROM {{{{ source('{RAW_SOURCE_NAME}', '{raw_table}') }}}}\n"
    )


def _downstream_stub(model_name: str, upstream: str) -> str:
    return (
        "{{ config(materialization='table') }}\n"
        "\n"
        f"-- STUB (G13-G15 render model {model_name})\n"
        "SELECT *\n"
        f"FROM {{{{ ref('{upstream}') }}}}\n"
    )


def render_dbt_project(validated: ValidatedScenario, destination: str | Path) -> RenderedDbtProject:
    """Render the fixed dbt project shell under ``destination`` (the ``dbt/`` dir)."""
    scenario = _require_validated(validated).scenario
    scenario_id = str(scenario.scenario_id)
    project_dir = Path(destination)

    ordered_intermediates = [
        str(name) for name in validated.topological_order if name in validated.intermediate_by_name
    ]
    by_name = {
        str(m.name): m
        for m in (
            *scenario.staging_models,
            *scenario.intermediate_models,
            *scenario.output_models,
        )
    }

    payloads: list[tuple[str, str]] = [
        ("dbt_project.yml", _dbt_project_yml()),
        ("profiles.yml", _profiles_yml()),
        (
            "models/sources.yml",
            _sources_yml(tuple(str(t.name) for t in scenario.raw_tables)),
        ),
    ]
    for model in scenario.staging_models:
        payloads.append(
            (
                f"models/staging/{model.name}.sql",
                _staging_stub(str(model.name), str(model.source)),
            )
        )
    for name in ordered_intermediates:
        upstream = model_dependencies(by_name[name])[0]
        payloads.append((f"models/intermediate/{name}.sql", _downstream_stub(name, upstream)))
    for model in scenario.output_models:
        upstream = model_dependencies(model)[0]
        payloads.append(
            (f"models/output/{model.name}.sql", _downstream_stub(str(model.name), upstream))
        )
    payloads.append(("models/assertions.yml", _assertions_placeholder()))

    (project_dir / "macros").mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    for relative, text in payloads:
        write_text_file(project_dir / relative, text)
        written.append(relative)
    return RenderedDbtProject(
        scenario_id=scenario_id, project_dir=project_dir, files=tuple(written)
    )
