"""G04 tests: part-2 scalar proposals (categorical / boolean / strings / template).

Technique notes: draws come from G02 named streams; ``ExhaustedDomain`` is
raised before consuming the stream so G06 retries stay deterministic. Type
fidelity relies on the scenario contract keeping ``True`` distinct from ``1``.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from data_pipeline_diagnostics.generator.raw_values import (
    ExhaustedDomain,
    generate_boolean,
    generate_categorical,
    generate_random_string,
    generate_scalar,
    generate_template,
)
from data_pipeline_diagnostics.generator.rng import stream as make_stream
from data_pipeline_diagnostics.scenario.generators import (
    BooleanGenerator,
    CategoricalGenerator,
    ForeignKeyGenerator,
    RandomStringGenerator,
    TemplateStringGenerator,
)

SCENARIO = "g04-test"


def _stream(name: str, seed: int = 11):
    return make_stream(SCENARIO, seed, f"values/t/{name}")


def test_categorical_type_fidelity_bool_vs_int():
    cfg = CategoricalGenerator(values=(True, 1))
    rng = _stream("cat")
    draws = [generate_categorical(cfg, rng, i) for i in range(50)]
    assert {type(v) for v in draws} == {bool, int}


def test_categorical_repeatable_and_in_domain():
    cfg = CategoricalGenerator(values=("a", "b", "c"))
    first = [generate_categorical(cfg, _stream("cat"), i) for i in range(30)]
    second = [generate_categorical(cfg, _stream("cat"), i) for i in range(30)]
    assert first == second
    assert set(first) <= {"a", "b", "c"}


def test_categorical_zero_weight_never_selected():
    cfg = CategoricalGenerator(values=("a", "b", "c"), weights=(1.0, 0.0, 2.0))
    rng = _stream("cat")
    draws = [generate_categorical(cfg, rng, i) for i in range(200)]
    assert "b" not in draws
    assert set(draws) <= {"a", "c"}


def test_categorical_unique_without_replacement_then_exhausts():
    cfg = CategoricalGenerator(values=("a", "b"), weights=(1.0, 1.0))
    rng = _stream("cat")
    first = generate_categorical(cfg, rng, 0, unique=True)
    second = generate_categorical(cfg, rng, 1, unique=True, already_drawn=[first])
    assert {first, second} == {"a", "b"}
    with pytest.raises(ExhaustedDomain):
        generate_categorical(cfg, rng, 2, unique=True, already_drawn=[first, second])


def test_categorical_unique_unweighted_exhaustion():
    cfg = CategoricalGenerator(values=("x",))
    with pytest.raises(ExhaustedDomain):
        generate_scalar(cfg, _stream("cat"), 1, unique=True, already_drawn=["x"])


def test_boolean_repeatable_and_extremes_deterministic():
    cfg = BooleanGenerator(true_probability=0.3)
    first = [generate_boolean(cfg, _stream("b"), i) for i in range(30)]
    second = [generate_boolean(cfg, _stream("b"), i) for i in range(30)]
    assert first == second
    rng = _stream("bv")
    varied = [generate_boolean(cfg, rng, i) for i in range(30)]
    assert set(varied) == {True, False}
    assert all(
        generate_boolean(BooleanGenerator(true_probability=0.0), _stream("b0"), i) is False
        for i in range(20)
    )
    assert all(
        generate_boolean(BooleanGenerator(true_probability=1.0), _stream("b1"), i) is True
        for i in range(20)
    )


def test_random_string_bounds_and_alphabet():
    cfg = RandomStringGenerator(min_length=3, max_length=8, alphabet="ab")
    first = [generate_random_string(cfg, _stream("s"), i) for i in range(30)]
    second = [generate_random_string(cfg, _stream("s"), i) for i in range(30)]
    assert first == second
    assert all(3 <= len(v) <= 8 for v in first)
    assert all(set(v) <= {"a", "b"} for v in first)


def test_template_mixed_types():
    cfg = TemplateStringGenerator(template="{name}#{n}#{f}#{b}#{d}#{ts}")
    row = {
        "name": "Müller",
        "n": 42,
        "f": 1.5,
        "b": True,
        "d": date(2024, 3, 5),
        "ts": datetime(2024, 3, 5, 10, 30, tzinfo=UTC),
    }
    assert (
        generate_template(cfg, _stream("t"), 0, row)
        == "Müller#42#1.5#true#2024-03-05#2024-03-05T10:30:00+00:00"
    )
    assert generate_scalar(cfg, _stream("t"), 0, placeholders=row) == (
        "Müller#42#1.5#true#2024-03-05#2024-03-05T10:30:00+00:00"
    )


def test_template_null_placeholder_yields_null():
    cfg = TemplateStringGenerator(template="id-{n}")
    assert generate_template(cfg, _stream("t"), 0, {"n": None}) is None


def test_template_requires_placeholders_in_dispatch():
    cfg = TemplateStringGenerator(template="id-{n}")
    with pytest.raises(ValueError):
        generate_scalar(cfg, _stream("t"), 0)


def test_dispatch_unknown_kind_raises():
    cfg = ForeignKeyGenerator(relationship="rel_a", target_side="left")
    with pytest.raises(ValueError):
        generate_scalar(cfg, _stream("fk"), 0)
