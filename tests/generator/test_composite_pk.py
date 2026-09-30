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
    ColumnPlan,
    FkGroupPlan,
    TablePlan,
    _assign_exact_pk_tuples,
    build_raw_plan,
    composite_pk_tier,
    execute_raw_plan,
    generate_raw_data,
)
from data_pipeline_diagnostics.generator.records import plan_stream_inventory
from data_pipeline_diagnostics.generator.relationships import sample_row_counts
from data_pipeline_diagnostics.generator.rng import rows_stream_name
from data_pipeline_diagnostics.generator.rng import stream as make_stream
from data_pipeline_diagnostics.scenario.generators import CategoricalGenerator
from data_pipeline_diagnostics.scenario.parsing import parse_scenario_file
from data_pipeline_diagnostics.scenario.semantic import validate_semantics
from data_pipeline_diagnostics.scenario.types import DataType, RowCount

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
    cat = ColumnPlan(
        name="cat",
        kind="leaf",
        config=CategoricalGenerator(values=("x",)),
        type=DataType.string,
        nullable=False,
        null_probability=0.0,
        unique=False,
        value_stream="values/t/cat",
        null_stream="nulls/t/cat",
    )
    fk = ColumnPlan(
        name="fk",
        kind="foreign_key",
        config=CategoricalGenerator(values=("x",)),
        type=DataType.string,
        nullable=False,
        null_probability=0.0,
        unique=False,
        relationship="rel",
        target_side="left",
        value_stream="foreign_key/rel/t/left",
        null_stream="nulls/foreign_key/rel/t/left",
    )
    table = TablePlan(
        name="t",
        rows=RowCount(min=3, max=3),
        columns=(fk, cat),
        declaration_column_order=("fk", "cat"),
        primary_key=("fk", "cat"),
    )
    group = FkGroupPlan(
        relationship="rel",
        dependent_table="t",
        target_side="left",
        dependent_columns=("fk",),
        target_table="u",
        target_columns=("k",),
        null_terms=((False, 0.0),),
        without_replacement=False,
        value_stream="foreign_key/rel/t/left",
        null_stream="nulls/foreign_key/rel/t/left",
    )
    groups = {("rel", "t", "left"): group}
    finished = {"u": [{"k": "a"}, {"k": "b"}]}
    rows = [{}, {}, {}]
    with pytest.raises(GenerationFailure) as exc_info:
        _assign_exact_pk_tuples("gate", 11, table, 3, finished, groups, rows)
    assert exc_info.value.reason == "composite-pk-domain-exhausted"


def test_count_conditioning_rejects_infeasible_range():
    validated = _validated("agriculture_exports_001.json")
    plan = build_raw_plan(validated)
    tables = tuple(
        replace(table, rows=RowCount(min=10, max=10)) if table.name == "raw_grades" else table
        for table in plan.tables
    )
    swollen = replace(plan, tables=tables)
    with pytest.raises(GenerationFailure) as exc_info:
        execute_raw_plan(swollen, 0)
    assert exc_info.value.reason == "row-count-capacity-exceeded"


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


def test_count_conditioning_resamples_capped_table():
    # Seed 11 draws 9 first for capped[1..10], violating cap 5, so the
    # resample path necessarily engages.
    rows = {"capped": RowCount(min=1, max=10), "other": RowCount(min=4, max=6)}
    first = sample_row_counts(scenario_id="g-cap", data_seed=11, rows=rows, caps={"capped": 5})
    assert first["capped"] <= 5
    assert first["capped"] != 9
    assert first == sample_row_counts(
        scenario_id="g-cap", data_seed=11, rows=rows, caps={"capped": 5}
    )
    expected_other = make_stream("g-cap", 11, rows_stream_name("other")).randint(4, 6)
    assert first["other"] == expected_other


def test_count_capacity_violations_fail_fast():
    with pytest.raises(GenerationFailure) as exc_info:
        sample_row_counts(
            scenario_id="g-cap",
            data_seed=2,
            rows={"t": RowCount(min=10, max=10)},
            caps={"t": 5},
        )
    assert exc_info.value.reason == "row-count-capacity-exceeded"
    with pytest.raises(GenerationFailure) as exc_info:
        sample_row_counts(
            scenario_id="g-cap",
            data_seed=2,
            rows={"t": RowCount(min=6, max=10)},
            caps={"t": 5},
        )
    assert exc_info.value.reason == "row-count-capacity-exceeded"
    with pytest.raises(ValueError):
        sample_row_counts(
            scenario_id="g-cap",
            data_seed=2,
            rows={"t": RowCount(min=1, max=2)},
            caps={"nope": 5},
        )


def test_conditioned_table_builds_end_to_end(tmp_path):
    validated = _validated("healthcare_protocols_001.json")
    data = generate_raw_data(validated, 0)
    arms = [(r["protocol_id"], r["arm_no"]) for r in data["raw_arms"]]
    assert len(arms) <= 8 * 3
    assert len(arms) == len(set(arms))
    result = build_clean_instance(
        validated=validated, data_seed=0, instance_dir=tmp_path / "instance"
    )
    assert result.success
