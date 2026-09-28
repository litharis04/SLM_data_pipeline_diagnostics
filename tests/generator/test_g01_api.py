"""G01 boundary tests: ValidatedScenario-only API + data_seed contract.

Technique notes (none deferred by the spec at this stage): the facade treats
a ``str`` source as a filesystem path when it points at an existing file and
as JSON content otherwise; ``data_seed`` uses strict ``type(x) is int`` so
``bool`` is rejected. Since G19, ``prepare_clean_instance`` really builds
(check-then-build), so acceptance paths use the tiny ``minimal.json``
(repaired in G17: validates and builds green) with isolated ``tmp_path``
cache dirs; the facade's bytes call hits the path call's cache entry.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from data_pipeline_diagnostics.generator import (
    MAX_DATA_SEED,
    build_raw_plan,
    prepare_clean_instance,
    prepare_clean_instance_from_json,
    render_dbt_project,
)
from data_pipeline_diagnostics.scenario.errors import (
    ScenarioParseError,
    SemanticValidationError,
)
from data_pipeline_diagnostics.scenario.parsing import parse_scenario_json
from data_pipeline_diagnostics.scenario.semantic import validate_semantics

FIXTURES = Path(__file__).resolve().parents[1] / "scenario" / "fixtures"
MINIMAL = FIXTURES / "valid" / "minimal.json"
INVALID = FIXTURES / "invalid" / "duplicate_column.json"


def _bare_scenario():
    return parse_scenario_json(MINIMAL.read_bytes())


def _validated():
    return validate_semantics(parse_scenario_json(MINIMAL.read_bytes()))


def test_all_functions_reject_bare_scenario_dict_and_path():
    bare = _bare_scenario()
    for fn, args in (
        (build_raw_plan, ()),
        (render_dbt_project, (Path("/tmp/dbt"),)),
        (prepare_clean_instance, (0, Path("/tmp/cache"))),
    ):
        with pytest.raises(TypeError):
            fn(bare, *args)
        with pytest.raises(TypeError):
            fn({"scenario_id": "x"}, *args)
        with pytest.raises(TypeError):
            fn("tests/scenario/fixtures/valid/minimal.json", *args)


def test_data_seed_bounds(tmp_path):
    validated = _validated()
    with pytest.raises(ValueError):
        prepare_clean_instance(validated, -1, tmp_path / "c1")
    with pytest.raises(ValueError):
        prepare_clean_instance(validated, 2**63, tmp_path / "c2")
    assert MAX_DATA_SEED == 2**63 - 1
    assert prepare_clean_instance(validated, 0, tmp_path / "cache").data_seed == 0
    assert prepare_clean_instance(validated, 2**63 - 1, tmp_path / "cache").data_seed == 2**63 - 1


def test_facade_reaches_compiler_entry_on_valid_fixture(tmp_path):
    inst = prepare_clean_instance_from_json(MINIMAL, 0, tmp_path)
    assert inst.data_seed == 0
    assert not inst.cache_hit
    raw = MINIMAL.read_bytes()
    inst2 = prepare_clean_instance_from_json(raw, 0, tmp_path)
    assert inst2.scenario_id == inst.scenario_id
    assert inst2.cache_hit


def test_facade_validates_before_build():
    with pytest.raises((ScenarioParseError, SemanticValidationError)):
        prepare_clean_instance_from_json(INVALID, 0, Path("/tmp/cache"))
