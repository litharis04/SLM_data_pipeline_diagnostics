"""Semantic validation tests — must parse but fail semantic (§19.3)."""

from __future__ import annotations

import pytest

from data_pipeline_diagnostics.scenario.errors import SemanticValidationError
from data_pipeline_diagnostics.scenario.models import Scenario
from data_pipeline_diagnostics.scenario.semantic import ValidatedScenario, validate_semantics

# ---------------------------------------------------------------------------
# Helpers – same as test_scenario
# ---------------------------------------------------------------------------

_INT_GEN = {"kind": "integer_range", "min": 1, "max": 10}
_STR_GEN = {"kind": "formatted_id", "digits": 5}
_FLOAT_GEN = {"kind": "float_range", "min": 1.0, "max": 2.0}


def _raw(
    name: str,
    col_type: str = "integer",
    gen: dict | None = None,
    pk: tuple[str, ...] = ("id",),
    nullable: bool = False,
) -> dict:
    gen = gen or _INT_GEN
    col = {"name": "id", "type": col_type, "generator": gen}
    if nullable:
        col["nullable"] = True
        col["null_probability"] = 0.5
    cols = (col,)
    # add second column for some tests
    return {"name": name, "rows": {"min": 1, "max": 10}, "columns": cols, "primary_key": pk}


def _raw_with_cols(
    name: str, cols: list[dict] | tuple[dict, ...], pk: tuple[str, ...] = ()
) -> dict:
    # ensure columns is tuple for strict validation
    cols_t = tuple(cols) if isinstance(cols, list) else cols
    return {"name": name, "rows": {"min": 1, "max": 10}, "columns": cols_t, "primary_key": pk}


def _rel(
    name: str,
    left_table: str,
    left_cols: tuple[str, ...],
    right_table: str,
    right_cols: tuple[str, ...],
    cardinality: str = "one_to_many",
) -> dict:
    return {
        "name": name,
        "cardinality": cardinality,
        "left": {"table": left_table, "columns": left_cols},
        "right": {"table": right_table, "columns": right_cols},
    }


def _stg(name: str, source: str, grain: tuple[str, ...] = ("id",)) -> dict:
    return {
        "name": name,
        "source": source,
        "columns": ({"source": "id", "target": "id"},),
        "grain": grain,
    }


def _trans(name: str, source: str) -> dict:
    return {
        "operation": "transform",
        "name": name,
        "source": source,
        "columns": ({"source": "id", "target": "id"},),
        "grain": ("id",),
    }


def _join(name: str, left: str, right: str, left_key: str = "id", right_key: str = "id") -> dict:
    return {
        "operation": "join",
        "name": name,
        "left": left,
        "right": right,
        "join": {"type": "inner", "on": ({"left": left_key, "right": right_key},)},
        "columns": ({"side": "left", "source": "id", "target": "id"},),
        "grain": ("id",),
    }


def _agg(name: str, source: str) -> dict:
    return {
        "operation": "aggregate",
        "name": name,
        "source": source,
        "group_by": ({"source": "id", "target": "id"},),
        "metrics": ({"name": "cnt", "function": "count_rows"},),
        "grain": ("id",),
    }


def _dedup(name: str, source: str) -> dict:
    return {
        "operation": "deduplicate",
        "name": name,
        "source": source,
        "keys": ("id",),
        "order_by": ({"column": "id"},),
        "grain": ("id",),
    }


def _out(name: str, source: str) -> dict:
    return {
        "name": name,
        "source": source,
        "group_by": ({"source": "id", "target": "id"},),
        "grain": ("id",),
        "metrics": ({"name": "cnt", "function": "count_rows"},),
    }


def _base_scenario() -> dict:
    # 3 raw, 3 staging, 2 intermediate (transform + join), 1 output – valid
    return {
        "schema_version": "1.0",
        "scenario_id": "test_scenario",
        "domain": "testdomain",
        "raw_tables": (
            _raw("raw_a"),
            _raw_with_cols(
                "raw_b",
                [
                    {"name": "id", "type": "integer", "generator": _INT_GEN},
                    {
                        "name": "a_id",
                        "type": "integer",
                        "generator": {
                            "kind": "foreign_key",
                            "relationship": "rel_a",
                            "target_side": "left",
                        },
                    },
                ],
                pk=(),
            ),
            _raw("raw_c"),
        ),
        "relationships": (
            _rel("rel_a", "raw_a", ("id",), "raw_b", ("a_id",), cardinality="one_to_many"),
        ),
        "staging_models": (
            _stg("stg_a", "raw_a"),
            {
                "name": "stg_b",
                "source": "raw_b",
                "columns": ({"source": "id", "target": "id"}, {"source": "a_id", "target": "a_id"}),
                "grain": ("id",),
            },
            _stg("stg_c", "raw_c"),
        ),
        "intermediate_models": (
            _trans("trans_a", "stg_c"),
            _join("join_a", "stg_a", "stg_b", left_key="id", right_key="a_id"),
        ),
        "output_models": (_out("out_a", "trans_a"), _out("out_b", "join_a")),
    }


