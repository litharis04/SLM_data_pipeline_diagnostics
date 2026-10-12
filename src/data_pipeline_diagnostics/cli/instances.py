"""Shared prepare-and-publish instance flow for ``open``/``seed`` (C09).

``run_instance`` implements the whole path once: ensure workspace, resolve
the catalog id, parse + semantically validate the stored scenario, load the
entry seed (or take the requested one), run authored saved-requirement
pre-checks, build through the clean-instance facade with credential env vars
scrubbed, atomically replace the owned working copy, update the entry
seed/hash/pointer, and print the §7 summary. Exits: ``2`` unknown id/invalid
stored input/bad requested seed, ``4`` authored-requirement violations,
``5`` workspace/materializer/integrity failure. Failures never touch the
stored seed/pointer.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import uuid
from collections.abc import Callable
from pathlib import Path

from data_pipeline_diagnostics.cli.cmd_connect import configured_credential_envs
from data_pipeline_diagnostics.cli.features import (
    AuthoringRequest,
    check_requirements,
    classify_raw_size,
    extract_features,
)
from data_pipeline_diagnostics.cli.workspace import (
    atomic_write_json,
    catalog_path,
    config_path,
    ensure_workspace,
    entry_path,
    instance_pointer,
    scenario_dir,
    scenario_json_path,
    validate_scenario_id,
)
from data_pipeline_diagnostics.generator.cache import CleanInstance, prepare_clean_instance
from data_pipeline_diagnostics.generator.physical import quote_ident
from data_pipeline_diagnostics.generator.raw_constraints import GenerationFailure
from data_pipeline_diagnostics.scenario import (
    ScenarioParseError,
    SemanticValidationError,
    parse_scenario_json,
    scenario_content_hash,
    validate_semantics,
)

Prepare = Callable[[object, int, Path], CleanInstance]

PREVIEW_ROW_LIMIT = 5
PREVIEW_CELL_WIDTH = 40
_MAX_DATA_SEED = 2**63 - 1


def _fail(message: str) -> int:
    print(message, file=sys.stderr)
    return 2


def _load_catalog_ids(root: Path) -> list[str] | None:
    try:
        catalog = json.loads(catalog_path(root).read_text(encoding="utf-8"))
    except OSError, ValueError:
        return None
    ids = catalog.get("scenario_ids") if isinstance(catalog, dict) else None
    if not isinstance(ids, list) or any(not isinstance(item, str) for item in ids):
        return None
    return ids


def _load_entry(root: Path, scenario_id: str) -> dict[str, object] | None:
    try:
        entry = json.loads(entry_path(root, scenario_id).read_text(encoding="utf-8"))
    except OSError, ValueError:
        return None
    return entry if isinstance(entry, dict) else None


def _valid_seed(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if 0 <= value <= _MAX_DATA_SEED else None


def _cell(value: object) -> str:
    text = "NULL" if value is None else str(value)
    if len(text) <= PREVIEW_CELL_WIDTH:
        return text
    return text[: PREVIEW_CELL_WIDTH - 1] + "…"


def _preview_output(
    db_path: Path, model_name: str, grain: tuple[str, ...]
) -> tuple[list[str], list[list[str]]]:
    """Up to ``PREVIEW_ROW_LIMIT`` rows ordered by grain (read-only)."""
    import duckdb

    order = " ORDER BY " + ", ".join(quote_ident(column) for column in grain) if grain else ""
    query = (
        f"SELECT * FROM {quote_ident('main')}.{quote_ident(model_name)}{order} "
        f"LIMIT {PREVIEW_ROW_LIMIT}"
    )
    connection = duckdb.connect(str(db_path), read_only=True)
    try:
        cursor = connection.execute(query)
        columns = [str(field[0]) for field in cursor.description]
        rows = [[_cell(value) for value in row] for row in cursor.fetchall()[:PREVIEW_ROW_LIMIT]]
    finally:
        connection.close()
    return [_cell(column) for column in columns], rows


def _dbt_summary(workdir: Path) -> str:
    try:
        results = json.loads(
            (workdir / "dbt" / "target" / "run_results.json").read_text(encoding="utf-8")
        )
        entries = results.get("results") if isinstance(results, dict) else None
        if not isinstance(entries, list):
            raise ValueError("missing results")
    except OSError, ValueError:
        return "dbt results unavailable"
    models = [
        e
        for e in entries
        if isinstance(e, dict) and str(e.get("unique_id", "")).startswith("model.")
    ]
    tests = [
        e
        for e in entries
        if isinstance(e, dict) and str(e.get("unique_id", "")).startswith("test.")
    ]
    models_ok = sum(1 for e in models if e.get("status") == "success")
    tests_ok = sum(1 for e in tests if e.get("status") == "pass")
    return (
        f"dbt build: success, {models_ok}/{len(models)} models ok, "
        f"{tests_ok}/{len(tests)} tests pass"
    )


def _print_summary(
    *,
    command: str,
    validated,
    seed: int,
    cache_hit: bool,
    record: dict[str, object],
    workdir: Path,
    scenario_file: Path,
    replaced: bool,
) -> None:
    scenario = validated.scenario
    staging = list(scenario.staging_models)
    intermediates = list(scenario.intermediate_models)
    outputs = list(scenario.output_models)
    layers = {"raw": "raw"}
    for model in staging:
        layers[str(model.name)] = "staging"
    for model in intermediates:
        layers[str(model.name)] = "intermediate"
    for model in outputs:
        layers[str(model.name)] = "output"
    raw_tables = record["raw_tables"]
    assert isinstance(raw_tables, list)
    realized = record.get("crosswalks", {})
    realized = realized.get("realized_row_counts", {}) if isinstance(realized, dict) else {}
    lines = [
        f"scenario: {scenario.scenario_id}",
        f"domain: {scenario.domain}",
        f"description: {scenario.description or '-'}",
        f"seed: {seed}",
        "state: prepared",
        f"models: staging={len(staging)} intermediate={len(intermediates)} output={len(outputs)}",
        "name layer rows",
    ]
    biggest = ("", -1)
    for table in raw_tables:
        name, count = str(table["name"]), int(table["row_count"])
        lines.append(f"{name} raw {count}")
        if count > biggest[1]:
            biggest = (name, count)
    for name in (
        [str(model.name) for model in staging]
        + [str(model.name) for model in intermediates]
        + [str(model.name) for model in outputs]
    ):
        count = realized.get(name)
        lines.append(f"{name} {layers[name]} {count if isinstance(count, int) else 'unknown'}")
    lines.append(
        f"largest raw table: {biggest[0]} ({biggest[1]} rows, {classify_raw_size(biggest[1])})"
    )
    lines.append(f"build: {'cache-hit (verified, no rebuild)' if cache_hit else 'fresh'}")
    lines.append(_dbt_summary(workdir))
    db_path = workdir / "pipeline.duckdb"
    for model in outputs:
        name = str(model.name)
        grain = tuple(str(column) for column in model.grain)
        try:
            columns, rows = _preview_output(db_path, name, grain)
        except Exception as exc:
            print(f"plgen {command}: preview failed for {name}: {exc}", file=sys.stderr)
            lines.append(f"output {name}: preview unavailable")
            continue
        lines.append(f"output {name} (ordered by grain: {', '.join(grain)}; at most 5 rows):")
        lines.append(" | ".join(columns))
        if rows:
            lines.extend(" | ".join(row) for row in rows)
        else:
            lines.append("(no rows)")
    lines.extend(
        [
            f"scenario: {scenario_file}",
            f"workdir: {workdir}",
            f"duckdb: {db_path}",
            f"dbt: {workdir / 'dbt'}",
            f"working copy {'replaced' if replaced else 'created'}: {workdir}",
        ]
    )
    print("\n".join(lines))


def scrubbed_build(build: Prepare, validated, seed: int, cache_dir: Path, config) -> CleanInstance:
    """Run ``build`` with configured credential env vars removed (restored after)."""
    removed: dict[str, str] = {}
    for name in configured_credential_envs(config):
        if name in os.environ:
            removed[name] = os.environ.pop(name)
    try:
        return build(validated, seed, cache_dir)
    finally:
        os.environ.update(removed)


def publish_working_copy(scenario_dir_path: Path, instance: CleanInstance) -> tuple[Path, bool]:
    """Replace the owned working copy with a fresh copy of the built instance.

    Returns the owned workdir and whether a previous copy was replaced. The
    builder's handoff copy is consumed (removed) afterwards.
    """
    scenario_path = Path(scenario_dir_path)
    workdir = (scenario_path / "work").resolve()
    replaced = workdir.exists() or workdir.is_symlink()
    staging_tmp = scenario_path / f"work.tmp-{uuid.uuid4().hex}"
    shutil.copytree(instance.instance_dir, staging_tmp)
    if replaced:
        shutil.rmtree(workdir, ignore_errors=True)
    os.replace(staging_tmp, workdir)
    shutil.rmtree(instance.instance_dir, ignore_errors=True)
    return workdir, replaced


def run_instance(
    root: Path,
    scenario_id_raw: str,
    requested_seed: int | None,
    *,
    command: str,
    prepare: Prepare | None = None,
    cache_root: Path | None = None,
    assert_bytes_identical: bool = False,
) -> int:
    """Shared ``open``/``seed`` flow; ``requested_seed=None`` keeps the entry seed."""
    root = Path(root)
    ensure_workspace(root)
    try:
        scenario_id = validate_scenario_id(scenario_id_raw)
    except ValueError as exc:
        return _fail(f"plgen {command}: {exc}")
    catalog_ids = _load_catalog_ids(root)
    if catalog_ids is None:
        print(f"plgen {command}: unreadable catalog {catalog_path(root)}", file=sys.stderr)
        return 5
    if scenario_id not in catalog_ids:
        return _fail(f"plgen {command}: unknown scenario {scenario_id!r}")
    scenario_file = scenario_json_path(root, scenario_id)
    try:
        raw_bytes = scenario_file.read_bytes()
    except OSError as exc:
        return _fail(f"plgen {command}: cannot read {scenario_file}: {exc}")
    try:
        scenario = parse_scenario_json(raw_bytes)
    except ScenarioParseError as exc:
        return _fail(f"plgen {command}: invalid scenario {scenario_id!r}: {exc}")
    try:
        validated = validate_semantics(scenario)
    except SemanticValidationError as exc:
        return _fail(f"plgen {command}: invalid scenario {scenario_id!r}: {exc}")
    entry = _load_entry(root, scenario_id)
    if entry is None:
        print(f"plgen {command}: unreadable entry {entry_path(root, scenario_id)}", file=sys.stderr)
        return 5
    if requested_seed is not None:
        seed = _valid_seed(requested_seed)
        if seed is None:
            return _fail(
                f"plgen {command}: invalid DATA_SEED {requested_seed!r}: "
                "expected 0 <= DATA_SEED <= 2**63 - 1"
            )
    else:
        seed = _valid_seed(entry.get("seed"))
        if seed is None:
            print(
                f"plgen {command}: invalid seed in {entry_path(root, scenario_id)}",
                file=sys.stderr,
            )
            return 5
    if entry.get("origin") == "authored":
        try:
            saved = AuthoringRequest.from_dict(entry.get("requirements"))
        except ValueError as exc:
            print(
                f"plgen {command}: invalid saved requirements for {scenario_id!r}: {exc}",
                file=sys.stderr,
            )
            return 5
        issues = check_requirements(extract_features(validated), saved)
        if issues:
            print(
                f"plgen {command}: saved requirements not met for {scenario_id!r}:",
                file=sys.stderr,
            )
            for issue in issues:
                print(f"  [{issue.code}] {issue.path}: {issue.message}", file=sys.stderr)
            return 4
    build = prepare or prepare_clean_instance
    cache_dir = Path(cache_root) if cache_root is not None else root / "cache"
    config: dict[str, object] = {}
    try:
        loaded = json.loads(config_path(root).read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            config = loaded
    except OSError, ValueError:
        config = {}
    try:
        instance = scrubbed_build(build, validated, seed, cache_dir, config)
    except GenerationFailure as exc:
        record_file = Path(getattr(exc, "workspace", cache_dir)) / "failure_record.json"
        print(
            f"plgen {command}: clean-control failure for {scenario_id!r} "
            f"({exc.reason}: {exc.detail}; see {record_file})",
            file=sys.stderr,
        )
        return 5
    if assert_bytes_identical:
        try:
            identical = scenario_file.read_bytes() == raw_bytes
        except OSError:
            identical = False
        if not identical:
            print(
                f"plgen {command}: scenario logic changed during build for {scenario_id!r}",
                file=sys.stderr,
            )
            return 5
    workdir, replaced = publish_working_copy(scenario_dir(root, scenario_id), instance)
    try:
        record = json.loads((workdir / "instance_record.json").read_text(encoding="utf-8"))
        if not isinstance(record, dict) or not isinstance(record.get("raw_tables"), list):
            raise ValueError("instance_record.json must hold a raw_tables list")
    except (OSError, ValueError) as exc:
        print(f"plgen {command}: unreadable instance record in {workdir}: {exc}", file=sys.stderr)
        return 5
    entry["seed"] = seed
    entry["scenario_hash"] = scenario_content_hash(validated.scenario)
    entry["instance"] = instance_pointer(workdir, instance.instance_digest)
    atomic_write_json(entry_path(root, scenario_id), entry)
    _print_summary(
        command=command,
        validated=validated,
        seed=seed,
        cache_hit=instance.cache_hit,
        record=record,
        workdir=workdir,
        scenario_file=scenario_file.resolve(),
        replaced=replaced,
    )
    return 0
