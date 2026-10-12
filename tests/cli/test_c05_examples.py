from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from data_pipeline_diagnostics.cli import examples
from data_pipeline_diagnostics.cli.examples import (
    ExampleAnnotations,
    covered_tags,
    requested_tags,
    select_examples,
)
from data_pipeline_diagnostics.cli.features import (
    ScenarioFeatures,
    extract_features,
    normalize_request,
)
from data_pipeline_diagnostics.scenario import (
    DataType,
    parse_scenario_json,
    validate_semantics,
)
from data_pipeline_diagnostics.scenario.staging import CastOperation, TrimOperation, UpperOperation

REPO_SCENARIOS = Path(__file__).resolve().parents[2] / "scenarios"


def load_validated(scenario_id: str):
    raw = (REPO_SCENARIOS / f"{scenario_id}.json").read_bytes()
    return validate_semantics(parse_scenario_json(raw))


@pytest.fixture(scope="module")
def v_coop():
    return load_validated("agriculture_coop_001")


@pytest.fixture(scope="module")
def v_harvests():
    return load_validated("agriculture_harvests_002")


@pytest.fixture(scope="module")
def v_livestock():
    return load_validated("agriculture_livestock_001")


@pytest.fixture(scope="module")
def v_admissions():
    return load_validated("education_admissions_001")


def _with_scenario(validated, **updates):
    return dataclasses.replace(validated, scenario=validated.scenario.model_copy(update=updates))


def _with_id(validated, scenario_id: str):
    return _with_scenario(validated, scenario_id=scenario_id)


def _with_description(validated, description):
    return _with_scenario(validated, description=description)


def _strip_staging(scenario):
    models = tuple(
        model.model_copy(
            update={
                "columns": tuple(
                    column.model_copy(update={"operations": ()}) for column in model.columns
                ),
                "row_operations": (),
            }
        )
        for model in scenario.staging_models
    )
    return scenario.model_copy(update={"staging_models": models})


def _with_first_col_op(validated, operation):
    scenario = _strip_staging(validated.scenario)
    first, rest = scenario.staging_models[0], scenario.staging_models[1:]
    head, tail = first.columns[0], first.columns[1:]
    head = head.model_copy(update={"operations": (operation,)})
    tail = tuple(column.model_copy(update={"operations": ()}) for column in tail)
    first = first.model_copy(update={"columns": (head, *tail), "row_operations": ()})
    return dataclasses.replace(
        validated, scenario=scenario.model_copy(update={"staging_models": (first, *rest)})
    )


def _with_composite_pk(validated):
    table = validated.scenario.raw_tables[0].model_copy(update={"primary_key": ("a_pk", "b_pk")})
    scenario = validated.scenario.model_copy(
        update={"raw_tables": (table, *validated.scenario.raw_tables[1:])}
    )
    return dataclasses.replace(validated, scenario=scenario)


def pair_ids(result) -> tuple[str, str]:
    _, _, annotations = result
    assert isinstance(annotations, ExampleAnnotations)
    return (annotations.first_id, annotations.second_id)


# ---------------------------------------------------------------------------
# Ranking criteria on sculpted fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def coverage_bundle(v_coop):
    a = _with_id(_with_description(_with_first_col_op(v_coop, TrimOperation()), "a"), "cov_a_001")
    b = _with_id(_with_description(_with_first_col_op(v_coop, UpperOperation()), "b"), "cov_b_001")
    c = _with_id(
        _with_description(_with_first_col_op(v_coop, TrimOperation()), "x" * 500), "cov_c_001"
    )
    d = _with_id(
        _with_description(
            dataclasses.replace(v_coop, scenario=_strip_staging(v_coop.scenario)), None
        ),
        "cov_d_001",
    )
    return (a, b, c, d)


def trim_cast_request():
    return normalize_request(
        scenario_id="demo_001", domain="demo", staging="trim,upper", joins="auto"
    )


