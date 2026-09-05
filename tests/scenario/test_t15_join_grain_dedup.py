"""Focused tests for T15 – join lineage, grain preservation, deterministic dedup."""

from __future__ import annotations

import pytest

from data_pipeline_diagnostics.scenario.errors import SemanticValidationError
from data_pipeline_diagnostics.scenario.models import Scenario
from data_pipeline_diagnostics.scenario.semantic import validate_semantics

_INT = {"kind": "integer_range", "min": 1, "max": 10}


def _raw(name, cols, pk):
    return {"name": name, "rows": {"min": 1, "max": 10}, "columns": tuple(cols), "primary_key": pk}


def _id_col(name="id", gen=None):
    return {"name": name, "type": "integer", "generator": gen or _INT}


def _fk_col(name, rel, side, unique=False):
    col = {
        "name": name,
        "type": "integer",
        "generator": {"kind": "foreign_key", "relationship": rel, "target_side": side},
    }
    if unique:
        col["unique"] = True
    return col


def _rel(name, lt, lc, rt, rc, card):
    return {
        "name": name,
        "cardinality": card,
        "left": {"table": lt, "columns": lc},
        "right": {"table": rt, "columns": rc},
    }


def _stg(name, source, pairs, grain):
    return {
        "name": name,
        "source": source,
        "columns": tuple({"source": s, "target": t} for s, t in pairs),
        "grain": grain,
    }


def _trans(name, source, pairs, grain):
    return {
        "operation": "transform",
        "name": name,
        "source": source,
        "columns": tuple({"source": s, "target": t} for s, t in pairs),
        "grain": grain,
    }


def _join(name, left, right, jtype, on, cols, grain):
    return {
        "operation": "join",
        "name": name,
        "left": left,
        "right": right,
        "join": {"type": jtype, "on": tuple({"left": a, "right": b} for a, b in on)},
        "columns": tuple({"side": s, "source": a, "target": b} for s, a, b in cols),
        "grain": grain,
    }


def _out(name, source):
    return {
        "name": name,
        "source": source,
        "group_by": ({"source": "id", "target": "id"},),
        "grain": ("id",),
        "metrics": ({"name": "cnt", "function": "count_rows"},),
    }


def _validate_ok(data):
    s = Scenario.model_validate(data)
    return validate_semantics(s)


def _validate_fails(data):
    s = Scenario.model_validate(data)
    with pytest.raises(SemanticValidationError) as exc:
        validate_semantics(s)
    return exc.value.issues


def _one_to_one_base():
    # raw_a.id PK, raw_b.b_id FK -> raw_a.id, raw_c filler
    return {
        "schema_version": "1.0",
        "scenario_id": "t15_o2o",
        "domain": "testdomain",
        "raw_tables": (
            _raw("raw_a", (_id_col("id"),), ("id",)),
            _raw("raw_b", (_id_col("id"), _fk_col("b_id", "rel_o2o", "left", unique=True)), ()),
            _raw("raw_c", (_id_col("id"),), ("id",)),
        ),
        "relationships": (_rel("rel_o2o", "raw_a", ("id",), "raw_b", ("b_id",), "one_to_one"),),
        "staging_models": (
            _stg("stg_a", "raw_a", (("id", "id"),), ("id",)),
            _stg("stg_b", "raw_b", (("id", "id"), ("b_id", "b_id")), ("id",)),
            _stg("stg_c", "raw_c", (("id", "id"),), ("id",)),
        ),
        "intermediate_models": (
            _trans("trans_a", "stg_c", (("id", "id"),), ("id",)),
            _join("join_a", "stg_a", "stg_b", "inner", (("id", "b_id"),), (("left", "id", "id"),), ("id",)),
        ),
        "output_models": (_out("out_a", "trans_a"), _out("out_b", "join_a")),
    }


def test_valid_one_to_one_both_orientations():
    data = _one_to_one_base()
    v = _validate_ok(data)
    assert "join_a" in v.topological_order
    # reverse orientation: left=stg_b, right=stg_a
    data["intermediate_models"] = (
        data["intermediate_models"][0],
        _join("join_a", "stg_b", "stg_a", "inner", (("b_id", "id"),), (("right", "id", "id"),), ("id",)),
    )
    v2 = _validate_ok(data)
    assert "join_a" in v2.topological_order


