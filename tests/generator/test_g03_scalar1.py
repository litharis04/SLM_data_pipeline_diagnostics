"""G03 tests: part-1 scalar proposals (id / numeric / date / timestamp).

Technique notes: all draws come from G02 named streams (no global RNG);
``formatted_id`` non-consumption is pinned via ``getstate`` comparison. The
contract forbids ``min == max`` (strict ``<``), so the degenerate date width
is covered by a one-day span asserting the result is one of the two bounds.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone

import pytest

from data_pipeline_diagnostics.generator.raw_values import (
    generate_date,
    generate_float,
    generate_formatted_id,
    generate_integer,
    generate_scalar,
    generate_timestamp,
)
from data_pipeline_diagnostics.generator.rng import stream as make_stream
from data_pipeline_diagnostics.scenario.generators import (
    DateRangeGenerator,
    FloatRangeGenerator,
    ForeignKeyGenerator,
    FormattedIdGenerator,
    IntegerRangeGenerator,
    TimestampRangeGenerator,
)

SCENARIO = "g03-test"


def _stream(name: str, seed: int = 11):
    return make_stream(SCENARIO, seed, f"values/t/{name}")


def test_formatted_id_zero_pad_and_prefix():
    cfg = FormattedIdGenerator(prefix="U-", digits=3, start=7)
    assert generate_formatted_id(cfg, _stream("id"), 0) == "U-007"
    assert generate_formatted_id(cfg, _stream("id"), 5) == "U-012"
    assert generate_scalar(cfg, _stream("id"), 0) == "U-007"


def test_formatted_id_overflow_raises():
    cfg = FormattedIdGenerator(prefix="X", digits=2, start=99)
    with pytest.raises(ValueError):
        generate_formatted_id(cfg, _stream("id"), 1)


def test_formatted_id_consumes_no_rng():
    cfg = FormattedIdGenerator(prefix="U-", digits=3, start=7)
    rng = _stream("id")
    before = rng.getstate()
    generate_formatted_id(cfg, rng, 3)
    assert rng.getstate() == before


def test_integer_range_repeatable_and_bounded():
    cfg = IntegerRangeGenerator(min=5, max=10)
    first = [generate_integer(cfg, _stream("n"), i) for i in range(20)]
    second = [generate_integer(cfg, _stream("n"), i) for i in range(20)]
    assert first == second
    assert all(5 <= v <= 10 for v in first)
    assert generate_scalar(cfg, _stream("n"), 0) in range(5, 11)


def test_integer_range_adjacent_bounds():
    cfg = IntegerRangeGenerator(min=8, max=9)
    assert {generate_integer(cfg, _stream("n"), i) for i in range(20)} <= {8, 9}


def test_float_range_repeatable_and_bounded():
    cfg = FloatRangeGenerator(min=1.5, max=2.5, decimal_places=1)
    first = [generate_float(cfg, _stream("f"), i) for i in range(30)]
    second = [generate_float(cfg, _stream("f"), i) for i in range(30)]
    assert first == second
    assert all(1.5 <= v <= 2.5 for v in first)
    assert all(round(v * 10) == v * 10 for v in first)


def test_float_range_zero_decimal_places():
    cfg = FloatRangeGenerator(min=1.0, max=2.0, decimal_places=0)
    assert {generate_float(cfg, _stream("f"), i) for i in range(20)} <= {1.0, 2.0}


def test_float_range_empty_lattice_raises():
    cfg = FloatRangeGenerator(min=0.05, max=0.06, decimal_places=0)
    with pytest.raises(ValueError):
        generate_float(cfg, _stream("f"), 0)


def test_date_range_repeatable_and_bounded():
    cfg = DateRangeGenerator(min=date(2024, 1, 1), max=date(2024, 12, 31))
    first = [generate_date(cfg, _stream("d"), i) for i in range(20)]
    second = [generate_date(cfg, _stream("d"), i) for i in range(20)]
    assert first == second
    assert all(date(2024, 1, 1) <= v <= date(2024, 12, 31) for v in first)


def test_date_range_one_day_span():
    cfg = DateRangeGenerator(min=date(2024, 5, 1), max=date(2024, 5, 2))
    assert {generate_date(cfg, _stream("d"), i) for i in range(20)} <= {
        date(2024, 5, 1),
        date(2024, 5, 2),
    }


def test_timestamp_range_utc_and_bounded():
    tz = timezone(timedelta(hours=2))
    cfg = TimestampRangeGenerator(
        min=datetime(2024, 1, 1, 0, 0, tzinfo=tz),
        max=datetime(2024, 1, 2, 0, 0, tzinfo=tz),
    )
    first = [generate_timestamp(cfg, _stream("ts"), i) for i in range(20)]
    second = [generate_timestamp(cfg, _stream("ts"), i) for i in range(20)]
    assert first == second
    lo = datetime(2024, 1, 1, 0, 0, tzinfo=tz).astimezone(UTC)
    hi = datetime(2024, 1, 2, 0, 0, tzinfo=tz).astimezone(UTC)
    for v in first:
        assert v.tzinfo is not None and v.utcoffset() == timedelta(0)
        assert lo <= v <= hi


def test_dispatch_unknown_kind_raises():
    # foreign_key has no scalar execution (§10.8); the part-1/2 dispatch rejects it.
    # (boolean was the placeholder here until G04 implemented it.)
    with pytest.raises(ValueError):
        generate_scalar(
            ForeignKeyGenerator(relationship="rel_a", target_side="left"),
            _stream("fk"),
            0,
        )
