"""SQL rendering primitives: staging columns, expressions, conditions (§§14.2–14.5).

Each staging column starts from its quoted raw source; operations wrap it in
declaration order; the target alias is applied once at the end. Strict
``CAST`` only — never ``TRY_CAST``.

Expressions dispatch exhaustively over the ``kind`` discriminator with full
parenthesization (binary results always wrapped; safe division exactly as
the §14.4 ``CASE`` template returning ``DOUBLE``; ``day_of_week`` via
``extract(isodow)`` for ISO 1=Monday..7=Sunday). Conditions keep ordinary
SQL three-valued semantics — no ``IS NOT DISTINCT FROM`` rewrites — and are
fully parenthesized (``IN`` honors ``negated``).

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
from data_pipeline_diagnostics.scenario.expressions import Condition, Expression
from data_pipeline_diagnostics.scenario.staging import (
    MapValuesOperation,
    StagingColumn,
    StagingColumnOperation,
)
from data_pipeline_diagnostics.scenario.types import DataType

__all__ = [
    "HELPER_ROW_NUMBER",
    "materialized_cte",
    "render_condition",
    "render_expression",
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


# ---------------------------------------------------------------------------
# Expressions (§14.4) and conditions (§14.5)
# ---------------------------------------------------------------------------

_BINARY_OPERATORS = {"add": "+", "subtract": "-", "multiply": "*"}

_COMPARISON_OPERATORS = {
    "eq": "=",
    "ne": "<>",
    "lt": "<",
    "lte": "<=",
    "gt": ">",
    "gte": ">=",
}

_DATE_PARTS = {"year": "year", "quarter": "quarter", "month": "month", "day": "day"}

# Leading underscore impossible in scenario identifiers, so generated SQL
# can never collide with this helper column.
HELPER_ROW_NUMBER = "_dpd_row_number"


def render_expression(expression: Expression) -> str:
    """Render one scalar expression (exhaustive over ``kind``)."""
    match expression.kind:
        case "column":
            return quote_ident(str(expression.column))
        case "literal":
            return typed_scalar_literal(expression.value)
        case "binary":
            left = render_expression(expression.left)
            right = render_expression(expression.right)
            if expression.operator == "divide":
                return (
                    f"(CASE WHEN {right} IS NULL OR {right} = 0 THEN NULL "
                    f"ELSE CAST({left} AS DOUBLE) / CAST({right} AS DOUBLE) END)"
                )
            try:
                symbol = _BINARY_OPERATORS[expression.operator]
            except KeyError:
                raise ValueError(f"unknown binary operator: {expression.operator!r}") from None
            return f"({left} {symbol} {right})"
        case "date_part":
            operand = render_expression(expression.value)
            if expression.part == "day_of_week":
                return f"extract(isodow from {operand})"
            try:
                part = _DATE_PARTS[expression.part]
            except KeyError:
                raise ValueError(f"unknown date part: {expression.part!r}") from None
            return f"extract({part} from {operand})"
        case "coalesce":
            return f"coalesce({', '.join(render_expression(v) for v in expression.values)})"
        case _:
            raise ValueError(f"unknown expression kind: {expression.kind!r}")


def render_condition(condition: Condition) -> str:
    """Render one boolean condition, fully parenthesized (exhaustive over ``kind``)."""
    match condition.kind:
        case "comparison":
            try:
                symbol = _COMPARISON_OPERATORS[condition.operator]
            except KeyError:
                raise ValueError(f"unknown comparison operator: {condition.operator!r}") from None
            left = render_expression(condition.left)
            right = render_expression(condition.right)
            return f"({left} {symbol} {right})"
        case "in":
            operand = render_expression(condition.value)
            options = ", ".join(typed_scalar_literal(v) for v in condition.options)
            keyword = "NOT IN" if condition.negated else "IN"
            return f"({operand} {keyword} ({options}))"
        case "is_null":
            operand = render_expression(condition.value)
            keyword = "IS NOT NULL" if condition.negated else "IS NULL"
            return f"({operand} {keyword})"
        case "all":
            return f"({' AND '.join(render_condition(c) for c in condition.conditions)})"
        case "any":
            return f"({' OR '.join(render_condition(c) for c in condition.conditions)})"
        case "not":
            return f"(NOT {render_condition(condition.condition)})"
        case _:
            raise ValueError(f"unknown condition kind: {condition.kind!r}")
