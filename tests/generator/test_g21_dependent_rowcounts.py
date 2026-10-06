"""G21 tests: dependent row-count capacities (§9.3 extension).

Row-count conditioning must use *sampled* parent counts, not declared
maxima: ``insurance_fleet_001`` succeeds on seed 0 but its 60..180 terms
against 20..60 policies fail on seeds 1/7 when the sampled combination
exceeds ``3 * policies``. These tests pin sampled-count capacities,
reachable-upper pre-checks, deterministic resampling, the constructive
fallback, and the seed-0 end-to-end regression (36 policies, 95 terms).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import data_pipeline_diagnostics.generator.relationships as relationships_module
from data_pipeline_diagnostics.generator.cache import prepare_clean_instance
from data_pipeline_diagnostics.generator.raw_constraints import GenerationFailure
from data_pipeline_diagnostics.generator.raw_plan import (
    ColumnPlan,
    FkGroupPlan,
    TablePlan,
    _pk_capacity,
    _PkCapacity,
    build_raw_plan,
    composite_pk_tier,
    generate_raw_data,
)
from data_pipeline_diagnostics.generator.relationships import (
    CountLink,
    _apply_count_fallback,
    sample_row_counts,
)
from data_pipeline_diagnostics.generator.rng import rows_stream_name
from data_pipeline_diagnostics.generator.rng import stream as make_stream
from data_pipeline_diagnostics.scenario.generators import (
    CategoricalGenerator,
    FloatRangeGenerator,
    ForeignKeyGenerator,
)
from data_pipeline_diagnostics.scenario.parsing import parse_scenario_file
from data_pipeline_diagnostics.scenario.semantic import validate_semantics
from data_pipeline_diagnostics.scenario.types import DataType, RowCount

REPO = Path(__file__).resolve().parents[2]


def _validated(name: str):
    return validate_semantics(parse_scenario_file(REPO / "scenarios" / name))


def _insurance():
    return _validated("insurance_fleet_001.json")


def test_insurance_terms_within_sampled_capacity():
    for seed in (0, 1, 7):
        data = generate_raw_data(_insurance(), seed)
        npol = len(data["raw_policies"])
        nterm = len(data["raw_terms"])
        nclaim = len(data["raw_claims"])
        assert 20 <= npol <= 60
        assert 60 <= nterm <= 180
        assert 150 <= nclaim <= 500
        assert nterm <= 3 * npol
        keys = [(r["policy_id"], r["term_no"]) for r in data["raw_terms"]]
        assert len(keys) == len(set(keys))
        puniverse = {r["policy_id"] for r in data["raw_policies"]}
        assert all(k[0] in puniverse for k in keys)
        tuniverse = set(keys)
        assert all(
            (r["policy_id"], r["term_no"]) in tuniverse
            for r in data["raw_claims"]
            if r["policy_id"] is not None and r["term_no"] is not None
        )


def test_insurance_seed_zero_first_proposals_retained():
    first = generate_raw_data(_insurance(), 0)
    assert len(first["raw_policies"]) == 36
    assert len(first["raw_terms"]) == 95
    assert generate_raw_data(_insurance(), 0) == first


def test_exact_child_resolves_parent_to_maximum():
    rows = {"child": RowCount(min=180, max=180), "parent": RowCount(min=20, max=60)}
    counts = sample_row_counts(
        scenario_id="g21-exact",
        data_seed=11,
        rows=rows,
        capacity_constraints=[_PkCapacity("child", 3, ("parent",))],
    )
    assert counts == {"child": 180, "parent": 60}


def test_exact_child_impossible_parent_max_fails_early(monkeypatch):
    def _boom(*args, **kwargs):
        raise AssertionError("no RNG draws before the pre-check")

    monkeypatch.setattr(relationships_module, "stream", _boom)
    rows = {"child": RowCount(min=180, max=180), "parent": RowCount(min=20, max=50)}
    with pytest.raises(GenerationFailure) as exc_info:
        sample_row_counts(
            scenario_id="g21-exact",
            data_seed=11,
            rows=rows,
            capacity_constraints=[_PkCapacity("child", 3, ("parent",))],
        )
    assert exc_info.value.reason == "row-count-capacity-exceeded"


def test_fixed_fixed_and_constant_cap_cases():
    rows = {"child": RowCount(min=100, max=100), "parent": RowCount(min=40, max=40)}
    assert sample_row_counts(
        scenario_id="g21-fixed",
        data_seed=11,
        rows=rows,
        capacity_constraints=[_PkCapacity("child", 3, ("parent",))],
    ) == {"child": 100, "parent": 40}
    rows = {"t": RowCount(min=1, max=20)}
    first = sample_row_counts(
        scenario_id="g21-const",
        data_seed=11,
        rows=rows,
        capacity_constraints=[_PkCapacity("t", 9, ())],
    )
    assert first["t"] <= 9
    assert first == sample_row_counts(
        scenario_id="g21-const",
        data_seed=11,
        rows=rows,
        capacity_constraints=[_PkCapacity("t", 9, ())],
    )
    with pytest.raises(GenerationFailure) as exc_info:
        sample_row_counts(
            scenario_id="g21-const",
            data_seed=11,
            rows={"t": RowCount(min=10, max=10)},
            capacity_constraints=[_PkCapacity("t", 9, ())],
        )
    assert exc_info.value.reason == "row-count-capacity-exceeded"


def test_descriptor_counts_fk_tuple_once():
    validated = _insurance()
    plan = build_raw_plan(validated)
    table = next(t for t in plan.tables if t.name == "raw_terms")
    desc = _pk_capacity(table, [g for g in plan.fk_groups if g.dependent_table == "raw_terms"])
    assert desc == _PkCapacity(
        dependent_table="raw_terms", multiplier=3, parent_tables=("raw_policies",)
    )

    def leaf(name, config):
        return ColumnPlan(
            name=name,
            kind="leaf",
            config=config,
            type=DataType.string,
            nullable=False,
            null_probability=0.0,
            unique=False,
            value_stream=f"values/t/{name}",
            null_stream=f"nulls/t/{name}",
        )

    def fkcol(name, rel, side):
        return ColumnPlan(
            name=name,
            kind="foreign_key",
            config=ForeignKeyGenerator(relationship=rel, target_side=side),
            type=DataType.string,
            nullable=False,
            null_probability=0.0,
            unique=False,
            relationship=rel,
            target_side=side,
            value_stream=f"foreign_key/{rel}/t/{side}",
            null_stream=f"nulls/foreign_key/{rel}/t/{side}",
        )

    def group(rel, table, side, cols, target):
        return FkGroupPlan(
            relationship=rel,
            dependent_table=table,
            target_side=side,
            dependent_columns=tuple(cols),
            target_table=target,
            target_columns=tuple(cols),
            null_terms=tuple((False, 0.0) for _ in cols),
            without_replacement=False,
            value_stream=f"foreign_key/{rel}/{table}/{side}",
            null_stream=f"nulls/foreign_key/{rel}/{table}/{side}",
        )

    cols = [
        fkcol("a", "r1", "left"),
        fkcol("b", "r2", "left"),
        leaf("c", CategoricalGenerator(values=("x", "y"))),
    ]
    table = TablePlan(
        name="t",
        rows=RowCount(min=1, max=10),
        columns=tuple(cols),
        declaration_column_order=("a", "b", "c"),
        primary_key=("a", "b", "c"),
    )
    groups = [group("r1", "t", "left", ["a"], "u"), group("r2", "t", "left", ["b"], "u")]
    desc = _pk_capacity(table, groups)
    assert desc is not None
    assert desc.parent_tables == ("u", "u")
    assert desc.multiplier == 2

    composite_cols = [
        fkcol("a", "r1", "left"),
        fkcol("b", "r1", "left"),
        leaf("c", CategoricalGenerator(values=("x", "y"))),
    ]
    composite_table = TablePlan(
        name="t",
        rows=RowCount(min=1, max=10),
        columns=tuple(composite_cols),
        declaration_column_order=("a", "b", "c"),
        primary_key=("a", "b", "c"),
    )
    composite_desc = _pk_capacity(composite_table, [group("r1", "t", "left", ["a", "b"], "u")])
    assert composite_desc is not None
    assert composite_desc.parent_tables == ("u",)
    assert composite_desc.multiplier == 2


def test_descriptor_unknown_domain_has_no_bound():
    cols = [
        ColumnPlan(
            name="a",
            kind="leaf",
            config=CategoricalGenerator(values=("x",)),
            type=DataType.string,
            nullable=False,
            null_probability=0.0,
            unique=False,
            value_stream="values/t/a",
            null_stream="nulls/t/a",
        ),
        ColumnPlan(
            name="b",
            kind="leaf",
            config=FloatRangeGenerator(min=0.0, max=1.0),
            type=DataType.float,
            nullable=False,
            null_probability=0.0,
            unique=False,
            value_stream="values/t/b",
            null_stream="nulls/t/b",
        ),
    ]
    table = TablePlan(
        name="t",
        rows=RowCount(min=1, max=10),
        columns=tuple(cols),
        declaration_column_order=("a", "b"),
        primary_key=("a", "b"),
    )
    assert composite_pk_tier(table, []) == "retry"
    assert _pk_capacity(table, []) is None
    single = TablePlan(
        name="s",
        rows=RowCount(min=1, max=10),
        columns=(cols[0],),
        declaration_column_order=("a",),
        primary_key=("a",),
    )
    assert composite_pk_tier(single, []) is None
    assert _pk_capacity(single, []) is None


def test_chained_capacities_propagate():
    rows = {
        "gp": RowCount(min=10, max=20),
        "mid": RowCount(min=5, max=100),
        "leaf": RowCount(min=120, max=120),
    }
    caps = [
        _PkCapacity("mid", 2, ("gp",)),
        _PkCapacity("leaf", 3, ("mid",)),
    ]
    counts = sample_row_counts(
        scenario_id="g21-chain", data_seed=11, rows=rows, capacity_constraints=caps
    )
    assert counts == {"gp": 20, "mid": 40, "leaf": 120}
    assert counts == sample_row_counts(
        scenario_id="g21-chain", data_seed=11, rows=rows, capacity_constraints=caps
    )


def test_chained_incompatible_minima_fail_before_sampling(monkeypatch):
    def _boom(*args, **kwargs):
        raise AssertionError("no RNG draws before the pre-check")

    monkeypatch.setattr(relationships_module, "stream", _boom)
    rows = {
        "gp": RowCount(min=10, max=20),
        "mid": RowCount(min=5, max=100),
        "leaf": RowCount(min=121, max=121),
    }
    caps = [
        _PkCapacity("mid", 2, ("gp",)),
        _PkCapacity("leaf", 3, ("mid",)),
    ]
    with pytest.raises(GenerationFailure) as exc_info:
        sample_row_counts(
            scenario_id="g21-chain", data_seed=11, rows=rows, capacity_constraints=caps
        )
    assert exc_info.value.reason == "row-count-capacity-exceeded"


def test_fallback_sets_uppers_without_draws(monkeypatch):
    monkeypatch.setattr(relationships_module, "ROW_COUNT_RETRY_LIMIT", 0)
    rows = {
        "child": RowCount(min=3600, max=3600),
        "p1": RowCount(min=20, max=60),
        "p2": RowCount(min=20, max=60),
        "q": RowCount(min=60, max=60),
        "u": RowCount(min=4, max=6),
    }
    caps = [_PkCapacity("child", 1, ("p1", "p2"))]
    links = [CountLink(relationship="r", dependent_table="p1", target_table="q")]
    counts = sample_row_counts(
        scenario_id="g21-fallback",
        data_seed=11,
        rows=rows,
        links=links,
        capacity_constraints=caps,
    )
    assert counts["child"] == 3600
    assert (counts["p1"], counts["p2"]) == (60, 60)
    assert counts["q"] == 60
    expected_u = make_stream("g21-fallback", 11, rows_stream_name("u")).randint(4, 6)
    assert counts["u"] == expected_u
    assert counts == sample_row_counts(
        scenario_id="g21-fallback",
        data_seed=11,
        rows=rows,
        links=links,
        capacity_constraints=caps,
    )


def test_fallback_performs_no_draws(monkeypatch):
    def _boom(*args, **kwargs):
        raise AssertionError("fallback must not draw")

    monkeypatch.setattr(relationships_module, "stream", _boom)

    rows = {
        "child": RowCount(min=3600, max=3600),
        "p1": RowCount(min=20, max=60),
        "p2": RowCount(min=20, max=60),
    }
    caps = [_PkCapacity("child", 1, ("p1", "p2"))]
    counts = {"child": 3600, "p1": 25, "p2": 25}
    upper = {"child": 3600, "p1": 60, "p2": 60}
    _apply_count_fallback(counts, upper, rows, [], {}, caps)
    assert counts == {"child": 3600, "p1": 60, "p2": 60}


def _stub_always_max_rng(calls):
    """RNG stub whose every draw returns the interval maximum (always violating
    a tight dependent capacity), recording each draw in ``calls``."""

    class _StubRng:
        def randint(self, low, high):
            calls.append((low, high))
            return high

    return lambda *args, **kwargs: _StubRng()


def test_resampling_budget_counts_rng_draws(monkeypatch):
    calls = []
    monkeypatch.setattr(relationships_module, "stream", _stub_always_max_rng(calls))
    monkeypatch.setattr(relationships_module, "ROW_COUNT_RETRY_LIMIT", 3)
    rows = {"dep": RowCount(min=1, max=200), "parent": RowCount(min=30, max=30)}
    counts = sample_row_counts(
        scenario_id="g21-budget",
        data_seed=11,
        rows=rows,
        capacity_constraints=[_PkCapacity("dep", 3, ("parent",))],
    )
    assert counts == {"dep": 90, "parent": 30}
    assert len(calls) == 4


def test_zero_retry_limit_falls_back_without_resampling(monkeypatch):
    calls = []
    monkeypatch.setattr(relationships_module, "stream", _stub_always_max_rng(calls))
    monkeypatch.setattr(relationships_module, "ROW_COUNT_RETRY_LIMIT", 0)
    rows = {"dep": RowCount(min=1, max=200), "parent": RowCount(min=30, max=30)}
    counts = sample_row_counts(
        scenario_id="g21-budget-zero",
        data_seed=11,
        rows=rows,
        capacity_constraints=[_PkCapacity("dep", 3, ("parent",))],
    )
    assert counts == {"dep": 90, "parent": 30}
    assert len(calls) == 1


def test_invalid_capacity_references():
    rows = {"t": RowCount(min=1, max=2)}
    with pytest.raises(ValueError):
        sample_row_counts(
            scenario_id="g21-bad",
            data_seed=11,
            rows=rows,
            capacity_constraints=[_PkCapacity("nope", 1, ())],
        )
    with pytest.raises(ValueError):
        sample_row_counts(
            scenario_id="g21-bad",
            data_seed=11,
            rows=rows,
            capacity_constraints=[_PkCapacity("t", 1, ("nope",))],
        )


def test_count_conditioning_preserves_unrelated_streams():
    rows = {
        "dep": RowCount(min=1, max=200),
        "parent": RowCount(min=30, max=30),
        "u": RowCount(min=4, max=6),
    }
    counts = sample_row_counts(
        scenario_id="g21-iso",
        data_seed=11,
        rows=rows,
        capacity_constraints=[_PkCapacity("dep", 3, ("parent",))],
    )
    assert counts["dep"] <= 90
    expected_u = make_stream("g21-iso", 11, rows_stream_name("u")).randint(4, 6)
    assert counts["u"] == expected_u


def test_insurance_end_to_end_seeds_and_cache(tmp_path):
    validated = _insurance()
    inst0 = prepare_clean_instance(validated, 0, tmp_path / "cache")
    assert not inst0.cache_hit
    inst1 = prepare_clean_instance(validated, 1, tmp_path / "cache")
    assert not inst1.cache_hit
    inst7 = prepare_clean_instance(validated, 7, tmp_path / "cache")
    assert not inst7.cache_hit
    repeat = prepare_clean_instance(validated, 0, tmp_path / "cache")
    assert repeat.cache_hit
    assert repeat.instance_digest == inst0.instance_digest
    record = json.loads((inst0.instance_dir / "instance_record.json").read_text())
    assert record["versions"]["raw_generator"] == "1.3.0"
    assert record["identity"]["instance_digest"] == inst0.instance_digest
