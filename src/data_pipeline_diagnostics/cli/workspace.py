"""Workspace bootstrap, catalog and entry metadata (C02).

Every command builds on this layer. All writes stay inside the workspace
root and go through write-tmp-plus-atomic-rename, so an interrupted command
may leave a tmp file behind but never a half-written catalog or entry.
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Mapping, Sequence
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from pydantic import TypeAdapter, ValidationError

from data_pipeline_diagnostics.scenario import (
    ScenarioId,
    ScenarioParseError,
    parse_scenario_json,
    scenario_content_hash,
)

CONFIG_FILENAME = "config.json"
CATALOG_FILENAME = "catalog.json"
SCENARIO_FILENAME = "scenario.json"
ENTRY_FILENAME = "entry.json"
SCENARIOS_DIRNAME = "scenarios"
CACHE_DIRNAME = "cache"
AUTHORING_DIRNAME = "authoring"

_SCENARIO_ID_ADAPTER = TypeAdapter(ScenarioId)


def _package_version() -> str:
    try:
        return version("data-pipeline-diagnostics")
    except PackageNotFoundError:
        return "0.1.0"


def package_major_minor() -> str:
    """Bootstrap contract version: package ``major.minor`` (patch excluded)."""
    parts = _package_version().split(".")
    if len(parts) >= 2:
        return f"{parts[0]}.{parts[1]}"
    return _package_version()


def validate_scenario_id(value: object) -> str:
    """Check ``value`` against the ``ScenarioId`` contract (shared helper)."""
    try:
        return _SCENARIO_ID_ADAPTER.validate_python(value)
    except ValidationError as exc:
        raise ValueError(f"invalid SCENARIO_ID {value!r}") from exc


def config_path(root: Path) -> Path:
    return Path(root) / CONFIG_FILENAME


def catalog_path(root: Path) -> Path:
    return Path(root) / CATALOG_FILENAME


def scenario_dir(root: Path, scenario_id: str) -> Path:
    return Path(root) / SCENARIOS_DIRNAME / scenario_id


def scenario_json_path(root: Path, scenario_id: str) -> Path:
    return scenario_dir(root, scenario_id) / SCENARIO_FILENAME


def entry_path(root: Path, scenario_id: str) -> Path:
    return scenario_dir(root, scenario_id) / ENTRY_FILENAME


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Write ``data`` to ``path`` via tmp file plus atomic rename."""
    tmp = path.parent / f"{path.name}.tmp-{uuid.uuid4().hex}"
    tmp.write_bytes(data)
    os.replace(tmp, path)


def atomic_write_json(path: Path, payload: Mapping[str, object]) -> None:
    """Write ``payload`` as UTF-8 JSON (LF, single final newline) atomically."""
    text = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    atomic_write_bytes(path, text.encode("utf-8"))


def minimal_imported_entry(scenario_hash: str) -> dict[str, object]:
    """Minimal ``entry.json`` for an imported example (no instance yet)."""
    return {
        "origin": "imported",
        "seed": 0,
        "scenario_hash": scenario_hash,
        "instance": None,
    }


def instance_pointer(workdir: Path, digest: str) -> dict[str, str]:
    """Current-instance pointer stored in ``entry.json`` (C08 writes, C03 reads)."""
    path = Path(workdir)
    if not path.is_absolute():
        raise ValueError(f"instance path must be absolute, got {workdir!r}")
    if not isinstance(digest, str) or not digest:
        raise ValueError("instance digest must be a non-empty string")
    return {"path": str(path), "digest": digest}


def init_workspace(root: Path, bundle: Sequence[tuple[str, bytes]]) -> bool:
    """Bootstrap ``root`` from ``bundle``; ``True`` when bootstrapped now.

    ``bundle`` is ``(scenario_id, json_bytes)`` pairs. The bootstrap runs
    once: when ``catalog.json`` already exists nothing is touched (deleted
    examples stay deleted, personal files are never overwritten). Scenario
    directories are written before ``catalog.json``, so an interrupted first
    run is recoverable by rerunning. ``ValueError`` on invalid bundle input.
    """
    root = Path(root)
    if catalog_path(root).exists():
        return False

    planned: list[tuple[str, bytes, str]] = []
    seen: set[str] = set()
    for raw_id, data in bundle:
        scenario_id = validate_scenario_id(raw_id)
        if scenario_id in seen:
            raise ValueError(f"duplicate bundled SCENARIO_ID {scenario_id!r}")
        seen.add(scenario_id)
        try:
            scenario = parse_scenario_json(data)
        except ScenarioParseError as exc:
            raise ValueError(f"invalid bundled scenario {scenario_id!r}: {exc}") from exc
        if scenario.scenario_id != scenario_id:
            raise ValueError(
                f"bundled SCENARIO_ID {scenario_id!r} "
                f"does not match document id {scenario.scenario_id!r}"
            )
        planned.append((scenario_id, bytes(data), scenario_content_hash(scenario)))
    planned.sort(key=lambda item: item[0])

    (root / SCENARIOS_DIRNAME).mkdir(parents=True, exist_ok=True)
    (root / CACHE_DIRNAME).mkdir(parents=True, exist_ok=True)
    (root / AUTHORING_DIRNAME).mkdir(parents=True, exist_ok=True)
    atomic_write_json(config_path(root), {"profiles": {}, "active_provider": None})
    for scenario_id, data, digest in planned:
        target = scenario_dir(root, scenario_id)
        target.mkdir(parents=True, exist_ok=True)
        atomic_write_bytes(target / SCENARIO_FILENAME, data)
        atomic_write_json(target / ENTRY_FILENAME, minimal_imported_entry(digest))
    atomic_write_json(
        catalog_path(root),
        {
            "bootstrap_major_minor": package_major_minor(),
            "scenario_ids": [scenario_id for scenario_id, _, _ in planned],
        },
    )
    return True


def ensure_workspace(root: Path) -> bool:
    """Bootstrap ``root`` from the bundled corpus when not yet initialized."""
    from data_pipeline_diagnostics.cli.bundle import iter_bundled_scenarios

    if catalog_path(Path(root)).exists():
        return False
    return init_workspace(root, iter_bundled_scenarios())
