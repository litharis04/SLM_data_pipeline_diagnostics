"""Focused tests for T12 – expression, condition, metric typing."""

import pytest

from data_pipeline_diagnostics.scenario.errors import SemanticValidationError
from data_pipeline_diagnostics.scenario.models import Scenario
from data_pipeline_diagnostics.scenario.semantic import validate_semantics


def _base():
    from datetime import date

    return {
        "schema_version": "1.0",
        "scenario_id": "test_t12",
        "domain": "testdomain",
        "raw_tables": (
            {
                "name": "raw_a",
                "rows": {"min": 1, "max": 10},
                "columns": (
                    {
                        "name": "id",
                        "type": "integer",
                        "generator": {"kind": "integer_range", "min": 1, "max": 10},
                    },
                    {
                        "name": "val",
                        "type": "integer",
                        "generator": {"kind": "integer_range", "min": 1, "max": 10},
                    },
                    {
                        "name": "str_col",
                        "type": "string",
                        "generator": {"kind": "formatted_id", "digits": 5},
                    },
                    {
                        "name": "d",
                        "type": "date",
                        "generator": {
                            "kind": "date_range",
                            "min": date(2020, 1, 1),
                            "max": date(2020, 1, 2),
                        },
                    },
                ),
                "primary_key": ("id",),
            },
            {
                "name": "raw_b",
                "rows": {"min": 1, "max": 10},
                "columns": (
                    {
                        "name": "id",
                        "type": "integer",
                        "generator": {"kind": "integer_range", "min": 1, "max": 10},
                    },
                    {
                        "name": "a_id",
                        "type": "integer",
                        "generator": {
                            "kind": "foreign_key",
                            "relationship": "rel_a",
                            "target_side": "left",
                        },
                    },
                ),
                "primary_key": (),
            },
            {
                "name": "raw_c",
                "rows": {"min": 1, "max": 10},
                "columns": (
                    {
                        "name": "id",
                        "type": "integer",
                        "generator": {"kind": "integer_range", "min": 1, "max": 10},
                    },
                ),
                "primary_key": ("id",),
            },
        ),
        "relationships": (
            {
                "name": "rel_a",
                "cardinality": "one_to_many",
                "left": {"table": "raw_a", "columns": ("id",)},
                "right": {"table": "raw_b", "columns": ("a_id",)},
            },
        ),
        "staging_models": (
            {
                "name": "stg_a",
                "source": "raw_a",
                "columns": (
                    {"source": "id", "target": "id"},
                    {"source": "val", "target": "val"},
                    {"source": "str_col", "target": "str_col"},
                    {"source": "d", "target": "d"},
                ),
                "grain": ("id",),
            },
            {
                "name": "stg_b",
                "source": "raw_b",
                "columns": ({"source": "id", "target": "id"}, {"source": "a_id", "target": "a_id"}),
                "grain": ("id",),
            },
            {
                "name": "stg_c",
                "source": "raw_c",
                "columns": ({"source": "id", "target": "id"},),
                "grain": ("id",),
            },
        ),
        "intermediate_models": (
            {
                "operation": "transform",
                "name": "trans_a",
                "source": "stg_a",
                "columns": ({"source": "id", "target": "id"},),
                "grain": ("id",),
            },
            {
                "operation": "join",
                "name": "join_a",
                "left": "stg_a",
                "right": "stg_b",
                "join": {"type": "inner", "on": ({"left": "id", "right": "a_id"},)},
                "columns": ({"side": "left", "source": "id", "target": "id"},),
                "grain": ("id",),
            },
        ),
        "output_models": (
            {
                "name": "out_a",
                "source": "trans_a",
                "group_by": ({"source": "id", "target": "id"},),
                "grain": ("id",),
                "metrics": ({"name": "cnt", "function": "count_rows"},),
            },
        ),
    }


