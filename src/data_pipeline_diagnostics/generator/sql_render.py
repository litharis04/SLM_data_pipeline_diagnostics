"""SQL rendering primitives: staging column pipelines (GENERATOR_SPEC §14.2).

Each staging column starts from its quoted raw source; operations wrap it in
declaration order; the target alias is applied once at the end. Strict
``CAST`` only — never ``TRY_CAST``.

Materialization barrier (one deterministic technique for the whole
renderer, G13–G15): the column-expression phase of every model is emitted
as a ``MATERIALIZED`` CTE (see :func:`materialized_cte`) before any
row-operation phase, so an error-producing cast or ``map_values(error)``
cannot be skipped or reordered past a filter by the optimizer. Column
expressions themselves stay plain function/CASE applications.
"""

from __future__ import annotations

from data_pipeline_diagnostics.generator.physical import (
    DUCKDB_TYPE,
    literal_string,
    quote_ident,
    sql_literal,
)
from data_pipeline_diagnostics.scenario.staging import (
    MapValuesOperation,
    StagingColumn,
    StagingColumnOperation,
)
from data_pipeline_diagnostics.scenario.types import DataType

__all__ = [
    "materialized_cte",
    "render_staging_column",
    "typed_scalar_literal",
]


def materialized_cte(name: str, body: str) -> str:
    """Render one phase boundary as a materialized CTE (optimizer barrier)."""
    return f"{quote_ident(name)} AS MATERIALIZED ({body})"


def typed_scalar_literal(value: object) -> str:
    """SQL literal for a ``ScalarValue`` with its JSON type preserved."""
    if type(value) is bool:
        return sql_literal(value, DataType.boolean)
    if type(value) is int:
        return sql_literal(value, DataType.integer)
    if type(value) is float:
        return sql_literal(value, DataType.float)
    if type(value) is str:
        return sql_literal(value, DataType.string)
    raise ValueError(f"unsupported scalar literal: {value!r}")


def render_staging_column(source: str, column: StagingColumn, *, model: str) -> str:
    """Render one staging column pipeline: ``<wrapped source> AS <target>``.

    ``source`` is the already-quoted raw column reference. ``model`` names
    the staging model for the ``dpd_unmapped_value`` error message (which
    carries no data value).
    """
    expression = source
    for operation in column.operations:
        expression = _apply_operation(expression, operation, model=model, column=str(column.target))
    return f"{expression} AS {quote_ident(str(column.target))}"


def _apply_operation(
    expression: str, operation: StagingColumnOperation, *, model: str, column: str
) -> str:
    match operation.op:
        case "trim":
            return f"trim({expression})"
        case "lower":
            return f"lower({expression})"
        case "upper":
            return f"upper({expression})"
        case "replace":
            return (
                f"replace({expression}, "
                f"{literal_string(operation.old)}, {literal_string(operation.new)})"
            )
        case "map_values":
            return _render_map_values(expression, operation, model=model, column=column)
        case "null_if":
            options = ", ".join(typed_scalar_literal(v) for v in operation.values)
            return f"CASE WHEN {expression} IN ({options}) THEN NULL ELSE {expression} END"
        case "coalesce":
            return f"coalesce({expression}, {typed_scalar_literal(operation.value)})"
        case "cast":
            target = DUCKDB_TYPE[operation.type]
            if operation.format is not None:
                return (
                    f"CAST(strptime({expression}, {literal_string(operation.format)}) AS {target})"
                )
            return f"CAST({expression} AS {target})"
        case _:
            raise ValueError(f"unknown staging column operation: {operation.op!r}")


def _render_map_values(
    expression: str, operation: MapValuesOperation, *, model: str, column: str
) -> str:
    branches = " ".join(
        f"WHEN {expression} = {literal_string(key)} THEN {literal_string(value)}"
        for key, value in operation.mapping.items()
    )
    match operation.on_unmapped:
        case "keep":
            tail = expression
        case "null":
            tail = "NULL"
        case "error":
            tail = f"error({literal_string(f'dpd_unmapped_value({model}, {column})')})"
        case _:
            raise ValueError(f"unknown on_unmapped mode: {operation.on_unmapped!r}")
    return f"CASE WHEN {expression} IS NULL THEN NULL {branches} ELSE {tail} END"
