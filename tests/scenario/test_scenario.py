"""Contract tests for Scenario root (SCENARIO_SPEC §6)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from data_pipeline_diagnostics.scenario.models import Scenario

# ---------------------------------------------------------------------------
# Helpers – minimal valid components
# ---------------------------------------------------------------------------

_INT_GEN = {"kind": "integer_range", "min": 1, "max": 10}
_STR_GEN = {"kind": "formatted_id", "digits": 5}


def _raw_table(name: str) -> dict:
    return {
        "name": name,
        "rows": {"min": 1, "max": 10},
        "columns": ({"name": "id", "type": "integer", "generator": _INT_GEN},),
        "primary_key": ("id",),
    }


def _relationship(name: str = "rel_a") -> dict:
    return {
        "name": name,
        "cardinality": "one_to_many",
        "left": {"table": "raw_a", "columns": ("id",)},
        "right": {"table": "raw_b", "columns": ("a_id",)},
    }


def _staging(name: str, source: str) -> dict:
    return {
        "name": name,
        "source": source,
        "columns": ({"source": "id", "target": "id"},),
        "grain": ("id",),
    }


def _transform(name: str, source: str) -> dict:
    return {
        "operation": "transform",
        "name": name,
        "source": source,
        "columns": ({"source": "id", "target": "id"},),
        "grain": ("id",),
    }


def _join(name: str, left: str, right: str) -> dict:
    return {
        "operation": "join",
        "name": name,
        "left": left,
        "right": right,
        "join": {"type": "inner", "on": ({"left": "id", "right": "id"},)},
        "columns": ({"side": "left", "source": "id", "target": "id"},),
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


def _aggregate(name: str, source: str) -> dict:
    return {
        "operation": "aggregate",
        "name": name,
        "source": source,
        "group_by": ({"source": "id", "target": "id"},),
        "metrics": ({"name": "cnt", "function": "count_rows"},),
        "grain": ("id",),
    }


def _output(name: str, source: str) -> dict:
    return {
        "name": name,
        "source": source,
        "group_by": ({"source": "id", "target": "id"},),
        "grain": ("id",),
        "metrics": ({"name": "cnt", "function": "count_rows"},),
    }


def _minimal_scenario() -> dict:
    return {
        "schema_version": "1.0",
        "scenario_id": "test_scenario",
        "domain": "testdomain",
        "raw_tables": (_raw_table("raw_a"), _raw_table("raw_b"), _raw_table("raw_c")),
        "relationships": (_relationship(),),
        "staging_models": (
            _staging("stg_a", "raw_a"),
            _staging("stg_b", "raw_b"),
            _staging("stg_c", "raw_c"),
        ),
        "intermediate_models": (_transform("trans_a", "stg_a"), _join("join_a", "stg_a", "stg_b")),
        "output_models": (_output("out_a", "trans_a"),),
    }


def _maximal_scenario() -> dict:
    return {
        "schema_version": "1.0",
        "scenario_id": "max_scenario",
        "domain": "testdomain",
        "description": "maximal scenario with all 4 layers",
        "raw_tables": (
            _raw_table("raw_a"),
            _raw_table("raw_b"),
            _raw_table("raw_c"),
            _raw_table("raw_d"),
        ),
        "relationships": (_relationship("rel_a"), _relationship("rel_b")),
        "staging_models": (
            _staging("stg_a", "raw_a"),
            _staging("stg_b", "raw_b"),
            _staging("stg_c", "raw_c"),
            _staging("stg_d", "raw_d"),
        ),
        "intermediate_models": (
            _transform("trans_a", "stg_a"),
            _join("join_a", "stg_a", "stg_b"),
            _dedup("dedup_a", "trans_a"),
        ),
        "output_models": (_output("out_a", "trans_a"), _output("out_b", "join_a")),
        "tests": (),
    }


# ---------------------------------------------------------------------------
# Positive
# ---------------------------------------------------------------------------


def test_minimal_valid():
    s = Scenario.model_validate(_minimal_scenario())
    assert s.schema_version == "1.0"
    assert len(s.raw_tables) == 3
    assert len(s.staging_models) == 3
    assert len(s.intermediate_models) == 2
    assert len(s.output_models) == 1


def test_maximal_valid():
    s = Scenario.model_validate(_maximal_scenario())
    assert len(s.raw_tables) == 4
    assert len(s.staging_models) == 4
    assert len(s.intermediate_models) == 3
    assert len(s.output_models) == 2
    assert s.description == "maximal scenario with all 4 layers"


def test_minimal_json_roundtrip():
    import json

    data = _minimal_scenario()
    s = Scenario.model_validate(data)
    dumped = s.model_dump(mode="json")
    s2 = Scenario.model_validate_json(json.dumps(dumped))
    assert s == s2


# ---------------------------------------------------------------------------
# Negative local (MUST fail in Pydantic)
# ---------------------------------------------------------------------------


def test_schema_version_must_be_literal():
    base = _minimal_scenario()
    for bad in ["2.0", "1", 1.0, 1, "1.0 "]:
        with pytest.raises(ValidationError):
            Scenario.model_validate({**base, "schema_version": bad})


def test_scenario_id_invalid():
    base = _minimal_scenario()
    with pytest.raises(ValidationError):
        Scenario.model_validate({**base, "scenario_id": "Invalid"})
    with pytest.raises(ValidationError):
        Scenario.model_validate({**base, "scenario_id": "a" * 101})


def test_raw_tables_bounds():
    base = _minimal_scenario()
    # 2 should fail (min 3)
    with pytest.raises(ValidationError):
        Scenario.model_validate({**base, "raw_tables": (_raw_table("raw_a"), _raw_table("raw_b"))})
    # 5 should fail (max 4)
    with pytest.raises(ValidationError):
        Scenario.model_validate(
            {
                **base,
                "raw_tables": (
                    _raw_table("raw_a"),
                    _raw_table("raw_b"),
                    _raw_table("raw_c"),
                    _raw_table("raw_d"),
                    _raw_table("raw_e"),
                ),
            }
        )


def test_staging_models_bounds():
    base = _minimal_scenario()
    with pytest.raises(ValidationError):
        Scenario.model_validate(
            {**base, "staging_models": (_staging("stg_a", "raw_a"), _staging("stg_b", "raw_b"))}
        )
    with pytest.raises(ValidationError):
        Scenario.model_validate(
            {
                **base,
                "staging_models": (
                    _staging("stg_a", "raw_a"),
                    _staging("stg_b", "raw_b"),
                    _staging("stg_c", "raw_c"),
                    _staging("stg_d", "raw_d"),
                    _staging("stg_e", "raw_e"),
                ),
            }
        )


def test_intermediate_models_bounds():
    base = _minimal_scenario()
    with pytest.raises(ValidationError):
        Scenario.model_validate({**base, "intermediate_models": (_transform("trans_a", "stg_a"),)})
    with pytest.raises(ValidationError):
        Scenario.model_validate(
            {
                **base,
                "intermediate_models": (
                    _transform("t1", "stg_a"),
                    _transform("t2", "stg_a"),
                    _transform("t3", "stg_a"),
                    _transform("t4", "stg_a"),
                ),
            }
        )


def test_output_models_bounds():
    base = _minimal_scenario()
    with pytest.raises(ValidationError):
        Scenario.model_validate({**base, "output_models": ()})
    with pytest.raises(ValidationError):
        Scenario.model_validate(
            {
                **base,
                "output_models": (
                    _output("out_a", "trans_a"),
                    _output("out_b", "trans_a"),
                    _output("out_c", "trans_a"),
                ),
            }
        )


def test_extra_fields_rejected():
    base = _minimal_scenario()
    with pytest.raises(ValidationError, match="extra_forbidden"):
        Scenario.model_validate({**base, "data_seed": 123})
    with pytest.raises(ValidationError, match="extra_forbidden"):
        Scenario.model_validate({**base, "fault_type": "missing_column"})
    with pytest.raises(ValidationError, match="extra_forbidden"):
        Scenario.model_validate({**base, "unknown_field": "x"})


def test_strict_no_coercion():
    base = _minimal_scenario()
    # scenario_id as int should be rejected (strict)
    with pytest.raises(ValidationError):
        Scenario.model_validate({**base, "scenario_id": 123})
    # raw_tables as string "3" should be rejected
    with pytest.raises(ValidationError):
        Scenario.model_validate({**base, "raw_tables": "3"})  # type: ignore[dict-item]


def test_domain_must_be_identifier():
    base = _minimal_scenario()
    with pytest.raises(ValidationError):
        Scenario.model_validate({**base, "domain": "BadDomain"})


# ---------------------------------------------------------------------------
# Boundary – semantically invalid references must parse
# ---------------------------------------------------------------------------


def test_semantically_invalid_references_parse():
    # non-existent tables/models – should parse locally, fail only in semantic
    base = _minimal_scenario()
    base["relationships"] = (
        {
            "name": "rel_ghost",
            "cardinality": "one_to_many",
            "left": {"table": "ghost_a", "columns": ("id",)},
            "right": {"table": "ghost_b", "columns": ("a_id",)},
        },
    )
    base["staging_models"] = (
        {
            "name": "stg_a",
            "source": "ghost_raw",
            "columns": ({"source": "ghost_col", "target": "ghost_col"},),
            "grain": ("ghost_col",),
        },
        {
            "name": "stg_b",
            "source": "ghost_raw2",
            "columns": ({"source": "c", "target": "c"},),
            "grain": ("c",),
        },
        {
            "name": "stg_c",
            "source": "ghost_raw3",
            "columns": ({"source": "c", "target": "c"},),
            "grain": ("c",),
        },
    )
    base["intermediate_models"] = (
        {
            "operation": "transform",
            "name": "trans_ghost",
            "source": "ghost_staging",
            "columns": ({"source": "id", "target": "id"},),
            "grain": ("id",),
        },
        {
            "operation": "join",
            "name": "join_ghost",
            "left": "ghost1",
            "right": "ghost2",
            "join": {"type": "inner", "on": ({"left": "id", "right": "id"},)},
            "columns": ({"side": "left", "source": "id", "target": "id"},),
            "grain": ("id",),
        },
    )
    base["output_models"] = (
        {
            "name": "out_ghost",
            "source": "ghost_intermediate",
            "group_by": ({"source": "id", "target": "id"},),
            "grain": ("id",),
            "metrics": ({"name": "cnt", "function": "count_rows"},),
        },
    )
    s = Scenario.model_validate(base)
    assert s.relationships[0].name == "rel_ghost"
    assert s.staging_models[0].source == "ghost_raw"