def test_string_plus_integer():
    data = _base()
    data["intermediate_models"] = (
        {
            "operation": "transform",
            "name": "trans_a",
            "source": "stg_a",
            "columns": ({"source": "id", "target": "id"},),
            "derived_columns": (
                {
                    "name": "bad",
                    "type": "string",
                    "expression": {
                        "kind": "binary",
                        "operator": "add",
                        "left": {"kind": "column", "column": "str_col"},
                        "right": {"kind": "column", "column": "val"},
                    },
                }
            ),
            "grain": ("id",),
        },
        data["intermediate_models"][1],
    )
    s = Scenario.model_validate(data)
    with pytest.raises(SemanticValidationError) as exc:
        validate_semantics(s)
    assert any(
        "E129" in i.code or "invalid expression" in i.message.lower() for i in exc.value.issues
    )


def test_divide_non_numeric():
    data = _base()
    data["intermediate_models"] = (
        {
            "operation": "transform",
            "name": "trans_a",
            "source": "stg_a",
            "columns": ({"source": "id", "target": "id"},),
            "derived_columns": (
                {
                    "name": "bad",
                    "type": "float",
                    "expression": {
                        "kind": "binary",
                        "operator": "divide",
                        "left": {"kind": "column", "column": "str_col"},
                        "right": {"kind": "literal", "value": 1},
                    },
                },
            ),
            "grain": ("id",),
        },
        data["intermediate_models"][1],
    )
    s = Scenario.model_validate(data)
    with pytest.raises(SemanticValidationError) as exc:
        validate_semantics(s)
    assert any("E129" in i.code for i in exc.value.issues)


def test_date_part_over_string():
    data = _base()
    data["intermediate_models"] = (
        {
            "operation": "transform",
            "name": "trans_a",
            "source": "stg_a",
            "columns": ({"source": "id", "target": "id"},),
            "derived_columns": (
                {
                    "name": "bad",
                    "type": "integer",
                    "expression": {
                        "kind": "date_part",
                        "part": "year",
                        "value": {"kind": "column", "column": "str_col"},
                    },
                }
            ),
            "grain": ("id",),
        },
        data["intermediate_models"][1],
    )
    s = Scenario.model_validate(data)
    with pytest.raises(SemanticValidationError) as exc:
        validate_semantics(s)
    assert any("E129" in i.code or "date_part" in i.message.lower() for i in exc.value.issues)


def test_coalesce_incompatible():
    data = _base()
    data["intermediate_models"] = (
        {
            "operation": "transform",
            "name": "trans_a",
            "source": "stg_a",
            "columns": ({"source": "id", "target": "id"},),
            "derived_columns": (
                {
                    "name": "bad",
                    "type": "integer",
                    "expression": {
                        "kind": "coalesce",
                        "values": (
                            {"kind": "column", "column": "val"},
                            {"kind": "literal", "value": "fallback"},
                        ),
                    },
                }
            ),
            "grain": ("id",),
        },
        data["intermediate_models"][1],
    )
    s = Scenario.model_validate(data)
    with pytest.raises(SemanticValidationError) as exc:
        validate_semantics(s)
    assert any("E129" in i.code or "coalesce" in i.message.lower() for i in exc.value.issues)


def test_in_options_string_vs_int():
    data = _base()
    data["staging_models"] = (
        {
            "name": "stg_a",
            "source": "raw_a",
            "columns": ({"source": "id", "target": "id"},),
            "grain": ("id",),
            "row_operations": (
                {
                    "op": "filter",
                    "condition": {
                        "kind": "in",
                        "value": {"kind": "column", "column": "id"},
                        "options": ("a", "b"),
                    },
                },
            ),
        },
        data["staging_models"][1],
        data["staging_models"][2],
    )
    s = Scenario.model_validate(data)
    with pytest.raises(SemanticValidationError) as exc:
        validate_semantics(s)
    assert any(
        "E130" in i.code or "incondition" in i.message.lower() or "in" in i.message.lower()
        for i in exc.value.issues
    )


