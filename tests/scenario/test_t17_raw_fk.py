"""Focused tests for T17 – raw FK null-probability agreement and raw FK acyclicity."""

import pytest

from data_pipeline_diagnostics.scenario.errors import SemanticValidationError
from data_pipeline_diagnostics.scenario.models import Scenario
from data_pipeline_diagnostics.scenario.semantic import validate_semantics


def _base():
    """Minimal valid 3-table chain: raw_c -> raw_b -> raw_a (all many_to_one)."""
    return {
        "schema_version": "1.0",
        "scenario_id": "test_t17",
        "domain": "testdomain",
        "raw_tables": (
            {
                "name": "raw_a",
                "rows": {"min": 10, "max": 20},
                "columns": (
                    {
                        "name": "id",
                        "type": "integer",
                        "generator": {"kind": "integer_range", "min": 1, "max": 100},
                    },
                ),
                "primary_key": ("id",),
            },
            {
                "name": "raw_b",
                "rows": {"min": 5, "max": 10},
                "columns": (
                    {
                        "name": "id",
                        "type": "integer",
                        "generator": {"kind": "integer_range", "min": 1, "max": 100},
                    },
                    {
                        "name": "a_id",
                        "type": "integer",
                        "generator": {
                            "kind": "foreign_key",
                            "relationship": "rel_ab",
                            "target_side": "right",
                        },
                    },
                ),
                "primary_key": ("id",),
            },
            {
                "name": "raw_c",
                "rows": {"min": 5, "max": 10},
                "columns": (
                    {
                        "name": "id",
                        "type": "integer",
                        "generator": {"kind": "integer_range", "min": 1, "max": 100},
                    },
                    {
                        "name": "b_id",
                        "type": "integer",
                        "generator": {
                            "kind": "foreign_key",
                            "relationship": "rel_bc",
                            "target_side": "right",
                        },
                    },
                ),
                "primary_key": ("id",),
            },
        ),
        "relationships": (
            {
                "name": "rel_ab",
                "cardinality": "many_to_one",
                "left": {"table": "raw_b", "columns": ("a_id",)},
                "right": {"table": "raw_a", "columns": ("id",)},
            },
            {
                "name": "rel_bc",
                "cardinality": "many_to_one",
                "left": {"table": "raw_c", "columns": ("b_id",)},
                "right": {"table": "raw_b", "columns": ("id",)},
            },
        ),
        "staging_models": (
            {
                "name": "stg_a",
                "source": "raw_a",
                "columns": ({"source": "id", "target": "id"},),
                "grain": ("id",),
            },
            {
                "name": "stg_b",
                "source": "raw_b",
                "columns": (
                    {"source": "id", "target": "id"},
                    {"source": "a_id", "target": "a_id"},
                ),
                "grain": ("id",),
            },
            {
                "name": "stg_c",
                "source": "raw_c",
                "columns": (
                    {"source": "id", "target": "id"},
                    {"source": "b_id", "target": "b_id"},
                ),
                "grain": ("id",),
            },
        ),
        "intermediate_models": (
            {
                "name": "t_a",
                "operation": "transform",
                "source": "stg_a",
                "columns": ({"source": "id", "target": "id"},),
                "grain": ("id",),
            },
            {
                "name": "j_ab",
                "operation": "join",
                "left": "stg_b",
                "right": "t_a",
                "join": {
                    "type": "inner",
                    "on": ({"left": "a_id", "right": "id"},),
                },
                "columns": (
                    {"side": "left", "source": "id", "target": "bid"},
                    {"side": "right", "source": "id", "target": "aid"},
                ),
                "grain": ("bid",),
            },
            {
                "name": "m_full",
                "operation": "join",
                "left": "stg_c",
                "right": "j_ab",
                "join": {
                    "type": "inner",
                    "on": ({"left": "b_id", "right": "bid"},),
                },
                "columns": (
                    {"side": "left", "source": "id", "target": "cid"},
                    {"side": "right", "source": "bid", "target": "bid"},
                    {"side": "right", "source": "aid", "target": "aid"},
                ),
                "grain": ("cid",),
            },
        ),
        "output_models": (
            {
                "name": "o_main",
                "source": "m_full",
                "group_by": ({"source": "cid", "target": "cid"},),
                "grain": ("cid",),
                "metrics": ({"name": "n", "function": "count_rows"},),
            },
        ),
    }


