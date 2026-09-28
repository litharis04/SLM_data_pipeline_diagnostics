"""G10 tests: quoting, literal round-trips, Jinja neutralization, constants.

Technique notes: Jinja neutralization uses SQL ``||`` concatenation whose
chunks never hold a brace pair contiguously (plain strings keep the fast
path). The observable contract — post-parse literal equals the scenario
value — is verified by evaluating every literal in DuckDB. YAML-embedded
strings cannot use SQL concatenation; their neutralizer belongs to
assertion lowering (G15).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone

import duckdb
import pytest

from data_pipeline_diagnostics.generator.physical import (
    DBT_PROFILE_NAME,
    DBT_PROJECT_NAME,
    DBT_TARGET_NAME,
    DUCKDB_TYPE,
    MAIN_SCHEMA_NAME,
    RAW_SCHEMA_NAME,
    RAW_SOURCE_NAME,
    literal_boolean,
    literal_date,
    literal_float,
    literal_integer,
    literal_string,
    literal_timestamp,
    quote_ident,
    sql_literal,
    write_text_file,
)
from data_pipeline_diagnostics.scenario.types import DataType


def _eval(expr: str):
    db = duckdb.connect()
    try:
        db.execute("SET TimeZone = 'UTC'")
        return db.execute(f"SELECT {expr}").fetchone()[0]
    finally:
        db.close()


def test_quote_ident_odd_names():
    assert quote_ident("select") == '"select"'
    assert quote_ident("with space") == '"with space"'
    assert quote_ident('we"ird') == '"we""ird"'
    assert quote_ident("raw") == '"raw"'
    with pytest.raises(TypeError):
        quote_ident("")
    with pytest.raises(TypeError):
        quote_ident(123)


def test_literal_round_trips():
    assert _eval(literal_string("O'Brien")) == "O'Brien"
    assert _eval(literal_string("")) == ""
    assert _eval(literal_string("plain")) == "plain"
    assert _eval(literal_integer(-42)) == -42
    assert _eval(literal_integer(0)) == 0
    assert _eval(literal_float(0.1)) == 0.1
    assert _eval(literal_float(-2.5)) == -2.5
    assert _eval(literal_boolean(True)) is True
    assert _eval(literal_boolean(False)) is False
    assert _eval(literal_date(date(1965, 3, 4))) == date(1965, 3, 4)
    moment = datetime(2024, 1, 1, 12, 0, tzinfo=timezone(timedelta(hours=5, minutes=30)))
    assert _eval(literal_timestamp(moment)) == moment.astimezone(UTC)


def test_literal_type_strictness():
    with pytest.raises(TypeError):
        literal_integer(True)
    with pytest.raises(TypeError):
        literal_boolean(1)
    with pytest.raises(TypeError):
        literal_date(datetime(2024, 1, 1))
    with pytest.raises(ValueError):
        literal_float(float("inf"))


def test_sql_literal_dispatch_exhaustive():
    assert set(DUCKDB_TYPE) == set(DataType)
    assert _eval(sql_literal("x", DataType.string)) == "x"
    assert _eval(sql_literal(7, DataType.integer)) == 7
    assert _eval(sql_literal(0.5, DataType.float)) == 0.5
    assert _eval(sql_literal(False, DataType.boolean)) is False
    assert _eval(sql_literal(date(2024, 5, 1), DataType.date)) == date(2024, 5, 1)
    moment = datetime(2023, 6, 1, tzinfo=UTC)
    assert _eval(sql_literal(moment, DataType.timestamp)) == moment


def test_jinja_payload_neutralized():
    for payload in ("{{ 7*7 }}", "{% if x %}hi{% endif %}", "{# note #}", "a{b}c"):
        rendered = literal_string(payload)
        assert "{{" not in rendered
        assert "}}" not in rendered
        assert "{%" not in rendered
        assert _eval(rendered) == payload


def test_fixed_constants():
    assert (RAW_SOURCE_NAME, RAW_SCHEMA_NAME, MAIN_SCHEMA_NAME) == ("raw", "raw", "main")
    assert (DBT_PROJECT_NAME, DBT_PROFILE_NAME, DBT_TARGET_NAME) == (
        "dpd_pipeline",
        "dpd_pipeline",
        "clean",
    )


def test_write_text_file_normalizes(tmp_path):
    target = write_text_file(tmp_path / "sub" / "model.sql", "SELECT 1;\r\nSELECT 2;")
    raw = target.read_bytes()
    assert raw == b"SELECT 1;\nSELECT 2;\n"
    assert write_text_file(tmp_path / "a.sql", "x\n\n").read_bytes() == b"x\n"