def test_nested_invalid_all_not():
    data = _base()
    data["intermediate_models"] = (
        {
            "operation": "transform",
            "name": "trans_a",
            "source": "stg_a",
            "columns": ({"source": "id", "target": "id"},),
            "grain": ("id",),
            "filters": (
                {
                    "kind": "all",
                    "conditions": (
                        {
                            "kind": "comparison",
                            "operator": "eq",
                            "left": {"kind": "column", "column": "str_col"},
                            "right": {"kind": "literal", "value": 1},
                        },
                        {
                            "kind": "comparison",
                            "operator": "eq",
                            "left": {"kind": "column", "column": "id"},
                            "right": {"kind": "literal", "value": 1},
                        },
                    ),
                },
            ),
        },
        data["intermediate_models"][1],
    )
    s = Scenario.model_validate(data)
    with pytest.raises(SemanticValidationError) as exc:
        validate_semantics(s)
    assert any("E130" in i.code or "type mismatch" in i.message.lower() for i in exc.value.issues)
    data2 = _base()
    data2["intermediate_models"] = (
        {
            "operation": "transform",
            "name": "trans_a",
            "source": "stg_a",
            "columns": ({"source": "id", "target": "id"},),
            "grain": ("id",),
            "filters": (
                {
                    "kind": "not",
                    "condition": {
                        "kind": "comparison",
                        "operator": "eq",
                        "left": {"kind": "column", "column": "str_col"},
                        "right": {"kind": "literal", "value": 1},
                    },
                },
            ),
        },
        data2["intermediate_models"][1],
    )
    s2 = Scenario.model_validate(data2)
    with pytest.raises(SemanticValidationError) as exc2:
        validate_semantics(s2)
    assert any("E130" in i.code or "type mismatch" in i.message.lower() for i in exc2.value.issues)


def test_transform_dropped_pre_projection():
    data = _base()
    data["intermediate_models"] = (
        {
            "operation": "transform",
            "name": "trans_a",
            "source": "stg_a",
            "columns": ({"source": "id", "target": "id"},),
            "derived_columns": (
                {
                    "name": "bad",
                    "type": "integer",
                    "expression": {"kind": "column", "column": "val"},
                }
            ),
            "grain": ("id",),
        },
        data["intermediate_models"][1],
    )
    s = Scenario.model_validate(data)
    with pytest.raises(SemanticValidationError) as exc:
        validate_semantics(s)
    assert any("not in schema" in i.message.lower() or "E105" in i.code for i in exc.value.issues)


def test_join_unprojected():
    data = _base()
    data["intermediate_models"] = (
        data["intermediate_models"][0],
        {
            "operation": "join",
            "name": "join_a",
            "left": "stg_a",
            "right": "stg_b",
            "join": {"type": "inner", "on": ({"left": "id", "right": "a_id"},)},
            "columns": ({"side": "left", "source": "id", "target": "id"},),
            "derived_columns": (
                {
                    "name": "bad",
                    "type": "integer",
                    "expression": {"kind": "column", "column": "val"},
                }
            ),
            "grain": ("id",),
        },
    )
    s = Scenario.model_validate(data)
    with pytest.raises(SemanticValidationError) as exc:
        validate_semantics(s)
    assert any("not in schema" in i.message.lower() for i in exc.value.issues)


def test_output_min_max_over_string():
    data = _base()
    data["intermediate_models"] = (
        {
            "operation": "transform",
            "name": "trans_a",
            "source": "stg_a",
            "columns": ({"source": "str_col", "target": "str_col"},),
            "grain": ("str_col",),
        },
        data["intermediate_models"][1],
    )
    data["output_models"] = (
        {
            "name": "out_a",
            "source": "trans_a",
            "group_by": ({"source": "str_col", "target": "str_col"},),
            "grain": ("str_col",),
            "metrics": ({"name": "mn", "function": "min", "column": "str_col"},),
        },
    )
    s = Scenario.model_validate(data)
    with pytest.raises(SemanticValidationError) as exc:
        validate_semantics(s)
    assert any("E126" in i.code or "numeric" in i.message.lower() for i in exc.value.issues)
    data["output_models"] = (
        {
            "name": "out_a",
            "source": "trans_a",
            "group_by": ({"source": "str_col", "target": "str_col"},),
            "grain": ("str_col",),
            "metrics": ({"name": "mx", "function": "max", "column": "str_col"},),
        },
    )
    s2 = Scenario.model_validate(data)
    with pytest.raises(SemanticValidationError) as exc2:
        validate_semantics(s2)
    assert any("E126" in i.code for i in exc2.value.issues)


