"""Tests for mini-generator models and GeneratorSpec union (SCENARIO_SPEC §8)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from data_pipeline_diagnostics.scenario.base import ContractModel
from data_pipeline_diagnostics.scenario.generators import (
    BooleanGenerator,
    CategoricalGenerator,
    CityGenerator,
    CompanyNameGenerator,
    DateRangeGenerator,
    EmailGenerator,
    FloatRangeGenerator,
    ForeignKeyGenerator,
    FormattedIdGenerator,
    GeneratorSpec,
    IntegerRangeGenerator,
    PersonNameGenerator,
    PhoneNumberGenerator,
    RandomStringGenerator,
    StreetAddressGenerator,
    TemplateStringGenerator,
    TimestampRangeGenerator,
)


class Wrap(ContractModel):
    gen: GeneratorSpec


# ---------------------------------------------------------------------------
# FormattedId
# ---------------------------------------------------------------------------


def test_formatted_id_valid():
    assert FormattedIdGenerator(digits=5).prefix == ""
    assert FormattedIdGenerator(prefix="id_", digits=5, start=0).digits == 5
    assert FormattedIdGenerator(prefix="a", digits=1, start=0).start == 0


def test_formatted_id_invalid():
    with pytest.raises(ValidationError):
        FormattedIdGenerator(prefix="bad prefix", digits=5)
    with pytest.raises(ValidationError):
        FormattedIdGenerator(prefix="x" * 33, digits=5)
    with pytest.raises(ValidationError):
        FormattedIdGenerator(digits=0)
    with pytest.raises(ValidationError):
        FormattedIdGenerator(digits=19)
    with pytest.raises(ValidationError):
        FormattedIdGenerator(digits="5")  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        FormattedIdGenerator(digits=5, start=-1)
    with pytest.raises(ValidationError):
        FormattedIdGenerator(digits=5, start="1")  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        FormattedIdGenerator(prefix="é", digits=5)


# ---------------------------------------------------------------------------
# IntegerRange
# ---------------------------------------------------------------------------


def test_integer_range_valid():
    assert IntegerRangeGenerator(min=1, max=10).min == 1


def test_integer_range_invalid():
    with pytest.raises(ValidationError):
        IntegerRangeGenerator(min=10, max=1)
    with pytest.raises(ValidationError):
        IntegerRangeGenerator(min=1, max=1)
    with pytest.raises(ValidationError):
        IntegerRangeGenerator(min=1.0, max=10)  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        IntegerRangeGenerator(min=True, max=10)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# FloatRange
# ---------------------------------------------------------------------------


def test_float_range_valid():
    assert FloatRangeGenerator(min=1.0, max=2.0).decimal_places == 2
    assert FloatRangeGenerator(min=1.0, max=2.0, decimal_places=0).decimal_places == 0


def test_float_range_invalid():
    with pytest.raises(ValidationError):
        FloatRangeGenerator(min=1, max=2.0)  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        FloatRangeGenerator(min=2.0, max=1.0)
    with pytest.raises(ValidationError):
        FloatRangeGenerator(min=float("inf"), max=2.0)
    with pytest.raises(ValidationError):
        FloatRangeGenerator(min=float("nan"), max=2.0)
    with pytest.raises(ValidationError):
        FloatRangeGenerator(min=1.0, max=2.0, decimal_places=13)
    with pytest.raises(ValidationError):
        FloatRangeGenerator(min=1.0, max=2.0, decimal_places=-1)


# ---------------------------------------------------------------------------
# DateRange
# ---------------------------------------------------------------------------


def test_date_range_valid():
    assert DateRangeGenerator(min=date(2020, 1, 1), max=date(2020, 1, 2)).min == date(2020, 1, 1)


def test_date_range_invalid():
    with pytest.raises(ValidationError):
        DateRangeGenerator(min=date(2020, 1, 2), max=date(2020, 1, 1))
    with pytest.raises(ValidationError):
        DateRangeGenerator(min=date(2020, 1, 1), max=date(2020, 1, 1))
    with pytest.raises(ValidationError):
        DateRangeGenerator(min="2020-01-01", max=date(2020, 1, 2))  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# TimestampRange
# ---------------------------------------------------------------------------


def test_timestamp_range_valid():
    assert (
        TimestampRangeGenerator(
            min=datetime(2020, 1, 1, tzinfo=UTC),
            max=datetime(2020, 1, 2, tzinfo=UTC),
        ).min.tzinfo
        is not None
    )
    # different offsets that normalize correctly
    assert TimestampRangeGenerator(
        min=datetime(2020, 1, 1, tzinfo=timezone(timedelta(hours=2))),
        max=datetime(2020, 1, 2, tzinfo=UTC),
    )


def test_timestamp_range_invalid():
    with pytest.raises(ValidationError):
        TimestampRangeGenerator(min=datetime(2020, 1, 1), max=datetime(2020, 1, 2, tzinfo=UTC))
    with pytest.raises(ValidationError):
        TimestampRangeGenerator(
            min=datetime(2020, 1, 2, tzinfo=UTC),
            max=datetime(2020, 1, 1, tzinfo=UTC),
        )
    with pytest.raises(ValidationError):
        TimestampRangeGenerator(
            min=datetime(2020, 1, 1, tzinfo=UTC),
            max=datetime(2020, 1, 1, tzinfo=UTC),
        )


# ---------------------------------------------------------------------------
# Categorical
# ---------------------------------------------------------------------------


def test_categorical_valid():
    assert CategoricalGenerator(values=("a", "b", "c")).weights is None
    assert CategoricalGenerator(values=("a", "b"), weights=(0.5, 0.5)).weights == (0.5, 0.5)
    assert CategoricalGenerator(values=(1, 2, 3)).values == (1, 2, 3)
    assert CategoricalGenerator(values=(True, False)).values == (True, False)
    # bool vs int distinct
    assert CategoricalGenerator(values=(True, 1)).values == (True, 1)


def test_categorical_invalid():
    with pytest.raises(ValidationError):
        CategoricalGenerator(values=("a", "a"))
    with pytest.raises(ValidationError):
        CategoricalGenerator(values=("a", "b"), weights=(0.5,))
    with pytest.raises(ValidationError):
        CategoricalGenerator(values=("a", "b"), weights=(-0.1, 0.5))
    with pytest.raises(ValidationError):
        CategoricalGenerator(values=("a", "b"), weights=(0.0, 0.0))
    with pytest.raises(ValidationError):
        CategoricalGenerator(values=())
    with pytest.raises(ValidationError):
        CategoricalGenerator(values=("a", "b"), weights=(float("inf"), 1.0))
    with pytest.raises(ValidationError):
        CategoricalGenerator(values=("a",), weights=(1,))  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Boolean
# ---------------------------------------------------------------------------


def test_boolean_valid():
    assert BooleanGenerator().true_probability == 0.5
    assert BooleanGenerator(true_probability=0.0).true_probability == 0.0
    assert BooleanGenerator(true_probability=1.0).true_probability == 1.0


def test_boolean_invalid():
    with pytest.raises(ValidationError):
        BooleanGenerator(true_probability=1)  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        BooleanGenerator(true_probability=1.5)
    with pytest.raises(ValidationError):
        BooleanGenerator(true_probability=float("inf"))


# ---------------------------------------------------------------------------
# RandomString
# ---------------------------------------------------------------------------


def test_random_string_valid():
    assert (
        RandomStringGenerator(min_length=1, max_length=5).alphabet
        == "abcdefghijklmnopqrstuvwxyz0123456789"
    )
    assert RandomStringGenerator(min_length=1, max_length=1, alphabet="abc").alphabet == "abc"


def test_random_string_invalid():
    with pytest.raises(ValidationError):
        RandomStringGenerator(min_length=5, max_length=1)
    with pytest.raises(ValidationError):
        RandomStringGenerator(min_length=1, max_length=5, alphabet="aa")
    with pytest.raises(ValidationError):
        RandomStringGenerator(min_length=0, max_length=5)
    with pytest.raises(ValidationError):
        RandomStringGenerator(min_length=1, max_length=5, alphabet="")


# ---------------------------------------------------------------------------
# TemplateString
# ---------------------------------------------------------------------------


def test_template_string_valid():
    assert TemplateStringGenerator(template="{a}").template == "{a}"
    assert (
        TemplateStringGenerator(template="hello {first_name} world").template
        == "hello {first_name} world"
    )
    assert (
        TemplateStringGenerator(template="{first_name}.{last_name}@example.test").template
        == "{first_name}.{last_name}@example.test"
    )


def test_template_string_invalid():
    with pytest.raises(ValidationError):
        TemplateStringGenerator(template="no placeholder")
    with pytest.raises(ValidationError):
        TemplateStringGenerator(template="{Invalid}")
    with pytest.raises(ValidationError):
        TemplateStringGenerator(template="{a")
    with pytest.raises(ValidationError):
        TemplateStringGenerator(template="{}")
    with pytest.raises(ValidationError):
        TemplateStringGenerator(template="a" * 257)


def test_template_string_duplicate_allowed():
    # duplicate placeholder names are allowed locally; semantic validator checks existence
    assert TemplateStringGenerator(template="{a} {a}").template == "{a} {a}"


# ---------------------------------------------------------------------------
# ForeignKey
# ---------------------------------------------------------------------------


def test_foreign_key_valid():
    assert ForeignKeyGenerator(relationship="my_rel", target_side="left").target_side == "left"
    assert ForeignKeyGenerator(relationship="my_rel", target_side="right").relationship == "my_rel"


def test_foreign_key_invalid():
    with pytest.raises(ValidationError):
        ForeignKeyGenerator(relationship="MyRel", target_side="left")
    with pytest.raises(ValidationError):
        ForeignKeyGenerator(relationship="my_rel", target_side="invalid")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Faker generators
# ---------------------------------------------------------------------------


def test_faker_generators_valid():
    assert PersonNameGenerator().kind == "person_name"
    assert EmailGenerator(locale="de_DE").locale == "de_DE"
    assert CityGenerator().locale == "en_US"
    assert StreetAddressGenerator().locale == "en_US"
    assert CompanyNameGenerator().locale == "en_US"
    assert PhoneNumberGenerator().locale == "en_US"
    for cls in [
        PersonNameGenerator,
        EmailGenerator,
        CityGenerator,
        StreetAddressGenerator,
        CompanyNameGenerator,
        PhoneNumberGenerator,
    ]:
        assert cls().kind in [
            "person_name",
            "email",
            "city",
            "street_address",
            "company_name",
            "phone_number",
        ]
        with pytest.raises(ValidationError):
            cls(locale="")  # type: ignore[arg-type]
        with pytest.raises(ValidationError):
            cls(locale=123)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Discriminated union
# ---------------------------------------------------------------------------


def test_generator_spec_union():
    assert Wrap(gen={"kind": "integer_range", "min": 1, "max": 2}).gen.kind == "integer_range"
    assert Wrap(gen={"kind": "boolean", "true_probability": 0.7}).gen.kind == "boolean"
    assert Wrap(gen={"kind": "person_name", "locale": "en_US"}).gen.kind == "person_name"
    with pytest.raises(ValidationError, match="union_tag_invalid"):
        Wrap(gen={"kind": "unknown", "min": 1})  # type: ignore[arg-type]
    with pytest.raises(ValidationError, match="union_tag_not_found"):
        Wrap(gen={"min": 1, "max": 2})  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        Wrap(gen={"kind": "integer_range", "min": 1, "max": 2, "extra": "x"})  # type: ignore[arg-type]
