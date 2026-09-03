"""Tests for T14 – key capacity and deep immutability."""

from types import MappingProxyType

import pytest

from data_pipeline_diagnostics.scenario.errors import SemanticValidationError
from data_pipeline_diagnostics.scenario.models import Scenario
from data_pipeline_diagnostics.scenario.semantic import validate_semantics


def _base():
    return {
        "schema_version": "1.0",
        "scenario_id": "test_t14",
        "domain": "testdomain",
        "raw_tables": (
            {"name": "raw_a", "rows": {"min": 1, "max": 10}, "columns": ({"name": "id", "type": "integer", "generator": {"kind": "integer_range", "min": 1, "max": 10}},), "primary_key": ("id",)},
            {"name": "raw_b", "rows": {"min": 1, "max": 10}, "columns": ({"name": "id", "type": "integer", "generator": {"kind": "integer_range", "min": 1, "max": 10}}, {"name": "a_id", "type": "integer", "generator": {"kind": "foreign_key", "relationship": "rel_a", "target_side": "left"}}), "primary_key": ()},
            {"name": "raw_c", "rows": {"min": 1, "max": 10}, "columns": ({"name": "id", "type": "integer", "generator": {"kind": "integer_range", "min": 1, "max": 10}},), "primary_key": ("id",)},
        ),
        "relationships": ({"name": "rel_a", "cardinality": "one_to_many", "left": {"table": "raw_a", "columns": ("id",)}, "right": {"table": "raw_b", "columns": ("a_id",)}},),
        "staging_models": (
            {"name": "stg_a", "source": "raw_a", "columns": ({"source": "id", "target": "id"},), "grain": ("id",)},
            {"name": "stg_b", "source": "raw_b", "columns": ({"source": "id", "target": "id"}, {"source": "a_id", "target": "a_id"}), "grain": ("id",)},
            {"name": "stg_c", "source": "raw_c", "columns": ({"source": "id", "target": "id"},), "grain": ("id",)},
        ),
        "intermediate_models": (
            {"operation": "transform", "name": "trans_a", "source": "stg_c", "columns": ({"source": "id", "target": "id"},), "grain": ("id",)},
            {"operation": "join", "name": "join_a", "left": "stg_a", "right": "stg_b", "join": {"type": "inner", "on": ({"left": "id", "right": "a_id"},)}, "columns": ({"side": "left", "source": "id", "target": "lid"}, {"side": "right", "source": "id", "target": "rid"}), "grain": ("lid", "rid")},
        ),
        "output_models": (
            {"name": "out_a", "source": "trans_a", "group_by": ({"source": "id", "target": "id"},), "grain": ("id",), "metrics": ({"name": "cnt", "function": "count_rows"},)},
            {"name": "out_b", "source": "join_a", "group_by": ({"source": "lid", "target": "lid"}, {"source": "rid", "target": "rid"}), "grain": ("lid", "rid"), "metrics": ({"name": "cnt2", "function": "count_rows"},)},
        ),
    }


def test_formatted_id_cannot_support_ten():
    # digits=1, start=1 => capacity 9 < 10, should fail
    data = _base()
    data["raw_tables"] = (
        {
            "name": "raw_a",
            "rows": {"min": 10, "max": 10},
            "columns": (
                {
                    "name": "id",
                    "type": "string",
                    "generator": {"kind": "formatted_id", "digits": 1, "start": 1},
                },
            ),
            "primary_key": ("id",),
        },
        data["raw_tables"][1],
        data["raw_tables"][2],
    )
    s = Scenario.model_validate(data)
    with pytest.raises(SemanticValidationError) as exc:
        validate_semantics(s)
    assert any("capacity" in i.message.lower() for i in exc.value.issues)