def test_coverage_union_beats_length(coverage_bundle):
    examples.clear_feature_cache()
    result = select_examples(trim_cast_request(), list(coverage_bundle))
    a, b, _, _ = coverage_bundle
    assert pair_ids(result) == (a.scenario.scenario_id, b.scenario.scenario_id)
    first, second, _ = result
    assert first is a and second is b


def test_compact_length_breaks_coverage_ties(coverage_bundle):
    examples.clear_feature_cache()
    request = normalize_request(scenario_id="demo_001", domain="demo", staging="trim", joins="auto")
    result = select_examples(request, list(coverage_bundle))
    a, _, _, d = coverage_bundle
    assert pair_ids(result) == (a.scenario.scenario_id, d.scenario.scenario_id)


def test_predicate_count_breaks_coverage_ties(v_coop, v_admissions):
    examples.clear_feature_cache()
    e = _with_id(_with_description(_with_composite_pk(v_coop), "e"), "pred_e_001")
    f = _with_id(_with_description(v_coop, "f"), "pred_f_001")
    g = _with_description(_with_composite_pk(v_admissions), "x" * 2000)
    request = normalize_request(
        scenario_id="demo_001",
        domain="demo",
        composite_keys="required",
        joins="inner",
    )
    assert select_examples(request, [e, f, g])[2].first_id == e.scenario.scenario_id
    result = select_examples(request, [g, f, e])
    assert pair_ids(result) == (e.scenario.scenario_id, f.scenario.scenario_id)


def test_lexicographic_ids_break_final_ties(v_coop):
    examples.clear_feature_cache()
    request = normalize_request(scenario_id="demo_001", domain="demo")
    assert requested_tags(request) == ()
    a = _with_id(v_coop, "ex_a_001")
    b = _with_id(v_coop, "ex_b_001")
    c = _with_id(v_coop, "ex_c_001")
    assert pair_ids(select_examples(request, [c, b, a])) == ("ex_a_001", "ex_b_001")
    assert pair_ids(select_examples(request, [a, b, c])) == ("ex_a_001", "ex_b_001")


def test_repeat_selection_is_deterministic(coverage_bundle):
    examples.clear_feature_cache()
    request = trim_cast_request()
    first = select_examples(request, list(coverage_bundle))
    second = select_examples(request, list(reversed(coverage_bundle)))
    assert pair_ids(first) == pair_ids(second)
    assert first[2] == second[2]


# ---------------------------------------------------------------------------
# Requested tags, domain exclusion, personal-catalog independence
# ---------------------------------------------------------------------------


def test_requested_tags_exclude_domain_size_seed():
    request = normalize_request(
        scenario_id="demo_001",
        domain="demo",
        size="large",
        composite_keys="required",
        staging="trim,filter",
        intermediate="derive",
        joins="mixed",
        metrics="sum",
        seed=7,
    )
    assert requested_tags(request) == tuple(
        sorted(
            {
                "staging:trim",
                "staging:filter",
                "intermediate:derive",
                "join:inner",
                "join:left",
                "metric:sum",
                "composite-pk:required",
            }
        )
    )
    assert requested_tags(normalize_request(scenario_id="d_001", domain="d")) == ()


def test_domain_swap_does_not_change_outcome(v_coop):
    examples.clear_feature_cache()
    request = normalize_request(scenario_id="demo_001", domain="zzz")
    a = _with_scenario(v_coop, scenario_id="dom_a_001", domain="x" * 11)
    b = _with_scenario(v_coop, scenario_id="dom_b_001", domain="y" * 11)
    d = _with_description(_with_id(v_coop, "dom_d_001"), "y" * 500)
    swapped = (
        _with_scenario(v_coop, scenario_id="dom_a_001", domain="y" * 11),
        _with_scenario(v_coop, scenario_id="dom_b_001", domain="x" * 11),
        d,
    )
    assert select_examples(request, [a, b, d])[2] == select_examples(request, list(swapped))[2]


