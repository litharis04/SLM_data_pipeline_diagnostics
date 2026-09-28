"""Scalar mini-generator execution, parts 1–2 + Faker (GENERATOR_SPEC §10).

Pure proposal functions. Each takes ``(generator_config, rng, row_index)``;
only the passed stream is consumed (``formatted_id``/``template_string``
consume none).
``categorical`` additionally accepts ``unique``/``already_drawn`` for
without-replacement selection (exhaustion raises :class:`ExhaustedDomain`
for the G06 caller to decide retry vs failure); ``template_string``
additionally takes the row's placeholder values as a dict. The six Faker
leaf kinds delegate to :mod:`faker_values` (``de_DE`` only). Null insertion
and hard constraints are applied later (G06); this module produces non-null
proposals only (a template resolving to null is the caller's null signal).
"""

from __future__ import annotations

import random
import re
from collections.abc import Iterable, Mapping
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

from data_pipeline_diagnostics.generator.faker_values import generate_faker
from data_pipeline_diagnostics.scenario.generators import (
    BooleanGenerator,
    CategoricalGenerator,
    DateRangeGenerator,
    FloatRangeGenerator,
    FormattedIdGenerator,
    GeneratorSpec,
    IntegerRangeGenerator,
    RandomStringGenerator,
    TemplateStringGenerator,
    TimestampRangeGenerator,
)

__all__ = [
    "ExhaustedDomain",
    "generate_boolean",
    "generate_categorical",
    "generate_date",
    "generate_float",
    "generate_formatted_id",
    "generate_integer",
    "generate_random_string",
    "generate_scalar",
    "generate_template",
    "generate_timestamp",
    "render_template",
]


class ExhaustedDomain(Exception):
    """A finite generator domain has no selectable value left (e.g. unique
    categorical with every value already drawn). The caller decides retry
    vs structured failure; this module never truncates or invents values."""


def _check_row_index(row_index: object) -> int:
    if type(row_index) is not int or row_index < 0:
        raise ValueError(f"row_index must be a non-negative int, got {row_index!r}")
    return row_index


def generate_formatted_id(config: FormattedIdGenerator, rng: random.Random, row_index: int) -> str:
    """Deterministic identifier: ``prefix + str(start + i)`` zero-padded to ``digits``."""
    _check_row_index(row_index)
    number = config.start + row_index
    digits = str(number)
    if len(digits) > config.digits:
        raise ValueError(f"formatted_id overflow: {number} exceeds {config.digits} digit capacity")
    return f"{config.prefix}{digits.zfill(config.digits)}"


def generate_integer(config: IntegerRangeGenerator, rng: random.Random, row_index: int) -> int:
    """Uniform int from inclusive ``[min, max]`` via the passed stream."""
    _check_row_index(row_index)
    return rng.randint(config.min, config.max)


def generate_float(config: FloatRangeGenerator, rng: random.Random, row_index: int) -> float:
    """Uniform sample from the decimal lattice implied by ``decimal_places``.

    Lattice bounds use the canonical decimal spelling of the inputs
    (``Decimal(str(x))``, never binary-float expansion). An empty lattice is
    an explicit failure.
    """
    _check_row_index(row_index)
    scale = 10**config.decimal_places
    lo = (Decimal(str(config.min)) * scale).to_integral_value(rounding=ROUND_CEILING)
    hi = (Decimal(str(config.max)) * scale).to_integral_value(rounding=ROUND_FLOOR)
    if lo > hi:
        raise ValueError(
            f"float_range empty lattice: [{config.min}, {config.max}] "
            f"at decimal_places={config.decimal_places}"
        )
    return rng.randint(int(lo), int(hi)) / scale


def generate_date(config: DateRangeGenerator, rng: random.Random, row_index: int) -> date:
    """Uniform calendar date from inclusive ``[min, max]`` via a day offset."""
    _check_row_index(row_index)
    span_days = (config.max - config.min).days
    return config.min + timedelta(days=rng.randint(0, span_days))


