"""G05 tests: Faker-backed proposals (`de_DE` only).

Technique notes: the wiring under test binds the caller's G02 values-stream
to ``Faker.random`` on every call (single cached ``de_DE`` instance), so
repeatability and cross-column independence reduce to stream properties.
Locale-robustness: no provider value is pinned; the `de_DE` flavor is
asserted via umlaut/ß presence (deterministic under the pinned Faker, whose
exact version is asserted here) rather than exact strings.
"""

from __future__ import annotations

import importlib.metadata
import random as py_random
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from data_pipeline_diagnostics.generator.faker_values import generate_faker
from data_pipeline_diagnostics.generator.raw_values import generate_scalar
from data_pipeline_diagnostics.generator.rng import stream as make_stream
from data_pipeline_diagnostics.scenario.generators import (
    CityGenerator,
    CompanyNameGenerator,
    EmailGenerator,
    PersonNameGenerator,
    PhoneNumberGenerator,
    StreetAddressGenerator,
)

SCENARIO = "g05-test"

CONFIGS = [
    PersonNameGenerator(),
    EmailGenerator(),
    CityGenerator(),
    StreetAddressGenerator(),
    CompanyNameGenerator(),
    PhoneNumberGenerator(),
]


def _stream(column: str, seed: int = 11):
    return make_stream(SCENARIO, seed, f"values/t/{column}")


def test_all_six_kinds_non_empty_and_repeatable():
    for cfg in CONFIGS:
        first = [generate_scalar(cfg, _stream("col"), i) for i in range(10)]
        second = [generate_scalar(cfg, _stream("col"), i) for i in range(10)]
        assert first == second
        assert all(type(v) is str and v for v in first)


def test_cross_column_independence():
    cfg = CityGenerator()
    col_a = [generate_faker(cfg, _stream("col_a"), i) for i in range(10)]
    col_b = [generate_faker(cfg, _stream("col_b"), i) for i in range(10)]
    assert col_a != col_b


def test_non_de_locale_rejected():
    with pytest.raises(ValidationError):
        PersonNameGenerator(locale="en_US")
    with pytest.raises(ValueError, match="de_DE"):
        generate_faker(SimpleNamespace(kind="person_name", locale="en_US"), _stream("col"), 0)


def test_de_flavor_and_pinned_version():
    assert importlib.metadata.version("faker") == "40.39.0"
    rng = _stream("name")
    names = [generate_faker(PersonNameGenerator(), rng, i) for i in range(300)]
    assert any(any(ord(ch) > 127 for ch in v) for v in names)


def test_no_module_global_random_dependence():
    cfg = CityGenerator()
    py_random.seed(1)
    first = [generate_scalar(cfg, _stream("col"), i) for i in range(10)]
    py_random.seed(999)
    second = [generate_scalar(cfg, _stream("col"), i) for i in range(10)]
    assert first == second