def test_personal_catalog_edits_cannot_change_pair(v_coop, v_harvests, v_livestock):
    examples.clear_feature_cache()
    request = normalize_request(
        scenario_id="demo_001",
        domain="demo",
        staging="lower,filter",
        intermediate="derive",
        joins="inner",
        metrics="count_rows,sum",
        composite_keys="forbidden",
    )
    bundled = select_examples(request, [v_coop, v_harvests])
    with_personal = select_examples(request, [v_harvests, v_coop, v_livestock])
    assert pair_ids(bundled) == ("agriculture_coop_001", "agriculture_harvests_002")
    assert pair_ids(with_personal) == pair_ids(bundled)
    assert with_personal[2] == bundled[2]


# ---------------------------------------------------------------------------
# Missing features are kept and reported
# ---------------------------------------------------------------------------


def test_uncoverable_request_kept_and_reported(v_coop, v_harvests):
    examples.clear_feature_cache()
    request = normalize_request(
        scenario_id="demo_001",
        domain="demo",
        staging="cast",
        joins="mixed",
        metrics="conditional_sum",
        composite_keys="required",
    )
    first, second, annotations = select_examples(request, [v_coop, v_harvests])
    assert first is v_coop and second is v_harvests
    assert annotations.covered_first == ("join:inner",)
    assert annotations.covered_second == ("join:inner",)
    assert annotations.absent == (
        "composite-pk:required",
        "join:left",
        "metric:conditional_sum",
        "staging:cast",
    )
    assert request.staging == frozenset({"cast"})
    assert annotations.format() == "\n".join(
        [
            "Example 1 agriculture_coop_001 covers: join:inner",
            "Example 2 agriculture_harvests_002 covers: join:inner",
            "Requested but absent from both examples: "
            "composite-pk:required, join:left, metric:conditional_sum, staging:cast",
        ]
    )


def test_empty_annotations_render_none(v_coop):
    examples.clear_feature_cache()
    _, _, annotations = select_examples(
        normalize_request(scenario_id="demo_001", domain="demo"),
        [_with_id(v_coop, "ex_a_001"), _with_id(v_coop, "ex_b_001")],
    )
    assert annotations.absent == ()
    assert "(none)" in annotations.format()


# ---------------------------------------------------------------------------
# Cache and input validation
# ---------------------------------------------------------------------------


def test_extractor_called_once_per_content_hash(v_coop, v_harvests, v_livestock, monkeypatch):
    import data_pipeline_diagnostics.cli.features as features_module

    examples.clear_feature_cache()
    calls: list[str] = []
    real = features_module.extract_features

    def counting(validated):
        calls.append(str(validated.scenario.scenario_id))
        return real(validated)

    monkeypatch.setattr(features_module, "extract_features", counting)
    request = normalize_request(scenario_id="demo_001", domain="demo", staging="trim")
    bundled = [v_coop, v_harvests, v_livestock]
    select_examples(request, bundled)
    select_examples(request, list(reversed(bundled)))
    assert sorted(calls) == [
        "agriculture_coop_001",
        "agriculture_harvests_002",
        "agriculture_livestock_001",
    ]


def test_covered_tags_come_from_features_only():
    tags = covered_tags(
        ScenarioFeatures(
            scenario_id="s",
            domain="d",
            has_composite_raw_pk=True,
            staging_ops=frozenset({"trim"}),
            intermediate_features=frozenset(),
            join_types=frozenset({"left"}),
            metric_functions=frozenset({"avg"}),
            max_raw_rows_max=3,
        )
    )
    assert tags == {"staging:trim", "join:left", "metric:avg", "composite-pk:required"}


def test_selection_needs_two_scenarios(v_coop):
    with pytest.raises(ValueError, match="at least two"):
        select_examples(normalize_request(scenario_id="demo_001", domain="demo"), [])
    with pytest.raises(ValueError, match="at least two"):
        select_examples(normalize_request(scenario_id="demo_001", domain="demo"), [v_coop])


def test_cast_operation_sculpting_sanity(v_coop):
    sculpted = _with_first_col_op(v_coop, CastOperation(type=DataType.string))
    assert "staging:cast" in covered_tags(extract_features(sculpted))
