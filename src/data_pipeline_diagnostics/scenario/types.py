"""Shared scalar types and references for the scenario contract.

Implements ``docs/SCENARIO_SPEC.md`` §5 and ``§7.1`` (RowCount).
All models inherit from :class:`ContractModel` to enforce strict, closed and
immutable validation.
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Literal, Union

from pydantic import (
    BeforeValidator,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    StringConstraints,
    field_validator,
)

from data_pipeline_diagnostics.scenario.base import ContractModel


def _strict_float_validator(v: object) -> object:
    """Reject ``int`` and ``bool`` for fields that require a strict ``float``.

    Pydantic's ``StrictFloat`` still accepts ``int`` values (``1`` -> ``1.0``)
    when ``strict=True`` is used only via ``Field``. SCENARIO_SPEC requires
    integers not to be coerced to floats (``Probability`` §5.4) and likewise
    for range generators. This validator ensures ``type(v) is float`` before
    the standard float validation runs.
    """
    if type(v) is not float:
        msg = "Input should be a valid number"
        raise ValueError(msg)
    return v


StrictProbabilityFloat = Annotated[
    float, BeforeValidator(_strict_float_validator), Field(ge=0.0, le=1.0)
]
StrictFiniteFloat = Annotated[float, BeforeValidator(_strict_float_validator)]

# ---------------------------------------------------------------------------
# Identifiers
# ---------------------------------------------------------------------------

Identifier = Annotated[
    str,
    StringConstraints(pattern=r"^[a-z][a-z0-9_]*$", min_length=1, max_length=63, strict=True),
]

ScenarioId = Annotated[
    str,
    StringConstraints(pattern=r"^[a-z][a-z0-9_]*$", min_length=1, max_length=100, strict=True),
]

DomainName = Identifier

Description = Annotated[
    str,
    StringConstraints(min_length=1, max_length=500, strict=True),
]

# ---------------------------------------------------------------------------
# Scalar data types
# ---------------------------------------------------------------------------


class DataType(str, Enum):  # noqa: UP042
    """Allowed column data types (SCENARIO_SPEC §5.2)."""

    string = "string"
    integer = "integer"
    float = "float"
    boolean = "boolean"
    date = "date"
    timestamp = "timestamp"


# ---------------------------------------------------------------------------
# JSON scalar values and probability
# ---------------------------------------------------------------------------

# Strict union: bool must be distinguished from int. Using Strict* guarantees
# that ``True`` is not accepted as ``1`` and vice-versa (SCENARIO_SPEC §5.3).
# For the ``float`` member we must also reject ``int``: Pydantic's
# ``StrictFloat`` still coerces ``int`` -> ``float`` even with ``strict=True``,
# so we add an explicit ``BeforeValidator`` that requires ``type(v) is float``.
ScalarValue = Union[  # noqa: UP007
    StrictStr,
    StrictInt,
    Annotated[float, BeforeValidator(_strict_float_validator)],
    StrictBool,
]

Probability = StrictProbabilityFloat

# ---------------------------------------------------------------------------
# References
# ---------------------------------------------------------------------------


class RelationshipEndpoint(ContractModel):
    """One side of a relationship (SCENARIO_SPEC §5.5)."""

    table: Identifier
    columns: Annotated[tuple[Identifier, ...], Field(min_length=1)]

    @field_validator("columns")
    @classmethod
    def _no_duplicate_columns(cls, v: tuple[Identifier, ...]) -> tuple[Identifier, ...]:
        if len(v) != len(set(v)):
            msg = "columns must not contain duplicates"
            raise ValueError(msg)
        return v


class SortKey(ContractModel):
    """Ordering key (SCENARIO_SPEC §5.5)."""

    column: Identifier
    direction: Literal["asc", "desc"] = "asc"


# ---------------------------------------------------------------------------
# RowCount (SCENARIO_SPEC §7.1) – dependency-free, therefore lives in types.py
# ---------------------------------------------------------------------------


class RowCount(ContractModel):
    """Allowed instance size of a raw table."""

    min: Annotated[int, Field(strict=True, ge=1)]
    max: Annotated[int, Field(strict=True, ge=1)]

    @field_validator("max")
    @classmethod
    def _max_ge_min(cls, v: int, info) -> int:  # type: ignore[no-untyped-def]
        # ``info.data`` contains already validated ``min`` when available.
        data = info.data
        if data is not None and "min" in data:
            min_val = data["min"]
            if v < min_val:
                msg = "max must be >= min"
                raise ValueError(msg)
        return v
