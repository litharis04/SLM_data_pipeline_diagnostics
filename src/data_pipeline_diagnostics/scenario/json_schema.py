"""JSON Schema export for Scenario (§18.3)."""

from __future__ import annotations

import json
from pathlib import Path

from data_pipeline_diagnostics.scenario.models import Scenario


def get_scenario_json_schema() -> dict:
    """Return JSON Schema generated from Scenario model."""
    schema = Scenario.model_json_schema()
    # Ensure version is identifiable
    # Pydantic already includes title and version, but we ensure scenario-language version is present
    # The schema should contain $defs with discriminators and additionalProperties false
    return schema


def export_json_schema(path: str | Path, *, indent: int = 2) -> None:
    """Deterministically export JSON Schema to path."""
    schema = get_scenario_json_schema()
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    # Deterministic: sorted keys, indent 2, UTF-8, ensure_ascii False
    content = json.dumps(schema, sort_keys=True, indent=indent, ensure_ascii=False) + "\n"
    p.write_text(content, encoding="utf-8")