def test_formatted_id_boundary():
    # digits=1, start=1, max_rows 9 should pass (capacity 9), 10 should fail
    data = _base()
    # Make raw_b's FK also string to match raw_a string PK
    data["raw_tables"] = (
        {
            "name": "raw_a",
            "rows": {"min": 9, "max": 9},
            "columns": (
                {
                    "name": "id",
                    "type": "string",
                    "generator": {"kind": "formatted_id", "digits": 1, "start": 1},
                },
            ),
            "primary_key": ("id",),
        },
        {"name": "raw_b", "rows": {"min": 1, "max": 10}, "columns": ({"name": "id", "type": "integer", "generator": {"kind": "integer_range", "min": 1, "max": 10}}, {"name": "a_id", "type": "string", "generator": {"kind": "foreign_key", "relationship": "rel_a", "target_side": "left"}}), "primary_key": ()},
        data["raw_tables"][2],
    )
    data["relationships"] = ({"name": "rel_a", "cardinality": "one_to_many", "left": {"table": "raw_a", "columns": ("id",)}, "right": {"table": "raw_b", "columns": ("a_id",)}},)
    s = Scenario.model_validate(data)
    # Should pass
    validate_semantics(s)
    # Now with 10 should fail
    data["raw_tables"] = (
        {
            "name": "raw_a",
            "rows": {"min": 10, "max": 10},
            "columns": (
                {
                    "name": "id",
                    "type": "string",
                    "generator": {"kind": "formatted_id", "digits": 1, "start": 1},
                },
            ),
            "primary_key": ("id",),
        },
        {"name": "raw_b", "rows": {"min": 1, "max": 10}, "columns": ({"name": "id", "type": "integer", "generator": {"kind": "integer_range", "min": 1, "max": 10}}, {"name": "a_id", "type": "string", "generator": {"kind": "foreign_key", "relationship": "rel_a", "target_side": "left"}}), "primary_key": ()},
        data["raw_tables"][2],
    )
    s2 = Scenario.model_validate(data)
    with pytest.raises(SemanticValidationError):
        validate_semantics(s2)
    # digits=1, start=0 => capacity 10, should pass for 10
    data["raw_tables"] = (
        {
            "name": "raw_a",
            "rows": {"min": 10, "max": 10},
            "columns": (
                {
                    "name": "id",
                    "type": "string",
                    "generator": {"kind": "formatted_id", "digits": 1, "start": 0},
                },
            ),
            "primary_key": ("id",),
        },
        {"name": "raw_b", "rows": {"min": 1, "max": 10}, "columns": ({"name": "id", "type": "integer", "generator": {"kind": "integer_range", "min": 1, "max": 10}}, {"name": "a_id", "type": "string", "generator": {"kind": "foreign_key", "relationship": "rel_a", "target_side": "left"}}), "primary_key": ()},
        data["raw_tables"][2],
    )
    s3 = Scenario.model_validate(data)
    validate_semantics(s3)


def test_random_string_capacity_one():
    data = _base()
    data["raw_tables"] = (
        {
            "name": "raw_a",
            "rows": {"min": 1, "max": 1},
            "columns": (
                {
                    "name": "id",
                    "type": "string",
                    "generator": {
                        "kind": "random_string",
                        "min_length": 1,
                        "max_length": 1,
                        "alphabet": "a",
                    },
                },
            ),
            "primary_key": ("id",),
        },
        {"name": "raw_b", "rows": {"min": 1, "max": 10}, "columns": ({"name": "id", "type": "integer", "generator": {"kind": "integer_range", "min": 1, "max": 10}}, {"name": "a_id", "type": "string", "generator": {"kind": "foreign_key", "relationship": "rel_a", "target_side": "left"}}), "primary_key": ()},
        data["raw_tables"][2],
    )
    data["relationships"] = ({"name": "rel_a", "cardinality": "one_to_many", "left": {"table": "raw_a", "columns": ("id",)}, "right": {"table": "raw_b", "columns": ("a_id",)}},)
    s = Scenario.model_validate(data)
    # capacity 1, max_rows 1 => pass
    validate_semantics(s)
    # Now with max_rows 2, capacity 1 <2 => fail
    data["raw_tables"] = (
        {
            "name": "raw_a",
            "rows": {"min": 2, "max": 2},
            "columns": (
                {
                    "name": "id",
                    "type": "string",
                    "generator": {
                        "kind": "random_string",
                        "min_length": 1,
                        "max_length": 1,
                        "alphabet": "a",
                    },
                },
            ),
            "primary_key": ("id",),
        },
        {"name": "raw_b", "rows": {"min": 1, "max": 10}, "columns": ({"name": "id", "type": "integer", "generator": {"kind": "integer_range", "min": 1, "max": 10}}, {"name": "a_id", "type": "string", "generator": {"kind": "foreign_key", "relationship": "rel_a", "target_side": "left"}}), "primary_key": ()},
        data["raw_tables"][2],
    )
    data["relationships"] = ({"name": "rel_a", "cardinality": "one_to_many", "left": {"table": "raw_a", "columns": ("id",)}, "right": {"table": "raw_b", "columns": ("a_id",)}},)
    s2 = Scenario.model_validate(data)
    with pytest.raises(SemanticValidationError) as exc:
        validate_semantics(s2)
    assert any("capacity" in i.message.lower() for i in exc.value.issues)