# ---------------------------------------------------------------------------
# Positive
# ---------------------------------------------------------------------------


def test_positive_full_scenario_passes():
    data = _base_scenario()
    s = Scenario.model_validate(data)
    validated = validate_semantics(s)
    assert isinstance(validated, ValidatedScenario)
    assert validated.scenario == s
    assert "trans_a" in validated.topological_order
    assert "join_a" in validated.topological_order
    # §17.1: resolved schemas and grains must be present
    assert "stg_a" in validated.staging_schemas
    assert "trans_a" in validated.intermediate_schemas
    assert "out_a" in validated.output_schemas
    assert "stg_a" in validated.resolved_grains
    assert "raw_a" in validated.resolved_keys
    assert validated.resolved_keys["raw_a"] == ("id",)
    # lineage must be raw lineage, not just current_model.column (now tuples for deep immutability)
    assert "stg_a" in validated.lineage
    assert validated.lineage["stg_a"]["id"] == ("raw_a.id",)
    assert validated.lineage["trans_a"]["id"] == ("raw_c.id",)
    # derived assertions must be comprehensive
    derived_types = {d["type"] for d in validated.derived_assertions}
    assert "not_null" in derived_types
    assert "unique" in derived_types
    assert "relationships" in derived_types
    assert "row_count" in derived_types
    # At least 5 derived assertions for this scenario
    assert len(validated.derived_assertions) >= 5
    # deterministic ordering – re-run gives same
    validated2 = validate_semantics(s)
    assert [d["name"] for d in validated.derived_assertions] == [
        d["name"] for d in validated2.derived_assertions
    ]
    assert list(validated.staging_schemas.keys()) == list(validated2.staging_schemas.keys())

    # compiler boundary – bare Scenario should not be accepted where ValidatedScenario required
    def _compiler_accepts(vs: ValidatedScenario) -> None:
        assert isinstance(vs, ValidatedScenario)

    _compiler_accepts(validated)
    with pytest.raises(Exception):
        _compiler_accepts(s)  # type: ignore[arg-type]


def test_deterministic_issue_ordering():
    # Create scenario with two independent missing refs – order should be deterministic
    base = _base_scenario()
    base["relationships"] = (
        {
            "name": "rel_a",
            "cardinality": "one_to_many",
            "left": {"table": "ghost1", "columns": ("id",)},
            "right": {"table": "ghost2", "columns": ("a_id",)},
        },
    )
    base["staging_models"] = (
        {
            "name": "stg_a",
            "source": "ghost_raw",
            "columns": ({"source": "id", "target": "id"},),
            "grain": ("id",),
        },
        {
            "name": "stg_b",
            "source": "raw_b",
            "columns": ({"source": "id", "target": "id"},),
            "grain": ("id",),
        },
        {
            "name": "stg_c",
            "source": "raw_c",
            "columns": ({"source": "id", "target": "id"},),
            "grain": ("id",),
        },
    )
    s = Scenario.model_validate(base)
    try:
        validate_semantics(s)
        assert False, "should have raised"
    except SemanticValidationError as e:
        codes1 = [(i.path, i.code) for i in e.issues]
        # re-run
        try:
            validate_semantics(s)
            assert False
        except SemanticValidationError as e2:
            codes2 = [(i.path, i.code) for i in e2.issues]
            assert codes1 == codes2


# ---------------------------------------------------------------------------
# Negative – each defect must parse but fail semantic
# ---------------------------------------------------------------------------


def test_missing_reference():
    base = _base_scenario()
    base["relationships"] = (
        {
            "name": "rel_a",
            "cardinality": "one_to_many",
            "left": {"table": "nonexistent", "columns": ("id",)},
            "right": {"table": "raw_b", "columns": ("a_id",)},
        },
    )
    s = Scenario.model_validate(base)
    with pytest.raises(SemanticValidationError) as exc:
        validate_semantics(s)
    assert any("nonexistent" in i.message for i in exc.value.issues)