def test_valid_many_to_one_preserves_many_grain():
    data = {
        "schema_version": "1.0",
        "scenario_id": "t15_m2o",
        "domain": "testdomain",
        "raw_tables": (
            _raw("raw_a", (_id_col("id"),), ("id",)),
            _raw("raw_b", (_id_col("id"), _fk_col("a_id", "rel_m2o", "right")), ()),
            _raw("raw_c", (_id_col("id"),), ("id",)),
        ),
        "relationships": (_rel("rel_m2o", "raw_b", ("a_id",), "raw_a", ("id",), "many_to_one"),),
        "staging_models": (
            _stg("stg_a", "raw_a", (("id", "id"),), ("id",)),
            _stg("stg_b", "raw_b", (("id", "id"), ("a_id", "a_id")), ("id",)),
            _stg("stg_c", "raw_c", (("id", "id"),), ("id",)),
        ),
        "intermediate_models": (
            _trans("trans_a", "stg_c", (("id", "id"),), ("id",)),
            _join("join_a", "stg_b", "stg_a", "inner", (("a_id", "id"),), (("left", "id", "id"),), ("id",)),
        ),
        "output_models": (_out("out_a", "trans_a"), _out("out_b", "join_a")),
    }
    _validate_ok(data)


def test_one_to_many_one_side_grain_invalid():
    data = {
        "schema_version": "1.0",
        "scenario_id": "t15_o2m_bad",
        "domain": "testdomain",
        "raw_tables": (
            _raw("raw_a", (_id_col("id"),), ("id",)),
            _raw("raw_b", (_id_col("id"), _fk_col("a_id", "rel_a", "left")), ()),
            _raw("raw_c", (_id_col("id"),), ("id",)),
        ),
        "relationships": (_rel("rel_a", "raw_a", ("id",), "raw_b", ("a_id",), "one_to_many"),),
        "staging_models": (
            _stg("stg_a", "raw_a", (("id", "id"),), ("id",)),
            _stg("stg_b", "raw_b", (("id", "id"), ("a_id", "a_id")), ("id",)),
            _stg("stg_c", "raw_c", (("id", "id"),), ("id",)),
        ),
        "intermediate_models": (
            _trans("trans_a", "stg_c", (("id", "id"),), ("id",)),
            # one-to-many inner join, grain only from one (left) side – fan-out duplicates it
            _join("join_a", "stg_a", "stg_b", "inner", (("id", "a_id"),), (("left", "id", "id"),), ("id",)),
        ),
        "output_models": (_out("out_a", "trans_a"), _out("out_b", "join_a")),
    }
    issues = _validate_fails(data)
    assert any(i.code == "E133" and "join_a" in i.path and "grain" in i.path for i in issues)


def test_left_join_right_only_grain_invalid():
    data = {
        "schema_version": "1.0",
        "scenario_id": "t15_left_bad",
        "domain": "testdomain",
        "raw_tables": (
            _raw("raw_a", (_id_col("id"),), ("id",)),
            _raw("raw_b", (_id_col("id"), _fk_col("a_id", "rel_a", "left")), ()),
            _raw("raw_c", (_id_col("id"),), ("id",)),
        ),
        "relationships": (_rel("rel_a", "raw_a", ("id",), "raw_b", ("a_id",), "one_to_many"),),
        "staging_models": (
            _stg("stg_a", "raw_a", (("id", "id"),), ("id",)),
            _stg("stg_b", "raw_b", (("id", "id"), ("a_id", "a_id")), ("id",)),
            _stg("stg_c", "raw_c", (("id", "id"),), ("id",)),
        ),
        "intermediate_models": (
            _trans("trans_a", "stg_c", (("id", "id"),), ("id",)),
            _join("join_a", "stg_a", "stg_b", "left", (("id", "a_id"),), (("right", "id", "id"),), ("id",)),
        ),
        "output_models": (_out("out_a", "trans_a"), _out("out_b", "join_a")),
    }
    issues = _validate_fails(data)
    assert any(i.code == "E133" for i in issues)


