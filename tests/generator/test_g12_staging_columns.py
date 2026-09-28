"""G12 tests: staging column pipelines (§14.2).

Barrier technique (whole renderer, G13–G15): the column-expression phase of
every model is emitted as a ``MATERIALIZED`` CTE (``materialized_cte``)
before any row-operation phase, so an error-producing cast or
``map_values(error)`` cannot be skipped or reordered past a filter by the
optimizer. Column expressions themselves stay plain function/CASE
applications, pinned here as exact snapshots.
"""

from __future__ import annotations

from types import SimpleNamespace

import duckdb
import pytest

from data_pipeline_diagnostics.generator.sql_render import (
    materialized_cte,
    render_staging_column,
)
from data_pipeline_diagnostics.scenario.staging import (
    CastOperation,
    CoalesceOperation,
    LowerOperation,
    MapValuesOperation,
    NullIfOperation,
    ReplaceOperation,
    StagingColumn,
    TrimOperation,
    UpperOperation,
)
from data_pipeline_diagnostics.scenario.types import DataType

SOURCE = '"code"'
MODEL = "stg_m"


def _render(operations, source=SOURCE, target="code"):
    return render_staging_column(
        source,
        StagingColumn(source="code", target=target, operations=tuple(operations)),
        model=MODEL,
    )


def test_single_op_snapshots():
    assert _render([TrimOperation()]) == 'trim("code") AS "code"'
    assert _render([LowerOperation()]) == 'lower("code") AS "code"'
    assert _render([UpperOperation()]) == 'upper("code") AS "code"'
    assert _render([ReplaceOperation(old="x", new="y")]) == (
        "replace(\"code\", 'x', 'y') AS \"code\""
    )
    assert _render([NullIfOperation(values=("x", "y"))]) == (
        'CASE WHEN "code" IN (\'x\', \'y\') THEN NULL ELSE "code" END AS "code"'
    )
    assert _render([CoalesceOperation(value="n/a")]) == ('coalesce("code", \'n/a\') AS "code"')
    assert _render([CoalesceOperation(value=3)]) == 'coalesce("code", 3) AS "code"'
    assert _render([CoalesceOperation(value=True)]) == 'coalesce("code", TRUE) AS "code"'


def test_three_op_type_changing_chain():
    rendered = _render(
        [
            TrimOperation(),
            ReplaceOperation(old="x", new="y"),
            CastOperation(type=DataType.integer),
        ]
    )
    assert rendered == "CAST(replace(trim(\"code\"), 'x', 'y') AS BIGINT) AS \"code\""


def test_map_values_modes():
    keep = _render([MapValuesOperation(mapping={"a": "A", "b": "B"}, on_unmapped="keep")])
    assert keep == (
        'CASE WHEN "code" IS NULL THEN NULL '
        "WHEN \"code\" = 'a' THEN 'A' WHEN \"code\" = 'b' THEN 'B' "
        'ELSE "code" END AS "code"'
    )
    null = _render([MapValuesOperation(mapping={"a": "A"}, on_unmapped="null")])
    assert null == (
        'CASE WHEN "code" IS NULL THEN NULL WHEN "code" = \'a\' THEN \'A\' ELSE NULL END AS "code"'
    )
    error = _render([MapValuesOperation(mapping={"a": "A"}, on_unmapped="error")])
    assert error == (
        "CASE WHEN \"code\" IS NULL THEN NULL WHEN \"code\" = 'a' THEN 'A' "
        "ELSE error('dpd_unmapped_value(stg_m, code)') END AS \"code\""
    )
    rendered = _render([MapValuesOperation(mapping={"zzz": "A"}, on_unmapped="error")])
    assert "error('dpd_unmapped_value(stg_m, code)')" in rendered
    assert rendered.count("zzz") == 1  # only the WHEN key; the message is value-free


def test_cast_with_and_without_format():
    assert _render([CastOperation(type=DataType.integer)]) == ('CAST("code" AS BIGINT) AS "code"')
    assert _render([CastOperation(type=DataType.date, format="%Y-%m-%d")]) == (
        'CAST(strptime("code", \'%Y-%m-%d\') AS DATE) AS "code"'
    )
    assert _render([CastOperation(type=DataType.timestamp, format="%Y-%m-%d %H:%M:%S")]) == (
        'CAST(strptime("code", \'%Y-%m-%d %H:%M:%S\') AS TIMESTAMP WITH TIME ZONE) AS "code"'
    )


def test_no_try_cast_in_rendered_sql():
    snapshots = [
        _render([TrimOperation()]),
        _render([LowerOperation()]),
        _render([MapValuesOperation(mapping={"a": "A"}, on_unmapped="error")]),
        _render([CastOperation(type=DataType.date, format="%Y-%m-%d")]),
        _render([CastOperation(type=DataType.integer)]),
        _render(
            [
                TrimOperation(),
                ReplaceOperation(old="x", new="y"),
                CastOperation(type=DataType.integer),
            ]
        ),
    ]
    assert all("TRY_CAST" not in snapshot for snapshot in snapshots)


def test_materialized_cte_shape():
    assert materialized_cte("cols", "SELECT 1") == '"cols" AS MATERIALIZED (SELECT 1)'


def test_unknown_op_raises():
    with pytest.raises(ValueError):
        render_staging_column(
            SOURCE,
            StagingColumn(source="code", target="code", operations=(SimpleNamespace(op="bogus"),)),
            model=MODEL,
        )


def test_expression_semantics_spot_check():
    db = duckdb.connect()
    try:
        db.execute("SET TimeZone = 'UTC'")
        (value,) = db.execute(
            "SELECT "
            + _render([TrimOperation()], source="'  ax  '", target="v").rsplit(" AS ", 1)[0]
        ).fetchone()
        assert value == "ax"
        (mapped,) = db.execute(
            "SELECT "
            + _render(
                [MapValuesOperation(mapping={"a": "A"}, on_unmapped="keep")],
                source="'b'",
                target="v",
            ).rsplit(" AS ", 1)[0]
        ).fetchone()
        assert mapped == "b"
    finally:
        db.close()
