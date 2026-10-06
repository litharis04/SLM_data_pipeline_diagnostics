"""Faker-backed proposals, ``de_DE`` only (GENERATOR_SPEC §10.7).

One cached single-locale ``Faker("de_DE")`` serves all columns; the caller's
G02 values-stream is bound to ``fake.random`` on every call, so the instance
draws only from that isolated stream (never module-global random state,
never ``.unique`` shared across columns). Fixed-seed repeatability and
cross-column independence follow from the stream contract.

Single-threaded use only: the cached instance's ``random`` is rebound per
call, which is not safe for concurrent draws from different streams.
"""

from __future__ import annotations

import random

from faker import Faker

from data_pipeline_diagnostics.scenario.generators import (
    CityGenerator,
    CompanyNameGenerator,
    EmailGenerator,
    PersonNameGenerator,
    PhoneNumberGenerator,
    StreetAddressGenerator,
)

__all__ = [
    "FAKER_LOCALE",
    "FakerGeneratorConfig",
    "generate_faker",
]

FAKER_LOCALE = "de_DE"

FakerGeneratorConfig = (
    PersonNameGenerator
    | EmailGenerator
    | CityGenerator
    | StreetAddressGenerator
    | CompanyNameGenerator
    | PhoneNumberGenerator
)

_FAKER: Faker | None = None


def _faker() -> Faker:
    """Cached single-locale Faker instance (``random`` rebound per call)."""
    global _FAKER
    if _FAKER is None:
        _FAKER = Faker(FAKER_LOCALE)
    return _FAKER


def generate_faker(config: FakerGeneratorConfig, rng: random.Random, row_index: int) -> str:
    """Proposal string from the §10.7 dispatch table for one row.

    The passed values-stream is the sole entropy source. A non-``de_DE``
    locale cannot arrive through the narrowed contract; it raises here
    defensively. Faker supplies the proposal string only and MUST NOT
    influence row counts, nulls, keys, or uniqueness (owned by G06+).
    """
    if type(row_index) is not int or row_index < 0:
        raise ValueError(f"row_index must be a non-negative int, got {row_index!r}")
    if getattr(config, "locale", None) != FAKER_LOCALE:
        raise ValueError(
            f"faker locale must be {FAKER_LOCALE!r}, got {getattr(config, 'locale', None)!r}"
        )
    fake = _faker()
    fake.random = rng
    match config.kind:
        case "person_name":
            value = fake.name()
        case "email":
            value = fake.email()
        case "city":
            value = fake.city()
        case "street_address":
            value = fake.street_address()
        case "company_name":
            value = fake.company()
        case "phone_number":
            value = fake.phone_number()
        case _:
            raise ValueError(f"unknown faker generator kind: {config.kind!r}")
    if type(value) is not str:
        raise TypeError(f"faker {config.kind} proposal must be str, got {type(value).__name__}")
    return value
