"""Clean control execution + failure semantics (GENERATOR_SPEC §§16–17).

:func:`run_clean_build` shells out to dbt (the first such call site):
``dbt build --profiles-dir . --target clean --threads 1`` from the ``dbt/``
directory under a fixed UTC environment, capturing command, exit status and
the combined log (``dbt/logs/dbt.log``). Success requires every model built,
every healthy test passed, and a readable ``manifest.json``/``run_results.json``
matching this project.

:func:`build_clean_instance` orchestrates the full §6 layout (minus
``instance_record.json``, which is G18's): canonical ``scenario.json``,
``raw/`` Parquet, ``pipeline.duckdb``, rendered ``dbt/``, then ``dbt
build``. Any failure writes ``failure_record.json`` with the exact §17
schema and never a ``SUCCESS`` marker: no seed switching, no omitted
scenarios, no weakened severity, no ``TRY_CAST`` repair, no truncation.
The failed workspace is preserved in place (this layer never deletes it;
fresh callers pass a fresh directory). The cache-aware
``prepare_clean_instance`` entry point is G19's, built on top of this module.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from data_pipeline_diagnostics.generator.dbt_render import render_dbt_project
from data_pipeline_diagnostics.generator.physical import (
    RawTableLayout,
    load_raw_into_duckdb,
    write_raw_parquet,
)
from data_pipeline_diagnostics.generator.raw_constraints import GenerationFailure
from data_pipeline_diagnostics.generator.raw_plan import build_raw_plan, execute_raw_plan
from data_pipeline_diagnostics.generator.versions import (
    DBT_RENDERER_VERSION,
    GENERATOR_CONTRACT_VERSION,
    RAW_GENERATOR_VERSION,
)
from data_pipeline_diagnostics.scenario.parsing import canonical_json, scenario_content_hash
from data_pipeline_diagnostics.scenario.semantic import ValidatedScenario

__all__ = [
    "BuiltInstance",
    "CleanBuildResult",
    "DBT_BUILD_COMMAND",
    "DBT_BUILD_TIMEOUT_SECONDS",
    "FAILURE_STAGES",
    "SUCCESS_CONTENT",
    "build_clean_instance",
    "run_clean_build",
    "validate_failure_record",
    "write_failure_record",
]

DBT_BUILD_COMMAND = ("dbt", "build", "--profiles-dir", ".", "--target", "clean", "--threads", "1")
DBT_BUILD_TIMEOUT_SECONDS = 600
SUCCESS_CONTENT = "success\n"

FAILURE_STAGES = (
    "raw_plan",
    "raw_generate",
    "raw_load",
    "dbt_render",
    "dbt_build",
    "integrity",
    "cache_publish",
)

_OK_STATUSES = ("success", "pass")


@dataclass(frozen=True)
class CleanBuildResult:
    """Outcome of one ``dbt build`` invocation with parsed results."""

    command: tuple[str, ...]
    exit_status: int | None
    success: bool
    category: str | None
    models_total: int = 0
    models_ok: int = 0
    tests_total: int = 0
    tests_ok: int = 0


@dataclass(frozen=True)
class BuiltInstance:
    """Outcome of one clean-instance build (workspace preserved either way)."""

    scenario_id: str
    data_seed: int
    instance_dir: Path
    success: bool


def _dbt_env() -> dict[str, str]:
    env = dict(os.environ)
    env["TZ"] = "UTC"
    return env


def run_clean_build(*, dbt_dir: str | Path) -> CleanBuildResult:
    """Run the fixed clean build and validate its outcome."""
    project_dir = Path(dbt_dir)
    command = DBT_BUILD_COMMAND
    log_path = project_dir / "logs" / "dbt.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        completed = subprocess.run(
            command,
            cwd=project_dir,
            env=_dbt_env(),
            capture_output=True,
            text=True,
            timeout=DBT_BUILD_TIMEOUT_SECONDS,
        )
    except FileNotFoundError as exc:
        raise GenerationFailure(
            table="*",
            column=None,
            reason="dbt-executable-not-found",
            detail=f"{type(exc).__name__}: {exc}",
        ) from exc
    except subprocess.TimeoutExpired as exc:
        log_path.write_text(
            f"$ {' '.join(command)}\nTIMEOUT after {exc.timeout}s\n", encoding="utf-8"
        )
        return CleanBuildResult(
            command=command, exit_status=None, success=False, category="dbt-timeout"
        )
    transcript = f"$ {' '.join(command)}\nexit_status={completed.returncode}\n{completed.stdout}\n{completed.stderr}"
    log_path.write_text(transcript, encoding="utf-8")
    return _validate_build(project_dir, command, completed.returncode)


def _read_json(path: Path) -> object:
    import json

    return json.loads(path.read_text(encoding="utf-8"))


def _validate_build(
    project_dir: Path, command: tuple[str, ...], exit_status: int
) -> CleanBuildResult:
    manifest_path = project_dir / "target" / "manifest.json"
    run_results_path = project_dir / "target" / "run_results.json"
    try:
        manifest = _read_json(manifest_path)
        run_results = _read_json(run_results_path)
        results = run_results["results"]
    except Exception:
        return CleanBuildResult(
            command=command, exit_status=exit_status, success=False, category="manifest-invalid"
        )
    models_total = models_ok = tests_total = tests_ok = 0
    model_ids: set[str] = set()
    failed_test = failed_model = False
    for result in results:
        unique_id = str(result.get("unique_id", ""))
        status = str(result.get("status", ""))
        ok = status in _OK_STATUSES
        # Skipped nodes are cascade collateral, never the root cause.
        hard_fail = status in ("fail", "error")
        if unique_id.startswith("test."):
            tests_total += 1
            tests_ok += ok
            failed_test = failed_test or hard_fail
        elif unique_id.startswith("model."):
            models_total += 1
            models_ok += ok
            model_ids.add(unique_id)
            failed_model = failed_model or hard_fail
    manifest_models = {
        unique_id
        for unique_id, node in dict(manifest.get("nodes", {})).items()
        if isinstance(node, dict)
        and node.get("resource_type") == "model"
        and str(unique_id).startswith("model.")
    }
    project_name = dict(manifest.get("metadata", {})).get("project_name")
    base = dict(
        command=command,
        exit_status=exit_status,
        models_total=models_total,
        models_ok=models_ok,
        tests_total=tests_total,
        tests_ok=tests_ok,
    )
    if exit_status != 0 or models_ok != models_total or tests_ok != tests_total:
        if failed_test and not failed_model:
            category = "dbt-test-failure"
        elif failed_model:
            category = "dbt-model-error"
        else:
            category = "dbt-build-failed"
        return CleanBuildResult(success=False, category=category, **base)
    if project_name != "dpd_pipeline" or not manifest_models or not manifest_models <= model_ids:
        return CleanBuildResult(success=False, category="manifest-mismatch", **base)
    return CleanBuildResult(success=True, category=None, **base)


def _identity(validated: ValidatedScenario, data_seed: int) -> dict[str, object]:
    scenario = validated.scenario
    return {
        "scenario_sha256": scenario_content_hash(scenario),
        "scenario_id": str(scenario.scenario_id),
        "data_seed": data_seed,
        "generator_contract": GENERATOR_CONTRACT_VERSION,
        "raw_generator": RAW_GENERATOR_VERSION,
        "dbt_renderer": DBT_RENDERER_VERSION,
    }


def write_failure_record(
    *,
    instance_dir: str | Path,
    identity: dict[str, object],
    stage: str,
    category: str,
    message: str,
    command: Sequence[str] = (),
    exit_status: int | None = None,
) -> Path:
    """Write ``failure_record.json`` with the exact §17 schema (no fault label)."""
    if stage not in FAILURE_STAGES:
        raise ValueError(f"unknown failure stage: {stage!r}")
    root = Path(instance_dir)
    paths = {}
    for key, relative in (
        ("log", "dbt/logs/dbt.log"),
        ("manifest", "dbt/target/manifest.json"),
        ("run_results", "dbt/target/run_results.json"),
    ):
        paths[key] = relative if (root / relative).is_file() else None
    import json

    record = {
        "identity": identity,
        "stage": stage,
        "category": category,
        "message": message,
        "command": list(command),
        "exit_status": exit_status,
        "paths": paths,
    }
    target = root / "failure_record.json"
    target.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return target


def validate_failure_record(record: object) -> list[str]:
    """Schema-conformance check for ``failure_record.json`` ([] means valid)."""
    violations: list[str] = []
    if not isinstance(record, dict):
        return ["record must be an object"]
    if set(record) != {
        "identity",
        "stage",
        "category",
        "message",
        "command",
        "exit_status",
        "paths",
    }:
        violations.append(
            f"top-level keys must be exactly identity/stage/category/message/command/exit_status/paths, got {sorted(record)}"
        )
        return violations
    identity = record["identity"]
    if not isinstance(identity, dict) or set(identity) != {
        "scenario_sha256",
        "scenario_id",
        "data_seed",
        "generator_contract",
        "raw_generator",
        "dbt_renderer",
    }:
        violations.append(
            "identity must hold exactly scenario_sha256/scenario_id/data_seed/generator_contract/raw_generator/dbt_renderer"
        )
    else:
        for key in (
            "scenario_sha256",
            "scenario_id",
            "generator_contract",
            "raw_generator",
            "dbt_renderer",
        ):
            if type(identity[key]) is not str:
                violations.append(f"identity.{key} must be str")
        if type(identity["data_seed"]) is not int:
            violations.append("identity.data_seed must be int")
    if record["stage"] not in FAILURE_STAGES:
        violations.append(f"stage must be one of {list(FAILURE_STAGES)}")
    if type(record["category"]) is not str:
        violations.append("category must be str")
    if type(record["message"]) is not str:
        violations.append("message must be str")
    if not isinstance(record["command"], list) or any(
        type(c) is not str for c in record["command"]
    ):
        violations.append("command must be a list of str")
    if record["exit_status"] is not None and type(record["exit_status"]) is not int:
        violations.append("exit_status must be int or null")
    paths = record["paths"]
    if not isinstance(paths, dict) or set(paths) != {"log", "manifest", "run_results"}:
        violations.append("paths must hold exactly log/manifest/run_results")
    else:
        for key, value in paths.items():
            if value is not None and type(value) is not str:
                violations.append(f"paths.{key} must be str or null")
    return violations


def _fail(
    root: Path,
    validated: ValidatedScenario,
    data_seed: int,
    stage: str,
    exc: Exception,
    *,
    command: Sequence[str] = (),
    exit_status: int | None = None,
) -> BuiltInstance:
    if isinstance(exc, GenerationFailure):
        category, message = exc.reason, str(exc)
    else:
        category, message = "unexpected-error", f"{type(exc).__name__}: {exc}"
    write_failure_record(
        instance_dir=root,
        identity=_identity(validated, data_seed),
        stage=stage,
        category=category,
        message=message,
        command=command,
        exit_status=exit_status,
    )
    scenario = validated.scenario
    return BuiltInstance(
        scenario_id=str(scenario.scenario_id),
        data_seed=data_seed,
        instance_dir=root,
        success=False,
    )


def build_clean_instance(
    *,
    validated: ValidatedScenario,
    data_seed: int,
    instance_dir: str | Path,
    write_success: bool = True,
) -> BuiltInstance:
    """Build one clean baseline in ``instance_dir`` (fresh workspace semantics:
    an existing directory is removed first and never reused).

    Returns success (with a ``SUCCESS`` marker unless ``write_success`` is
    false — the G19 cache flow defers it until after the instance record)
    or writes ``failure_record.json`` and returns failure — never raising
    for data/contract problems, never retrying another seed.
    """
    if type(data_seed) is not int or not 0 <= data_seed <= 2**63 - 1:
        raise ValueError(f"data_seed must be a strict int in [0, 2**63 - 1], got {data_seed!r}")
    root = Path(instance_dir)
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    scenario = validated.scenario
    scenario_id = str(scenario.scenario_id)

    try:
        plan = build_raw_plan(validated)
    except Exception as exc:
        return _fail(root, validated, data_seed, "raw_plan", exc)
    try:
        data = execute_raw_plan(plan, data_seed)
        raw_dir = root / "raw"
        layouts = {}
        for table_name, rows in data.items():
            spec = next(t for t in scenario.raw_tables if str(t.name) == table_name)
            layout = RawTableLayout(
                columns=tuple((str(col.name), col.type) for col in spec.columns),
                row_count=len(rows),
            )
            layouts[table_name] = layout
            write_raw_parquet(raw_dir=raw_dir, table=table_name, layout=layout, rows=rows)
        (root / "scenario.json").write_bytes(canonical_json(scenario))
    except Exception as exc:
        return _fail(root, validated, data_seed, "raw_generate", exc)
    try:
        load_raw_into_duckdb(raw_dir=root / "raw", db_path=root / "pipeline.duckdb", tables=layouts)
    except Exception as exc:
        return _fail(root, validated, data_seed, "raw_load", exc)
    try:
        render_dbt_project(validated, root / "dbt")
    except Exception as exc:
        return _fail(root, validated, data_seed, "dbt_render", exc)
    try:
        result = run_clean_build(dbt_dir=root / "dbt")
    except GenerationFailure as exc:
        return _fail(root, validated, data_seed, "dbt_build", exc)
    if not result.success:
        stage = (
            "integrity"
            if result.category in ("manifest-invalid", "manifest-mismatch")
            else "dbt_build"
        )
        return _fail(
            root,
            validated,
            data_seed,
            stage,
            GenerationFailure(
                table="*", column=None, reason=result.category or "dbt-build-failed", detail=""
            ),
            command=result.command,
            exit_status=result.exit_status,
        )
    if write_success:
        (root / "SUCCESS").write_text(SUCCESS_CONTENT, encoding="utf-8")
    return BuiltInstance(
        scenario_id=scenario_id, data_seed=data_seed, instance_dir=root, success=True
    )