def test_generator_type_mismatch():
    # string column with integer_range generator
    base = _base_scenario()
    base["raw_tables"] = (
        {
            "name": "raw_a",
            "rows": {"min": 1, "max": 10},
            "columns": (
                {
                    "name": "id",
                    "type": "string",
                    "generator": {"kind": "integer_range", "min": 1, "max": 10},
                },
            ),
            "primary_key": ("id",),
        },
        base["raw_tables"][1],
        base["raw_tables"][2],
    )
    s = Scenario.model_validate(base)
    with pytest.raises(SemanticValidationError) as exc:
        validate_semantics(s)
    assert any("generator" in i.message.lower() for i in exc.value.issues)


def test_cyclic_template():
    base = _base_scenario()
    base["raw_tables"] = (
        {
            "name": "raw_a",
            "rows": {"min": 1, "max": 10},
            "columns": (
                {
                    "name": "col_a",
                    "type": "string",
                    "generator": {"kind": "template_string", "template": "{col_b}"},
                },
                {
                    "name": "col_b",
                    "type": "string",
                    "generator": {"kind": "template_string", "template": "{col_a}"},
                },
            ),
            "primary_key": (),
        },
        base["raw_tables"][1],
        base["raw_tables"][2],
    )
    # Fix staging to match new columns
    base["staging_models"] = (
        {
            "name": "stg_a",
            "source": "raw_a",
            "columns": (
                {"source": "col_a", "target": "col_a"},
                {"source": "col_b", "target": "col_b"},
            ),
            "grain": ("col_a",),
        },
        base["staging_models"][1],
        base["staging_models"][2],
    )
    s = Scenario.model_validate(base)
    with pytest.raises(SemanticValidationError) as exc:
        validate_semantics(s)
    assert any("cycle" in i.message.lower() for i in exc.value.issues)


def test_invalid_pk():
    base = _base_scenario()
    # PK member does not exist
    base["raw_tables"] = (
        {
            "name": "raw_a",
            "rows": {"min": 1, "max": 10},
            "columns": ({"name": "id", "type": "integer", "generator": _INT_GEN},),
            "primary_key": ("nonexistent",),
        },
        base["raw_tables"][1],
        base["raw_tables"][2],
    )
    s = Scenario.model_validate(base)
    with pytest.raises(SemanticValidationError) as exc:
        validate_semantics(s)
    assert any("PK" in i.message or "primary" in i.message.lower() for i in exc.value.issues)
    # PK nullable
    base2 = _base_scenario()
    base2["raw_tables"] = (
        {
            "name": "raw_a",
            "rows": {"min": 1, "max": 10},
            "columns": (
                {
                    "name": "id",
                    "type": "integer",
                    "nullable": True,
                    "null_probability": 0.5,
                    "generator": _INT_GEN,
                },
            ),
            "primary_key": ("id",),
        },
        base2["raw_tables"][1],
        base2["raw_tables"][2],
    )
    s2 = Scenario.model_validate(base2)
    with pytest.raises(SemanticValidationError) as exc2:
        validate_semantics(s2)
    assert any("non-nullable" in i.message.lower() or "PK" in i.message for i in exc2.value.issues)


def test_relationship_arity_mismatch():
    base = _base_scenario()
    base["relationships"] = (
        {
            "name": "rel_a",
            "cardinality": "one_to_one",
            "left": {"table": "raw_a", "columns": ("id",)},
            "right": {"table": "raw_b", "columns": ("a_id", "extra")},
        },
    )
    # need raw_b to have extra column
    base["raw_tables"] = (
        base["raw_tables"][0],
        {
            "name": "raw_b",
            "rows": {"min": 1, "max": 10},
            "columns": (
                {"name": "id", "type": "integer", "generator": _INT_GEN},
                {
                    "name": "a_id",
                    "type": "integer",
                    "generator": {
                        "kind": "foreign_key",
                        "relationship": "rel_a",
                        "target_side": "left",
                    },
                },
                {"name": "extra", "type": "integer", "generator": _INT_GEN},
            ),
            "primary_key": (),
        },
        base["raw_tables"][2],
    )
    s = Scenario.model_validate(base)
    with pytest.raises(SemanticValidationError) as exc:
        validate_semantics(s)
    assert any("arity" in i.message.lower() for i in exc.value.issues)


