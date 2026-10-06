"""G07 tests: row-count conditioning and FK-tuple sampling.

Technique notes: samplers take the caller-built target universe explicitly;
table assembly (G08) only feeds it. The conditioning test uses seed 2, whose
first proposals (dep=5, tgt=3) violate the 1:1 link, proving the resample
path engages (final counts differ from first draws yet stay deterministic).
Uninvolved streams are drawn exactly once (pinned against fresh streams).
"""

from __future__ import annotations

import pytest

from data_pipeline_diagnostics.generator.raw_constraints import (
    GenerationFailure,
    generate_composite_null_mask,
)
from data_pipeline_diagnostics.generator.relationships import (
    CountLink,
    direct_fk_plan,
    resolve_relationships,
    sample_fk_tuples,
    sample_row_counts,
)
from data_pipeline_diagnostics.generator.rng import (
    rows_stream_name,
    stream,
)
from data_pipeline_diagnostics.scenario.generators import (
    ForeignKeyGenerator,
    IntegerRangeGenerator,
)
from data_pipeline_diagnostics.scenario.raw import RawColumn, RawTable
from data_pipeline_diagnostics.scenario.relationships import (
    BridgeReference,
    ManyToManyRelationship,
    ManyToOneRelationship,
    OneToManyRelationship,
    OneToOneRelationship,
)
from data_pipeline_diagnostics.scenario.types import RelationshipEndpoint, RowCount

SCENARIO = "g07-test"


def _sample(**kwargs):
    base = {
        "scenario_id": SCENARIO,
        "data_seed": 11,
        "relationship": "rel_a",
        "dependent_table": "orders",
        "target_side": "right",
    }
    base.update(kwargs)
    return sample_fk_tuples(**base)


def test_one_to_many_membership_and_repeatability():
    universe = [(i,) for i in range(5)]
    mask = [i % 3 == 0 for i in range(20)]
    first = _sample(universe=universe, row_count=20, null_mask=mask, without_replacement=False)
    assert (
        _sample(universe=universe, row_count=20, null_mask=mask, without_replacement=False) == first
    )
    assert all(t is None or t in universe for t in first)
    assert any(t is None for t in first) and any(t is not None for t in first)


def test_one_to_one_distinct_and_too_small():
    universe = [(i,) for i in range(4)]
    mask = [False] * 4
    sampled = _sample(universe=universe, row_count=4, null_mask=mask, without_replacement=True)
    assert sorted(sampled) == universe
    with pytest.raises(GenerationFailure) as exc_info:
        _sample(universe=universe, row_count=5, null_mask=[False] * 5, without_replacement=True)
    assert exc_info.value.reason == "target-universe-too-small"


def test_empty_universe_and_null_in_universe():
    with pytest.raises(GenerationFailure) as exc_info:
        _sample(universe=[], row_count=2, null_mask=[False] * 2, without_replacement=False)
    assert exc_info.value.reason == "empty-target-universe"
    with pytest.raises(GenerationFailure) as exc_info:
        _sample(
            universe=[(1,), (None,)],
            row_count=1,
            null_mask=[False],
            without_replacement=False,
        )
    assert exc_info.value.reason == "target-universe-contains-null"


def test_bridge_sides_resolve_independently():
    left_universe = [(f"l{i}",) for i in range(3)]
    right_universe = [(f"r{i}",) for i in range(4)]
    left = _sample(
        dependent_table="bridge",
        target_side="left",
        universe=left_universe,
        row_count=10,
        null_mask=[False] * 10,
        without_replacement=False,
    )
    right = _sample(
        dependent_table="bridge",
        target_side="right",
        universe=right_universe,
        row_count=10,
        null_mask=[False] * 10,
        without_replacement=False,
    )
    assert all(t in left_universe for t in left)
    assert all(t in right_universe for t in right)
    assert left == _sample(
        dependent_table="bridge",
        target_side="left",
        universe=left_universe,
        row_count=10,
        null_mask=[False] * 10,
        without_replacement=False,
    )


def test_composite_atomicity_with_g06_null_layer():
    mask = generate_composite_null_mask(
        scenario_id=SCENARIO,
        data_seed=11,
        relationship="rel_a",
        dependent_table="orders",
        target_side="left",
        row_count=200,
        components=[(True, 0.4), (True, 0.4)],
    )
    universe = [(i, f"k{i}") for i in range(10)]
    sampled = _sample(
        target_side="left",
        universe=universe,
        row_count=200,
        null_mask=mask,
        without_replacement=False,
    )
    assert all(t is None or (len(t) == 2 and t in universe) for t in sampled)
    assert any(t is None for t in sampled)


