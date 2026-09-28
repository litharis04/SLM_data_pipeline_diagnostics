"""G08 tests: RawPlan ordering, orchestration, and boundary limits.

Technique notes: the diamond case augments a real accepted scenario with
three appended template columns (unused downstream, so revalidation holds).
The reorder case reverses ``raw_tables`` — stream isolation (not plan order)
carries data identity, so per-table data must be exactly equal. Purity pins
``build_raw_plan`` against global-RNG consumption and filesystem writes.
"""

from __future__ import annotations

import copy
import json
import random as py_random
from dataclasses import fields, is_dataclass
from pathlib import Path, PurePath

import pytest

from data_pipeline_diagnostics.generator import build_raw_plan
from data_pipeline_diagnostics.generator.raw_plan import execute_raw_plan, generate_raw_data
from data_pipeline_diagnostics.scenario.parsing import parse_scenario_json
from data_pipeline_diagnostics.scenario.semantic import validate_semantics

REPO = Path(__file__).resolve().parents[2]
BASE_SCENARIO = REPO / "scenarios" / "agriculture_coop_001.json"

_BANNED = ("dbt", "sql", "path", "fault")


def _base_validated(seed_json: bool = False):
    data = json.loads(BASE_SCENARIO.read_text())
    if seed_json:
        return data
    return validate_semantics(parse_scenario_json(json.dumps(data)))


def _diamond_validated():
    data = json.loads(BASE_SCENARIO.read_text())
    data["scenario_id"] = "diamond_probe_001"
    for table in data["raw_tables"]:
        if table["name"] == "raw_varieties":
            table["columns"].extend(
                [
                    {
                        "name": "t_one",
                        "type": "string",
                        "nullable": False,
                        "null_probability": 0.0,
                        "unique": False,
                        "generator": {
                            "kind": "template_string",
                            "template": "{variety_name}_x",
                        },
                    },
                    {
                        "name": "t_two",
                        "type": "string",
                        "nullable": False,
                        "null_probability": 0.0,
                        "unique": False,
                        "generator": {
                            "kind": "template_string",
                            "template": "{variety_name}_y",
                        },
                    },
                    {
                        "name": "t_three",
                        "type": "string",
                        "nullable": False,
                        "null_probability": 0.0,
                        "unique": False,
                        "generator": {
                            "kind": "template_string",
                            "template": "{t_one}|{t_two}",
                        },
                    },
                ]
            )
    return validate_semantics(parse_scenario_json(json.dumps(data)))


def test_template_diamond_order_and_values():
    validated = _diamond_validated()
    plan = build_raw_plan(validated)
    varieties = next(t for t in plan.tables if t.name == "raw_varieties")
    names = [c.name for c in varieties.columns]
    assert names.index("variety_name") < names.index("t_one")
    assert names.index("variety_name") < names.index("t_two")
    assert names.index("t_one") < names.index("t_three")
    assert names.index("t_two") < names.index("t_three")

    data = execute_raw_plan(plan, 11)["raw_varieties"]
    assert data
    for row in data:
        assert row["t_one"] == f"{row['variety_name']}_x"
        assert row["t_two"] == f"{row['variety_name']}_y"
        assert row["t_three"] == f"{row['t_one']}|{row['t_two']}"


def test_fk_chain_samples_existing_keys():
    validated = _base_validated()
    data = generate_raw_data(validated, 11)
    variety_universe = {row["variety_code"] for row in data["raw_varieties"]}
    seed_universe = {row["seed_id"] for row in data["raw_seeds"]}
    assert variety_universe and seed_universe
    for row in data["raw_seeds"]:
        assert row["variety_code"] is None or row["variety_code"] in variety_universe
    for row in data["raw_plantings"]:
        assert row["seed_id"] in seed_universe
    # Deterministic across calls.
    assert generate_raw_data(validated, 11) == data


def test_reordered_tables_yield_identical_data():
    data = _base_validated(seed_json=True)
    reversed_data = copy.deepcopy(data)
    reversed_data["raw_tables"] = list(reversed(reversed_data["raw_tables"]))
    first = generate_raw_data(validate_semantics(parse_scenario_json(json.dumps(data))), 11)
    second = generate_raw_data(
        validate_semantics(parse_scenario_json(json.dumps(reversed_data))), 11
    )
    assert first == second


def _walk(value, seen: set[int]):
    if id(value) in seen:
        return
    seen.add(id(value))
    if isinstance(value, PurePath):
        raise AssertionError(f"RawPlan holds a path: {value}")
    if is_dataclass(value) and not isinstance(value, type):
        for f in fields(value):
            assert not any(b in f.name.lower() for b in _BANNED), f.name
            _walk(getattr(value, f.name), seen)
    elif isinstance(value, dict):
        for k, v in value.items():
            assert not any(b in str(k).lower() for b in _BANNED), k
            _walk(v, seen)
    elif isinstance(value, (tuple, list)):
        for item in value:
            _walk(item, seen)
    elif isinstance(value, str):
        assert "SELECT" not in value.upper() or " " not in value


def test_raw_plan_holds_no_dbt_sql_path_fault():
    plan = build_raw_plan(_base_validated())
    assert plan.scenario_id == "agriculture_coop_001"
    _walk(plan, set())
    for table in plan.tables:
        assert table.rows.min >= 1


def test_build_raw_plan_pure(monkeypatch, tmp_path):
    validated = _base_validated()
    monkeypatch.chdir(tmp_path)
    py_random.seed(12345)
    before = py_random.getstate()
    first = build_raw_plan(validated)
    assert py_random.getstate() == before
    assert build_raw_plan(validated) == first
    assert list(tmp_path.iterdir()) == []


def test_build_rejects_bare_scenario():
    with pytest.raises(TypeError):
        build_raw_plan({"scenario_id": "x"})