def test_valid_chain_passes():
    validated = validate_semantics(Scenario(**_base()))
    assert validated.scenario.scenario_id == "test_t17"


def test_composite_null_probability_mismatch_rejected():
    data = _base()
    raw_tables = [dict(t) for t in data["raw_tables"]]
    raw_a = dict(raw_tables[0])
    raw_a["columns"] = (
        {
            "name": "id1",
            "type": "integer",
            "generator": {"kind": "integer_range", "min": 1, "max": 100},
        },
        {
            "name": "id2",
            "type": "integer",
            "generator": {"kind": "integer_range", "min": 1, "max": 100},
        },
    )
    raw_a["primary_key"] = ("id1", "id2")
    raw_b = dict(raw_tables[1])
    raw_b["columns"] = (
        {
            "name": "id",
            "type": "integer",
            "generator": {"kind": "integer_range", "min": 1, "max": 100},
        },
        {
            "name": "a1",
            "type": "integer",
            "nullable": True,
            "null_probability": 0.2,
            "generator": {
                "kind": "foreign_key",
                "relationship": "rel_ab",
                "target_side": "right",
            },
        },
        {
            "name": "a2",
            "type": "integer",
            "nullable": True,
            "null_probability": 0.5,
            "generator": {
                "kind": "foreign_key",
                "relationship": "rel_ab",
                "target_side": "right",
            },
        },
    )
    raw_tables[0] = raw_a
    raw_tables[1] = raw_b
    data["raw_tables"] = tuple(raw_tables)
    data["relationships"] = (
        {
            "name": "rel_ab",
            "cardinality": "many_to_one",
            "left": {"table": "raw_b", "columns": ("a1", "a2")},
            "right": {"table": "raw_a", "columns": ("id1", "id2")},
        },
        {
            "name": "rel_bc",
            "cardinality": "many_to_one",
            "left": {"table": "raw_c", "columns": ("b_id",)},
            "right": {"table": "raw_b", "columns": ("id",)},
        },
    )
    with pytest.raises(SemanticValidationError) as exc:
        validate_semantics(Scenario(**data))
    codes = [i.code for i in exc.value.issues]
    assert "E111" in codes


