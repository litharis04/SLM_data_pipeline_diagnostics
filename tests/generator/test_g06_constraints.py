"""G06 tests: null insertion and single-column hard constraints.

Technique notes: retries consume only the column's own values stream (the
per-row null decision is drawn once and kept); full-column repeatability
pins this. ``RETRY_LIMIT`` is exercised through the tiny-integer-domain path
(3rd+ distinct value impossible); the categorical path raises
``unique-domain-exhausted`` without burning the retry budget.
"""

from __future__ import annotations

import pytest

from data_pipeline_diagnostics.generator.raw_constraints import (
    RETRY_LIMIT,
    GenerationFailure,
    generate_composite_null_mask,
    generate_scalar_column,
    generate_scalar_null_mask,
)
from data_pipeline_diagnostics.scenario.generators import (
    CategoricalGenerator,
    ForeignKeyGenerator,
    IntegerRangeGenerator,
    TemplateStringGenerator,
)
from data_pipeline_diagnostics.scenario.raw import RawColumn

SCENARIO = "g06-test"


def _col(**kwargs) -> RawColumn:
    base = {
        "name": "status",
        "type": "string",
        "nullable": True,
        "null_probability": 0.3,
        "unique": False,
        "generator": CategoricalGenerator(values=("a", "b", "c")),
    }
    base.update(kwargs)
    return RawColumn(**base)


def _call(column: RawColumn, rows: int, seed: int = 11, **kwargs):
    return generate_scalar_column(
        scenario_id=SCENARIO,
        data_seed=seed,
        table="orders",
        column=column,
        row_count=rows,
        **kwargs,
    )


def test_null_probability_edges_exact():
    assert (
        generate_scalar_null_mask(
            scenario_id=SCENARIO,
            data_seed=11,
            table="t",
            column="c",
            row_count=50,
            nullable=True,
            null_probability=0.0,
        )
        == [False] * 50
    )
    assert (
        generate_scalar_null_mask(
            scenario_id=SCENARIO,
            data_seed=11,
            table="t",
            column="c",
            row_count=50,
            nullable=True,
            null_probability=1.0,
        )
        == [True] * 50
    )


def test_nullable_column_yields_both_nulls_and_values():
    values = _call(_col(), 200)
    assert any(v is None for v in values)
    assert any(v is not None for v in values)


def test_non_nullable_and_pk_never_null():
    plain = _call(_col(nullable=False, null_probability=0.0), 50)
    assert all(v is not None for v in plain)
    forced = _call(_col(), 50, force_non_null=True)
    assert all(v is not None for v in forced)


def test_full_column_repeatable():
    col = _col(generator=IntegerRangeGenerator(min=0, max=10**6), unique=True)
    assert _call(col, 100) == _call(col, 100)


def test_composite_tuple_atomicity():
    mask = generate_composite_null_mask(
        scenario_id=SCENARIO,
        data_seed=11,
        relationship="rel_a",
        dependent_table="orders",
        target_side="left",
        row_count=200,
        components=[(True, 0.4), (True, 0.4)],
    )
    tuples = [(None, None) if m else ("v1", "v2") for m in mask]
    assert all((a is None) == (b is None) for a, b in tuples)
    assert any(m for m in mask) and any(not m for m in mask)


def test_composite_disagreement_and_non_nullable():
    with pytest.raises(GenerationFailure) as exc_info:
        generate_composite_null_mask(
            scenario_id=SCENARIO,
            data_seed=11,
            relationship="rel_a",
            dependent_table="orders",
            target_side="left",
            row_count=10,
            components=[(True, 0.4), (True, 0.5)],
        )
    assert exc_info.value.reason == "composite-null-disagreement"
    assert (
        generate_composite_null_mask(
            scenario_id=SCENARIO,
            data_seed=11,
            relationship="rel_a",
            dependent_table="orders",
            target_side="left",
            row_count=10,
            components=[(False, 0.0), (False, 0.0)],
        )
        == [False] * 10
    )


def test_unique_column_no_duplicate_non_nulls():
    col = _col(
        nullable=True,
        generator=IntegerRangeGenerator(min=0, max=10**6),
        unique=True,
    )
    values = _call(col, 200)
    non_null = [v for v in values if v is not None]
    assert len(set(non_null)) == len(non_null)
    assert any(v is None for v in values)


def test_categorical_tiny_domain_exhaustion():
    col = _col(
        nullable=False,
        null_probability=0.0,
        unique=True,
        generator=CategoricalGenerator(
            values=("a", "b", "c", "d", "e"),
        ),
    )
    with pytest.raises(GenerationFailure) as exc_info:
        _call(col, 10)
    assert exc_info.value.reason == "unique-domain-exhausted"


def test_retry_limit_exhaustion():
    assert RETRY_LIMIT == 1000
    col = _col(
        nullable=False,
        null_probability=0.0,
        unique=True,
        generator=IntegerRangeGenerator(min=0, max=1),
    )
    with pytest.raises(GenerationFailure) as exc_info:
        _call(col, 5)
    assert exc_info.value.reason == "unique-retry-limit-exceeded"


def test_context_kinds_rejected():
    fk_col = _col(generator=ForeignKeyGenerator(relationship="rel_a", target_side="left"))
    with pytest.raises(ValueError):
        _call(fk_col, 5)
    tpl_col = _col(generator=TemplateStringGenerator(template="id-{x}"))
    with pytest.raises(ValueError):
        _call(tpl_col, 5)
