"""Content-addressed clean-baseline cache + immutable handoff (§§7, 19).

Layout (no service, no database)::

    <cache_root>/v1/<instance_digest>/   # published immutable baseline
    <cache_root>/v1/.tmp-<uuid>/         # sibling build dir (never reused on failure)
    <cache_root>/work/<uuid>/            # handed-off isolated copies (caller-owned)

The canonical identity constructor lives in :mod:`records` (defined there
per the G18/G19 coordination rule) and is re-exported here.
:func:`prepare_clean_instance` implements check-then-build: a hit requires
digest match, ``SUCCESS`` present, a success ``instance_record.json``
matching path and key, all artifacts present, and every digest verifying —
otherwise it builds in a sibling temp dir and publishes atomically with
``os.replace`` (never repaired in place). Handoff is always an isolated
filesystem copy: fault injection MUST occur outside the cached directory.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path

from data_pipeline_diagnostics.generator.clean import (
    DBT_BUILD_COMMAND,
    SUCCESS_CONTENT,
    build_clean_instance,
)
from data_pipeline_diagnostics.generator.raw_constraints import GenerationFailure
from data_pipeline_diagnostics.generator.records import identity_digest, write_instance_record
from data_pipeline_diagnostics.scenario.semantic import ValidatedScenario

__all__ = [
    "CleanInstance",
    "identity_digest",
    "prepare_clean_instance",
    "verify_cache_entry",
]

_CACHE_VERSION = "v1"


@dataclass(frozen=True)
class CleanInstance:
    """Handle to a handed-off isolated working copy of a clean baseline."""

    scenario_id: str
    data_seed: int
    cache_root: Path
    instance_dir: Path
    instance_digest: str
    cache_hit: bool


def _entry_dir(cache_root: Path, digest: str) -> Path:
    return cache_root / _CACHE_VERSION / digest


def verify_cache_entry(entry_dir: str | Path, digest: str) -> dict | None:
    """Verify a published entry (dict record) or return None on a clean miss.

    A present-but-incomplete entry raises (never repaired, never reused).
    """
    entry = Path(entry_dir)
    if not entry.is_dir():
        return None
    if not (entry / "SUCCESS").is_file():
        raise GenerationFailure(
            table="*",
            column=None,
            reason="cache-entry-incomplete",
            detail=f"{entry} has no SUCCESS marker",
        )
    record_path = entry / "instance_record.json"
    if not record_path.is_file():
        raise GenerationFailure(
            table="*",
            column=None,
            reason="cache-entry-incomplete",
            detail=f"{entry} has no instance_record.json",
        )
    try:
        record = json.loads(record_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise GenerationFailure(
            table="*",
            column=None,
            reason="cache-entry-corrupt",
            detail=f"{record_path}: {type(exc).__name__}: {exc}",
        ) from exc
    identity = record.get("identity", {})
    if record.get("status") != "success" or identity.get("instance_digest") != digest:
        raise GenerationFailure(
            table="*",
            column=None,
            reason="cache-entry-mismatch",
            detail=f"{entry} record does not match digest {digest}",
        )
    for artifact in record.get("artifacts", []):
        path = entry / artifact["path"]
        if not path.is_file():
            raise GenerationFailure(
                table="*",
                column=None,
                reason="cache-entry-incomplete",
                detail=f"{entry} lacks artifact {artifact['path']}",
            )
        data = path.read_bytes()
        if (
            len(data) != artifact["size_bytes"]
            or hashlib.sha256(data).hexdigest() != artifact["sha256"]
        ):
            raise GenerationFailure(
                table="*",
                column=None,
                reason="cache-entry-corrupt",
                detail=f"{entry} artifact {artifact['path']} digest mismatch",
            )
    return record


def _read_failure(tmp: Path) -> GenerationFailure:
    try:
        record = json.loads((tmp / "failure_record.json").read_text(encoding="utf-8"))
        return GenerationFailure(
            table="*",
            column=None,
            reason=str(record.get("category", "build-failed")),
            detail=str(record.get("message", "")),
        )
    except Exception:
        return GenerationFailure(
            table="*",
            column=None,
            reason="build-failed",
            detail=f"see {tmp}",
        )


def prepare_clean_instance(
    validated: ValidatedScenario, data_seed: int, cache_root: str | Path
) -> CleanInstance:
    """Check-then-build a clean baseline and hand off an isolated copy."""
    if not isinstance(validated, ValidatedScenario):
        raise TypeError(
            "compiler input must be a ValidatedScenario "
            f"(got {type(validated).__name__}); "
            "use prepare_clean_instance_from_json for JSON/path input"
        )
    if type(data_seed) is not int:
        raise ValueError(f"data_seed must be a strict int, got {type(data_seed).__name__}")
    if not 0 <= data_seed <= 2**63 - 1:
        raise ValueError(f"data_seed {data_seed} out of range [0, 2**63 - 1]")
    root = Path(cache_root)
    digest = identity_digest(validated, data_seed)
    entry = _entry_dir(root, digest)
    if entry.exists():
        verify_cache_entry(entry, digest)
        cache_hit = True
    else:
        siblings = root / _CACHE_VERSION
        siblings.mkdir(parents=True, exist_ok=True)
        tmp = siblings / f".tmp-{uuid.uuid4().hex}"
        built = build_clean_instance(
            validated=validated, data_seed=data_seed, instance_dir=tmp, write_success=False
        )
        if not built.success:
            raise _read_failure(tmp)
        write_instance_record(
            instance_dir=tmp,
            validated=validated,
            data_seed=data_seed,
            dbt_command=list(DBT_BUILD_COMMAND),
            dbt_exit_status=0,
        )
        (tmp / "SUCCESS").write_text(SUCCESS_CONTENT, encoding="utf-8")
        try:
            os.replace(tmp, entry)
        except OSError:
            if verify_cache_entry(entry, digest) is None:
                raise GenerationFailure(
                    table="*",
                    column=None,
                    reason="cache-publish-failed",
                    detail=f"could not publish {entry}",
                )
        else:
            verify_cache_entry(entry, digest)
        cache_hit = False
    work = root / "work" / uuid.uuid4().hex
    shutil.copytree(entry, work)
    scenario = validated.scenario
    return CleanInstance(
        scenario_id=str(scenario.scenario_id),
        data_seed=data_seed,
        cache_root=root,
        instance_dir=work,
        instance_digest=digest,
        cache_hit=cache_hit,
    )