def test_relationship_type_mismatch():
    base = _base_scenario()
    # left id integer, right a_id string
    base["raw_tables"] = (
        {
            "name": "raw_a",
            "rows": {"min": 1, "max": 10},
            "columns": ({"name": "id", "type": "integer", "generator": _INT_GEN},),
            "primary_key": ("id",),
        },
        {
            "name": "raw_b",
            "rows": {"min": 1, "max": 10},
            "columns": (
                {
                    "name": "a_id",
                    "type": "string",
                    "generator": {
                        "kind": "foreign_key",
                        "relationship": "rel_a",
                        "target_side": "left",
                    },
                },
            ),
            "primary_key": (),
        },
        base["raw_tables"][2],
    )
    base["relationships"] = (
        {
            "name": "rel_a",
            "cardinality": "one_to_many",
            "left": {"table": "raw_a", "columns": ("id",)},
            "right": {"table": "raw_b", "columns": ("a_id",)},
        },
    )
    s = Scenario.model_validate(base)
    with pytest.raises(SemanticValidationError) as exc:
        validate_semantics(s)
    assert any("type mismatch" in i.message.lower() for i in exc.value.issues)


def test_wrong_fk_side():
    base = _base_scenario()
    # one_to_many should have FK on right targeting left, but we give wrong side
    base["raw_tables"] = (
        base["raw_tables"][0],
        {
            "name": "raw_b",
            "rows": {"min": 1, "max": 10},
            "columns": (
                {
                    "name": "a_id",
                    "type": "integer",
                    "generator": {
                        "kind": "foreign_key",
                        "relationship": "rel_a",
                        "target_side": "right",
                    },
                },
            ),
            "primary_key": (),
        },
        base["raw_tables"][2],
    )
    s = Scenario.model_validate(base)
    with pytest.raises(SemanticValidationError) as exc:
        validate_semantics(s)
    assert any("foreign_key" in i.message.lower() for i in exc.value.issues)


def test_invalid_staging_chain():
    # staging operation chain: trim on integer column must fail (only valid on string)
    base = _base_scenario()
    base["staging_models"] = (
        {
            "name": "stg_a",
            "source": "raw_a",
            "columns": ({"source": "id", "target": "id", "operations": ({"op": "trim"},)},),
            "grain": ("id",),
        },
        base["staging_models"][1],
        base["staging_models"][2],
    )
    s = Scenario.model_validate(base)
    with pytest.raises(SemanticValidationError) as exc:
        validate_semantics(s)
    assert any(
        "trim" in i.message.lower() or "string" in i.message.lower() for i in exc.value.issues
    )
    # also test grain still fails if needed, but chain is primary
    base2 = _base_scenario()
    base2["staging_models"] = (
        {
            "name": "stg_a",
            "source": "raw_a",
            "columns": ({"source": "id", "target": "id"},),
            "grain": ("nonexistent",),
        },
        base2["staging_models"][1],
        base2["staging_models"][2],
    )
    s2 = Scenario.model_validate(base2)
    with pytest.raises(SemanticValidationError) as exc2:
        validate_semantics(s2)
    assert any("grain" in i.message.lower() for i in exc2.value.issues)


def test_cyclic_dag():
    base = _base_scenario()
    base["intermediate_models"] = (
        {
            "operation": "transform",
            "name": "trans_a",
            "source": "trans_b",
            "columns": ({"source": "id", "target": "id"},),
            "grain": ("id",),
        },
        {
            "operation": "transform",
            "name": "trans_b",
            "source": "trans_a",
            "columns": ({"source": "id", "target": "id"},),
            "grain": ("id",),
        },
    )
    # need at least 2 intermediate, but we have 2 that cycle, need output to reference one
    base["output_models"] = (
        {
            "name": "out_a",
            "source": "trans_a",
            "group_by": ({"source": "id", "target": "id"},),
            "grain": ("id",),
            "metrics": ({"name": "cnt", "function": "count_rows"},),
        },
    )
    s = Scenario.model_validate(base)
    with pytest.raises(SemanticValidationError) as exc:
        validate_semantics(s)
    assert any("cycle" in i.message.lower() for i in exc.value.issues)


