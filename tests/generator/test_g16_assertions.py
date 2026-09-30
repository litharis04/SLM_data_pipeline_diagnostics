"""G16 tests: healthy-assertion lowering (§15).

Text assertions pin the §15.2 lowering table on the real
``education_cohorts_001`` project (explicit ``accepted_values`` plus derived
single/composite, relationship and row-count assertions); crafted
``LogicalAssertion`` inputs pin range bounds, physical naming and
dedup-by-identity. Runtime behavior runs in G17, not here.
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml
from jinja2 import Environment

from data_pipeline_diagnostics.generator.dbt_render import (
    LogicalAssertion,
    _deduplicate,
    _partition_sources,
    collect_assertions,
    render_assertion_macros,
    render_assertions_yml,
    render_sources_yml,
)
from data_pipeline_diagnostics.generator.physical import yaml_string
from data_pipeline_diagnostics.scenario.parsing import parse_scenario_json
from data_pipeline_diagnostics.scenario.semantic import validate_semantics

REPO = Path(__file__).resolve().parents[2]


def _validated(name: str):
    data = json.loads((REPO / "scenarios" / name).read_text())
    return validate_semantics(parse_scenario_json(json.dumps(data)))


def _project_files(name: str = "education_cohorts_001.json") -> tuple[str, str]:
    """Rendered (sources.yml, assertions.yml): source tests live with the
    single ``raw`` source definition (dbt forbids redefining it per file)."""
    validated = _validated(name)
    collected = collect_assertions(validated)
    raw_tables = [str(t.name) for t in validated.scenario.raw_tables]
    sources = render_sources_yml(raw_tables, _partition_sources(collected, raw_tables))
    models = render_assertions_yml(
        collected,
        raw_tables=raw_tables,
        staging=[str(m.name) for m in validated.scenario.staging_models],
        intermediates=[str(n) for n in validated.topological_order],
        outputs=[str(m.name) for m in validated.scenario.output_models],
    )
    return sources, models


def test_selection_builtin_vs_custom():
    sources, models = _project_files()
    combined = sources + models
    assert "- not_null:" in combined
    assert "- unique:" in combined
    assert "composite_unique:" in combined
    assert "composite_relationships:" in combined
    assert "row_count_between:" in combined
    assert "- accepted_values:" in combined


def test_explicit_accepted_values_on_model():
    _, models = _project_files()
    parsed = yaml.safe_load(models)
    by_name = {m["name"]: m for m in parsed["models"]}
    tests = by_name["stg_enrollments"]["tests"]
    accepted = [t["accepted_values"] for t in tests if "accepted_values" in t]
    assert len(accepted) == 1
    assert accepted[0]["arguments"]["column_name"] == "status"
    assert accepted[0]["arguments"]["values"] == ["active", "dropped"]


def test_range_inclusive_exclusive_and_one_sided():
    inclusive = LogicalAssertion(
        name="r_inc",
        origin="explicit",
        type="column_range",
        model="stg_x",
        column="c",
        min=1,
        max=10,
        inclusive=True,
    )
    exclusive = LogicalAssertion(
        name="r_exc",
        origin="explicit",
        type="column_range",
        model="stg_x",
        column="c",
        max=10,
        inclusive=False,
    )
    text = render_assertions_yml(
        [inclusive, exclusive], raw_tables=[], staging=["stg_x"], intermediates=[], outputs=[]
    )
    assert "min_value: 1" in text
    assert "inclusive: true" in text
    assert "inclusive: false" in text
    max_only = [line for line in text.splitlines() if "max_value" in line]
    assert len(max_only) == 2
    exc_block = next(b for b in text.split("# explicit: ") if b.startswith("r_exc"))
    assert "min_value" not in exc_block
    assert "max_value: 10" in exc_block


def test_row_count_one_sided():
    assertion = LogicalAssertion(name="rc", origin="derived", type="row_count", model="o_y", min=1)
    text = render_assertions_yml(
        [assertion], raw_tables=[], staging=[], intermediates=[], outputs=["o_y"]
    )
    assert "min_value: 1" in text
    assert "max_value" not in text


def test_dedup_keeps_first_with_origin():
    explicit = LogicalAssertion(
        name="author_check", origin="explicit", type="unique", model="m", columns=("c",)
    )
    derived = LogicalAssertion(
        name="derived_unique_m", origin="derived", type="unique", model="m", columns=("c",)
    )
    assert _deduplicate([explicit, derived]) == [explicit]
    wider = LogicalAssertion(
        name="derived_unique_m2",
        origin="derived",
        type="unique",
        model="m",
        columns=("c", "d"),
    )
    assert _deduplicate([explicit, derived, wider]) == [explicit, wider]


def test_severity_error_everywhere_and_no_utils():
    sources, models = _project_files()
    assert "dbt_utils" not in render_assertion_macros()
    assert "warn" not in (sources + models).lower()
    nodes = []
    parsed_sources = yaml.safe_load(sources)
    for source in parsed_sources.get("sources", []):
        for table in source.get("tables", []):
            nodes.extend(table.get("tests", []))
    for model in yaml.safe_load(models).get("models", []):
        nodes.extend(model.get("tests", []))
    assert nodes
    assert all(
        next(iter(test.values())).get("config", {}).get("severity") == "error" for test in nodes
    )


def test_physical_names_deterministic_and_bounded():
    assert _project_files() == _project_files()
    long_name = LogicalAssertion(
        name="x" * 100, origin="explicit", type="not_null", model="m", columns=("c",)
    )
    text = render_assertions_yml(
        [long_name], raw_tables=[], staging=["m"], intermediates=[], outputs=[]
    )
    physical = [line for line in text.splitlines() if "name: not_null" in line][0]
    assert len(physical.split("name: ")[1]) <= 64


def test_yaml_string_jinja_round_trip():
    for payload in ("{{ 7*7 }}", "a{b}c", "O'Brien"):
        loaded = yaml.safe_load(yaml_string(payload))
        assert Environment().from_string(loaded).render() == payload


def test_macro_semantics_text():
    macros = render_assertion_macros()
    assert "IS NOT NULL" in macros
    assert "count(*) > 1" in macros
    assert "'<' if inclusive else '<='" in macros
    assert "min_value is not none" in macros
    assert "max_value is not none" in macros


def _render_macro_body(name: str, **kwargs: object) -> str:
    """Render one vendored {% test %} block as plain Jinja (dbt's test tags
    stripped) so its SQL executes directly in DuckDB."""
    source = render_assertion_macros()
    start = source.index(f"{{% test {name}(")
    end = source.index("{% endtest %}", start)
    body = "\n".join(
        line
        for line in source[start:end].splitlines()
        if not line.strip().startswith("{% test ") and line.strip() != "{% endtest %}"
    )
    # Mirror dbt's macro-signature defaults (plain Jinja has none).
    defaults = {"min_value": None, "max_value": None, "inclusive": True}
    return Environment().from_string(body).render(**{**defaults, **kwargs})


def _macro_db():
    import duckdb

    db = duckdb.connect()
    db.execute("SET TimeZone = 'UTC'")
    db.execute("CREATE TABLE child (a VARCHAR, b INTEGER, v DOUBLE)")
    db.execute("CREATE TABLE parent (x VARCHAR, y INTEGER)")
    return db


def test_macro_execution_both_directions():
    db = _macro_db()
    try:
        db.execute("INSERT INTO child VALUES ('k', 1, 1.5), ('k', 2, 2.5)")
        db.execute("INSERT INTO parent VALUES ('k', 1), ('k', 2)")
        unique_sql = _render_macro_body(
            "composite_unique", model='"child"', column_names=['"a"', '"b"']
        )
        assert db.execute(unique_sql).fetchall() == []
        db.execute("INSERT INTO child VALUES ('k', 1, 9.9)")
        assert len(db.execute(unique_sql).fetchall()) == 1
        db.execute("DELETE FROM child WHERE v = 9.9")

        rel_sql = _render_macro_body(
            "composite_relationships",
            model='"child"',
            column_names=['"a"', '"b"'],
            to='"parent"',
            to_columns=['"x"', '"y"'],
            join_on='child."a" = parent."x" AND child."b" = parent."y"',
            orphan_filter='parent."x" IS NULL AND NOT (child."a" IS NULL AND child."b" IS NULL)',
        )
        assert db.execute(rel_sql).fetchall() == []
        db.execute("INSERT INTO child VALUES ('orphan', 99, 0.0)")
        orphans = db.execute(rel_sql).fetchall()
        assert orphans == [("orphan", 99)]
        db.execute("INSERT INTO child VALUES (NULL, NULL, 0.0)")
        assert db.execute(rel_sql).fetchall() == [("orphan", 99)]

        count_sql = _render_macro_body("row_count_between", model='"child"', min_value=2)
        assert db.execute(count_sql).fetchall() == []
        over = _render_macro_body("row_count_between", model='"child"', min_value=2, max_value=3)
        assert db.execute(over).fetchall() != []

        range_sql = _render_macro_body(
            "column_range",
            model='"child"',
            column_name='"b"',
            min_value=1,
            max_value=2,
            inclusive=True,
        )
        assert db.execute(range_sql).fetchall() == [(99,)]
        exclusive = _render_macro_body(
            "column_range",
            model='"child"',
            column_name='"b"',
            min_value=1,
            max_value=2,
            inclusive=False,
        )
        assert len(db.execute(exclusive).fetchall()) == 3
    finally:
        db.close()