def test_valid_combined_grain_multiplicative_join():
    data = {
        "schema_version": "1.0",
        "scenario_id": "t15_combined",
        "domain": "testdomain",
        "raw_tables": (
            _raw("raw_a", (_id_col("id"),), ("id",)),
            _raw("raw_b", (_id_col("id"), _fk_col("a_id", "rel_a", "left")), ()),
            _raw("raw_c", (_id_col("id"),), ("id",)),
        ),
        "relationships": (_rel("rel_a", "raw_a", ("id",), "raw_b", ("a_id",), "one_to_many"),),
        "staging_models": (
            _stg("stg_a", "raw_a", (("id", "id"),), ("id",)),
            _stg("stg_b", "raw_b", (("id", "id"), ("a_id", "a_id")), ("id",)),
            _stg("stg_c", "raw_c", (("id", "id"),), ("id",)),
        ),
        "intermediate_models": (
            _trans("trans_a", "stg_c", (("id", "id"),), ("id",)),
            _join(
                "join_a",
                "stg_a",
                "stg_b",
                "inner",
                (("id", "a_id"),),
                (("left", "id", "lid"), ("right", "id", "rid")),
                ("lid", "rid"),
            ),
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
                "group_by": ({"source": "lid", "target": "lid"}, {"source": "rid", "target": "rid"}),
                "grain": ("lid", "rid"),
                "metrics": ({"name": "cnt2", "function": "count_rows"},),
            },
        ),
    }
    _validate_ok(data)


def _composite_base():
    return {
        "schema_version": "1.0",
        "scenario_id": "t15_comp",
        "domain": "testdomain",
        "raw_tables": (
            _raw("raw_a", (_id_col("id"), _id_col("seq")), ("id", "seq")),
            _raw(
                "raw_b",
                (_id_col("id"), _fk_col("a_id", "rel_c", "left"), _fk_col("a_seq", "rel_c", "left")),
                (),
            ),
            _raw("raw_c", (_id_col("id"),), ("id",)),
        ),
        "relationships": (_rel("rel_c", "raw_a", ("id", "seq"), "raw_b", ("a_id", "a_seq"), "one_to_many"),),
        "staging_models": (
            _stg("stg_a", "raw_a", (("id", "id"), ("seq", "seq")), ("id", "seq")),
            _stg("stg_b", "raw_b", (("id", "id"), ("a_id", "a_id"), ("a_seq", "a_seq")), ("id",)),
            _stg("stg_c", "raw_c", (("id", "id"),), ("id",)),
        ),
        "intermediate_models": (
            _trans("trans_a", "stg_c", (("id", "id"),), ("id",)),
            _join(
                "join_a",
                "stg_a",
                "stg_b",
                "inner",
                (("id", "a_id"), ("seq", "a_seq")),
                (("right", "id", "id"),),
                ("id",),
            ),
        ),
        "output_models": (_out("out_a", "trans_a"), _out("out_b", "join_a")),
    }


def test_composite_swapped_components():
    data = _composite_base()
    data["intermediate_models"] = (
        data["intermediate_models"][0],
        _join(
            "join_a",
            "stg_a",
            "stg_b",
            "inner",
            (("id", "a_seq"), ("seq", "a_id")),
            (("right", "id", "id"),),
            ("id",),
        ),
    )
    issues = _validate_fails(data)
    assert any(i.code == "E131" and "join.on" in i.path for i in issues)


def test_composite_missing_component():
    data = _composite_base()
    data["intermediate_models"] = (
        data["intermediate_models"][0],
        _join(
            "join_a",
            "stg_a",
            "stg_b",
            "inner",
            (("id", "a_id"),),
            (("right", "id", "id"),),
            ("id",),
        ),
    )
    issues = _validate_fails(data)
    assert any(i.code == "E131" and "join.on" in i.path for i in issues)