def test_invalid_layer_dependency():
    base = _base_scenario()
    base["intermediate_models"] = (
        {
            "operation": "transform",
            "name": "trans_a",
            "source": "raw_a",
            "columns": ({"source": "id", "target": "id"},),
            "grain": ("id",),
        },  # raw table as source – invalid
        {
            "operation": "join",
            "name": "join_a",
            "left": "stg_a",
            "right": "stg_b",
            "join": {"type": "inner", "on": ({"left": "id", "right": "id"},)},
            "columns": ({"side": "left", "source": "id", "target": "id"},),
            "grain": ("id",),
        },
    )
    s = Scenario.model_validate(base)
    with pytest.raises(SemanticValidationError) as exc:
        validate_semantics(s)
    assert any("raw table" in i.message.lower() for i in exc.value.issues)


def test_join_key_mismatch():
    base = _base_scenario()
    # Create raw tables where join keys have different types: raw_a id integer, raw_c id string
    base["raw_tables"] = (
        {
            "name": "raw_a",
            "rows": {"min": 1, "max": 10},
            "columns": ({"name": "id", "type": "integer", "generator": _INT_GEN},),
            "primary_key": ("id",),
        },
        base["raw_tables"][1],
        {
            "name": "raw_c",
            "rows": {"min": 1, "max": 10},
            "columns": ({"name": "id", "type": "string", "generator": _STR_GEN},),
            "primary_key": ("id",),
        },
    )
    # staging for those
    base["staging_models"] = (
        {
            "name": "stg_a",
            "source": "raw_a",
            "columns": ({"source": "id", "target": "id"},),
            "grain": ("id",),
        },
        base["staging_models"][1],
        {
            "name": "stg_c",
            "source": "raw_c",
            "columns": ({"source": "id", "target": "id"},),
            "grain": ("id",),
        },
    )
    base["intermediate_models"] = (
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
            "right": "stg_c",
            "join": {"type": "inner", "on": ({"left": "id", "right": "id"},)},
            "columns": ({"side": "left", "source": "id", "target": "id"},),
            "grain": ("id",),
        },
    )
    s = Scenario.model_validate(base)
    with pytest.raises(SemanticValidationError) as exc:
        validate_semantics(s)
    assert any(
        "join" in i.message.lower() and "type" in i.message.lower() for i in exc.value.issues
    )


def test_impossible_grain():
    base = _base_scenario()
    base["output_models"] = (
        {
            "name": "out_a",
            "source": "trans_a",
            "group_by": ({"source": "id", "target": "id"},),
            "grain": ("nonexistent",),
            "metrics": ({"name": "cnt", "function": "count_rows"},),
        },
    )
    s = Scenario.model_validate(base)
    with pytest.raises(SemanticValidationError) as exc:
        validate_semantics(s)
    assert any("grain" in i.message.lower() for i in exc.value.issues)


def test_invalid_metric_type():
    base = _base_scenario()
    # Use sum on string column – should fail
    # Make raw_c's id be string type (trans_a is from stg_c)
    base["raw_tables"] = (
        base["raw_tables"][0],
        base["raw_tables"][1],
        {
            "name": "raw_c",
            "rows": {"min": 1, "max": 10},
            "columns": ({"name": "id", "type": "string", "generator": _STR_GEN},),
            "primary_key": ("id",),
        },
    )
    # intermediate trans_a will have id string type (from stg_c)
    # output sum on id string should fail
    base["output_models"] = (
        {
            "name": "out_a",
            "source": "trans_a",
            "group_by": ({"source": "id", "target": "id"},),
            "grain": ("id",),
            "metrics": ({"name": "total", "function": "sum", "column": "id"},),
        },
    )
    s = Scenario.model_validate(base)
    with pytest.raises(SemanticValidationError) as exc:
        validate_semantics(s)
    assert any("numeric" in i.message.lower() for i in exc.value.issues)


def test_disconnected_model():
    base = _base_scenario()
    # Add extra intermediate not connected to output
    base["intermediate_models"] = (
        {
            "operation": "transform",
            "name": "trans_a",
            "source": "stg_a",
            "columns": ({"source": "id", "target": "id"},),
            "grain": ("id",),
        },
        {
            "operation": "transform",
            "name": "orphan",
            "source": "stg_c",
            "columns": ({"source": "id", "target": "id"},),
            "grain": ("id",),
        },
    )
    # output only uses trans_a, not orphan
    base["output_models"] = (
        {
            "name": "out_a",
            "source": "trans_a",
            "group_by": ({"source": "id", "target": "id"},),
            "grain": ("id",),
            "metrics": ({"name": "cnt", "function": "count_rows"},),
        },
    )
    s = Scenario.model_validate(base)
    with pytest.raises(SemanticValidationError) as exc:
        validate_semantics(s)
    assert any(
        "not ancestor" in i.message.lower() or "disconnected" in i.message.lower()
        for i in exc.value.issues
    )


