"""G01 boundary tests: ValidatedScenario-only API + data_seed contract.

Technique notes (none deferred by the spec at this stage): the facade treats
a ``str`` source as a filesystem path when it points at an existing file and
as JSON content otherwise; ``data_seed`` uses strict ``type(x) is int`` so
``bool`` is rejected.
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
# Minimal fixture predates current semantic rules (fails E131); use an accepted
# corpus scenario for the valid facade path.
VALID_CORPUS = Path(__file__).resolve().parents[2] / "scenarios" / "agriculture_coop_001.json"


def _bare_scenario():
    return parse_scenario_json(MINIMAL.read_bytes())


def _validated():
    return validate_semantics(parse_scenario_json(VALID_CORPUS.read_bytes()))


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


def test_data_seed_bounds():
    validated = _validated()
    with pytest.raises(ValueError):
        prepare_clean_instance(validated, -1, Path("/tmp/cache"))
    with pytest.raises(ValueError):
        prepare_clean_instance(validated, 2**63, Path("/tmp/cache"))
    assert MAX_DATA_SEED == 2**63 - 1
    assert prepare_clean_instance(validated, 0, Path("/tmp/cache")).data_seed == 0
    assert prepare_clean_instance(validated, 2**63 - 1, Path("/tmp/cache")).data_seed == 2**63 - 1


def test_facade_reaches_compiler_entry_on_valid_fixture(tmp_path):
    inst = prepare_clean_instance_from_json(VALID_CORPUS, 0, tmp_path)
    assert inst.data_seed == 0
    raw = VALID_CORPUS.read_bytes()
    inst2 = prepare_clean_instance_from_json(raw, 0, tmp_path)
    assert inst2.scenario_id == inst.scenario_id


def test_facade_validates_before_build():
    with pytest.raises((ScenarioParseError, SemanticValidationError)):
        prepare_clean_instance_from_json(INVALID, 0, Path("/tmp/cache"))