def test_join_pairs_from_two_relationships():
    data = {
        "schema_version": "1.0",
        "scenario_id": "t15_mixed",
        "domain": "testdomain",
        "raw_tables": (
            _raw("raw_a", (_id_col("id"),), ("id",)),
            _raw("raw_b", (_id_col("id"), _fk_col("a_id", "rel_1", "left")), ()),
            _raw("raw_c", (_id_col("id"), _fk_col("x_id", "rel_2", "left")), ()),
        ),
        "relationships": (
            _rel("rel_1", "raw_a", ("id",), "raw_b", ("a_id",), "one_to_many"),
            _rel("rel_2", "raw_a", ("id",), "raw_c", ("x_id",), "one_to_many"),
        ),
        "staging_models": (
            _stg("stg_a", "raw_a", (("id", "id"),), ("id",)),
            _stg("stg_b", "raw_b", (("id", "id"), ("a_id", "a_id")), ("id",)),
            _stg("stg_c", "raw_c", ({"source": "id", "target": "id"}, {"source": "x_id", "target": "x_id"}), ("id",)),
        ),
        "intermediate_models": (
            _trans("trans_a", "stg_a", (("id", "id"),), ("id",)),
            _join("join_a", "stg_b", "stg_c", "inner", (("a_id", "x_id"),), (("left", "id", "id"),), ("id",)),
        ),
        "output_models": (_out("out_a", "trans_a"), _out("out_b", "join_a")),
    }
    # fix typo: staging stg_c columns must be tuple of dicts
    data["staging_models"] = (
        data["staging_models"][0],
        data["staging_models"][1],
        _stg("stg_c", "raw_c", (("id", "id"), ("x_id", "x_id")), ("id",)),
    )
    issues = _validate_fails(data)
    assert any(i.code == "E131" for i in issues)


def test_transform_drops_source_grain():
    data = {
        "schema_version": "1.0",
        "scenario_id": "t15_drop",
        "domain": "testdomain",
        "raw_tables": (
            _raw("raw_a", (_id_col("id"), _id_col("val")), ("id",)),
            _raw("raw_b", (_id_col("id"),), ("id",)),
            _raw("raw_c", (_id_col("id"),), ("id",)),
        ),
        "relationships": (_rel("rel_a", "raw_a", ("id",), "raw_b", ("id",), "one_to_one"),),
        "staging_models": (
            _stg("stg_a", "raw_a", (("id", "id"), ("val", "val")), ("id",)),
            _stg("stg_b", "raw_b", (("id", "id"),), ("id",)),
            _stg("stg_c", "raw_c", (("id", "id"),), ("id",)),
        ),
        "intermediate_models": (
            {
                "operation": "transform",
                "name": "trans_a",
                "source": "stg_a",
                "columns": ({"source": "val", "target": "val"},),
                "grain": ("val",),
            },
            _trans("trans_b", "stg_b", (("id", "id"),), ("id",)),
        ),
        "output_models": (_out("out_a", "trans_b"),),
    }
    # fix outputs: need trans_a connected too – use two outputs
    data["output_models"] = (
        {
            "name": "out_a",
            "source": "trans_a",
            "group_by": ({"source": "val", "target": "val"},),
            "grain": ("val",),
            "metrics": ({"name": "cnt", "function": "count_rows"},),
        },
        _out("out_b", "trans_b"),
    )
    # fix one_to_one FK side for rel_a validity
    data["raw_tables"] = (
        _raw("raw_a", (_id_col("id"), _id_col("val")), ("id",)),
        _raw("raw_b", (_id_col("id"), _fk_col("a_id", "rel_a", "left")), ()),
        _raw("raw_c", (_id_col("id"),), ("id",)),
    )
    data["relationships"] = (_rel("rel_a", "raw_a", ("id",), "raw_b", ("a_id",), "one_to_one"),)
    data["staging_models"] = (
        _stg("stg_a", "raw_a", (("id", "id"), ("val", "val")), ("id",)),
        _stg("stg_b", "raw_b", (("id", "id"), ("a_id", "a_id")), ("id",)),
        _stg("stg_c", "raw_c", (("id", "id"),), ("id",)),
    )
    issues = _validate_fails(data)
    assert any("projected image" in i.message.lower() or i.code == "E125" for i in issues)


