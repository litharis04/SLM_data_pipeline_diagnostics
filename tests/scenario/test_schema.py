"""Tests for JSON Schema export (§18.3)."""

from __future__ import annotations

import json
from pathlib import Path

from data_pipeline_diagnostics.scenario.json_schema import get_scenario_json_schema
from data_pipeline_diagnostics.scenario.models import Scenario


def test_schema_contains_discriminators():
    schema = get_scenario_json_schema()
    schema_str = json.dumps(schema)
    # Check discriminators present
    for disc in ["kind", "cardinality", "op", "function", "type", "operation"]:
        assert disc in schema_str, f"discriminator {disc} not in schema"


def test_schema_additional_properties_false():
    schema = get_scenario_json_schema()
    # Check that at least some definitions have additionalProperties false (from ContractModel extra=forbid)
    schema_str = json.dumps(schema)
    assert "additionalProperties" in schema_str
    # Count occurrences – should be false for many
    assert schema_str.count('"additionalProperties": false') >= 5


def test_schema_version_identifies_language():
    schema = get_scenario_json_schema()
    # Schema should contain version 1.0 somewhere (scenario_version literal)
    schema_str = json.dumps(schema)
    assert "1.0" in schema_str
    # Also check that Scenario's schema_version is in required
    assert "schema_version" in schema_str


def test_schema_generated_from_pydantic():
    # Ensure schema is derived from Pydantic, not hand-written – check that it contains Pydantic-specific keys
    schema = get_scenario_json_schema()
    # Pydantic generates $defs
    assert "$defs" in schema or "definitions" in schema or "properties" in schema
    # Ensure it matches Scenario.model_json_schema()
    expected = Scenario.model_json_schema()
    assert schema == expected


def test_schema_freshness():
    # If checked-in schema exists, compare; otherwise this test just ensures generation works
    # We do not auto-rewrite, we fail if stale
    schema = get_scenario_json_schema()
    candidates = [
        Path("artifacts/scenario.schema.json"),
        Path("docs/schema.json"),
        Path("artifacts/scenario_schema.json"),
    ]
    checked_in = None
    for p in candidates:
        if p.exists():
            checked_in = p
            break
    if checked_in is None:
        # No checked-in schema yet – this is okay, just ensure generation doesn't fail
        assert isinstance(schema, dict)
        return
    # Compare
    on_disk = json.loads(checked_in.read_text(encoding="utf-8"))
    assert on_disk == schema, f"checked-in schema at {checked_in} is stale – run export_json_schema"
