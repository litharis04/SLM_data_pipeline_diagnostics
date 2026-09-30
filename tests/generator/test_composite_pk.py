"""Joint composite-PK sampling: exact product shuffle vs bounded retry.

Per-component independent sampling cannot satisfy tight product domains
(``raw_grades``: 9 rows over 3×3 combinations, P ≈ 0.09%) or jointly unique
FK tuples sampled per group (``raw_enrollments``: 240 rows over ~400
combinations, P ≈ e⁻⁷²). These tests pin the joint paths on real corpus
scenarios: exact shuffle via the dedicated ``pk/<table>`` stream when every
component is finitely enumerable, bounded tuple retry otherwise, and a loud
precheck when rows exceed capacity. Single-column keys and injective-member
tables keep their existing paths (see G06/G08 suites).
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from data_pipeline_diagnostics.generator.clean import build_clean_instance
from data_pipeline_diagnostics.generator.raw_constraints import GenerationFailure
from data_pipeline_diagnostics.generator.raw_plan import (
    build_raw_plan,
    composite_pk_tier,
    execute_raw_plan,
    generate_raw_data,
)
from data_pipeline_diagnostics.generator.records import plan_stream_inventory
from data_pipeline_diagnostics.scenario.parsing import parse_scenario_file
from data_pipeline_diagnostics.scenario.semantic import validate_semantics
from data_pipeline_diagnostics.scenario.types import RowCount

REPO = Path(__file__).resolve().parents[2]


def _validated(name: str):
    return validate_semantics(parse_scenario_file(REPO / "scenarios" / name))


def _tier(validated, table_name: str) -> str | None:
    plan = build_raw_plan(validated)
    table = next(t for t in plan.tables if t.name == table_name)
    return composite_pk_tier(table, [g for g in plan.fk_groups if g.dependent_table == table_name])


def test_exact_tight_product_permutation():
    validated = _validated("agriculture_exports_001.json")
    assert _tier(validated, "raw_grades") == "exact"
    first = generate_raw_data(validated, 0)
    grades = [(r["crop"], r["grade"]) for r in first["raw_grades"]]
    assert len(grades) == 9
    assert set(grades) == {
        (crop, grade) for crop in ("barley", "corn", "wheat") for grade in ("A", "B", "C")
    }
    assert generate_raw_data(validated, 0) == first


def test_joint_fk_keys_distinct_and_referential():
    validated = _validated("education_cohorts_001.json")
    assert _tier(validated, "raw_enrollments") == "exact"
    assert _tier(validated, "raw_transcripts") == "exact"
    data = generate_raw_data(validated, 11)
    enrollments = [(r["student_id"], r["course_id"]) for r in data["raw_enrollments"]]
    assert len(enrollments) == len(set(enrollments))
    transcripts = [(r["student_id"], r["course_id"]) for r in data["raw_transcripts"]]
    assert len(transcripts) == len(set(transcripts))
    assert set(transcripts) <= set(enrollments)
    assert generate_raw_data(validated, 11) == data


def test_domain_exhaustion_precheck():
    validated = _validated("agriculture_exports_001.json")
    plan = build_raw_plan(validated)
    tables = tuple(
        replace(table, rows=RowCount(min=10, max=10)) if table.name == "raw_grades" else table
        for table in plan.tables
    )
    swollen = replace(plan, tables=tables)
    with pytest.raises(GenerationFailure) as exc_info:
        execute_raw_plan(swollen, 0)
    assert "composite-pk-domain-exhausted" in str(exc_info.value.reason)


def test_retry_tier_open_domains():
    validated = _validated("hospitality_stays_001.json")
    assert _tier(validated, "raw_room_nights") == "retry"
    data = generate_raw_data(validated, 11)
    nights = [(r["room_id"], r["night_date"]) for r in data["raw_room_nights"]]
    assert len(nights) == len(set(nights))
    assert generate_raw_data(validated, 11) == data


def test_safe_tables_take_no_joint_tier():
    validated = _validated("agriculture_coop_001.json")
    plan = build_raw_plan(validated)
    assert all(
        composite_pk_tier(table, [g for g in plan.fk_groups if g.dependent_table == table.name])
        is None
        for table in plan.tables
    )
    assert not [s for s in plan_stream_inventory(plan) if s.startswith("pk/")]


def test_inventory_lists_pk_stream_only_for_exact():
    plan = build_raw_plan(_validated("agriculture_exports_001.json"))
    inventory = plan_stream_inventory(plan)
    assert "pk/raw_grades" in inventory
    assert "values/raw_grades/crop" not in inventory
    assert "values/raw_grades/grade" not in inventory


def test_exact_table_builds_end_to_end(tmp_path):
    validated = _validated("agriculture_exports_001.json")
    result = build_clean_instance(
        validated=validated, data_seed=0, instance_dir=tmp_path / "instance"
    )
    assert result.success