def test_deterministic_dedup_with_source_key():
    # Intermediate dedup whose keys + order_by cover the source grain and raw PK.
    data = {
        "schema_version": "1.0",
        "scenario_id": "t15_dedup_ok",
        "domain": "testdomain",
        "raw_tables": (
            _raw("raw_a", (_id_col("id"),), ("id",)),
            _raw("raw_b", (_id_col("id"), _fk_col("a_id", "rel_a", "left")), ()),
            _raw("raw_c", (_id_col("id"), _id_col("ts")), ("id",)),
        ),
        "relationships": (_rel("rel_a", "raw_a", ("id",), "raw_b", ("a_id",), "one_to_many"),),
        "staging_models": (
            _stg("stg_a", "raw_a", (("id", "id"),), ("id",)),
            _stg("stg_b", "raw_b", (("id", "id"), ("a_id", "a_id")), ("id",)),
            _stg("stg_c", "raw_c", (("id", "id"), ("ts", "ts")), ("id",)),
        ),
        "intermediate_models": (
            _join("join_a", "stg_a", "stg_b", "inner", (("id", "a_id"),), (("right", "id", "id"),), ("id",)),
            {
                "operation": "deduplicate",
                "name": "dedup_a",
                "source": "stg_c",
                "keys": ("id",),
                "order_by": ({"column": "ts"},),
                "grain": ("id",),
            },
        ),
        "output_models": (_out("out_a", "join_a"), _out("out_b", "dedup_a")),
    }
    _validate_ok(data)


def test_nondeterministic_dedup_extra_not_unique():
    data = {
        "schema_version": "1.0",
        "scenario_id": "t15_dedup_bad",
        "domain": "testdomain",
        "raw_tables": (
            _raw("raw_a", (_id_col("id"), _id_col("val")), ("id",)),
            _raw("raw_b", (_id_col("id"),), ("id",)),
            _raw("raw_c", (_id_col("id"),), ("id",)),
        ),
        "relationships": (_rel("rel_a", "raw_a", ("id",), "raw_b", ("id",), "one_to_one"),),
        "staging_models": (
            _stg("stg_a", "raw_a", (("id", "id"), ("val", "val")), ("id",)),
            _stg("stg_b", "raw_b", (("id", "id"),), ("id",)),
            _stg("stg_c", "raw_c", (("id", "id"),), ("id",)),
        ),
        "intermediate_models": (
            _trans("trans_a", "stg_c", (("id", "id"),), ("id",)),
            {
                "operation": "deduplicate",
                "name": "dedup_a",
                "source": "stg_a",
                "keys": ("val",),
                "order_by": ({"column": "val"},),
                "grain": ("val",),
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
                "source": "dedup_a",
                "group_by": ({"source": "val", "target": "val"},),
                "grain": ("val",),
                "metrics": ({"name": "cnt2", "function": "count_rows"},),
            },
        ),
    }
    # fix one_to_one FK
    data["raw_tables"] = (
        _raw("raw_a", (_id_col("id"), _id_col("val")), ("id",)),
        _raw("raw_b", (_id_col("id"), _fk_col("a_id", "rel_a", "left")), ()),
        _raw("raw_c", (_id_col("id"),), ("id",)),
    )
    data["relationships"] = (_rel("rel_a", "raw_a", ("id",), "raw_b", ("a_id",), "one_to_one"),)
    data["staging_models"] = (
        _stg("stg_a", "raw_a", (("id", "id"), ("val", "val")), ("id",)),
        _stg("stg_b", "raw_b", (("id", "id"), ("a_id", "a_id")), ("id",)),
        _stg("stg_c", "raw_c", (("id", "id"),), ("id",)),
    )
    issues = _validate_fails(data)
    assert any(i.code == "E134" and "order_by" in i.path for i in issues)


