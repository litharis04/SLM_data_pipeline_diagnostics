"""dbt project scaffolding: fixed shell, staging + non-aggregate intermediate
models (GENERATOR_SPEC §§6, 13–14).

Renders ``dbt_project.yml``, ``profiles.yml``, ``models/sources.yml``,
per-model ``.sql`` files, an ``assertions.yml`` placeholder and the
``macros/`` directory. Staging models render fully (§§14.1–14.3),
transform/join/deduplicate intermediates render fully (§§14.6–14.7, 14.9),
and aggregate intermediates and outputs render fully (§14.8); healthy tests
arrive in G16.

Conventions: fixed names from :mod:`physical`; every model materialized
``table`` in schema ``main`` with quoted identifiers; staging uses
``source()``, downstream models use ``ref()``; intermediates render in
``ValidatedScenario.topological_order``, staging/output in declaration
order; file enumeration deterministic. Dispatch is exhaustive over
discriminator fields — an unknown variant raises (never passthrough/omit).
"""

from __future__ import annotations

import hashlib
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
    yaml_scalar,
    yaml_string,
)
from data_pipeline_diagnostics.generator.sql_render import (
    HELPER_ROW_NUMBER,
    materialized_cte,
    render_condition,
    render_expression,
    render_staging_column,
    typed_scalar_literal,
)
from data_pipeline_diagnostics.scenario.expressions import Expression
from data_pipeline_diagnostics.scenario.intermediate import IntermediateModel
from data_pipeline_diagnostics.scenario.semantic import ValidatedScenario
from data_pipeline_diagnostics.scenario.staging import StagingModel