def test_valid_promotion_and_date_part():
    from datetime import date

    data = _base()
    # Valid int+float promotion to float and valid date_part on date - fully connected
    data["raw_tables"] = (
        {
            "name": "raw_a",
            "rows": {"min": 1, "max": 10},
            "columns": (
                {
                    "name": "id",
                    "type": "integer",
                    "generator": {"kind": "integer_range", "min": 1, "max": 10},
                },
                {
                    "name": "val",
                    "type": "float",
                    "generator": {"kind": "float_range", "min": 1.0, "max": 2.0},
                },
                {
                    "name": "d",
                    "type": "date",
                    "generator": {
                        "kind": "date_range",
                        "min": date(2020, 1, 1),
                        "max": date(2020, 1, 2),
                    },
                },
            ),
            "primary_key": ("id",),
        },
        data["raw_tables"][1],
        data["raw_tables"][2],
    )
    data["staging_models"] = (
        {
            "name": "stg_a",
            "source": "raw_a",
            "columns": (
                {"source": "id", "target": "id"},
                {"source": "val", "target": "val"},
                {"source": "d", "target": "d"},
            ),
            "grain": ("id",),
        },
        data["staging_models"][1],
        data["staging_models"][2],
    )
    data["intermediate_models"] = (
        {
            "operation": "transform",
            "name": "trans_a",
            "source": "stg_c",
            "columns": ({"source": "id", "target": "id"},),
            "grain": ("id",),
        },
        {
            "operation": "transform",
            "name": "trans_b",
            "source": "stg_a",
            "columns": (
                {"source": "id", "target": "id"},
                {"source": "val", "target": "val"},
                {"source": "d", "target": "d"},
            ),
            "derived_columns": (
                {
                    "name": "calc",
                    "type": "float",
                    "expression": {
                        "kind": "binary",
                        "operator": "add",
                        "left": {"kind": "column", "column": "id"},
                        "right": {"kind": "column", "column": "val"},
                    },
                },
                {
                    "name": "year",
                    "type": "integer",
                    "expression": {
                        "kind": "date_part",
                        "part": "year",
                        "value": {"kind": "column", "column": "d"},
                    },
                },
            ),
            "grain": ("id",),
        },
        {
            "operation": "join",
            "name": "join_a",
            "left": "trans_b",
            "right": "stg_b",
            "join": {"type": "inner", "on": ({"left": "id", "right": "a_id"},)},
            "columns": ({"side": "left", "source": "id", "target": "id"},),
            "grain": ("id",),
        },
    )
    data["output_models"] = (
        {
            "name": "out_a",
            "source": "trans_a",
            "group_by": ({"source": "id", "target": "id"},),
            "grain": ("id",),
            "metrics": ({"name": "cnt", "function": "count_rows"},),
        },
        {
            "name": "out_b",
            "source": "join_a",
            "group_by": ({"source": "id", "target": "id"},),
            "grain": ("id",),
            "metrics": ({"name": "cnt2", "function": "count_rows"},),
        },
    )
    s = Scenario.model_validate(data)
    # Should pass
    validated = validate_semantics(s)
    assert validated.intermediate_schemas["trans_b"]["calc"] is not None


def test_error_code_and_path():
    data = _base()
    data["intermediate_models"] = (
        {
            "operation": "transform",
            "name": "trans_a",
            "source": "stg_a",
            "columns": ({"source": "id", "target": "id"},),
            "derived_columns": (
                {
                    "name": "bad",
                    "type": "integer",
                    "expression": {
                        "kind": "binary",
                        "operator": "add",
                        "left": {"kind": "column", "column": "str_col"},
                        "right": {"kind": "column", "column": "val"},
                    },
                }
            ),
            "grain": ("id",),
        },
        data["intermediate_models"][1],
    )
    s = Scenario.model_validate(data)
    with pytest.raises(SemanticValidationError) as exc:
        validate_semantics(s)
    # Check that issue has specific code and path
    issues = exc.value.issues
    assert any(i.code == "E129" for i in issues)
    assert any("trans_a" in i.path and "derived_columns" in i.path for i in issues)