def _to_microseconds(delta: timedelta) -> int:
    return (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds


def generate_timestamp(
    config: TimestampRangeGenerator, rng: random.Random, row_index: int
) -> datetime:
    """Uniform UTC timestamp from the inclusive microsecond interval.

    Both bounds are normalized to UTC first; local time, DST rules and the
    current clock are never consulted. The result is tz-aware UTC.
    """
    _check_row_index(row_index)
    min_utc = config.min.astimezone(UTC)
    max_utc = config.max.astimezone(UTC)
    span_us = _to_microseconds(max_utc - min_utc)
    return min_utc + timedelta(microseconds=rng.randint(0, span_us))


def generate_scalar(
    config: GeneratorSpec,
    rng: random.Random,
    row_index: int,
    *,
    unique: bool = False,
    already_drawn: Iterable[object] = frozenset(),
    placeholders: Mapping[str, object] | None = None,
) -> str | int | float | bool | date | datetime | None:
    """Dispatch a scalar generator config to its executor (exhaustive).

    ``unique``/``already_drawn`` apply to ``categorical`` without-replacement
    selection; ``placeholders`` supplies the row values for ``template_string``
    (required for that kind).
    """
    match config.kind:
        case "formatted_id":
            return generate_formatted_id(config, rng, row_index)
        case "integer_range":
            return generate_integer(config, rng, row_index)
        case "float_range":
            return generate_float(config, rng, row_index)
        case "date_range":
            return generate_date(config, rng, row_index)
        case "timestamp_range":
            return generate_timestamp(config, rng, row_index)
        case "categorical":
            return generate_categorical(
                config, rng, row_index, unique=unique, already_drawn=already_drawn
            )
        case "boolean":
            return generate_boolean(config, rng, row_index)
        case "random_string":
            return generate_random_string(config, rng, row_index)
        case "template_string":
            if placeholders is None:
                raise ValueError("template_string requires a placeholders mapping")
            return generate_template(config, rng, row_index, placeholders)
        case "person_name" | "email" | "city" | "street_address" | "company_name" | "phone_number":
            return generate_faker(config, rng, row_index)
        case _:
            raise ValueError(f"unknown scalar generator kind: {config.kind!r}")


# ---------------------------------------------------------------------------
# Part 2: categorical / boolean / strings / template (§§10.4–10.6)
# ---------------------------------------------------------------------------


def _value_key(value: object) -> tuple[str, object]:
    """Identity key preserving JSON scalar type (``True`` vs ``1`` distinct)."""
    return (type(value).__name__, value)


def generate_categorical(
    config: CategoricalGenerator,
    rng: random.Random,
    row_index: int,
    *,
    unique: bool = False,
    already_drawn: Iterable[object] = frozenset(),
) -> str | int | float | bool:
    """Sample one categorical value, preserving JSON scalar type exactly.

    Without weights every declared value is equally likely. With weights they
    are normalized by their sum in declaration order; a zero-weight value is
    never selected. For ``unique`` columns selection is without replacement
    over the not-yet-drawn values with renormalized weights; an empty
    remainder raises :class:`ExhaustedDomain` before consuming the stream.
    """
    _check_row_index(row_index)
    drawn = {_value_key(v) for v in already_drawn}

    if config.weights is None:
        pool = [v for v in config.values if not unique or _value_key(v) not in drawn]
        if not pool:
            raise ExhaustedDomain("categorical without-replacement pool exhausted")
        return pool[rng.randint(0, len(pool) - 1)]

    pool = [
        (v, w)
        for v, w in zip(config.values, config.weights, strict=True)
        if w > 0 and (not unique or _value_key(v) not in drawn)
    ]
    if not pool:
        raise ExhaustedDomain("categorical without-replacement pool exhausted")
    total = sum(w for _, w in pool)
    pick = rng.random() * total
    for value, weight in pool:
        if pick < weight:
            return value
        pick -= weight
    return pool[-1][0]


def generate_boolean(config: BooleanGenerator, rng: random.Random, row_index: int) -> bool:
    """``True`` when a uniform draw in ``[0, 1)`` is below ``true_probability``."""
    _check_row_index(row_index)
    return rng.random() < config.true_probability


def generate_random_string(
    config: RandomStringGenerator, rng: random.Random, row_index: int
) -> str:
    """Uniform length from ``[min_length, max_length]``, each character uniform
    and independent from ``alphabet`` in declaration order."""
    _check_row_index(row_index)
    length = rng.randint(config.min_length, config.max_length)
    return "".join(rng.choice(config.alphabet) for _ in range(length))


_PLACEHOLDER_RE = re.compile(r"\{([a-z][a-z0-9_]*)\}")


def _template_text(value: object) -> str:
    """Text form of one placeholder value per the §10.6 table."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, str):
        return value
    if isinstance(value, datetime):
        moment = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
        return moment.astimezone(UTC).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    raise TypeError(f"unsupported template placeholder type: {type(value).__name__}")


def render_template(template: str, placeholders: Mapping[str, object]) -> str | None:
    """Pure ``template_string`` render over supplied row values.

    Literal text is copied verbatim; each ``{column}`` is replaced by the
    §10.6 text form of its value. Any referenced null yields a null result.
    No escaping, nested lookup, or Jinja evaluation occurs.
    """
    texts: dict[str, str] = {}
    for name in dict.fromkeys(_PLACEHOLDER_RE.findall(template)):
        value = placeholders[name]
        if value is None:
            return None
        texts[name] = _template_text(value)
    result = template
    for name, text in texts.items():
        result = result.replace("{" + name + "}", text)
    return result


def generate_template(
    config: TemplateStringGenerator,
    rng: random.Random,
    row_index: int,
    placeholders: Mapping[str, object],
) -> str | None:
    """Render a template column for one row (consumes no RNG)."""
    _check_row_index(row_index)
    return render_template(config.template, placeholders)
