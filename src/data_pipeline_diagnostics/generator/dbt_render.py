"""dbt project scaffolding: fixed shell, staging models (GENERATOR_SPEC §§6, 13–14).

Renders ``dbt_project.yml``, ``profiles.yml``, ``models/sources.yml``,
per-model ``.sql`` files, an ``assertions.yml`` placeholder and the
``macros/`` directory. Staging models render fully (§§14.1–14.3: quoted raw
source → column pipelines in a ``MATERIALIZED`` phase → one row-set
boundary per row operation in declared order → output in
``StagingModel.columns`` order). Intermediate/output bodies arrive in
G14–G15 and healthy tests in G16; their stubs carry the correct
``ref(`` skeleton so the shell stays ``dbt parse``-able.

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
    quote_ident,
    write_text_file,
)
from data_pipeline_diagnostics.generator.sql_render import (
    HELPER_ROW_NUMBER,
    materialized_cte,
    render_condition,
    render_staging_column,
)
from data_pipeline_diagnostics.scenario.semantic import ValidatedScenario
from data_pipeline_diagnostics.scenario.staging import StagingModel

__all__ = [
    "RenderedDbtProject",
    "model_dependencies",
    "render_dbt_project",
    "render_staging_sql",
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


def _downstream_stub(model_name: str, upstream: str) -> str:
    return (
        "{{ config(materialization='table') }}\n"
        "\n"
        f"-- STUB (G14-G15 render model {model_name})\n"
        "SELECT *\n"
        f"FROM {{{{ ref('{upstream}') }}}}\n"
    )


def render_staging_sql(model: StagingModel) -> str:
    """Render one staging model: source CTE → MATERIALIZED column phase →
    one row-set boundary per row operation → ordered output."""
    name = str(model.name)
    phases = [
        f"{quote_ident('base')} AS (\n"
        f"    SELECT * FROM {{{{ source('{RAW_SOURCE_NAME}', '{model.source}') }}}}"
        f"\n)",
        materialized_cte(
            "columns",
            "SELECT\n"
            + ",\n".join(
                "        " + render_staging_column(quote_ident(str(col.source)), col, model=name)
                for col in model.columns
            )
            + f"\n    FROM {quote_ident('base')}",
        ),
    ]
    previous = "columns"
    for index, operation in enumerate(model.row_operations):
        phase = f"row_{index}"
        if operation.op == "filter":
            phases.append(
                f"{quote_ident(phase)} AS (\n"
                f"    SELECT * FROM {quote_ident(previous)}\n"
                f"    WHERE {render_condition(operation.condition)}\n)"
            )
        elif operation.op == "deduplicate":
            keys = ", ".join(quote_ident(str(k)) for k in operation.keys)
            ordering = ", ".join(
                f"{quote_ident(str(term.column))} "
                f"{'ASC' if term.direction == 'asc' else 'DESC'} NULLS LAST"
                for term in operation.order_by
            )
            phases.append(
                f"{quote_ident(phase)} AS (\n"
                f"    SELECT * FROM (SELECT {quote_ident(previous)}.*, "
                f"row_number() OVER (PARTITION BY {keys} ORDER BY {ordering}) "
                f"AS {quote_ident(HELPER_ROW_NUMBER)} FROM {quote_ident(previous)})\n"
                f"    WHERE {quote_ident(HELPER_ROW_NUMBER)} = 1\n)"
            )
        else:
            raise ValueError(f"model {name!r}: unknown row operation {operation.op!r}")
        previous = phase
    targets = ", ".join(quote_ident(str(col.target)) for col in model.columns)
    return (
        "{{ config(materialization='table') }}\n"
        "\n"
        "WITH " + ",\n".join(phases) + "\n"
        f"SELECT {targets} FROM {quote_ident(previous)}\n"
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
        payloads.append((f"models/staging/{model.name}.sql", render_staging_sql(model)))
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
