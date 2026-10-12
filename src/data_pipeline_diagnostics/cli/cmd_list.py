"""``plgen list`` — read-only personal catalog listing (C03).

Bootstraps the workspace on first use (the only permitted writes), then
prints one row per registered scenario in lexicographic id order. Display
is total: unreadable files yield ``invalid``/``unknown`` markers, never a
crash. Sizes come only from prepared ``instance_record.json`` realized row
counts, never from planned ``rows`` intervals. No LLM, no materialization.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

from data_pipeline_diagnostics.cli.features import SIZE_PRESET_CEILINGS, classify_raw_size
from data_pipeline_diagnostics.cli.workspace import (
    catalog_path,
    ensure_workspace,
    entry_path,
    scenario_json_path,
    validate_scenario_id,
)
from data_pipeline_diagnostics.scenario import parse_scenario_json

# Alias kept for backward compatibility; canonical table lives in features.py.
RAW_SIZE_CEILINGS = SIZE_PRESET_CEILINGS

STATE_PREPARED = "prepared"
STATE_UNPREPARED = "unprepared"
INVALID = "invalid"
SIZE_ABSENT = "-"
SIZE_UNKNOWN = "unknown"

HEADER = "scenario_id domain seed state size description"


@dataclass(frozen=True)
class ListedScenario:
    """One display row; every field is pre-rendered text."""

    scenario_id: str
    domain: str
    description: str
    seed: str
    state: str
    size: str

    def format(self) -> str:
        return (
            f"{self.scenario_id}  {self.domain}  {self.seed}  "
            f"{self.state}  {self.size}  {self.description}"
        )


def _instance_size(instance: object) -> str:
    """Observed size category for a prepared entry, or ``unknown``."""
    record_dir: Path | None = None
    if isinstance(instance, dict) and isinstance(instance.get("path"), str):
        record_dir = Path(instance["path"])
    if record_dir is None:
        return SIZE_UNKNOWN
    try:
        record = json.loads((record_dir / "instance_record.json").read_text(encoding="utf-8"))
        tables = record.get("raw_tables") if isinstance(record, dict) else None
        if not isinstance(tables, list) or not tables:
            return SIZE_UNKNOWN
        counts: list[int] = []
        for table in tables:
            if not isinstance(table, dict):
                return SIZE_UNKNOWN
            count = table.get("row_count")
            if not isinstance(count, int) or isinstance(count, bool) or count < 0:
                return SIZE_UNKNOWN
            counts.append(count)
        return classify_raw_size(max(counts))
    except OSError, ValueError:
        return SIZE_UNKNOWN


def build_row(root: Path, scenario_id: str) -> ListedScenario:
    """Build one row; unreadable files become markers, never exceptions."""
    try:
        scenario = parse_scenario_json(scenario_json_path(root, scenario_id).read_bytes())
    except Exception:
        domain = description = INVALID
    else:
        domain, description = str(scenario.domain), str(scenario.description)
    try:
        entry = json.loads(entry_path(root, scenario_id).read_text(encoding="utf-8"))
        if not isinstance(entry, dict):
            raise ValueError("entry.json must be a JSON object")
    except Exception:
        return ListedScenario(scenario_id, domain, description, INVALID, INVALID, SIZE_ABSENT)
    seed = entry.get("seed")
    seed_text = str(seed) if isinstance(seed, int) and not isinstance(seed, bool) else INVALID
    instance = entry.get("instance")
    if instance is None:
        return ListedScenario(
            scenario_id, domain, description, seed_text, STATE_UNPREPARED, SIZE_ABSENT
        )
    return ListedScenario(
        scenario_id, domain, description, seed_text, STATE_PREPARED, _instance_size(instance)
    )


def collect_rows(root: Path, scenario_ids: list[str]) -> list[ListedScenario]:
    """Rows in locale-independent lexicographic (codepoint) id order."""
    return [build_row(root, scenario_id) for scenario_id in sorted(scenario_ids)]


def format_table(rows: list[ListedScenario]) -> str:
    return "\n".join([HEADER, *(row.format() for row in rows)])


def run_list(workspace: Path) -> int:
    """Bootstrap if needed, then print the catalog table to stdout."""
    root = Path(workspace)
    ensure_workspace(root)
    try:
        catalog = json.loads(catalog_path(root).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"plgen list: cannot read catalog {catalog_path(root)}: {exc}", file=sys.stderr)
        return 5
    ids = catalog.get("scenario_ids") if isinstance(catalog, dict) else None
    if not isinstance(ids, list) or any(not isinstance(item, str) for item in ids):
        print(f"plgen list: malformed catalog {catalog_path(root)}", file=sys.stderr)
        return 5
    try:
        for item in ids:
            validate_scenario_id(item)
    except ValueError as exc:
        print(f"plgen list: malformed catalog {catalog_path(root)}: {exc}", file=sys.stderr)
        return 5
    print(format_table(collect_rows(root, ids)))
    return 0
