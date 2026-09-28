"""Instance reproducibility passport (GENERATOR_SPEC §18).

:func:`identity_digest` builds the canonical §7.2 identity object and its
SHA-256 digest — the single constructor shared with the G19 cache (defined
here per the G18/G19 coordination rule, no duplicates).
:func:`write_instance_record` emits ``instance_record.json`` with the exact
§18.2 field sets (no extras in the strict sections) plus SHOULD crosswalks
under one ``crosswalks`` object. It inlines no raw rows, logs, secrets, or
fault metadata.

``artifacts[]`` inventories every published artifact except
``instance_record.json`` itself (a file cannot carry its own digest;
tamper-evidence for the record comes from the ``SUCCESS`` marker written
after it — the G19 publish flow refreshes ``SUCCESS`` last; see the
``write_success`` flag on ``build_clean_instance``).
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import platform
import sys
from collections.abc import Sequence
from pathlib import Path

import duckdb

from data_pipeline_diagnostics.generator.dbt_render import (
    LogicalAssertion,
    collect_assertions,
)
from data_pipeline_diagnostics.generator.physical import (
    DUCKDB_TYPE,
    physical_test_name,
    quote_ident,
)
from data_pipeline_diagnostics.generator.raw_constraints import GenerationFailure
from data_pipeline_diagnostics.generator.raw_plan import RawPlan, build_raw_plan
from data_pipeline_diagnostics.generator.rng import STREAM_SCHEME
from data_pipeline_diagnostics.generator.versions import (
    DBT_RENDERER_VERSION,
    GENERATOR_CONTRACT_VERSION,
    RAW_GENERATOR_VERSION,
)
from data_pipeline_diagnostics.scenario.parsing import scenario_content_hash
from data_pipeline_diagnostics.scenario.semantic import ValidatedScenario

__all__ = [
    "identity_digest",
    "identity_object",
    "plan_stream_inventory",
    "write_instance_record",
]

_RECORD_VERSION = "1.0"

_KIND_BY_PATH = (
    ("scenario.json", "scenario"),
    ("pipeline.duckdb", "duckdb"),
    ("instance_record.json", None),  # self-describing; excluded (see docstring)
    ("SUCCESS", "marker"),
    ("failure_record.json", None),  # never present on success; excluded
)


def _tool_version(distribution: str) -> str:
    return importlib.metadata.version(distribution)


def identity_object(validated: ValidatedScenario, data_seed: int) -> dict[str, object]:
    """Canonical §7.2 identity object (key order fixed; serialized sorted)."""
    scenario = validated.scenario
    major, minor = sys.version_info.major, sys.version_info.minor
    return {
        "scenario_sha256": scenario_content_hash(scenario),
        "data_seed": data_seed,
        "generator_contract": GENERATOR_CONTRACT_VERSION,
        "raw_generator": RAW_GENERATOR_VERSION,
        "dbt_renderer": DBT_RENDERER_VERSION,
        "python": f"{major}.{minor}",
        "duckdb": _tool_version("duckdb"),
        "dbt_core": _tool_version("dbt-core"),
        "dbt_duckdb": _tool_version("dbt-duckdb"),
        "faker": _tool_version("faker"),
        "faker_locale": "de_DE",
    }


def identity_digest(validated: ValidatedScenario, data_seed: int) -> str:
    """SHA-256 over the canonical JSON of :func:`identity_object`."""
    canonical = json.dumps(
        identity_object(validated, data_seed), sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def plan_stream_inventory(plan: RawPlan) -> list[str]:
    """Every randomness stream the plan defines (deterministic superset of
    consumed streams: untouched streams cost nothing, and every allocated
    subseed stays reconstructible)."""
    tables = {table.name: table for table in plan.tables}
    streams: set[str] = set()
    for table in plan.tables:
        if table.rows.min != table.rows.max:
            streams.add(f"rows/{table.name}")
    for table in plan.tables:
        primary = set(table.primary_key)
        for column in table.columns:
            if column.kind == "leaf":
                streams.add(column.value_stream)
                if column.nullable and column.name not in primary:
                    streams.add(column.null_stream)
            elif column.kind == "template":
                streams.add(column.value_stream)
    for group in plan.fk_groups:
        streams.add(group.value_stream)
        forced = bool(set(group.dependent_columns) & set(tables[group.dependent_table].primary_key))
        if not forced and any(nullable for nullable, _ in group.null_terms):
            streams.add(group.null_stream)
    return sorted(streams)


def _sha256_file(path: Path) -> tuple[int, str]:
    data = path.read_bytes()
    return len(data), hashlib.sha256(data).hexdigest()


def _walk_files(root: Path, prefix: str) -> list[str]:
    found: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root / prefix):
        dirnames.sort()
        for filename in sorted(filenames):
            full = Path(dirpath) / filename
            found.append(full.relative_to(root).as_posix())
    return found


def _classify(relative: str) -> str | None:
    for marker, kind in _KIND_BY_PATH:
        if relative == marker:
            return kind
    if relative.startswith("raw/") and relative.endswith(".parquet"):
        return "parquet"
    if relative.startswith("dbt/target/"):
        return "dbt_target"
    if relative.startswith("dbt/logs/"):
        return "log"
    if relative.startswith("dbt/"):
        return "dbt_project"
    return None


def _logical_checksum(rows: Sequence[Sequence[object]]) -> str:
    canonical = json.dumps(
        [tuple("null" if v is None else repr(v) for v in row) for row in rows],
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _table_rows(db: duckdb.DuckDBPyConnection, schema: str, table: str) -> list[tuple]:
    columns = [
        row[0]
        for row in db.execute(
            "SELECT column_name FROM duckdb_columns() "
            f"WHERE schema_name = '{schema}' AND table_name = '{table}' ORDER BY column_index"
        ).fetchall()
    ]
    quoted = ", ".join(quote_ident(c) for c in columns)
    order = ", ".join(f"{quote_ident(c)} NULLS LAST" for c in columns)
    return db.execute(
        f"SELECT {quoted} FROM {quote_ident(schema)}.{quote_ident(table)} ORDER BY {order}"
    ).fetchall()


def _physical_test_names(assertion: LogicalAssertion) -> list[str]:
    match assertion.type:
        case "not_null":
            return [physical_test_name("not_null", assertion.name, c) for c in assertion.columns]
        case "unique":
            if len(assertion.columns) == 1:
                return [physical_test_name("unique", assertion.name)]
            return [physical_test_name("composite_unique", assertion.name)]
        case "accepted_values":
            return [physical_test_name("accepted_values", assertion.name)]
        case "relationships":
            if len(assertion.columns) == 1:
                return [physical_test_name("relationships", assertion.name)]
            return [physical_test_name("composite_relationships", assertion.name)]
        case "row_count":
            return [physical_test_name("row_count_between", assertion.name)]
        case "column_range":
            return [physical_test_name("column_range", assertion.name)]
        case _:
            raise ValueError(f"unknown assertion type: {assertion.type!r}")


def write_instance_record(
    *,
    instance_dir: str | Path,
    validated: ValidatedScenario,
    data_seed: int,
    dbt_command: Sequence[str],
    dbt_exit_status: int,
) -> dict:
    """Build and write ``instance_record.json`` for a successful instance.

    Raises :class:`GenerationFailure` when the workspace is incomplete
    (missing ``SUCCESS`` inputs) or a crosswalk cannot be resolved — a
    record is never written for a partial baseline.
    """
    root = Path(instance_dir)
    scenario = validated.scenario
    for required in ("scenario.json", "pipeline.duckdb", "dbt/target/manifest.json"):
        if not (root / required).is_file():
            raise GenerationFailure(
                table="*",
                column=None,
                reason="incomplete-instance",
                detail=f"missing required artifact {required}",
            )
    digest = identity_digest(validated, data_seed)
    major, minor = sys.version_info.major, sys.version_info.minor

    plan = build_raw_plan(validated)
    raw_tables = []
    for table in scenario.raw_tables:
        name = str(table.name)
        parquet = root / "raw" / f"{name}.parquet"
        if not parquet.is_file():
            raise GenerationFailure(
                table=name,
                column=None,
                reason="incomplete-instance",
                detail=f"missing raw/{name}.parquet",
            )
        size, sha = _sha256_file(parquet)
        raw_tables.append(
            {
                "name": name,
                "row_count": 0,  # replaced below with the loaded count
                "duckdb_relation": f"{quote_ident('raw')}.{quote_ident(name)}",
                "parquet_path": f"raw/{name}.parquet",
                "columns": [
                    {"name": str(col.name), "duckdb_type": DUCKDB_TYPE[col.type]}
                    for col in table.columns
                ],
                "sha256": sha,
            }
        )

    db = duckdb.connect(str(root / "pipeline.duckdb"), read_only=True)
    try:
        loaded_counts: dict[str, int] = {}
        for entry in raw_tables:
            (count,) = db.execute(
                f"SELECT COUNT(*) FROM {quote_ident('raw')}.{quote_ident(entry['name'])}"
            ).fetchone()
            entry["row_count"] = count
            loaded_counts[entry["name"]] = count
        manifest = json.loads(
            (root / "dbt" / "target" / "manifest.json").read_text(encoding="utf-8")
        )
        model_nodes = {
            unique_id: node
            for unique_id, node in dict(manifest.get("nodes", {})).items()
            if isinstance(node, dict) and node.get("resource_type") == "model"
        }
        test_names = {
            str(node.get("name")): unique_id
            for unique_id, node in dict(manifest.get("nodes", {})).items()
            if isinstance(node, dict) and node.get("resource_type") == "test"
        }
        model_names = [str(m.name) for m in scenario.staging_models]
        model_names += [
            str(n) for n in validated.topological_order if n in validated.intermediate_by_name
        ]
        model_names += [str(m.name) for m in scenario.output_models]
        models_crosswalk = {}
        for name in model_names:
            matches = sorted(
                u
                for u in model_nodes
                if u.startswith("model.dpd_pipeline.") and u.split(".")[-1] == name
            )
            if not matches:
                raise GenerationFailure(
                    table="*",
                    column=None,
                    reason="crosswalk-incomplete",
                    detail=f"model {name!r} missing from manifest",
                )
            unique_id = sorted(matches)[0]
            node = model_nodes[unique_id]
            relation = ".".join(
                str(node.get(k)) for k in ("database", "schema", "alias") if node.get(k)
            )
            models_crosswalk[name] = {"unique_id": unique_id, "relation": relation}
        assertions = collect_assertions(validated)
        assertions_crosswalk = {}
        for assertion in assertions:
            unique_ids = []
            for physical in _physical_test_names(assertion):
                if physical not in test_names:
                    raise GenerationFailure(
                        table="*",
                        column=None,
                        reason="crosswalk-incomplete",
                        detail=f"physical test {physical!r} missing from manifest",
                    )
                unique_ids.append(test_names[physical])
            assertions_crosswalk[assertion.name] = {
                "origin": assertion.origin,
                "unique_ids": unique_ids,
            }
        realized_counts = {}
        checksums = {}
        for name in model_names:
            (count,) = db.execute(
                f"SELECT COUNT(*) FROM {quote_ident('main')}.{quote_ident(name)}"
            ).fetchone()
            realized_counts[name] = count
            checksums[name] = _logical_checksum(_table_rows(db, "main", name))
        for name in loaded_counts:
            checksums[f"raw.{name}"] = _logical_checksum(_table_rows(db, "raw", name))
    finally:
        db.close()

    artifacts = []
    for relative in [
        "scenario.json",
        *[f"raw/{t}.parquet" for t in [str(t.name) for t in scenario.raw_tables]],
        "pipeline.duckdb",
        *_walk_files(root, "dbt"),
        "SUCCESS",
    ]:
        path = root / relative
        if not path.is_file():
            raise GenerationFailure(
                table="*",
                column=None,
                reason="incomplete-instance",
                detail=f"missing artifact {relative}",
            )
        kind = _classify(relative)
        if kind is None:
            continue
        size, sha = _sha256_file(path)
        artifacts.append({"path": relative, "kind": kind, "size_bytes": size, "sha256": sha})

    record = {
        "record_version": _RECORD_VERSION,
        "status": "success",
        "identity": {
            "instance_digest": digest,
            "scenario_id": str(scenario.scenario_id),
            "scenario_schema_version": str(scenario.schema_version),
            "scenario_sha256": scenario_content_hash(scenario),
            "data_seed": data_seed,
        },
        "versions": {
            "generator_contract": GENERATOR_CONTRACT_VERSION,
            "raw_generator": RAW_GENERATOR_VERSION,
            "dbt_renderer": DBT_RENDERER_VERSION,
            "python": f"{major}.{minor}",
            "python_full": platform.python_version(),
            "duckdb": _tool_version("duckdb"),
            "dbt_core": _tool_version("dbt-core"),
            "dbt_duckdb": _tool_version("dbt-duckdb"),
            "faker": _tool_version("faker"),
            "faker_locale": "de_DE",
        },
        "randomness": {
            "stream_scheme": STREAM_SCHEME,
            "streams": plan_stream_inventory(plan),
        },
        "raw_tables": raw_tables,
        "dbt": {
            "command": list(dbt_command),
            "exit_status": dbt_exit_status,
            "project_dir": "dbt",
            "log_path": "dbt/logs/dbt.log",
            "manifest_path": "dbt/target/manifest.json",
            "run_results_path": "dbt/target/run_results.json",
        },
        "artifacts": artifacts,
        "crosswalks": {
            "models": models_crosswalk,
            "assertions": assertions_crosswalk,
            "realized_row_counts": realized_counts,
            "logical_checksums": checksums,
        },
    }
    (root / "instance_record.json").write_text(
        json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return record