def test_random_string_multiple_lengths():
    data = _base()
    # alphabet "ab" size 2, min 1 max 2 => capacity 2+4=6
    data["raw_tables"] = (
        {
            "name": "raw_a",
            "rows": {"min": 6, "max": 6},
            "columns": (
                {
                    "name": "id",
                    "type": "string",
                    "generator": {
                        "kind": "random_string",
                        "min_length": 1,
                        "max_length": 2,
                        "alphabet": "ab",
                    },
                },
            ),
            "primary_key": ("id",),
        },
        {"name": "raw_b", "rows": {"min": 1, "max": 10}, "columns": ({"name": "id", "type": "integer", "generator": {"kind": "integer_range", "min": 1, "max": 10}}, {"name": "a_id", "type": "string", "generator": {"kind": "foreign_key", "relationship": "rel_a", "target_side": "left"}}), "primary_key": ()},
        data["raw_tables"][2],
    )
    data["relationships"] = ({"name": "rel_a", "cardinality": "one_to_many", "left": {"table": "raw_a", "columns": ("id",)}, "right": {"table": "raw_b", "columns": ("a_id",)}},)
    s = Scenario.model_validate(data)
    validate_semantics(s)  # 6 should pass
    data["raw_tables"] = (
        {
            "name": "raw_a",
            "rows": {"min": 7, "max": 7},
            "columns": (
                {
                    "name": "id",
                    "type": "string",
                    "generator": {
                        "kind": "random_string",
                        "min_length": 1,
                        "max_length": 2,
                        "alphabet": "ab",
                    },
                },
            ),
            "primary_key": ("id",),
        },
        {"name": "raw_b", "rows": {"min": 1, "max": 10}, "columns": ({"name": "id", "type": "integer", "generator": {"kind": "integer_range", "min": 1, "max": 10}}, {"name": "a_id", "type": "string", "generator": {"kind": "foreign_key", "relationship": "rel_a", "target_side": "left"}}), "primary_key": ()},
        data["raw_tables"][2],
    )
    s2 = Scenario.model_validate(data)
    with pytest.raises(SemanticValidationError):
        validate_semantics(s2)


def test_no_arbitrary_faker_rejection():
    data = _base()
    data["raw_tables"] = (
        {
            "name": "raw_a",
            "rows": {"min": 100, "max": 100},
            "columns": (
                {
                    "name": "id",
                    "type": "string",
                    "generator": {"kind": "person_name", "locale": "en_US"},
                },
            ),
            "primary_key": ("id",),
        },
        {"name": "raw_b", "rows": {"min": 1, "max": 10}, "columns": ({"name": "id", "type": "integer", "generator": {"kind": "integer_range", "min": 1, "max": 10}}, {"name": "a_id", "type": "string", "generator": {"kind": "foreign_key", "relationship": "rel_a", "target_side": "left"}}), "primary_key": ()},
        data["raw_tables"][2],
    )
    data["relationships"] = ({"name": "rel_a", "cardinality": "one_to_many", "left": {"table": "raw_a", "columns": ("id",)}, "right": {"table": "raw_b", "columns": ("a_id",)}},)
    s = Scenario.model_validate(data)
    # Faker has no finite capacity proven, should not be rejected based on capacity
    validate_semantics(s)


