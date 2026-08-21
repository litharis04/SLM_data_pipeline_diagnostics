"""Structured scalar expressions and boolean conditions.

Implements ``docs/SCENARIO_SPEC.md`` §10.
All models inherit from :class:`ContractModel` and use discriminated unions
with discriminator ``kind``.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from data_pipeline_diagnostics.scenario.base import ContractModel
from data_pipeline_diagnostics.scenario.types import Identifier, ScalarValue

# ---------------------------------------------------------------------------
# Expression models
# ---------------------------------------------------------------------------


class ColumnExpression(ContractModel):
    kind: Literal["column"] = "column"
    column: Identifier


class LiteralExpression(ContractModel):
    kind: Literal["literal"] = "literal"
    value: ScalarValue


class BinaryExpression(ContractModel):
    kind: Literal["binary"] = "binary"
    operator: Literal["add", "subtract", "multiply", "divide"]
    left: Expression  # type: ignore[valid-type]  # forward ref, rebuilt later
    right: Expression  # type: ignore[valid-type]


class DatePartExpression(ContractModel):
    kind: Literal["date_part"] = "date_part"
    part: Literal["year", "quarter", "month", "day", "day_of_week"]
    value: Expression  # type: ignore[valid-type]


class CoalesceExpression(ContractModel):
    kind: Literal["coalesce"] = "coalesce"
    values: Annotated[tuple[Expression, ...], Field(min_length=2)]  # type: ignore[valid-type]


Expression = Annotated[
    ColumnExpression
    | LiteralExpression
    | BinaryExpression
    | DatePartExpression
    | CoalesceExpression,
    Field(discriminator="kind"),
]

# ---------------------------------------------------------------------------
# Condition models
# ---------------------------------------------------------------------------


class ComparisonCondition(ContractModel):
    kind: Literal["comparison"] = "comparison"
    operator: Literal["eq", "ne", "lt", "lte", "gt", "gte"]
    left: Expression
    right: Expression


class InCondition(ContractModel):
    kind: Literal["in"] = "in"
    value: Expression
    options: Annotated[tuple[ScalarValue, ...], Field(min_length=1)]
    negated: bool = False


class NullCondition(ContractModel):
    kind: Literal["is_null"] = "is_null"
    value: Expression
    negated: bool = False


class BooleanCondition(ContractModel):
    kind: Literal["all"] = "all"
    conditions: Annotated[tuple[Condition, ...], Field(min_length=2)]  # type: ignore[valid-type]


class AnyCondition(ContractModel):
    kind: Literal["any"] = "any"
    conditions: Annotated[tuple[Condition, ...], Field(min_length=2)]  # type: ignore[valid-type]


class NotCondition(ContractModel):
    kind: Literal["not"] = "not"
    condition: Condition  # type: ignore[valid-type]


Condition = Annotated[
    ComparisonCondition
    | InCondition
    | NullCondition
    | BooleanCondition
    | AnyCondition
    | NotCondition,
    Field(discriminator="kind"),
]

# Rebuild forward references for recursive models.
# Pydantic requires explicit rebuild when using forward refs with discriminated unions.
BinaryExpression.model_rebuild()
DatePartExpression.model_rebuild()
CoalesceExpression.model_rebuild()
ComparisonCondition.model_rebuild()
InCondition.model_rebuild()
NullCondition.model_rebuild()
BooleanCondition.model_rebuild()
AnyCondition.model_rebuild()
NotCondition.model_rebuild()
