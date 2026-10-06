"""Fixed physical contract: naming, quoting, literals, types (§§5.1–5.3).

Single source of truth for identifier mappings, the one quoting helper,
type-aware SQL literals and the DuckDB type map.

Identity mappings: raw table ``T`` → ``raw/T.parquet``, ``"raw"."T"``,
``source('raw', 'T')``; model ``M`` → ``M.sql``, ``ref('M')``,
``"main"."M"``; column ``C`` → ``C`` verbatim. All identifiers pass through
:func:`quote_ident` (double quotes, ``"`` escaped by doubling) — the ONLY
identifier quoter; scenario strings are never concatenated into executable
Jinja and never interpolated unquoted into SQL.

Jinja neutralization (observable contract: after dbt parsing, every literal
originating from a scenario string evaluates to that exact string): SQL
string literals containing ``{`` or ``}`` are emitted as ``||``
concatenations whose chunks never hold a brace pair contiguously, so raw
generated text contains no ``{{``, ``}}``, ``{%`` or ``{#`` outside real
``source(``/``ref(`` calls. Plain strings keep the single-literal fast path.
YAML-embedded strings cannot use SQL concatenation; their neutralizer (e.g.
Jinja echo) belongs to assertion lowering (G15), not here.

Generated text files: UTF-8, LF newlines, exactly one final newline
(:func:`write_text_file`); the canonical ``scenario.json`` snapshot is the
exception — it preserves the exact ``canonical_json`` bytes (written by the
instance assembler, G17).

Parquet/DuckDB I/O below implements §§5.2–5.3 and 12 on top of this
contract (DuckDB-native writer, fresh-DB loader with exact verification).
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

import duckdb

from data_pipeline_diagnostics.generator.raw_constraints import GenerationFailure
from data_pipeline_diagnostics.scenario.types import DataType

__all__ = [
    "DBT_PROFILE_NAME",
    "DBT_PROJECT_NAME",
    "DBT_TARGET_NAME",
    "DUCKDB_TYPE",
    "MAIN_SCHEMA_NAME",
    "PARQUET_COMPRESSION",
    "PARQUET_ROW_GROUP_SIZE",
    "RAW_SCHEMA_NAME",
    "RAW_SOURCE_NAME",
    "RawTableLayout",
    "literal_boolean",
    "literal_date",
    "literal_float",
    "literal_integer",
    "literal_string",
    "literal_timestamp",
    "load_raw_into_duckdb",
    "physical_test_name",
    "quote_ident",
    "raw_parquet_path",
    "sql_literal",
    "write_raw_parquet",
    "write_text_file",
    "yaml_scalar",
    "yaml_string",
]

DUCKDB_TYPE: dict[DataType, str] = {
    DataType.string: "VARCHAR",
    DataType.integer: "BIGINT",
    DataType.float: "DOUBLE",
    DataType.boolean: "BOOLEAN",
    DataType.date: "DATE",
    DataType.timestamp: "TIMESTAMP WITH TIME ZONE",
}

RAW_SOURCE_NAME = "raw"
RAW_SCHEMA_NAME = "raw"
MAIN_SCHEMA_NAME = "main"
DBT_PROJECT_NAME = "dpd_pipeline"
DBT_PROFILE_NAME = "dpd_pipeline"
DBT_TARGET_NAME = "clean"

PARQUET_COMPRESSION = "snappy"
PARQUET_ROW_GROUP_SIZE = 122880


def quote_ident(name: str) -> str:
    """The only identifier quoter: double quotes, ``\"`` escaped by doubling."""
    if type(name) is not str or not name:
        raise TypeError(f"identifier must be a non-empty str, got {name!r}")
    return '"' + name.replace('"', '""') + '"'


def physical_test_name(assertion_type: str, logical: str, *roles: str) -> str:
    """Deterministic physical test name from logical name + component roles
    (stable hash suffix when long — no naming policy)."""
    base = "__".join((assertion_type, logical, *roles)) if roles else f"{assertion_type}__{logical}"
    if len(base) <= 64:
        return base
    digest = hashlib.sha256(base.encode("utf-8")).hexdigest()[:12]
    return base[: 64 - 13] + "_" + digest