def test_contradictory_assertion():
    # Duplicate explicit assertions with same effective meaning
    base = _base_scenario()
    base["tests"] = (
        {"name": "assert1", "model": "out_a", "type": "not_null", "columns": ("id",)},
        {"name": "assert2", "model": "out_a", "type": "not_null", "columns": ("id",)},
    )
    s = Scenario.model_validate(base)
    with pytest.raises(SemanticValidationError) as exc:
        validate_semantics(s)
    assert any("duplicate" in i.message.lower() for i in exc.value.issues)
    # Explicit that duplicates derived (PK not_null is derived)
    base2 = _base_scenario()
    # raw_a has PK id, derived will be not_null on raw_a.id, so explicit duplicate should fail
    base2["tests"] = (
        {"name": "dup_derived", "model": "raw_a", "type": "not_null", "columns": ("id",)},
    )
    s2 = Scenario.model_validate(base2)
    with pytest.raises(SemanticValidationError) as exc2:
        validate_semantics(s2)
    assert any(
        "derived" in i.message.lower() or "duplicate" in i.message.lower()
        for i in exc2.value.issues
    )
    # Also test missing ref still is semantic, but contradictory is primary
    base3 = _base_scenario()
    base3["tests"] = (
        {"name": "assert1", "model": "nonexistent_model", "type": "not_null", "columns": ("id",)},
    )
    s3 = Scenario.model_validate(base3)
    with pytest.raises(SemanticValidationError) as exc3:
        validate_semantics(s3)
    assert any("does not exist" in i.message.lower() for i in exc3.value.issues)


def test_fk_missing_relationship():
    base = _base_scenario()
    base["raw_tables"] = (
        base["raw_tables"][0],
        {
            "name": "raw_b",
            "rows": {"min": 1, "max": 10},
            "columns": (
                {"name": "id", "type": "integer", "generator": _INT_GEN},
                {
                    "name": "a_id",
                    "type": "integer",
                    "generator": {
                        "kind": "foreign_key",
                        "relationship": "rel_a",
                        "target_side": "left",
                    },
                },
                {
                    "name": "ghost_fk",
                    "type": "integer",
                    "generator": {
                        "kind": "foreign_key",
                        "relationship": "ghost_rel",
                        "target_side": "left",
                    },
                },
            ),
            "primary_key": (),
        },
        base["raw_tables"][2],
    )
    s = Scenario.model_validate(base)
    with pytest.raises(SemanticValidationError) as exc:
        validate_semantics(s)
    assert any(
        "ghost_rel" in i.message and "does not exist" in i.message.lower() for i in exc.value.issues
    )


def test_join_without_supporting_lineage():
    base = _base_scenario()
    # Change join to use keys that have no supporting relationship (stg_b.id + stg_c.id has no rel)
    base["intermediate_models"] = (
        {
            "operation": "transform",
            "name": "trans_a",
            "source": "stg_c",
            "columns": ({"source": "id", "target": "id"},),
            "grain": ("id",),
        },
        {
            "operation": "join",
            "name": "join_a",
            "left": "stg_b",
            "right": "stg_c",
            "join": {"type": "inner", "on": ({"left": "id", "right": "id"},)},
            "columns": ({"side": "left", "source": "id", "target": "id"},),
            "grain": ("id",),
        },
    )
    s = Scenario.model_validate(base)
    with pytest.raises(SemanticValidationError) as exc:
        validate_semantics(s)
    assert any(
        "not supported" in i.message.lower() and "relationship" in i.message.lower()
        for i in exc.value.issues
    )


def test_staging_filter_type_mismatch():
    base = _base_scenario()
    base["staging_models"] = (
        {
            "name": "stg_a",
            "source": "raw_a",
            "columns": ({"source": "id", "target": "id"},),
            "grain": ("id",),
            "row_operations": (
                {
                    "op": "filter",
                    "condition": {
                        "kind": "comparison",
                        "operator": "eq",
                        "left": {"kind": "column", "column": "id"},
                        "right": {"kind": "literal", "value": "wrong_type"},
                    },
                },
            ),
        },
        base["staging_models"][1],
        base["staging_models"][2],
    )
    s = Scenario.model_validate(base)
    with pytest.raises(SemanticValidationError) as exc:
        validate_semantics(s)
    assert any("type mismatch" in i.message.lower() for i in exc.value.issues)


