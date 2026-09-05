"""Mini-generator models and discriminated union.

Implements ``docs/SCENARIO_SPEC.md`` §8.
All models inherit from :class:`ContractModel` and are discriminated by ``kind``.
"""

from __future__ import annotations

import re
import string
from datetime import UTC, date, datetime
from typing import Annotated, Literal

from pydantic import (
    BeforeValidator,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from data_pipeline_diagnostics.scenario.base import ContractModel
from data_pipeline_diagnostics.scenario.types import (
    Identifier,
    Probability,
    ScalarValue,
)

# ---------------------------------------------------------------------------
# Helpers for strict float validation (reject int/bool)
# ---------------------------------------------------------------------------


def _strict_float_validator(v: object) -> object:
    if type(v) is not float:
        msg = "Input should be a valid number"
        raise ValueError(msg)
    return v


StrictFloatNoInt = Annotated[float, BeforeValidator(_strict_float_validator)]
FiniteFloat = Annotated[
    float, BeforeValidator(_strict_float_validator), Field(ge=None)
]  # allow_inf_nan handled by model_config

# ---------------------------------------------------------------------------
# FormattedIdGenerator
# ---------------------------------------------------------------------------


class FormattedIdGenerator(ContractModel):
    kind: Literal["formatted_id"] = "formatted_id"
    prefix: Annotated[str, StringConstraints(max_length=32, strict=True)] = ""
    digits: Annotated[int, Field(strict=True, ge=1, le=18)]
    start: Annotated[int, Field(strict=True, ge=0)] = 1

    @field_validator("prefix")
    @classmethod
    def _validate_prefix(cls, v: str) -> str:
        # printable ASCII: 0x20-0x7E ; whitespace must be rejected
        if any(c not in string.printable or c.isspace() for c in v):
            # string.printable includes whitespace; we explicitly forbid whitespace
            # and non-printable. Printable without whitespace is 0x21-0x7E
            msg = "prefix must contain only printable ASCII without whitespace"
            raise ValueError(msg)
        # also reject any char outside 0x20-0x7E (but whitespace already rejected)
        for ch in v:
            code = ord(ch)
            if code < 0x20 or code > 0x7E:
                msg = "prefix must contain only printable ASCII without whitespace"
                raise ValueError(msg)
            if ch.isspace():
                msg = "prefix must not contain whitespace"
                raise ValueError(msg)
        return v


# ---------------------------------------------------------------------------
# IntegerRangeGenerator
# ---------------------------------------------------------------------------


class IntegerRangeGenerator(ContractModel):
    kind: Literal["integer_range"] = "integer_range"
    min: Annotated[int, Field(strict=True)]
    max: Annotated[int, Field(strict=True)]

    @model_validator(mode="after")
    def _check_range(self) -> IntegerRangeGenerator:
        if self.min >= self.max:
            msg = "min must be < max"
            raise ValueError(msg)
        return self


# ---------------------------------------------------------------------------
# FloatRangeGenerator
# ---------------------------------------------------------------------------


class FloatRangeGenerator(ContractModel):
    kind: Literal["float_range"] = "float_range"
    min: StrictFloatNoInt
    max: StrictFloatNoInt
    decimal_places: Annotated[int, Field(strict=True, ge=0, le=12)] = 2

    @model_validator(mode="after")
    def _check_range(self) -> FloatRangeGenerator:
        if self.min >= self.max:
            msg = "min must be < max"
            raise ValueError(msg)
        return self


# ---------------------------------------------------------------------------
# DateRangeGenerator
# ---------------------------------------------------------------------------


class DateRangeGenerator(ContractModel):
    kind: Literal["date_range"] = "date_range"
    min: date
    max: date

    @model_validator(mode="after")
    def _check_range(self) -> DateRangeGenerator:
        if self.min >= self.max:
            msg = "min must be < max"
            raise ValueError(msg)
        return self


# ---------------------------------------------------------------------------
# TimestampRangeGenerator
# ---------------------------------------------------------------------------


class TimestampRangeGenerator(ContractModel):
    kind: Literal["timestamp_range"] = "timestamp_range"
    min: datetime
    max: datetime

    @field_validator("min", "max")
    @classmethod
    def _must_be_tz_aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None or v.utcoffset() is None:
            msg = "timestamp must include a UTC offset"
            raise ValueError(msg)
        # ensure it can be normalized to UTC
        try:
            v.astimezone(UTC)
        except Exception as exc:
            msg = "offset cannot be normalized to UTC"
            raise ValueError(msg) from exc
        return v

    @model_validator(mode="after")
    def _check_range(self) -> TimestampRangeGenerator:
        # compare after normalization to UTC
        min_utc = self.min.astimezone(UTC)
        max_utc = self.max.astimezone(UTC)
        if min_utc >= max_utc:
            msg = "min must be < max after normalization to UTC"
            raise ValueError(msg)
        return self


# ---------------------------------------------------------------------------
# CategoricalGenerator
# ---------------------------------------------------------------------------


class CategoricalGenerator(ContractModel):
    kind: Literal["categorical"] = "categorical"
    values: Annotated[tuple[ScalarValue, ...], Field(min_length=1)]
    weights: Annotated[tuple[FiniteFloat, ...] | None, Field(default=None)]

    @field_validator("values")
    @classmethod
    def _unique_values(cls, v: tuple[ScalarValue, ...]) -> tuple[ScalarValue, ...]:
        # unique by both value and JSON scalar type (bool vs int distinct)
        seen: set[tuple[str, object]] = set()
        for item in v:
            key = (type(item).__name__, item)
            # For float, need to handle -0.0 vs 0.0? Keep simple.
            # Use (type, value) – but for floats, ensure distinction not conflated
            # Python's bool is subclass of int, but type name distinguishes.
            if key in seen:
                msg = "values must be unique by value and type"
                raise ValueError(msg)
            seen.add(key)
        return v

    @field_validator("weights")
    @classmethod
    def _validate_weights(cls, v: tuple[float, ...] | None) -> tuple[float, ...] | None:
        if v is None:
            return v
        if len(v) == 0:
            msg = "weights must be non-empty when present"
            raise ValueError(msg)
        for w in v:
            if w < 0:
                msg = "weights must be non-negative"
                raise ValueError(msg)
            # finite enforced by ContractModel and strict validator
        if not any(w > 0 for w in v):
            msg = "at least one weight must be > 0"
            raise ValueError(msg)
        return v

    @model_validator(mode="after")
    def _check_weights_length(self) -> CategoricalGenerator:
        if self.weights is not None and len(self.weights) != len(self.values):
            msg = "weights length must equal values length"
            raise ValueError(msg)
        return self


# ---------------------------------------------------------------------------
# BooleanGenerator
# ---------------------------------------------------------------------------


class BooleanGenerator(ContractModel):
    kind: Literal["boolean"] = "boolean"
    true_probability: Probability = 0.5


# ---------------------------------------------------------------------------
# RandomStringGenerator
# ---------------------------------------------------------------------------

_DEFAULT_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789"


class RandomStringGenerator(ContractModel):
    kind: Literal["random_string"] = "random_string"
    min_length: Annotated[int, Field(strict=True, ge=1)]
    max_length: Annotated[int, Field(strict=True, ge=1)]
    alphabet: Annotated[str, StringConstraints(min_length=1, strict=True)] = _DEFAULT_ALPHABET

    @field_validator("alphabet")
    @classmethod
    def _unique_alphabet(cls, v: str) -> str:
        if len(set(v)) != len(v):
            msg = "alphabet characters must be unique"
            raise ValueError(msg)
        return v

    @model_validator(mode="after")
    def _check_lengths(self) -> RandomStringGenerator:
        if self.min_length > self.max_length:
            msg = "min_length must be <= max_length"
            raise ValueError(msg)
        return self


# ---------------------------------------------------------------------------
# TemplateStringGenerator
# ---------------------------------------------------------------------------

_IDENTIFIER_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_VALID_PLACEHOLDER_RE = re.compile(r"\{[a-z][a-z0-9_]*\}")


class TemplateStringGenerator(ContractModel):
    kind: Literal["template_string"] = "template_string"
    template: Annotated[str, StringConstraints(min_length=1, max_length=256, strict=True)]

    @field_validator("template")
    @classmethod
    def _validate_template(cls, v: str) -> str:
        # At least one placeholder required
        placeholders = _VALID_PLACEHOLDER_RE.findall(v)
        if not placeholders:
            msg = "template must contain at least one placeholder {column_name}"
            raise ValueError(msg)

        # Check for unmatched braces or invalid placeholders
        # Remove valid placeholders, then any remaining { or } indicates error
        remaining = _VALID_PLACEHOLDER_RE.sub("", v)
        if "{" in remaining or "}" in remaining:
            msg = "template has unmatched braces or invalid placeholder"
            raise ValueError(msg)

        # Also ensure no placeholder contains invalid identifier (already filtered by regex)
        # But we must also reject placeholders like {Invalid} or {1bad} – they would not be matched,
        # and would be caught as unmatched braces above.
        # Additional check: extract content between braces for each placeholder and validate
        # (redundant but explicit)
        for ph in placeholders:
            inner = ph[1:-1]  # strip {}
            if not _IDENTIFIER_RE.match(inner):
                msg = f"placeholder {ph!r} is not a valid Identifier"
                raise ValueError(msg)

        return v


# ---------------------------------------------------------------------------
# ForeignKeyGenerator
# ---------------------------------------------------------------------------


class ForeignKeyGenerator(ContractModel):
    kind: Literal["foreign_key"] = "foreign_key"
    relationship: Identifier
    target_side: Literal["left", "right"]


# ---------------------------------------------------------------------------
# Faker-backed generators
# ---------------------------------------------------------------------------


class PersonNameGenerator(ContractModel):
    kind: Literal["person_name"] = "person_name"
    locale: Annotated[str, StringConstraints(min_length=1, strict=True)] = "en_US"


class EmailGenerator(ContractModel):
    kind: Literal["email"] = "email"
    locale: Annotated[str, StringConstraints(min_length=1, strict=True)] = "en_US"


class CityGenerator(ContractModel):
    kind: Literal["city"] = "city"
    locale: Annotated[str, StringConstraints(min_length=1, strict=True)] = "en_US"


class StreetAddressGenerator(ContractModel):
    kind: Literal["street_address"] = "street_address"
    locale: Annotated[str, StringConstraints(min_length=1, strict=True)] = "en_US"


class CompanyNameGenerator(ContractModel):
    kind: Literal["company_name"] = "company_name"
    locale: Annotated[str, StringConstraints(min_length=1, strict=True)] = "en_US"


class PhoneNumberGenerator(ContractModel):
    kind: Literal["phone_number"] = "phone_number"
    locale: Annotated[str, StringConstraints(min_length=1, strict=True)] = "en_US"


# ---------------------------------------------------------------------------
# Discriminated union
# ---------------------------------------------------------------------------

GeneratorSpec = Annotated[
    FormattedIdGenerator
    | IntegerRangeGenerator
    | FloatRangeGenerator
    | DateRangeGenerator
    | TimestampRangeGenerator
    | CategoricalGenerator
    | BooleanGenerator
    | RandomStringGenerator
    | TemplateStringGenerator
    | ForeignKeyGenerator
    | PersonNameGenerator
    | EmailGenerator
    | CityGenerator
    | StreetAddressGenerator
    | CompanyNameGenerator
    | PhoneNumberGenerator,
    Field(discriminator="kind"),
]
