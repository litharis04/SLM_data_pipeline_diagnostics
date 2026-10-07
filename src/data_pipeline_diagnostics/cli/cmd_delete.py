"""``plgen delete`` — remove a personal scenario and its owned paths (C10).

Removes the owned working copy, then the scenario files, then the catalog
entry last (atomic rewrite), so an interrupted deletion is resumable by
rerunning. Never touches package examples, repository files, sibling
entries, provider profiles, cache baselines, or authoring history. The
explicit command is authorization; no confirmation is requested.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from data_pipeline_diagnostics.cli.workspace import (
    atomic_write_json,
    catalog_path,
    ensure_workspace,
    validate_scenario_id,
)

SCENARIO_FILENAMES = ("scenario.json", "entry.json")


def _confined_dir(root: Path, scenario_id: str) -> Path | None:
    """Scenario dir strictly under ``<root>/scenarios``; ``None`` on escape."""
    base = (Path(root) / "scenarios").resolve()
    try:
        target = (base / scenario_id).resolve()
    except OSError:
        return None
    try:
        target.relative_to(base)
    except ValueError:
        return None
    if target == base:
        return None
    return target


def _remove_owned_workdir(target: Path) -> None:
    workdir = target / "work"
    if workdir.is_symlink() or workdir.is_file():
        workdir.unlink()
    elif workdir.is_dir():
        shutil.rmtree(workdir)


def _remove_scenario_files(target: Path) -> None:
    for filename in SCENARIO_FILENAMES:
        candidate = target / filename
        if candidate.is_symlink() or candidate.is_file():
            candidate.unlink()
    for leftover in sorted(target.glob("work.tmp-*")):
        if leftover.is_symlink() or leftover.is_file():
            leftover.unlink()
        elif leftover.is_dir():
            shutil.rmtree(leftover)
    try:
        target.rmdir()
    except OSError:
        pass


def run_delete(workspace: Path, args: argparse.Namespace) -> int:
    """Delete one personal scenario; see module docstring for order/guarantees."""
    try:
        scenario_id = validate_scenario_id(args.scenario_id)
    except ValueError as exc:
        print(f"plgen delete: {exc}", file=sys.stderr)
        return 2
    root = Path(workspace)
    ensure_workspace(root)
    try:
        catalog = json.loads(catalog_path(root).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"plgen delete: unreadable catalog {catalog_path(root)}: {exc}", file=sys.stderr)
        return 5
    ids = catalog.get("scenario_ids") if isinstance(catalog, dict) else None
    if not isinstance(ids, list) or any(not isinstance(item, str) for item in ids):
        print(f"plgen delete: malformed catalog {catalog_path(root)}", file=sys.stderr)
        return 5
    if scenario_id not in ids:
        print(f"plgen delete: unknown scenario {scenario_id!r}", file=sys.stderr)
        return 2
    target = _confined_dir(root, scenario_id)
    if target is None:
        print(f"plgen delete: rejected out-of-workspace id {scenario_id!r}", file=sys.stderr)
        return 2
    try:
        _remove_owned_workdir(target)
        _remove_scenario_files(target)
        remaining = [item for item in ids if item != scenario_id]
        atomic_write_json(
            catalog_path(root),
            {
                "bootstrap_major_minor": catalog.get("bootstrap_major_minor"),
                "scenario_ids": remaining,
            },
        )
    except Exception as exc:
        print(f"plgen delete: workspace failure for {scenario_id!r}: {exc}", file=sys.stderr)
        return 5
    print(f"deleted {scenario_id}")
    return 0
