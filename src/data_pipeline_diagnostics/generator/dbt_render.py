"""dbt project scaffolding: fixed shell, staging + non-aggregate intermediate
models (GENERATOR_SPEC §§6, 13–14).

Renders ``dbt_project.yml``, ``profiles.yml``, ``models/sources.yml``,
per-model ``.sql`` files, an ``assertions.yml`` placeholder and the
``macros/`` directory. Staging models render fully (§§14.1–14.3) and so do
transform/join/deduplicate intermediates (§§14.6–14.7, 14.9); aggregate
intermediates and outputs arrive in G15 and healthy tests in G16 (their
stubs carry the correct ``ref(`` skeleton so the shell stays parse-able).

Conventions: fixed names from :mod:`physical`; every model materialized
``table`` in schema ``main`` with quoted identifiers; staging uses
``source()``, downstream models use ``ref()``; intermediates render in
``ValidatedScenario.topological_order``, staging/output in declaration
order; file enumeration deterministic. Dispatch is exhaustive over
discriminator fields — an unknown variant raises (never passthrough/omit).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
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
    render_expression,
    render_staging_column,
)
from data_pipeline_diagnostics.scenario.expressions import Expression
from data_pipeline_diagnostics.scenario.intermediate import IntermediateModel
from data_pipeline_diagnostics.scenario.semantic import ValidatedScenario
from data_pipeline_diagnostics.scenario.staging import StagingModel

__all__ = [
    "RenderedDbtProject",
    "model_dependencies",
    "model_output_columns",
    "render_dbt_project",
    "render_deduplicate_sql",
    "render_intermediate_sql",
    "render_join_sql",
    "render_staging_sql",
    "render_transform_sql",
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


def render_staging_sql(model: StagingModel, source_columns: Sequence[str]) -> str:
    """Render one staging model: source CTE → MATERIALIZED column phase →
    one row-set boundary per row operation → ordered output. Every SELECT
    list is explicit (no wildcards); ``source_columns`` are the raw table's
    columns in declaration order."""
    name = str(model.name)
    base_list = ", ".join(quote_ident(str(col)) for col in source_columns)
    targets = [quote_ident(str(col.target)) for col in model.columns]
    phases = [
        f"{quote_ident('base')} AS (\n"
        f"    SELECT {base_list} FROM {{{{ source('{RAW_SOURCE_NAME}', '{model.source}') }}}}"
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
                f"    SELECT {', '.join(targets)} FROM {quote_ident(previous)}\n"
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
                f"    SELECT {', '.join(targets)} FROM ("
                f"SELECT {', '.join(targets)}, "
                f"row_number() OVER (PARTITION BY {keys} ORDER BY {ordering}) "
                f"AS {quote_ident(HELPER_ROW_NUMBER)} FROM {quote_ident(previous)})\n"
                f"    WHERE {quote_ident(HELPER_ROW_NUMBER)} = 1\n)"
            )
        else:
            raise ValueError(f"model {name!r}: unknown row operation {operation.op!r}")
        previous = phase
    return (
        "{{ config(materialization='table') }}\n"
        "\n"
        "WITH " + ",\n".join(phases) + "\n"
        f"SELECT {', '.join(targets)} FROM {quote_ident(previous)}\n"
    )


def _expression_columns(expression: Expression, *, into: set[str]) -> None:
    """Collect ``column`` references of one expression tree."""
    match expression.kind:
        case "column":
            into.add(str(expression.column))
        case "literal":
            return
        case "binary":
            _expression_columns(expression.left, into=into)
            _expression_columns(expression.right, into=into)
        case "date_part":
            _expression_columns(expression.value, into=into)
        case "coalesce":
            for value in expression.values:
                _expression_columns(value, into=into)
        case _:
            raise ValueError(f"unknown expression kind: {expression.kind!r}")


def _condition_columns(condition: object, *, into: set[str]) -> None:
    """Collect ``column`` references of one condition tree."""
    match condition.kind:
        case "comparison":
            _expression_columns(condition.left, into=into)
            _expression_columns(condition.right, into=into)
        case "in" | "is_null":
            _expression_columns(condition.value, into=into)
        case "all" | "any":
            for child in condition.conditions:
                _condition_columns(child, into=into)
        case "not":
            _condition_columns(condition.condition, into=into)
        case _:
            raise ValueError(f"unknown condition kind: {condition.kind!r}")


def _check_namespace(model: str, *, derived: object, filters: object, projected: object) -> None:
    """Defensive namespace check: derived expressions see projected targets
    only (never sibling derived aliases); filters see projected + derived.
    The semantic layer owns this invariant; the renderer asserts it."""
    projected_names = {str(name) for name in projected}
    for column in derived:
        refs: set[str] = set()
        _expression_columns(column.expression, into=refs)
        unknown = refs - projected_names
        if unknown:
            raise ValueError(
                f"model {model!r}: derived column {column.name!r} "
                f"references non-projected {sorted(unknown)}"
            )
    allowed = projected_names | {str(column.name) for column in derived}
    for condition in filters:
        refs = set()
        _condition_columns(condition, into=refs)
        unknown = refs - allowed
        if unknown:
            raise ValueError(f"model {model!r}: filter references unknown {sorted(unknown)}")


def _filtered_tail(
    phases: list[str], previous: str, filters: object, output: Sequence[str]
) -> tuple[list[str], str]:
    if not filters:
        return phases, previous
    phase = "filtered"
    phases.append(
        f"{quote_ident(phase)} AS (\n"
        f"    SELECT {', '.join(output)} FROM {quote_ident(previous)}\n"
        f"    WHERE {' AND '.join(render_condition(c) for c in filters)}\n)"
    )
    return phases, phase


def render_transform_sql(model: object, source_columns: Sequence[str]) -> str:
    """Transform: source → projection/renames → derived (one CTE) → filters."""
    name = str(model.name)
    projected = [str(col.target) for col in model.columns]
    _check_namespace(
        model=name, derived=model.derived_columns, filters=model.filters, projected=projected
    )
    source_list = ", ".join(quote_ident(str(col)) for col in source_columns)
    projection = ",\n".join(
        f"        {quote_ident(str(col.source))} AS {quote_ident(str(col.target))}"
        for col in model.columns
    )
    projected_list = ", ".join(quote_ident(col) for col in projected)
    phases = [
        f"{quote_ident('source')} AS (\n"
        f"    SELECT {source_list} FROM {{{{ ref('{model.source}') }}}}"
        f"\n)",
        materialized_cte("projected", f"SELECT\n{projection}\n    FROM {quote_ident('source')}"),
    ]
    previous = "projected"
    if model.derived_columns:
        derived = ",\n".join(
            f"        {render_expression(col.expression)} AS {quote_ident(str(col.name))}"
            for col in model.derived_columns
        )
        phases.append(
            materialized_cte(
                "derived",
                f"SELECT {projected_list},\n{derived}\n    FROM {quote_ident('projected')}",
            )
        )
        previous = "derived"
    output = [quote_ident(col) for col in projected + [str(c.name) for c in model.derived_columns]]
    phases, previous = _filtered_tail(phases, previous, model.filters, output)
    targets = ", ".join(output)
    return (
        "{{ config(materialization='table') }}\n"
        "\n"
        "WITH " + ",\n".join(phases) + "\n"
        f"SELECT {targets} FROM {quote_ident(previous)}\n"
    )


def render_join_sql(
    model: object, left_columns: Sequence[str], right_columns: Sequence[str]
) -> str:
    """Join: refs → INNER/LEFT equality on all ordered pairs → explicit
    side-qualified projection → derived → filters. Nothing implicit."""
    name = str(model.name)
    projected = [str(col.target) for col in model.columns]
    _check_namespace(
        model=name, derived=model.derived_columns, filters=model.filters, projected=projected
    )
    if model.join.type == "inner":
        join_keyword = "INNER JOIN"
    elif model.join.type == "left":
        join_keyword = "LEFT JOIN"
    else:
        raise ValueError(f"model {name!r}: unknown join type {model.join.type!r}")
    on_clause = " AND ".join(
        f"{quote_ident('left')}.{quote_ident(str(pair.left))} = "
        f"{quote_ident('right')}.{quote_ident(str(pair.right))}"
        for pair in model.join.on
    )
    projection = ",\n".join(
        f"        {quote_ident(str(col.side))}.{quote_ident(str(col.source))} "
        f"AS {quote_ident(str(col.target))}"
        for col in model.columns
    )
    left_list = ", ".join(quote_ident(str(col)) for col in left_columns)
    right_list = ", ".join(quote_ident(str(col)) for col in right_columns)
    projected_list = ", ".join(quote_ident(col) for col in projected)
    phases = [
        f"{quote_ident('left')} AS (\n    SELECT {left_list} FROM {{{{ ref('{model.left}') }}}}\n)",
        f"{quote_ident('right')} AS (\n"
        f"    SELECT {right_list} FROM {{{{ ref('{model.right}') }}}}"
        f"\n)",
        materialized_cte(
            "joined",
            f"SELECT\n{projection}\n"
            f"    FROM {quote_ident('left')} {join_keyword} {quote_ident('right')} "
            f"ON {on_clause}",
        ),
    ]
    previous = "joined"
    if model.derived_columns:
        derived = ",\n".join(
            f"        {render_expression(col.expression)} AS {quote_ident(str(col.name))}"
            for col in model.derived_columns
        )
        phases.append(
            materialized_cte(
                "derived",
                f"SELECT {projected_list},\n{derived}\n    FROM {quote_ident('joined')}",
            )
        )
        previous = "derived"
    output = [quote_ident(col) for col in projected + [str(c.name) for c in model.derived_columns]]
    phases, previous = _filtered_tail(phases, previous, model.filters, output)
    targets = ", ".join(output)
    return (
        "{{ config(materialization='table') }}\n"
        "\n"
        "WITH " + ",\n".join(phases) + "\n"
        f"SELECT {targets} FROM {quote_ident(previous)}\n"
    )


def render_deduplicate_sql(model: object, source_columns: Sequence[str]) -> str:
    """Deduplicate: ref source, rank exactly as §14.3, keep all source
    columns in unchanged order minus the helper."""
    ordering = ", ".join(
        f"{quote_ident(str(term.column))} {'ASC' if term.direction == 'asc' else 'DESC'} NULLS LAST"
        for term in model.order_by
    )
    keys = ", ".join(quote_ident(str(key)) for key in model.keys)
    targets = ", ".join(quote_ident(str(col)) for col in source_columns)
    return (
        "{{ config(materialization='table') }}\n"
        "\n"
        f"WITH {quote_ident('source')} AS (\n"
        f"    SELECT {targets} FROM {{{{ ref('{model.source}') }}}}"
        f"\n),\n"
        + materialized_cte(
            "ranked",
            f"SELECT {targets}, "
            f"row_number() OVER (PARTITION BY {keys} ORDER BY {ordering}) "
            f"AS {quote_ident(HELPER_ROW_NUMBER)} FROM {quote_ident('source')}",
        )
        + f"\nSELECT {targets} FROM {quote_ident('ranked')} "
        f"WHERE {quote_ident(HELPER_ROW_NUMBER)} = 1\n"
    )


def render_intermediate_sql(
    model: IntermediateModel, output_columns: Mapping[str, Sequence[str]]
) -> str:
    """Dispatch one intermediate model (exhaustive; aggregates land in G15).

    ``output_columns`` maps every upstream model name to its output columns
    in order, so source CTEs stay wildcard-free.
    """
    match model.operation:
        case "transform":
            return render_transform_sql(model, output_columns[str(model.source)])
        case "join":
            return render_join_sql(
                model,
                output_columns[str(model.left)],
                output_columns[str(model.right)],
            )
        case "deduplicate":
            return render_deduplicate_sql(model, output_columns[str(model.source)])
        case "aggregate":
            raise ValueError(f"model {model.name!r}: aggregate models render in G15")
        case _:
            raise ValueError(f"model {model.name!r}: unknown operation {model.operation!r}")


def model_output_columns(
    model: object, by_name: dict[str, object], memo: dict[str, tuple[str, ...]]
) -> tuple[str, ...]:
    """Output column names of one staging/intermediate model in order."""
    name = str(model.name)
    if name in memo:
        return memo[name]
    if hasattr(model, "columns") and not hasattr(model, "operation"):
        memo[name] = tuple(str(col.target) for col in model.columns)
    elif getattr(model, "operation", None) in ("transform", "join"):
        memo[name] = tuple(str(col.target) for col in model.columns) + tuple(
            str(col.name) for col in model.derived_columns
        )
    elif getattr(model, "operation", None) == "aggregate":
        memo[name] = tuple(str(g.target) for g in model.group_by) + tuple(
            str(m.name) for m in model.metrics
        )
    elif getattr(model, "operation", None) == "deduplicate":
        memo[name] = model_output_columns(by_name[str(model.source)], by_name, memo)
    else:
        raise ValueError(f"model {name!r}: unknown model variant")
    return memo[name]


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
    raw_columns = {str(t.name): tuple(str(c.name) for c in t.columns) for t in scenario.raw_tables}
    for model in scenario.staging_models:
        payloads.append(
            (
                f"models/staging/{model.name}.sql",
                render_staging_sql(model, raw_columns[str(model.source)]),
            )
        )
    memo: dict[str, tuple[str, ...]] = {}
    for name in ordered_intermediates:
        model = by_name[name]
        for upstream in model_dependencies(model):
            if upstream in by_name:
                model_output_columns(by_name[upstream], by_name, memo)
        payloads.append((f"models/intermediate/{name}.sql", render_intermediate_sql(model, memo)))
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