__all__ = [
    "LogicalAssertion",
    "RenderedDbtProject",
    "collect_assertions",
    "model_dependencies",
    "model_output_columns",
    "render_aggregate_sql",
    "render_assertion_macros",
    "render_assertions_yml",
    "render_dbt_project",
    "render_deduplicate_sql",
    "render_intermediate_sql",
    "render_join_sql",
    "render_metric",
    "render_output_sql",
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
    """Dispatch one intermediate model (exhaustive over ``operation``).

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
            return render_aggregate_sql(model, output_columns[str(model.source)])
        case _:
            raise ValueError(f"model {model.name!r}: unknown operation {model.operation!r}")


def render_metric(metric: object) -> str:
    """One metric expression with its alias, exactly per the §14.8 table."""
    name = quote_ident(str(metric.name))
    match metric.function:
        case "count_rows":
            return f"COUNT(*) AS {name}"
        case "count":
            return f"COUNT({quote_ident(str(metric.column))}) AS {name}"
        case "count_distinct":
            return f"COUNT(DISTINCT {quote_ident(str(metric.column))}) AS {name}"
        case "sum":
            return f"CAST(SUM({quote_ident(str(metric.column))}) AS DOUBLE) AS {name}"
        case "avg":
            return f"CAST(AVG({quote_ident(str(metric.column))}) AS DOUBLE) AS {name}"
        case "min":
            return f"MIN({quote_ident(str(metric.column))}) AS {name}"
        case "max":
            return f"MAX({quote_ident(str(metric.column))}) AS {name}"
        case "conditional_count":
            return f"COUNT(*) FILTER (WHERE {render_condition(metric.condition)}) AS {name}"
        case "conditional_sum":
            return (
                f"CAST(SUM({quote_ident(str(metric.column))}) "
                f"FILTER (WHERE {render_condition(metric.condition)}) AS DOUBLE) AS {name}"
            )
        case _:
            raise ValueError(f"metric {metric.name!r}: unknown function {metric.function!r}")


def _check_grouped_namespace(
    model_name: str, group_by: object, metrics: object, filters: object, source_columns: object
) -> None:
    """Defensive check: group sources, metric columns and every condition
    reference source columns only (the semantic layer owns this invariant)."""
    available = {str(col) for col in source_columns}
    refs: set[str] = set()
    for entry in group_by:
        refs.add(str(entry.source))
    for metric in metrics:
        if getattr(metric, "column", None) is not None:
            refs.add(str(metric.column))
        if getattr(metric, "condition", None) is not None:
            _condition_columns(metric.condition, into=refs)
    for condition in filters:
        _condition_columns(condition, into=refs)
    unknown = refs - available
    if unknown:
        raise ValueError(f"model {model_name!r}: references unknown {sorted(unknown)}")


def _render_grouped(
    *,
    model_name: str,
    source_ref: str,
    source_columns: Sequence[str],
    group_by: object,
    metrics: object,
    filters: object,
) -> str:
    """Shared aggregate/output shape: filters pre-aggregation (conjunction,
    TRUE-only), then grouping by every declared source with target aliases
    and metrics in declaration order. ``grain``/``dimensions`` are metadata
    only and never change this SQL."""
    _check_grouped_namespace(model_name, group_by, metrics, filters, source_columns)
    group_selects = [
        f"{quote_ident(str(entry.source))} AS {quote_ident(str(entry.target))}"
        for entry in group_by
    ]
    group_keys = ", ".join(quote_ident(str(entry.source)) for entry in group_by)
    metric_selects = [render_metric(metric) for metric in metrics]
    select_list = ",\n".join(f"        {item}" for item in (*group_selects, *metric_selects))
    passthrough = ", ".join(quote_ident(str(col)) for col in source_columns)
    phases = [
        f"{quote_ident('source')} AS (\n"
        f"    SELECT {passthrough} FROM {{{{ ref('{source_ref}') }}}}"
        f"\n)"
    ]
    previous = "source"
    if filters:
        where = " AND ".join(render_condition(c) for c in filters)
        phases.append(
            f"{quote_ident('filtered')} AS (\n"
            f"    SELECT {passthrough} FROM {quote_ident('source')}\n"
            f"    WHERE {where}\n)"
        )
        previous = "filtered"
    return (
        "{{ config(materialization='table') }}\n"
        "\n"
        "WITH " + ",\n".join(phases) + "\n"
        f"SELECT\n{select_list}\n"
        f"    FROM {quote_ident(previous)}\n"
        f"    GROUP BY {group_keys}\n"
    )


def render_aggregate_sql(model: object, source_columns: Sequence[str]) -> str:
    """Aggregate intermediate: filters pre-aggregation, then grouping."""
    return _render_grouped(
        model_name=str(model.name),
        source_ref=str(model.source),
        source_columns=source_columns,
        group_by=model.group_by,
        metrics=model.metrics,
        filters=model.filters,
    )


def render_output_sql(model: object, source_columns: Sequence[str]) -> str:
    """Output model: same grouped shape over exactly one intermediate source."""
    return _render_grouped(
        model_name=str(model.name),
        source_ref=str(model.source),
        source_columns=source_columns,
        group_by=model.group_by,
        metrics=model.metrics,
        filters=model.filters,
    )


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

    return memo[name]


# ---------------------------------------------------------------------------
# Healthy-assertion lowering (§15)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LogicalAssertion:
    """One assertion with traceable origin (explicit author vs derived)."""

    name: str
    origin: str  # "explicit" | "derived"
    type: str
    model: str
    columns: tuple[str, ...] = ()
    column: str | None = None
    values: tuple[object, ...] = ()
    to_model: str | None = None
    to_columns: tuple[str, ...] = ()
    min: object = None
    max: object = None
    inclusive: bool = True

    def identity(self) -> tuple:
        """Effective semantic identity for deduplication (bounds/values/order
        significant; origin and logical name are not)."""
        if self.type in ("not_null", "unique"):
            return (self.model, self.type, self.columns)
        if self.type == "accepted_values":
            return (self.model, self.type, self.column, self.values)
        if self.type == "relationships":
            return (self.model, self.type, self.columns, self.to_model, self.to_columns)
        if self.type == "row_count":
            return (self.model, self.type, self.min, self.max)
        if self.type == "column_range":
            return (self.model, self.type, self.column, self.min, self.max, self.inclusive)
        raise ValueError(f"unknown assertion type: {self.type!r}")


def _logical_from_explicit(assertion: object) -> LogicalAssertion:
    return LogicalAssertion(
        name=str(assertion.name),
        origin="explicit",
        type=str(assertion.type),
        model=str(assertion.model),
        columns=tuple(str(c) for c in getattr(assertion, "columns", ())),
        column=_optional_str(getattr(assertion, "column", None)),
        values=tuple(getattr(assertion, "values", ())),
        to_model=_optional_str(getattr(assertion, "to_model", None)),
        to_columns=tuple(str(c) for c in getattr(assertion, "to_columns", ())),
        min=getattr(assertion, "min", None),
        max=getattr(assertion, "max", None),
        inclusive=bool(getattr(assertion, "inclusive", True)),
    )


def _logical_from_derived(record: Mapping[str, object]) -> LogicalAssertion:
    columns = record.get("columns", ())
    to_columns = record.get("to_columns", ())
    return LogicalAssertion(
        name=str(record["name"]),
        origin="derived",
        type=str(record["type"]),
        model=str(record["model"]),
        columns=tuple(str(c) for c in columns),
        column=None,
        values=(),
        to_model=_optional_str(record.get("to_model")),
        to_columns=tuple(str(c) for c in to_columns),
        min=record.get("min"),
        max=record.get("max"),
        inclusive=True,
    )


def _optional_str(value: object) -> str | None:
    return None if value is None else str(value)


def _deduplicate(logicals: Sequence[LogicalAssertion]) -> list[LogicalAssertion]:
    """First occurrence wins per semantic identity (explicit lists precede
    derived facts at the call site); origin stays traceable on survivors."""
    collected: list[LogicalAssertion] = []
    seen: set[tuple] = set()
    for logical in logicals:
        key = logical.identity()
        if key in seen:
            continue
        seen.add(key)
        collected.append(logical)
    return collected


def collect_assertions(validated: ValidatedScenario) -> list[LogicalAssertion]:
    """Explicit + derived assertions deduplicated by semantic identity."""
    scenario = _require_validated(validated).scenario
    explicit = [_logical_from_explicit(a) for a in scenario.tests]
    derived = [_logical_from_derived(record) for record in validated.derived_assertions]
    return _deduplicate([*explicit, *derived])


def _physical_name(assertion_type: str, logical: str, *roles: str) -> str:
    """Deterministic physical test name from logical name + component roles
    (stable hash suffix when long — no naming policy)."""
    base = "__".join((assertion_type, logical, *roles)) if roles else f"{assertion_type}__{logical}"
    if len(base) <= 64:
        return base
    digest = hashlib.sha256(base.encode("utf-8")).hexdigest()[:12]
    return base[: 64 - 13] + "_" + digest


def _target_ref(model: str, raw_tables: set[str]) -> str:
    if model in raw_tables:
        return f"source('{RAW_SOURCE_NAME}', '{model}')"
    return f"ref('{model}')"


def _yaml_ident_list(columns: Sequence[str]) -> str:
    """Flow list of double-quoted quoted-identifiers: ['"a"', '"b"']."""
    return "[" + ", ".join(f"'{quote_ident(str(col))}'" for col in columns) + "]"


def _emit_test(lines: list[str], test: str, args: list[str], physical: str) -> None:
    lines.append(f"          - {test}:")
    for arg in args:
        lines.append(f"              {arg}")
    lines.append(f"              name: {physical}")
    lines.append("              severity: error")


def render_assertions_yml(
    assertions: Sequence[LogicalAssertion],
    *,
    raw_tables: Sequence[str],
    staging: Sequence[str],
    intermediates: Sequence[str],
    outputs: Sequence[str],
) -> str:
    """Lower assertions to dbt generic tests (§15.2 table).

    Raw-table assertions attach to ``source()``; generated models attach to
    their ``ref()``. One-column ``not_null``/``unique``/``relationships``
    use dbt built-ins; composite/``row_count``/``column_range`` use the
    vendored macros. Every node carries an explicit error severity and a
    deterministic physical name; origin comments keep traceability.
    """
    raw_set = set(raw_tables)
    known = raw_set | set(staging) | set(intermediates) | set(outputs)
    by_target: dict[tuple[str, str], list[LogicalAssertion]] = {}
    for assertion in assertions:
        if assertion.model not in known:
            raise ValueError(f"assertion {assertion.name!r}: unknown model {assertion.model!r}")
        section = "source" if assertion.model in raw_set else "model"
        by_target.setdefault((section, assertion.model), []).append(assertion)

    lines = ["version: 2", ""]
    source_tables = [t for t in raw_tables if ("source", t) in by_target]
    if source_tables:
        lines.append("sources:")
        lines.append(f"  - name: {RAW_SOURCE_NAME}")
        lines.append("    tables:")
        for table in source_tables:
            lines.append(f"      - name: {table}")
            lines.append("        tests:")
            for assertion in by_target[("source", table)]:
                _emit_assertion(lines, assertion, raw_set)
    ordered_models = [m for m in (*staging, *intermediates, *outputs) if ("model", m) in by_target]
    if ordered_models:
        lines.append("models:")
        for model in ordered_models:
            lines.append(f"  - name: {model}")
            lines.append("    tests:")
            for assertion in by_target[("model", model)]:
                _emit_assertion(lines, assertion, raw_set)
    return "\n".join(lines) + "\n"


def _emit_assertion(lines: list[str], assertion: LogicalAssertion, raw_tables: set[str]) -> None:
    lines.append(f"          # {assertion.origin}: {assertion.name}")
    match assertion.type:
        case "not_null":
            for column in assertion.columns:
                _emit_test(
                    lines,
                    "not_null",
                    [f"column_name: {column}"],
                    _physical_name("not_null", assertion.name, column),
                )
        case "unique":
            if len(assertion.columns) == 1:
                (column,) = assertion.columns
                _emit_test(
                    lines,
                    "unique",
                    [f"column_name: {column}"],
                    _physical_name("unique", assertion.name),
                )
            else:
                _emit_test(
                    lines,
                    "composite_unique",
                    [f"column_names: {_yaml_ident_list(assertion.columns)}"],
                    _physical_name("composite_unique", assertion.name),
                )
        case "accepted_values":
            values = ", ".join(yaml_scalar(v) for v in assertion.values)
            _emit_test(
                lines,
                "accepted_values",
                [f"column_name: {assertion.column}", f"values: [{values}]"],
                _physical_name("accepted_values", assertion.name),
            )
        case "relationships":
            to = _target_ref(str(assertion.to_model), raw_tables)
            if len(assertion.columns) == 1:
                (column,) = assertion.columns
                (to_column,) = assertion.to_columns
                _emit_test(
                    lines,
                    "relationships",
                    [f"column_name: {column}", f"to: {to}", f"field: {to_column}"],
                    _physical_name("relationships", assertion.name),
                )
            else:
                child = [quote_ident(c) for c in assertion.columns]
                parent = [quote_ident(c) for c in assertion.to_columns]
                join_on = " AND ".join(f"child.{c} = parent.{p}" for c, p in zip(child, parent))
                orphans = (
                    f"parent.{parent[0]} IS NULL AND NOT ("
                    + " AND ".join(f"child.{c} IS NULL" for c in child)
                    + ")"
                )
                _emit_test(
                    lines,
                    "composite_relationships",
                    [
                        f"column_names: {_yaml_ident_list(assertion.columns)}",
                        f"to: {to}",
                        f"to_columns: {_yaml_ident_list(assertion.to_columns)}",
                        f"join_on: {yaml_string(join_on)}",
                        f"orphan_filter: {yaml_string(orphans)}",
                    ],
                    _physical_name("composite_relationships", assertion.name),
                )
        case "row_count":
            args = []
            if assertion.min is not None:
                args.append(f"min_value: {assertion.min}")
            if assertion.max is not None:
                args.append(f"max_value: {assertion.max}")
            _emit_test(
                lines,
                "row_count_between",
                args,
                _physical_name("row_count_between", assertion.name),
            )
        case "column_range":
            args = [f"column_name: {assertion.column}"]
            if assertion.min is not None:
                args.append(f"min_value: {typed_scalar_literal(assertion.min)}")
            if assertion.max is not None:
                args.append(f"max_value: {typed_scalar_literal(assertion.max)}")
            args.append(f"inclusive: {'true' if assertion.inclusive else 'false'}")
            _emit_test(
                lines,
                "column_range",
                args,
                _physical_name("column_range", assertion.name),
            )
        case _:
            raise ValueError(f"assertion {assertion.name!r}: unknown type {assertion.type!r}")


def render_assertion_macros() -> str:
    """Vendored generic tests (no dbt-utils): exact §15.3 semantics."""
    return """{% test composite_unique(model, column_names) %}
select {{ column_names | join(', ') }} from {{ model }}
where {{ column_names | join(' IS NOT NULL AND ') }} IS NOT NULL
group by {{ column_names | join(', ') }} having count(*) > 1
{% endtest %}

{% test composite_relationships(model, column_names, to, to_columns, join_on, orphan_filter) %}
select {{ column_names | join(', ') }} from {{ model }} as child
left join (select distinct {{ to_columns | join(', ') }} from {{ to }}) as parent
  on {{ join_on }}
where {{ orphan_filter }}
{% endtest %}

{% test row_count_between(model, min_value=none, max_value=none) %}
with __counts as (select count(*) as n from {{ model }})
select n from __counts
where 1 = 0
{% if min_value is not none %} or n < {{ min_value }}{% endif %}
{% if max_value is not none %} or n > {{ max_value }}{% endif %}
{% endtest %}

{% test column_range(model, column_name, min_value=none, max_value=none, inclusive=true) %}
select {{ column_name }} from {{ model }}
where {{ column_name }} is not null
{% if min_value is not none %}
  and {{ column_name }} {{ '<' if inclusive else '<=' }} {{ min_value }}
{% endif %}
{% if max_value is not none %}
  and {{ column_name }} {{ '>' if inclusive else '>=' }} {{ max_value }}
{% endif %}
{% endtest %}
"""


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
        if upstream in by_name:
            model_output_columns(by_name[upstream], by_name, memo)
        payloads.append(
            (f"models/output/{model.name}.sql", render_output_sql(model, memo[upstream]))
        )
    payloads.append(
        (
            "models/assertions.yml",
            render_assertions_yml(
                collect_assertions(validated),
                raw_tables=[str(t.name) for t in scenario.raw_tables],
                staging=[str(m.name) for m in scenario.staging_models],
                intermediates=ordered_intermediates,
                outputs=[str(m.name) for m in scenario.output_models],
            ),
        )
    )
    payloads.append(("macros/generated_assertions.sql", render_assertion_macros()))

    (project_dir / "macros").mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    for relative, text in payloads:
        write_text_file(project_dir / relative, text)
        written.append(relative)
    return RenderedDbtProject(
        scenario_id=scenario_id, project_dir=project_dir, files=tuple(written)
    )
