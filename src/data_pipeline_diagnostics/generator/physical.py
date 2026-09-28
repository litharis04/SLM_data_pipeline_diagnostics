"""Fixed physical contract: Parquet output + DuckDB loading (§§5.1–5.3, 12).

Single source of truth for the scenario-type → DuckDB-type map (G10 extends
this module with quoting/literal helpers; the map itself is final here).

Writer (DuckDB-native, no extra dependency): one Parquet file per raw table
at ``raw/<T>.parquet`` with exactly the declared columns in declaration
order — no index, lineage, seed or label columns. Timestamps are normalized
to UTC before the write; the session timezone is UTC. Compression and
row-group settings are fixed module constants (recorded in the instance
record later).

Loader: a fresh ``pipeline.duckdb`` in the caller's workspace directory
(any pre-existing file is removed, never merged), ``raw`` schema, one typed
table per Parquet file, then exact verification of column names, order,
DuckDB types and row counts. A mismatch raises :class:`GenerationFailure`
— an integration check, not a third validation stage. The database is
checkpointed and closed before return.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import duckdb

from data_pipeline_diagnostics.generator.raw_constraints import GenerationFailure
from data_pipeline_diagnostics.scenario.types import DataType

__all__ = [
    "DUCKDB_TYPE",
    "PARQUET_COMPRESSION",
    "PARQUET_ROW_GROUP_SIZE",
    "RawTableLayout",
    "load_raw_into_duckdb",
    "raw_parquet_path",
    "write_raw_parquet",
]

DUCKDB_TYPE: dict[DataType, str] = {
    DataType.string: "VARCHAR",
    DataType.integer: "BIGINT",
    DataType.float: "DOUBLE",
    DataType.boolean: "BOOLEAN",
    DataType.date: "DATE",
    DataType.timestamp: "TIMESTAMP WITH TIME ZONE",
}

PARQUET_COMPRESSION = "snappy"
PARQUET_ROW_GROUP_SIZE = 122880


@dataclass(frozen=True)
class RawTableLayout:
    """Declared raw-table shape: columns in order plus expected row count."""

    columns: tuple[tuple[str, DataType], ...]
    row_count: int


def raw_parquet_path(raw_dir: str | Path, table: str) -> Path:
    """Fixed identity mapping: raw table ``T`` → ``raw/T.parquet``."""
    return Path(raw_dir) / f"{table}.parquet"


def _quote(identifier: str) -> str:
    """Minimal double-quote helper (G10 owns the canonical one)."""
    return '"' + identifier.replace('"', '""') + '"'


def _normalize_value(value: object) -> object:
    if isinstance(value, datetime):
        moment = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
        return moment.astimezone(UTC)
    return value


def write_raw_parquet(
    *,
    raw_dir: str | Path,
    table: str,
    layout: RawTableLayout,
    rows: Sequence[Mapping[str, object]],
) -> Path:
    """Write exactly ``layout.columns`` (declaration order) to ``raw/<T>.parquet``."""
    declared = [name for name, _ in layout.columns]
    for i, row in enumerate(rows):
        extra = set(row) - set(declared)
        if extra:
            raise GenerationFailure(
                table=table,
                column=None,
                reason="extra-column",
                detail=f"row {i} carries undeclared columns {sorted(extra)}",
            )
        missing = set(declared) - set(row)
        if missing:
            raise GenerationFailure(
                table=table,
                column=None,
                reason="missing-column",
                detail=f"row {i} lacks declared columns {sorted(missing)}",
            )
    directory = Path(raw_dir)
    directory.mkdir(parents=True, exist_ok=True)
    path = raw_parquet_path(directory, table)
    columns_ddl = ", ".join(
        f"{_quote(name)} {DUCKDB_TYPE[dtype]}" for name, dtype in layout.columns
    )
    placeholders = ", ".join(["?"] * len(declared))
    try:
        mem = duckdb.connect()
        try:
            mem.execute("SET TimeZone = 'UTC'")
            mem.execute(f"CREATE TABLE staged ({columns_ddl})")
            mem.executemany(
                f"INSERT INTO staged VALUES ({placeholders})",
                [[_normalize_value(row[name]) for name in declared] for row in rows],
            )
            mem.execute(
                f"COPY staged TO '{path}' (FORMAT PARQUET, "
                f"COMPRESSION {PARQUET_COMPRESSION}, "
                f"ROW_GROUP_SIZE {PARQUET_ROW_GROUP_SIZE})"
            )
        finally:
            mem.close()
    except GenerationFailure:
        raise
    except Exception as exc:
        raise GenerationFailure(
            table=table,
            column=None,
            reason="parquet-write-failed",
            detail=f"{type(exc).__name__}: {exc}",
        ) from exc
    return path


def load_raw_into_duckdb(
    *,
    raw_dir: str | Path,
    db_path: str | Path,
    tables: Mapping[str, RawTableLayout],
) -> dict[str, int]:
    """Load Parquet files into a fresh ``pipeline.duckdb`` (UTC session).

    Returns per-table loaded row counts. Any mismatch against the declared
    layout raises ``GenerationFailure``.
    """
    destination = Path(db_path)
    if destination.exists():
        destination.unlink()
    destination.parent.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}
    try:
        db = duckdb.connect(str(destination))
        try:
            db.execute("SET TimeZone = 'UTC'")
            db.execute("CREATE SCHEMA raw")
            for table, layout in tables.items():
                path = raw_parquet_path(raw_dir, table)
                described = db.execute(f"DESCRIBE SELECT * FROM read_parquet('{path}')").fetchall()
                seen = [row[0] for row in described]
                declared = [name for name, _ in layout.columns]
                if seen != declared:
                    raise GenerationFailure(
                        table=table,
                        column=None,
                        reason="parquet-schema-mismatch",
                        detail=f"parquet columns {seen} != declared {declared}",
                    )
                select_list = ", ".join(
                    f"CAST({_quote(name)} AS {DUCKDB_TYPE[dtype]}) AS {_quote(name)}"
                    for name, dtype in layout.columns
                )
                db.execute(
                    f"CREATE TABLE {_quote('raw')}.{_quote(table)} AS "
                    f"SELECT {select_list} FROM read_parquet('{path}')"
                )
                info = db.execute(
                    f"SELECT column_name, data_type FROM duckdb_columns() "
                    f"WHERE schema_name = 'raw' AND table_name = '{table}' "
                    "ORDER BY column_index"
                ).fetchall()
                actual = [(row[0], str(row[1]).upper()) for row in info]
                expected = [(name, DUCKDB_TYPE[dtype]) for name, dtype in layout.columns]
                if actual != expected:
                    raise GenerationFailure(
                        table=table,
                        column=None,
                        reason="duckdb-type-mismatch",
                        detail=f"loaded {actual} != declared {expected}",
                    )
                (count,) = db.execute(
                    f"SELECT COUNT(*) FROM {_quote('raw')}.{_quote(table)}"
                ).fetchone()
                if count != layout.row_count:
                    raise GenerationFailure(
                        table=table,
                        column=None,
                        reason="row-count-mismatch",
                        detail=f"loaded {count} rows, expected {layout.row_count}",
                    )
                counts[table] = count
            db.execute("CHECKPOINT")
        finally:
            db.close()
    except GenerationFailure:
        raise
    except Exception as exc:
        raise GenerationFailure(
            table="*",
            column=None,
            reason="duckdb-load-failed",
            detail=f"{type(exc).__name__}: {exc}",
        ) from exc
    return counts