def test_assertion_missing_to_model_and_wrong_type():
    base = _base_scenario()
    base["tests"] = (
        {
            "name": "assert1",
            "model": "out_a",
            "type": "relationships",
            "columns": ("id",),
            "to_model": "ghost_model",
            "to_columns": ("id",),
        },
    )
    s = Scenario.model_validate(base)
    with pytest.raises(SemanticValidationError) as exc:
        validate_semantics(s)
    assert any(
        "ghost_model" in i.message and "does not exist" in i.message.lower()
        for i in exc.value.issues
    )
    base2 = _base_scenario()
    base2["tests"] = (
        {
            "name": "assert1",
            "model": "out_a",
            "type": "column_range",
            "column": "id",
            "min": "wrong_type",
        },
    )
    s2 = Scenario.model_validate(base2)
    with pytest.raises(SemanticValidationError) as exc2:
        validate_semantics(s2)
    assert any("column_range" in i.path or "type" in i.message.lower() for i in exc2.value.issues)


def test_aggregate_filter_type_mismatch():
    base = _base_scenario()
    base["intermediate_models"] = (
        {
            "operation": "aggregate",
            "name": "agg_a",
            "source": "stg_a",
            "group_by": ({"source": "id", "target": "id"},),
            "metrics": ({"name": "cnt", "function": "count_rows"},),
            "filters": (
                {
                    "kind": "comparison",
                    "operator": "eq",
                    "left": {"kind": "column", "column": "id"},
                    "right": {"kind": "literal", "value": "wrong_type"},
                },
            ),
            "grain": ("id",),
        },
        base["intermediate_models"][1],
    )
    s = Scenario.model_validate(base)
    with pytest.raises(SemanticValidationError) as exc:
        validate_semantics(s)
    assert any("type mismatch" in i.message.lower() for i in exc.value.issues)


def test_deterministic_tie_breaking():
    base = _base_scenario()
    base["intermediate_models"] = (
        base["intermediate_models"][0],
        {
            "operation": "deduplicate",
            "name": "dedup_a",
            "source": "stg_a",
            "keys": ("id",),
            "order_by": ({"column": "id"},),  # order_by same as keys – not deterministic
            "grain": ("id",),
        },
    )
    base["output_models"] = (
        {
            "name": "out_a",
            "source": "dedup_a",
            "group_by": ({"source": "id", "target": "id"},),
            "grain": ("id",),
            "metrics": ({"name": "cnt", "function": "count_rows"},),
        },
    )
    s = Scenario.model_validate(base)
    with pytest.raises(SemanticValidationError) as exc:
        validate_semantics(s)
    assert any("deterministic" in i.message.lower() for i in exc.value.issues)