def test_row_count_conditioning_materializes():
    rows = {
        "dep": RowCount(min=4, max=6),
        "tgt": RowCount(min=1, max=6),
        "other": RowCount(min=4, max=6),
        "exact": RowCount(min=7, max=7),
    }
    links = [CountLink(relationship="rel_a", dependent_table="dep", target_table="tgt")]
    counts = sample_row_counts(scenario_id=SCENARIO, data_seed=2, rows=rows, links=links)
    assert counts["dep"] <= counts["tgt"]
    assert counts["exact"] == 7
    # Same seed -> same counts; first proposals (dep=5, tgt=3) violated, so the
    # resample path necessarily engaged.
    assert sample_row_counts(scenario_id=SCENARIO, data_seed=2, rows=rows, links=links) == counts
    assert (counts["dep"], counts["tgt"]) != (5, 3)
    # Uninvolved ranged table drawn exactly once.
    expected_other = stream(SCENARIO, 2, rows_stream_name("other")).randint(4, 6)
    assert counts["other"] == expected_other


def test_row_count_unresolvable_and_unknown_table():
    with pytest.raises(GenerationFailure) as exc_info:
        sample_row_counts(
            scenario_id=SCENARIO,
            data_seed=11,
            rows={"dep": RowCount(min=5, max=5), "tgt": RowCount(min=3, max=3)},
            links=[CountLink(relationship="r", dependent_table="dep", target_table="tgt")],
        )
    assert exc_info.value.reason == "row-count-unresolvable"
    with pytest.raises(ValueError):
        sample_row_counts(
            scenario_id=SCENARIO,
            data_seed=11,
            rows={"dep": RowCount(min=1, max=2)},
            links=[CountLink(relationship="r", dependent_table="dep", target_table="nope")],
        )


def _tables_for_one_to_one():
    left = RawTable(
        name="customers",
        rows=RowCount(min=1, max=5),
        columns=(
            RawColumn(
                name="id",
                type="integer",
                generator=IntegerRangeGenerator(min=1, max=9),
            ),
        ),
    )
    right = RawTable(
        name="profiles",
        rows=RowCount(min=1, max=5),
        columns=(
            RawColumn(
                name="customer_fk",
                type="integer",
                nullable=True,
                null_probability=0.0,
                generator=ForeignKeyGenerator(relationship="rel_a", target_side="left"),
            ),
        ),
    )
    return left, right


def test_resolve_and_direct_plans():
    left, right = _tables_for_one_to_one()
    rel = OneToOneRelationship(
        name="rel_a",
        left=RelationshipEndpoint(table="customers", columns=("id",)),
        right=RelationshipEndpoint(table="profiles", columns=("customer_fk",)),
    )
    resolved = resolve_relationships([rel], {"customers": left, "profiles": right})["rel_a"]
    assert (resolved.dependent_table, resolved.target_table) == ("profiles", "customers")
    plan = direct_fk_plan(resolved)
    assert (plan.dependent_table, plan.target_side, plan.without_replacement) == (
        "profiles",
        "left",
        True,
    )

    rel_1n = OneToManyRelationship(
        name="rel_1n",
        left=RelationshipEndpoint(table="customers", columns=("id",)),
        right=RelationshipEndpoint(table="profiles", columns=("customer_fk",)),
    )
    plan_1n = direct_fk_plan(resolve_relationships([rel_1n], {})["rel_1n"])
    assert (plan_1n.dependent_table, plan_1n.target_side, plan_1n.without_replacement) == (
        "profiles",
        "left",
        False,
    )

    rel_n1 = ManyToOneRelationship(
        name="rel_n1",
        left=RelationshipEndpoint(table="profiles", columns=("customer_fk",)),
        right=RelationshipEndpoint(table="customers", columns=("id",)),
    )
    plan_n1 = direct_fk_plan(resolve_relationships([rel_n1], {})["rel_n1"], unique_dependent=True)
    assert (plan_n1.dependent_table, plan_n1.target_side, plan_n1.without_replacement) == (
        "profiles",
        "right",
        True,
    )

    rel_mm = ManyToManyRelationship(
        name="rel_mm",
        left=RelationshipEndpoint(table="customers", columns=("id",)),
        right=RelationshipEndpoint(table="profiles", columns=("customer_fk",)),
        bridge=BridgeReference(table="bridge", left_columns=("c_id",), right_columns=("p_fk",)),
    )
    with pytest.raises(ValueError):
        direct_fk_plan(resolve_relationships([rel_mm], {})["rel_mm"])


def test_unresolved_one_to_one_is_boundary_failure():
    rel = OneToOneRelationship(
        name="rel_x",
        left=RelationshipEndpoint(table="a", columns=("id",)),
        right=RelationshipEndpoint(table="b", columns=("fk",)),
    )
    resolved = resolve_relationships([rel], {})["rel_x"]
    with pytest.raises(GenerationFailure) as exc_info:
        direct_fk_plan(resolved)
    assert exc_info.value.reason == "unresolved-relationship"