def test_composite_null_probability_agreement_accepted():
    data = _base()
    raw_tables = [dict(t) for t in data["raw_tables"]]
    raw_a = dict(raw_tables[0])
    raw_a["columns"] = (
        {
            "name": "id1",
            "type": "integer",
            "generator": {"kind": "integer_range", "min": 1, "max": 100},
        },
        {
            "name": "id2",
            "type": "integer",
            "generator": {"kind": "integer_range", "min": 1, "max": 100},
        },
    )
    raw_a["primary_key"] = ("id1", "id2")
    raw_b = dict(raw_tables[1])
    raw_b["columns"] = (
        {
            "name": "id",
            "type": "integer",
            "generator": {"kind": "integer_range", "min": 1, "max": 100},
        },
        {
            "name": "a1",
            "type": "integer",
            "nullable": True,
            "null_probability": 0.2,
            "generator": {
                "kind": "foreign_key",
                "relationship": "rel_ab",
                "target_side": "right",
            },
        },
        {
            "name": "a2",
            "type": "integer",
            "nullable": True,
            "null_probability": 0.2,
            "generator": {
                "kind": "foreign_key",
                "relationship": "rel_ab",
                "target_side": "right",
            },
        },
    )
    raw_tables[0] = raw_a
    raw_tables[1] = raw_b
    data["raw_tables"] = tuple(raw_tables)
    data["relationships"] = (
        {
            "name": "rel_ab",
            "cardinality": "many_to_one",
            "left": {"table": "raw_b", "columns": ("a1", "a2")},
            "right": {"table": "raw_a", "columns": ("id1", "id2")},
        },
        {
            "name": "rel_bc",
            "cardinality": "many_to_one",
            "left": {"table": "raw_c", "columns": ("b_id",)},
            "right": {"table": "raw_b", "columns": ("id",)},
        },
    )
    # Composite FK with equal null_probability is atomic and valid.
    # Renamed raw columns must stay visible through staging and the join.
    staging = [dict(s) for s in data["staging_models"]]
    staging[0] = {
        "name": "stg_a",
        "source": "raw_a",
        "columns": (
            {"source": "id1", "target": "id1"},
            {"source": "id2", "target": "id2"},
        ),
        "grain": ("id1", "id2"),
    }
    staging[1] = {
        "name": "stg_b",
        "source": "raw_b",
        "columns": (
            {"source": "id", "target": "id"},
            {"source": "a1", "target": "a1"},
            {"source": "a2", "target": "a2"},
        ),
        "grain": ("id",),
    }
    data["staging_models"] = tuple(staging)
    intermediate = [dict(m) for m in data["intermediate_models"]]
    intermediate[0] = {
        "name": "t_a",
        "operation": "transform",
        "source": "stg_a",
        "columns": (
            {"source": "id1", "target": "id1"},
            {"source": "id2", "target": "id2"},
        ),
        "grain": ("id1", "id2"),
    }
    intermediate[1] = {
        "name": "j_ab",
        "operation": "join",
        "left": "stg_b",
        "right": "t_a",
        "join": {
            "type": "inner",
            "on": (
                {"left": "a1", "right": "id1"},
                {"left": "a2", "right": "id2"},
            ),
        },
        "columns": (
            {"side": "left", "source": "id", "target": "bid"},
            {"side": "right", "source": "id1", "target": "aid1"},
            {"side": "right", "source": "id2", "target": "aid2"},
        ),
        "grain": ("bid",),
    }
    intermediate[2] = {
        "name": "m_full",
        "operation": "join",
        "left": "stg_c",
        "right": "j_ab",
        "join": {
            "type": "inner",
            "on": ({"left": "b_id", "right": "bid"},),
        },
        "columns": (
            {"side": "left", "source": "id", "target": "cid"},
            {"side": "right", "source": "bid", "target": "bid"},
            {"side": "right", "source": "aid1", "target": "aid1"},
            {"side": "right", "source": "aid2", "target": "aid2"},
        ),
        "grain": ("cid",),
    }
    data["intermediate_models"] = tuple(intermediate)
    validate_semantics(Scenario(**data))


def test_raw_fk_cycle_rejected():
    data = _base()
    raw_tables = [dict(t) for t in data["raw_tables"]]
    raw_a = dict(raw_tables[0])
    raw_a["columns"] = (
        {
            "name": "id",
            "type": "integer",
            "generator": {"kind": "integer_range", "min": 1, "max": 100},
        },
        {
            "name": "c_id",
            "type": "integer",
            "generator": {
                "kind": "foreign_key",
                "relationship": "rel_ca",
                "target_side": "right",
            },
        },
    )
    raw_tables[0] = raw_a
    data["raw_tables"] = tuple(raw_tables)
    data["relationships"] = tuple(data["relationships"]) + (
        {
            "name": "rel_ca",
            "cardinality": "many_to_one",
            "left": {"table": "raw_a", "columns": ("c_id",)},
            "right": {"table": "raw_c", "columns": ("id",)},
        },
    )
    with pytest.raises(SemanticValidationError) as exc:
        validate_semantics(Scenario(**data))
    codes = [i.code for i in exc.value.issues]
    assert "E135" in codes
    cycle_issue = next(i for i in exc.value.issues if i.code == "E135")
    assert "raw_a" in cycle_issue.message
    assert "rel_ca" in cycle_issue.message