def test_composite_pk_feasibility():
    base = _base_scenario()
    # Composite PK with two columns each capacity 2 (integer_range 1-2) but max rows 10 -> composite capacity 4 <10, should fail
    base["raw_tables"] = (
        {
            "name": "raw_a",
            "rows": {"min": 1, "max": 10},
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
        base["raw_tables"][1],
        base["raw_tables"][2],
    )
    # Fix staging to match new columns
    base["staging_models"] = (
        {
            "name": "stg_a",
            "source": "raw_a",
            "columns": ({"source": "id", "target": "id"}, {"source": "seq", "target": "seq"}),
            "grain": ("id", "seq"),
        },
        base["staging_models"][1],
        base["staging_models"][2],
    )
    base["relationships"] = (
        {
            "name": "rel_a",
            "cardinality": "one_to_many",
            "left": {"table": "raw_a", "columns": ("id", "seq")},
            "right": {"table": "raw_b", "columns": ("a_id", "extra")},
        },
    )
    # Need to update raw_b to have matching FK columns for composite
    base["raw_tables"] = (
        base["raw_tables"][0],
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
                {
                    "name": "extra",
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
        base["raw_tables"][2],
    )
    s = Scenario.model_validate(base)
    with pytest.raises(SemanticValidationError) as exc:
        validate_semantics(s)
    assert any("composite pk capacity" in i.message.lower() for i in exc.value.issues)


def test_one_to_one_composite_derived():
    # One-to-one with composite keys should derive correctly with all columns, not just first
    base = {
        "schema_version": "1.0",
        "scenario_id": "test_one_to_one_comp",
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
                        "name": "seq",
                        "type": "integer",
                        "generator": {"kind": "integer_range", "min": 1, "max": 10},
                    },
                ),
                "primary_key": ("id", "seq"),
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
                        "unique": True,
                        "generator": {
                            "kind": "foreign_key",
                            "relationship": "rel_a",
                            "target_side": "left",
                        },
                    },
                    {
                        "name": "a_seq",
                        "type": "integer",
                        "unique": True,
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
                "cardinality": "one_to_one",
                "left": {"table": "raw_a", "columns": ("id", "seq")},
                "right": {"table": "raw_b", "columns": ("a_id", "a_seq")},
            },
        ),
        "staging_models": (
            {
                "name": "stg_a",
                "source": "raw_a",
                "columns": ({"source": "id", "target": "id"}, {"source": "seq", "target": "seq"}),
                "grain": ("id", "seq"),
            },
            {
                "name": "stg_b",
                "source": "raw_b",
                "columns": (
                    {"source": "id", "target": "id"},
                    {"source": "a_id", "target": "a_id"},
                    {"source": "a_seq", "target": "a_seq"},
                ),
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
                "source": "stg_c",
                "columns": ({"source": "id", "target": "id"},),
                "grain": ("id",),
            },
            {
                "operation": "join",
                "name": "join_a",
                "left": "stg_a",
                "right": "stg_b",
                "join": {
                    "type": "inner",
                    "on": ({"left": "id", "right": "a_id"}, {"left": "seq", "right": "a_seq"}),
                },
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
            {
                "name": "out_b",
                "source": "join_a",
                "group_by": ({"source": "id", "target": "id"},),
                "grain": ("id",),
                "metrics": ({"name": "cnt2", "function": "count_rows"},),
            },
        ),
    }
    s = Scenario.model_validate(base)
    validated = validate_semantics(s)
    # Check that derived assertion for one_to_one includes both columns, not just first
    derived_rels = [
        d
        for d in validated.derived_assertions
        if d["type"] == "relationships" and d["model"] == "raw_b"
    ]
    assert len(derived_rels) == 1
    assert set(derived_rels[0]["columns"]) == {"a_id", "a_seq"}


def test_validated_scenario_frozen_deep():
    data = _base_scenario()
    s = Scenario.model_validate(data)
    validated = validate_semantics(s)
    # Top-level frozen
    with pytest.raises(Exception):
        validated.scenario = None  # type: ignore[misc]
    # Dicts should be immutable via MappingProxyType
    with pytest.raises(Exception):
        validated.raw_by_name["new"] = None  # type: ignore[index]
    with pytest.raises(Exception):
        validated.lineage["new"] = {}  # type: ignore[index]
    # Inner dicts also immutable
    with pytest.raises(Exception):
        validated.lineage["stg_a"]["new_col"] = ["raw_a.new"]  # type: ignore[index]
    with pytest.raises(Exception):
        validated.staging_schemas["stg_a"]["new_col"] = "test"  # type: ignore[index]


def test_compiler_accepts_only_validated():
    data = _base_scenario()
    s = Scenario.model_validate(data)
    validated = validate_semantics(s)
    # ValidatedScenario must be distinct type, not alias
    assert type(validated) is not Scenario
    assert isinstance(validated, ValidatedScenario)
    assert not isinstance(s, ValidatedScenario)
    assert validated.scenario is s
    assert hasattr(validated, "topological_order")
    assert hasattr(validated, "staging_schemas")
    assert hasattr(validated, "derived_assertions")

    # Simulate compiler that only accepts ValidatedScenario
    def requires_validated(vs: ValidatedScenario) -> int:
        if not isinstance(vs, ValidatedScenario):
            raise TypeError("compiler requires ValidatedScenario, got bare Scenario")
        return len(vs.topological_order)

    assert requires_validated(validated) >= 1
    with pytest.raises(TypeError, match="ValidatedScenario"):
        requires_validated(s)  # type: ignore[arg-type]
    # Also check that raw lineage and resolved schemas are present (now tuples for deep immutability)
    assert validated.lineage["stg_a"]["id"] == ("raw_a.id",)
    assert (
        validated.staging_schemas["stg_a"]["id"] == validated.intermediate_schemas["trans_a"]["id"]
    )