def test_staging_dedup_renamed_keys():
    # Staging dedup through renamed columns: keys + order_by cover the raw PK
    # via lineage, and the downstream join uses a combined grain.
    data = {
        "schema_version": "1.0",
        "scenario_id": "t15_stg_rename",
        "domain": "testdomain",
        "raw_tables": (
            _raw("raw_a", (_id_col("id"), _id_col("ts")), ("id",)),
            _raw("raw_b", (_id_col("id"), _fk_col("a_id", "rel_a", "left")), ()),
            _raw("raw_c", (_id_col("id"),), ("id",)),
        ),
        "relationships": (_rel("rel_a", "raw_a", ("id",), "raw_b", ("a_id",), "one_to_many"),),
        "staging_models": (
            {
                "name": "stg_a",
                "source": "raw_a",
                "columns": ({"source": "id", "target": "user_id"}, {"source": "ts", "target": "updated_at"}),
                "grain": ("user_id",),
                "row_operations": (
                    {"op": "deduplicate", "keys": ("user_id",), "order_by": ({"column": "updated_at"},)},
                ),
            },
            _stg("stg_b", "raw_b", (("id", "id"), ("a_id", "a_id")), ("id",)),
            _stg("stg_c", "raw_c", (("id", "id"),), ("id",)),
        ),
        "intermediate_models": (
            _trans("trans_a", "stg_c", (("id", "id"),), ("id",)),
            _join(
                "join_a",
                "stg_a",
                "stg_b",
                "inner",
                (("user_id", "a_id"),),
                (("left", "user_id", "uid_l"), ("right", "id", "rid")),
                ("uid_l", "rid"),
            ),
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
                "group_by": ({"source": "uid_l", "target": "uid_l"}, {"source": "rid", "target": "rid"}),
                "grain": ("uid_l", "rid"),
                "metrics": ({"name": "cnt2", "function": "count_rows"},),
            },
        ),
    }
    _validate_ok(data)


def test_valid_bridge_mediated_join():
    # many-to-many rel with bridge; join bridge staging to endpoint staging.
    data = {
        "schema_version": "1.0",
        "scenario_id": "t15_bridge",
        "domain": "testdomain",
        "raw_tables": (
            _raw("raw_a", (_id_col("id"),), ("id",)),
            _raw("raw_c", (_id_col("id"),), ("id",)),
            _raw(
                "bridge_t",
                (_fk_col("a_id", "rel_m2m", "left"), _fk_col("b_id", "rel_m2m", "right")),
                (),
            ),
        ),
        "relationships": (
            {
                "name": "rel_m2m",
                "cardinality": "many_to_many",
                "left": {"table": "raw_a", "columns": ("id",)},
                "right": {"table": "raw_c", "columns": ("id",)},
                "bridge": {"table": "bridge_t", "left_columns": ("a_id",), "right_columns": ("b_id",)},
            },
        ),
        "staging_models": (
            _stg("stg_a", "raw_a", (("id", "id"),), ("id",)),
            _stg("stg_c", "raw_c", (("id", "id"),), ("id",)),
            _stg("stg_bridge", "bridge_t", (("a_id", "a_id"), ("b_id", "b_id")), ("a_id", "b_id")),
        ),
        "intermediate_models": (
            _trans("trans_a", "stg_c", (("id", "id"),), ("id",)),
            _join("join_a", "stg_bridge", "stg_a", "inner", (("a_id", "id"),), (("left", "a_id", "aid"), ("left", "b_id", "bid")), ("aid", "bid")),
        ),
        "output_models": (
            _out("out_a", "trans_a"),
            {
                "name": "out_b",
                "source": "join_a",
                "group_by": ({"source": "aid", "target": "aid"}, {"source": "bid", "target": "bid"}),
                "grain": ("aid", "bid"),
                "metrics": ({"name": "cnt2", "function": "count_rows"},),
            },
        ),
    }
    _validate_ok(data)


def test_corrected_positive_fixture_grains_possible():
    from tests.scenario.test_semantic import _base_scenario

    data = _base_scenario()
    v = _validate_ok(data)
    assert v.intermediate_schemas["join_a"]["id"] is not None
    assert set(data["intermediate_models"][1]["grain"]).issubset(
        set(v.intermediate_schemas["join_a"].keys())
    )
