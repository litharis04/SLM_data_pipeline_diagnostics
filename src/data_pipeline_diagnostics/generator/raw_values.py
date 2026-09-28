"""Scalar mini-generator execution, part 1 (GENERATOR_SPEC §§10.1–10.3).

Pure proposal functions for ``formatted_id``, ``integer_range``,
``float_range``, ``date_range`` and ``timestamp_range``. Each takes
``(generator_config, rng, row_index)``; only the passed stream is consumed
(``formatted_id`` consumes none). Null insertion and hard constraints are
applied later (G06); this module produces non-null proposals only.
"""

from __future__ import annotations

import random
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

from data_pipeline_diagnostics.scenario.generators import (
    DateRangeGenerator,
    FloatRangeGenerator,
    FormattedIdGenerator,
    GeneratorSpec,
    IntegerRangeGenerator,
    TimestampRangeGenerator,
)

__all__ = [
    "generate_date",
    "generate_float",
    "generate_formatted_id",
    "generate_integer",
    "generate_scalar",
    "generate_timestamp",
]


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
    config: GeneratorSpec, rng: random.Random, row_index: int
) -> str | int | float | date | datetime:
    """Dispatch a part-1 generator config to its executor (exhaustive)."""
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
        case _:
            raise ValueError(f"unknown scalar generator kind: {config.kind!r}")
