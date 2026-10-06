"""G09 tests: Parquet round-trip and DuckDB loading.

Technique notes: DuckDB-native writer (no extra dependency); fixed
``snappy`` compression and row-group size are module constants. Row
comparisons are order-insensitive (relations are unordered); column order is
pinned separately via the catalog. The negative case crafts the bad Parquet
with DuckDB itself so the test needs no second writer.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path

import duckdb
import pytest

from data_pipeline_diagnostics.generator.physical import (
    DUCKDB_TYPE,
    GenerationFailure,
    RawTableLayout,
    load_raw_into_duckdb,
    write_raw_parquet,
)
from data_pipeline_diagnostics.scenario.types import DataType

LAYOUT = RawTableLayout(
    columns=(
        ("s", DataType.string),
        ("n", DataType.integer),
        ("f", DataType.float),
        ("b", DataType.boolean),
        ("d", DataType.date),
        ("ts", DataType.timestamp),
    ),
    row_count=3,
)

ROWS = [
    {
        "s": "a",
        "n": 1,
        "f": 1.5,
        "b": True,
        "d": date(1965, 3, 4),
        "ts": datetime(2024, 1, 1, 12, 0, tzinfo=timezone(timedelta(hours=5, minutes=30))),
    },
    {
        "s": "b",
        "n": -(2**62),
        "f": -0.25,
        "b": False,
        "d": date(2023, 12, 31),
        "ts": datetime(1970, 1, 1, tzinfo=UTC),
    },
    {
        "s": "",
        "n": 0,
        "f": 0.0,
        "b": True,
        "d": date(2000, 2, 29),
        "ts": datetime(1999, 12, 31, 23, 59, 59, tzinfo=timezone(timedelta(hours=-8))),
    },
]


def _read_all(db_path: Path, table: str) -> list[tuple]:
    db = duckdb.connect(str(db_path))
    try:
        db.execute("SET TimeZone = 'UTC'")
        return db.execute(f'SELECT * FROM "raw"."{table}" ORDER BY "s"').fetchall()
    finally:
        db.close()


def test_type_map_covers_all_six():
    assert DUCKDB_TYPE == {
        DataType.string: "VARCHAR",
        DataType.integer: "BIGINT",
        DataType.float: "DOUBLE",
        DataType.boolean: "BOOLEAN",
        DataType.date: "DATE",
        DataType.timestamp: "TIMESTAMP WITH TIME ZONE",
    }


def test_round_trip_all_types(tmp_path):
    raw_dir = tmp_path / "raw"
    db_path = tmp_path / "pipeline.duckdb"
    assert (
        write_raw_parquet(raw_dir=raw_dir, table="t", layout=LAYOUT, rows=ROWS).name == "t.parquet"
    )
    assert load_raw_into_duckdb(raw_dir=raw_dir, db_path=db_path, tables={"t": LAYOUT}) == {"t": 3}
    expected = sorted(
        (
            row["s"],
            row["n"],
            row["f"],
            row["b"],
            row["d"],
            row["ts"].astimezone(UTC),
        )
        for row in ROWS
    )
    assert _read_all(db_path, "t") == expected


def test_column_order_preserved(tmp_path):
    layout = RawTableLayout(
        columns=(("b_col", DataType.integer), ("a_col", DataType.string)), row_count=2
    )
    rows = [{"b_col": 1, "a_col": "x"}, {"b_col": 2, "a_col": "y"}]
    write_raw_parquet(raw_dir=tmp_path / "raw", table="t", layout=layout, rows=rows)
    load_raw_into_duckdb(
        raw_dir=tmp_path / "raw", db_path=tmp_path / "pipeline.duckdb", tables={"t": layout}
    )
    db = duckdb.connect(str(tmp_path / "pipeline.duckdb"))
    try:
        names = [
            row[0]
            for row in db.execute(
                "SELECT column_name FROM duckdb_columns() "
                "WHERE schema_name = 'raw' AND table_name = 't' ORDER BY column_index"
            ).fetchall()
        ]
    finally:
        db.close()
    assert names == ["b_col", "a_col"]


def _craft_parquet(raw_dir: Path, table: str, ddl: str, values: list[tuple]):
    raw_dir.mkdir(parents=True, exist_ok=True)
    mem = duckdb.connect()
    try:
        mem.execute(f"CREATE TABLE bad ({ddl})")
        mem.executemany(f"INSERT INTO bad VALUES ({', '.join(['?'] * len(values[0]))})", values)
        mem.execute(f"COPY bad TO '{raw_dir / f'{table}.parquet'}' (FORMAT PARQUET)")
    finally:
        mem.close()


def test_extra_column_rejected(tmp_path):
    _craft_parquet(tmp_path / "raw", "t", '"s" VARCHAR, "hack" INTEGER', [("a", 1)])
    layout = RawTableLayout(columns=(("s", DataType.string),), row_count=1)
    with pytest.raises(GenerationFailure) as exc_info:
        load_raw_into_duckdb(
            raw_dir=tmp_path / "raw", db_path=tmp_path / "pipeline.duckdb", tables={"t": layout}
        )
    assert exc_info.value.reason == "parquet-schema-mismatch"


def test_reordered_columns_rejected(tmp_path):
    _craft_parquet(tmp_path / "raw", "t", '"a_col" VARCHAR, "b_col" INTEGER', [("x", 1)])
    layout = RawTableLayout(
        columns=(("b_col", DataType.integer), ("a_col", DataType.string)), row_count=1
    )
    with pytest.raises(GenerationFailure):
        load_raw_into_duckdb(
            raw_dir=tmp_path / "raw", db_path=tmp_path / "pipeline.duckdb", tables={"t": layout}
        )


def test_reload_replaces_never_merges(tmp_path):
    raw_dir = tmp_path / "raw"
    db_path = tmp_path / "pipeline.duckdb"
    layout_v1 = RawTableLayout(columns=(("s", DataType.string),), row_count=3)
    layout_t2 = RawTableLayout(columns=(("s", DataType.string),), row_count=2)
    write_raw_parquet(raw_dir=raw_dir, table="t1", layout=layout_v1, rows=[{"s": "a"}] * 3)
    write_raw_parquet(raw_dir=raw_dir, table="t2", layout=layout_t2, rows=[{"s": "b"}] * 2)
    assert load_raw_into_duckdb(
        raw_dir=raw_dir, db_path=db_path, tables={"t1": layout_v1, "t2": layout_t2}
    ) == {"t1": 3, "t2": 2}

    layout_v2 = RawTableLayout(columns=(("s", DataType.string),), row_count=5)
    write_raw_parquet(raw_dir=raw_dir, table="t2", layout=layout_v2, rows=[{"s": "c"}] * 5)
    assert load_raw_into_duckdb(raw_dir=raw_dir, db_path=db_path, tables={"t2": layout_v2}) == {
        "t2": 5
    }
    db = duckdb.connect(str(db_path))
    try:
        names = {
            row[0]
            for row in db.execute(
                "SELECT table_name FROM duckdb_tables() WHERE schema_name = 'raw'"
            ).fetchall()
        }
        count = db.execute('SELECT COUNT(*) FROM "raw"."t2"').fetchone()[0]
    finally:
        db.close()
    assert names == {"t2"}
    assert count == 5