def _quote_chunk(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def literal_string(value: str) -> str:
    """Single-quoted literal (embedded quotes doubled); brace content is
    split into ``||`` chunks so no Jinja delimiter survives contiguously."""
    if type(value) is not str:
        raise TypeError(f"string literal needs str, got {type(value).__name__}")
    if "{" not in value and "}" not in value:
        return _quote_chunk(value)
    parts = [p for p in re.split(r"([{}])", value) if p != ""]
    return " || ".join(_quote_chunk(p) for p in parts)


def literal_integer(value: int) -> str:
    """Base-10 decimal literal (bools rejected: they are not integers here)."""
    if type(value) is not int:
        raise TypeError(f"integer literal needs int, got {type(value).__name__}")
    return str(value)


def literal_float(value: float) -> str:
    """Finite round-trip representation, explicitly ``DOUBLE`` (a bare
    ``0.1`` would parse as ``DECIMAL`` in DuckDB)."""
    if type(value) is not float:
        raise TypeError(f"float literal needs float, got {type(value).__name__}")
    if not math.isfinite(value):
        raise ValueError(f"float literal must be finite, got {value!r}")
    return f"CAST({repr(value)} AS DOUBLE)"


def literal_boolean(value: bool) -> str:
    """``TRUE`` or ``FALSE``."""
    if type(value) is not bool:
        raise TypeError(f"boolean literal needs bool, got {type(value).__name__}")
    return "TRUE" if value else "FALSE"


def literal_date(value: date) -> str:
    """Typed DuckDB date literal (datetimes rejected: not dates)."""
    if type(value) is not date:
        raise TypeError(f"date literal needs date, got {type(value).__name__}")
    return f"DATE '{value.isoformat()}'"


def literal_timestamp(value: datetime) -> str:
    """Typed UTC timestamp literal (naive inputs assumed UTC, never local)."""
    if not isinstance(value, datetime):
        raise TypeError(f"timestamp literal needs datetime, got {type(value).__name__}")
    moment = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return f"TIMESTAMPTZ '{moment.astimezone(UTC).isoformat(sep=' ')}'"


def sql_literal(value: object, data_type: DataType) -> str:
    """Type-aware literal dispatcher (exhaustive over ``DataType``)."""
    match data_type:
        case DataType.string:
            return literal_string(value)  # type: ignore[arg-type]
        case DataType.integer:
            return literal_integer(value)  # type: ignore[arg-type]
        case DataType.float:
            return literal_float(value)  # type: ignore[arg-type]
        case DataType.boolean:
            return literal_boolean(value)  # type: ignore[arg-type]
        case DataType.date:
            return literal_date(value)  # type: ignore[arg-type]
        case DataType.timestamp:
            return literal_timestamp(value)  # type: ignore[arg-type]
        case _:
            raise ValueError(f"unknown data type: {data_type!r}")


def write_text_file(path: str | Path, text: str) -> Path:
    """Write a generated text file: UTF-8, LF newlines, one final newline."""
    if type(text) is not str:
        raise TypeError(f"text file content must be str, got {type(text).__name__}")
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        text.replace("\r\n", "\n").replace("\r", "\n").rstrip("\n") + "\n", encoding="utf-8"
    )
    return target


def yaml_string(value: str) -> str:
    """Single-quoted YAML string safe under dbt's Jinja rendering.

    SQL ``||``-chunking is meaningless in YAML, so brace content uses Jinja
    echo instead: ``{`` becomes ``{{ '{' }}``. After dbt parsing the value is
    exactly the scenario string (single-brace text never triggers Jinja).
    The substitution runs in a single pass so echo syntax is never re-escaped.
    """
    if type(value) is not str:
        raise TypeError(f"YAML string needs str, got {type(value).__name__}")
    echoed = re.sub(
        r"[{}]", lambda match: "{{ '{' }}" if match.group() == "{" else "{{ '}' }}", value
    )
    return "'" + echoed.replace("'", "''") + "'"


def yaml_scalar(value: object) -> str:
    """YAML rendering for a ``ScalarValue`` (bools lowercase, floats round-trip)."""
    if type(value) is bool:
        return "true" if value else "false"
    if type(value) is int:
        return str(value)
    if type(value) is float:
        return repr(value)
    if type(value) is str:
        return yaml_string(value)
    raise TypeError(f"unsupported YAML scalar: {value!r}")


@dataclass(frozen=True)
class RawTableLayout:
    """Declared raw-table shape: columns in order plus expected row count."""

    columns: tuple[tuple[str, DataType], ...]
    row_count: int


def raw_parquet_path(raw_dir: str | Path, table: str) -> Path:
    """Fixed identity mapping: raw table ``T`` → ``raw/T.parquet``."""
    return Path(raw_dir) / f"{table}.parquet"


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
        f"{quote_ident(name)} {DUCKDB_TYPE[dtype]}" for name, dtype in layout.columns
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
            db.execute(f"CREATE SCHEMA {quote_ident(RAW_SCHEMA_NAME)}")
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
                    f"CAST({quote_ident(name)} AS {DUCKDB_TYPE[dtype]}) AS {quote_ident(name)}"
                    for name, dtype in layout.columns
                )
                db.execute(
                    f"CREATE TABLE {quote_ident(RAW_SCHEMA_NAME)}.{quote_ident(table)} AS "
                    f"SELECT {select_list} FROM read_parquet('{path}')"
                )
                info = db.execute(
                    "SELECT column_name, data_type FROM duckdb_columns() "
                    f"WHERE schema_name = '{RAW_SCHEMA_NAME}' AND table_name = '{table}' "
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
                    f"SELECT COUNT(*) FROM {quote_ident(RAW_SCHEMA_NAME)}.{quote_ident(table)}"
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