def test_composite_insufficient():
    data = _base()
    data["raw_tables"] = (
        {
            "name": "raw_a",
            "rows": {"min": 10, "max": 10},
            "columns": (
                {
                    "name": "id",
                    "type": "integer",
                    "generator": {"kind": "integer_range", "min": 1, "max": 2},
                },
                {
                    "name": "seq",
                    "type": "integer",
                    "generator": {"kind": "integer_range", "min": 1, "max": 2},
                },
            ),
            "primary_key": ("id", "seq"),
        },
        data["raw_tables"][1],
        data["raw_tables"][2],
    )
    data["staging_models"] = (
        {
            "name": "stg_a",
            "source": "raw_a",
            "columns": ({"source": "id", "target": "id"}, {"source": "seq", "target": "seq"}),
            "grain": ("id", "seq"),
        },
        data["staging_models"][1],
        data["staging_models"][2],
    )
    s = Scenario.model_validate(data)
    with pytest.raises(SemanticValidationError) as exc:
        validate_semantics(s)
    assert any("composite" in i.message.lower() for i in exc.value.issues)


def test_composite_with_unknown_does_not_fail():
    data = _base()
    s = Scenario.model_validate(data)
    validate_semantics(s)


def test_mutation_top_level_mappings():
    data = _base()
    s = Scenario.model_validate(data)
    v = validate_semantics(s)
    # Top-level mappings should be immutable
    with pytest.raises((TypeError, AttributeError)):
        v.raw_by_name["new"] = None  # type: ignore[index]
    with pytest.raises((TypeError, AttributeError)):
        v.staging_by_name["new"] = None  # type: ignore[index]
    with pytest.raises((TypeError, AttributeError)):
        v.intermediate_by_name["new"] = None  # type: ignore[index]
    with pytest.raises((TypeError, AttributeError)):
        v.output_by_name["new"] = None  # type: ignore[index]


def test_mutation_lineage_sequences():
    data = _base()
    s = Scenario.model_validate(data)
    v = validate_semantics(s)
    # Lineage inner lists should be tuples, not mutable lists
    for model_lineage in v.lineage.values():
        for col, lineage in model_lineage.items():
            assert isinstance(lineage, tuple), (
                f"lineage for {col} should be tuple, got {type(lineage)}"
            )
            with pytest.raises((TypeError, AttributeError)):
                lineage.append("new")  # type: ignore[attr-defined]
    # Lineage outer dict should be immutable
    with pytest.raises((TypeError, AttributeError)):
        v.lineage["new"] = {}  # type: ignore[index]


def test_mutation_derived_assertions():
    data = _base()
    s = Scenario.model_validate(data)
    v = validate_semantics(s)
    # Derived assertions tuple should be immutable, and inner dicts should be MappingProxyType
    with pytest.raises((TypeError, AttributeError)):
        v.derived_assertions += ({"name": "new"},)  # type: ignore[arg-type]
    # Inner dict should be immutable
    first = v.derived_assertions[0]
    assert isinstance(first, MappingProxyType)
    with pytest.raises((TypeError, AttributeError)):
        first["new_key"] = "value"  # type: ignore[index]
    with pytest.raises((TypeError, AttributeError)):
        first["columns"] = ("new",)  # type: ignore[index]


def test_mutation_resolved_keys_grains():
    data = _base()
    s = Scenario.model_validate(data)
    v = validate_semantics(s)
    with pytest.raises((TypeError, AttributeError)):
        v.resolved_keys["new"] = ("id",)  # type: ignore[index]
    with pytest.raises((TypeError, AttributeError)):
        v.resolved_grains["new"] = ("id",)  # type: ignore[index]
    # Inner tuple should be immutable (already)
    with pytest.raises((AttributeError, TypeError)):
        v.resolved_keys["raw_a"] += ("new",)  # type: ignore[operator]


def test_repeated_validation_equal_deterministic():
    data = _base()
    s = Scenario.model_validate(data)
    v1 = validate_semantics(s)
    v2 = validate_semantics(s)
    assert v1.topological_order == v2.topological_order
    assert list(v1.staging_schemas.keys()) == list(v2.staging_schemas.keys())
    assert v1.derived_assertions == v2.derived_assertions
    assert v1.lineage == v2.lineage
    # Check that derived assertions are deterministically ordered
    assert [d["name"] for d in v1.derived_assertions] == [d["name"] for d in v2.derived_assertions]
