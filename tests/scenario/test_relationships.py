"""Contract tests for RelationshipSpec and bridge (SCENARIO_SPEC §9)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from data_pipeline_diagnostics.scenario.base import ContractModel
from data_pipeline_diagnostics.scenario.relationships import (
    BridgeReference,
    ManyToManyRelationship,
    ManyToOneRelationship,
    OneToManyRelationship,
    OneToOneRelationship,
    RelationshipSpec,
)


class Wrap(ContractModel):
    rel: RelationshipSpec


# ---------------------------------------------------------------------------
# Positive – one per cardinality
# ---------------------------------------------------------------------------


def test_one_to_one_simple():
    rel = OneToOneRelationship(
        name="rel_a",
        left={"table": "t_a", "columns": ("id",)},
        right={"table": "t_b", "columns": ("id",)},
    )
    assert rel.cardinality == "one_to_one"
    assert Wrap(rel=rel).rel.cardinality == "one_to_one"


def test_one_to_many_simple():
    rel = OneToManyRelationship(
        name="rel_one_many",
        left={"table": "t_a", "columns": ("id",)},
        right={"table": "t_b", "columns": ("a_id",)},
    )
    assert rel.cardinality == "one_to_many"


def test_many_to_one_simple():
    rel = ManyToOneRelationship(
        name="rel_many_one",
        left={"table": "t_a", "columns": ("b_id",)},
        right={"table": "t_b", "columns": ("id",)},
    )
    assert rel.cardinality == "many_to_one"


def test_many_to_many_with_bridge_simple():
    rel = ManyToManyRelationship(
        name="rel_mm",
        left={"table": "t_a", "columns": ("id",)},
        right={"table": "t_b", "columns": ("id",)},
        bridge={"table": "bridge_t", "left_columns": ("a_id",), "right_columns": ("b_id",)},
    )
    assert rel.cardinality == "many_to_many"
    assert rel.bridge.table == "bridge_t"


def test_many_to_many_composite_keys():
    rel = ManyToManyRelationship(
        name="rel_mm_comp",
        left={"table": "t_a", "columns": ("id", "seq")},
        right={"table": "t_b", "columns": ("id", "code")},
        bridge={
            "table": "bridge_t",
            "left_columns": ("a_id", "a_seq"),
            "right_columns": ("b_id", "b_code"),
        },
    )
    assert rel.left.columns == ("id", "seq")
    assert rel.bridge.left_columns == ("a_id", "a_seq")
    assert rel.bridge.right_columns == ("b_id", "b_code")


def test_composite_keys_one_to_many():
    rel = OneToManyRelationship(
        name="rel_comp",
        left={"table": "t_a", "columns": ("id", "seq")},
        right={"table": "t_b", "columns": ("a_id", "a_seq")},
    )
    assert rel.left.columns == ("id", "seq")
    assert rel.right.columns == ("a_id", "a_seq")


def test_relationship_with_description():
    rel = OneToOneRelationship(
        name="rel_desc",
        left={"table": "t_a", "columns": ("id",)},
        right={"table": "t_b", "columns": ("id",)},
        description="links a to b",
    )
    assert rel.description == "links a to b"


def test_bridge_reference_direct():
    br = BridgeReference(table="bridge_t", left_columns=("a_id",), right_columns=("b_id",))
    assert br.table == "bridge_t"


# ---------------------------------------------------------------------------
# Discriminated union parsing via JSON dict
# ---------------------------------------------------------------------------


def test_union_parsing_via_wrap():
    assert (
        Wrap(
            rel={
                "name": "r1",
                "cardinality": "one_to_one",
                "left": {"table": "a", "columns": ("id",)},
                "right": {"table": "b", "columns": ("id",)},
            }
        ).rel.cardinality
        == "one_to_one"
    )
    assert (
        Wrap(
            rel={
                "name": "r2",
                "cardinality": "one_to_many",
                "left": {"table": "a", "columns": ("id",)},
                "right": {"table": "b", "columns": ("a_id",)},
            }
        ).rel.cardinality
        == "one_to_many"
    )
    assert (
        Wrap(
            rel={
                "name": "r3",
                "cardinality": "many_to_one",
                "left": {"table": "a", "columns": ("b_id",)},
                "right": {"table": "b", "columns": ("id",)},
            }
        ).rel.cardinality
        == "many_to_one"
    )
    assert (
        Wrap(
            rel={
                "name": "r4",
                "cardinality": "many_to_many",
                "left": {"table": "a", "columns": ("id",)},
                "right": {"table": "b", "columns": ("id",)},
                "bridge": {"table": "br", "left_columns": ("a_id",), "right_columns": ("b_id",)},
            }
        ).rel.cardinality
        == "many_to_many"
    )


# ---------------------------------------------------------------------------
# Negative – local validation
# ---------------------------------------------------------------------------


def test_unknown_cardinality_rejected():
    with pytest.raises(ValidationError, match="union_tag_invalid"):
        Wrap.model_validate(
            {
                "rel": {
                    "name": "r",
                    "cardinality": "unknown",
                    "left": {"table": "a", "columns": ("id",)},
                    "right": {"table": "b", "columns": ("id",)},
                }
            }
        )
    with pytest.raises(ValidationError, match="union_tag_invalid"):
        Wrap.model_validate(
            {
                "rel": {
                    "name": "r",
                    "cardinality": "one_to_few",
                    "left": {"table": "a", "columns": ("id",)},
                    "right": {"table": "b", "columns": ("id",)},
                }
            }
        )


def test_missing_discriminator_rejected():
    with pytest.raises(ValidationError, match="union_tag_not_found"):
        Wrap.model_validate(
            {
                "rel": {
                    "name": "r",
                    "left": {"table": "a", "columns": ("id",)},
                    "right": {"table": "b", "columns": ("id",)},
                }
            }
        )
    with pytest.raises(ValidationError, match="union_tag_not_found"):
        Wrap.model_validate(
            {
                "rel": {
                    "name": "r",
                    "left": {"table": "a", "columns": ("id",)},
                    "right": {"table": "b", "columns": ("id",)},
                    "bridge": {
                        "table": "br",
                        "left_columns": ("a_id",),
                        "right_columns": ("b_id",),
                    },
                }
            }
        )


def test_empty_columns_rejected():
    with pytest.raises(ValidationError):
        OneToOneRelationship(
            name="r", left={"table": "a", "columns": ()}, right={"table": "b", "columns": ("id",)}
        )
    with pytest.raises(ValidationError):
        BridgeReference(table="br", left_columns=(), right_columns=("b_id",))
    with pytest.raises(ValidationError):
        BridgeReference(table="br", left_columns=("a_id",), right_columns=())


def test_duplicate_columns_in_endpoint_rejected():
    with pytest.raises(ValidationError, match="columns must not contain duplicates"):
        OneToOneRelationship(
            name="r",
            left={"table": "a", "columns": ("id", "id")},
            right={"table": "b", "columns": ("id",)},
        )
    with pytest.raises(ValidationError, match="columns must not contain duplicates"):
        from data_pipeline_diagnostics.scenario.types import RelationshipEndpoint as RE

        RE(table="a", columns=("id", "id"))


def test_bridge_overlapping_columns_rejected():
    with pytest.raises(ValidationError, match="left_columns and right_columns must be disjoint"):
        BridgeReference(table="br", left_columns=("a_id",), right_columns=("a_id",))
    with pytest.raises(ValidationError, match="must be disjoint"):
        ManyToManyRelationship(
            name="r",
            left={"table": "a", "columns": ("id",)},
            right={"table": "b", "columns": ("id",)},
            bridge={"table": "br", "left_columns": ("x",), "right_columns": ("x",)},
        )
    # overlapping with composite
    with pytest.raises(ValidationError, match="must be disjoint"):
        BridgeReference(table="br", left_columns=("a", "b"), right_columns=("b", "c"))


def test_bridge_duplicate_within_side_rejected():
    with pytest.raises(ValidationError, match="must be unique within side"):
        BridgeReference(table="br", left_columns=("a", "a"), right_columns=("b",))
    with pytest.raises(ValidationError, match="must be unique within side"):
        BridgeReference(table="br", left_columns=("a",), right_columns=("b", "b"))


def test_extra_field_rejected():
    with pytest.raises(ValidationError, match="extra_forbidden"):
        OneToOneRelationship.model_validate(
            {
                "name": "r",
                "cardinality": "one_to_one",
                "left": {"table": "a", "columns": ("id",)},
                "right": {"table": "b", "columns": ("id",)},
                "extra": "x",
            }
        )
    with pytest.raises(ValidationError, match="extra_forbidden"):
        BridgeReference.model_validate(
            {"table": "br", "left_columns": ("a",), "right_columns": ("b",), "extra": "x"}
        )
    with pytest.raises(ValidationError, match="extra_forbidden"):
        ManyToManyRelationship.model_validate(
            {
                "name": "r",
                "cardinality": "many_to_many",
                "left": {"table": "a", "columns": ("id",)},
                "right": {"table": "b", "columns": ("id",)},
                "bridge": {"table": "br", "left_columns": ("a_id",), "right_columns": ("b_id",)},
                "coverage": 0.5,
            }
        )


def test_coercion_rejected():
    with pytest.raises(ValidationError):
        OneToOneRelationship.model_validate(
            {
                "name": 123,
                "cardinality": "one_to_one",
                "left": {"table": "a", "columns": ("id",)},
                "right": {"table": "b", "columns": ("id",)},
            }
        )
    with pytest.raises(ValidationError):
        BridgeReference.model_validate(
            {"table": 123, "left_columns": ("a",), "right_columns": ("b",)}
        )


# ---------------------------------------------------------------------------
# Boundary – semantic defects must parse
# ---------------------------------------------------------------------------


def test_nonexistent_table_parses():
    # Local validation must not check table existence – should parse
    rel = OneToOneRelationship(
        name="r",
        left={"table": "ghost_a", "columns": ("id",)},
        right={"table": "ghost_b", "columns": ("id",)},
    )
    assert rel.left.table == "ghost_a"
    rel2 = ManyToManyRelationship(
        name="r2",
        left={"table": "ghost_a", "columns": ("id",)},
        right={"table": "ghost_b", "columns": ("id",)},
        bridge={"table": "ghost_br", "left_columns": ("a_id",), "right_columns": ("b_id",)},
    )
    assert rel2.bridge.table == "ghost_br"
    # also via union wrapper
    assert (
        Wrap(
            rel={
                "name": "r3",
                "cardinality": "one_to_many",
                "left": {"table": "nonexistent", "columns": ("id",)},
                "right": {"table": "also_ghost", "columns": ("a_id",)},
            }
        ).rel.name
        == "r3"
    )
