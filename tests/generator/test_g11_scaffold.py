"""G11 tests: dbt project shell layout and stub wiring.

Technique notes: the task names ``tests/scenario/fixtures/valid/minimal.json``,
but that fixture predates current semantics and fails validation (E131), so
tests render the accepted corpus scenario ``agriculture_coop_001`` instead —
the boundary requires ``ValidatedScenario``. ``dbt parse`` itself is proven
manually (accept allows deferral); tests assert the structural contract fast
and hermetically.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from data_pipeline_diagnostics.generator import render_dbt_project
from data_pipeline_diagnostics.generator.dbt_render import model_dependencies
from data_pipeline_diagnostics.scenario.parsing import parse_scenario_file
from data_pipeline_diagnostics.scenario.semantic import validate_semantics

REPO = Path(__file__).resolve().parents[2]
SCENARIO = REPO / "scenarios" / "agriculture_coop_001.json"

EXPECTED_FILES = (
    "dbt_project.yml",
    "profiles.yml",
    "models/sources.yml",
    "models/staging/stg_varieties.sql",
    "models/staging/stg_seeds.sql",
    "models/staging/stg_plantings.sql",
    "models/intermediate/j_plant_seeds.sql",
    "models/intermediate/j_full.sql",
    "models/output/o_area_by_variety.sql",
    "models/assertions.yml",
)


def _validated():
    return validate_semantics(parse_scenario_file(SCENARIO))


def _render(tmp_path):
    project_dir = tmp_path / "dbt"
    rendered = render_dbt_project(_validated(), project_dir)
    return rendered, project_dir


def test_layout_matches_instance_subtree(tmp_path):
    rendered, project_dir = _render(tmp_path)
    assert rendered.scenario_id == "agriculture_coop_001"
    assert rendered.project_dir == project_dir
    assert rendered.files == EXPECTED_FILES
    for relative in EXPECTED_FILES:
        assert (project_dir / relative).is_file()
    assert (project_dir / "macros").is_dir()
    assert not (project_dir / "packages.yml").exists()


def test_sources_and_profile_content(tmp_path):
    _, project_dir = _render(tmp_path)
    sources = (project_dir / "models" / "sources.yml").read_text(encoding="utf-8")
    assert "- name: raw" in sources
    assert "schema: raw" in sources
    for table in ("raw_varieties", "raw_seeds", "raw_plantings"):
        assert f"- name: {table}" in sources
        assert f"identifier: {table}" in sources

    profiles = (project_dir / "profiles.yml").read_text(encoding="utf-8")
    assert "../pipeline.duckdb" in profiles
    assert "threads: 1" in profiles
    lowered = profiles.lower()
    for secret in ("password", "passwd", "token", "secret", "user:"):
        assert secret not in lowered

    project = (project_dir / "dbt_project.yml").read_text(encoding="utf-8")
    assert "name: dpd_pipeline" in project
    assert "profile: dpd_pipeline" in project
    assert "+materialization: table" in project
    assert "+schema: main" in project
    assert "http://" not in project and "https://" not in project


def test_stub_wiring(tmp_path):
    _, project_dir = _render(tmp_path)
    for model, raw_table in (
        ("stg_varieties", "raw_varieties"),
        ("stg_seeds", "raw_seeds"),
        ("stg_plantings", "raw_plantings"),
    ):
        sql = (project_dir / "models" / "staging" / f"{model}.sql").read_text(encoding="utf-8")
        assert f"source('raw', '{raw_table}')" in sql
    j_full = (project_dir / "models" / "intermediate" / "j_full.sql").read_text(encoding="utf-8")
    assert "ref('" in j_full
    out = (project_dir / "models" / "output" / "o_area_by_variety.sql").read_text(encoding="utf-8")
    assert "ref('j_full')" in out


def test_model_dependencies_dispatch():
    assert model_dependencies(SimpleNamespace(name="s", source="raw_t")) == ("raw_t",)
    assert model_dependencies(SimpleNamespace(name="t", operation="transform", source="a")) == (
        "a",
    )
    assert model_dependencies(SimpleNamespace(name="a", operation="aggregate", source="b")) == (
        "b",
    )
    assert model_dependencies(SimpleNamespace(name="d", operation="deduplicate", source="c")) == (
        "c",
    )
    assert model_dependencies(SimpleNamespace(name="j", operation="join", left="l", right="r")) == (
        "l",
        "r",
    )
    with pytest.raises(ValueError):
        model_dependencies(SimpleNamespace(name="x", operation="bogus"))
    with pytest.raises(ValueError):
        model_dependencies(SimpleNamespace(name="y"))


def test_generated_text_conventions(tmp_path):
    _, project_dir = _render(tmp_path)
    for relative in EXPECTED_FILES:
        raw = (project_dir / relative).read_bytes()
        assert b"\r" not in raw
        assert raw.endswith(b"\n") and not raw.endswith(b"\n\n")
