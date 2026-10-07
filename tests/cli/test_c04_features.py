from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest
from pydantic import TypeAdapter

from data_pipeline_diagnostics.cli.features import (
    INTERMEDIATE_VOCABULARY,
    METRIC_VOCABULARY,
    STAGING_VOCABULARY,
    AuthoringRequest,
    RequirementIssue,
    ScenarioFeatures,
    check_requirements,
    classify_raw_size,
    extract_features,
    normalize_request,
)
from data_pipeline_diagnostics.scenario import (
    Expression,
    parse_scenario_json,
    validate_semantics,
)

REPO_SCENARIOS = Path(__file__).resolve().parents[2] / "scenarios"
_EXPRESSION_ADAPTER = TypeAdapter(Expression)


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
def v_yield():
    return load_validated("agriculture_yield_001")


@pytest.fixture(scope="module")
def v_admissions():
    return load_validated("education_admissions_001")


@pytest.fixture(scope="module")
def v_livestock():
    return load_validated("agriculture_livestock_001")


@pytest.fixture(scope="module")
def v_exports():
    return load_validated("agriculture_exports_001")


@pytest.fixture(scope="module")
def v_meters():
    return load_validated("energy_meters_002")


BASE = ScenarioFeatures(
    scenario_id="demo_001",
    domain="demo",
    has_composite_raw_pk=False,
    staging_ops=frozenset({"trim", "filter"}),
    intermediate_features=frozenset({"filter", "derive"}),
    join_types=frozenset({"inner"}),
    metric_functions=frozenset({"count_rows", "sum"}),
    max_raw_rows_max=500,
)


def base_request(**overrides) -> AuthoringRequest:
    params = {
        "scenario_id": "demo_001",
        "domain": "demo",
        "size": "small",
        "composite_keys": "forbidden",
        "staging": "trim,filter",
        "intermediate": "filter,derive",
        "joins": "inner",
        "metrics": "count_rows,sum",
        "seed": 0,
    }
    params.update(overrides)
    return normalize_request(**params)


def codes(issues: tuple[RequirementIssue, ...]) -> tuple[str, ...]:
    return tuple(issue.code for issue in issues)


# ---------------------------------------------------------------------------
# Extraction pins on real scenarios
# ---------------------------------------------------------------------------


def test_extract_coop_exact(v_coop):
    assert extract_features(v_coop) == ScenarioFeatures(
        scenario_id="agriculture_coop_001",
        domain="agriculture",
        has_composite_raw_pk=False,
        staging_ops=frozenset({"lower", "upper"}),
        intermediate_features=frozenset(),
        join_types=frozenset({"inner"}),
        metric_functions=frozenset({"count_rows", "sum"}),
        max_raw_rows_max=500,
    )


def test_extract_harvests_staging_filter_without_intermediate_filter(v_harvests):
    features = extract_features(v_harvests)
    assert "filter" in features.staging_ops
    assert "filter" not in features.intermediate_features
    assert features.intermediate_features == frozenset({"derive"})
    assert features.join_types == frozenset({"inner"})
    assert features.has_composite_raw_pk is False


def test_extract_yield_dedup_and_derive(v_yield):
    features = extract_features(v_yield)
    assert features.intermediate_features == frozenset({"derive", "deduplicate"})
    assert features.staging_ops == frozenset()


def test_extract_admissions_mixed_joins(v_admissions):
    assert extract_features(v_admissions).join_types == frozenset({"inner", "left"})


def test_extract_composite_grain_does_not_count_as_raw_pk(v_livestock):
    assert any(len(grain) > 1 for grain in v_livestock.resolved_grains.values())
    assert extract_features(v_livestock).has_composite_raw_pk is False


def test_extract_composite_raw_pk_detected(v_exports):
    assert extract_features(v_exports).has_composite_raw_pk is True


def test_extract_max_rows_max_from_declared_intervals(v_coop):
    raw = json.loads((REPO_SCENARIOS / "agriculture_coop_001.json").read_text(encoding="utf-8"))
    declared = max(table["rows"]["max"] for table in raw["raw_tables"])
    assert extract_features(v_coop).max_raw_rows_max == declared == 500


# ---------------------------------------------------------------------------
# End-to-end: matching requests on real scenarios
# ---------------------------------------------------------------------------


def test_matching_request_passes_on_coop(v_coop):
    request = normalize_request(
        scenario_id="agriculture_coop_001",
        domain="agriculture",
        size="small",
        composite_keys="forbidden",
        staging="lower,upper",
        intermediate=None,
        joins="inner",
        metrics="count_rows,sum",
    )
    assert check_requirements(extract_features(v_coop), request) == ()


def test_matching_request_passes_on_yield(v_yield):
    request = normalize_request(
        scenario_id="agriculture_yield_001",
        domain="agriculture",
        size="small",
        composite_keys="forbidden",
        staging=None,
        intermediate="derive,deduplicate",
        joins="inner",
        metrics="count_rows,sum",
    )
    assert check_requirements(extract_features(v_yield), request) == ()


def test_matching_request_passes_on_admissions(v_admissions):
    request = normalize_request(
        scenario_id="education_admissions_001",
        domain="education",
        size="small",
        composite_keys="forbidden",
        staging=None,
        intermediate=None,
        joins="mixed",
        metrics="avg,count_rows",
    )
    assert check_requirements(extract_features(v_admissions), request) == ()


# ---------------------------------------------------------------------------
# Predicate logic on hand-built features
# ---------------------------------------------------------------------------


def test_matching_hand_built_request_passes():
    assert check_requirements(BASE, base_request()) == ()


def test_permitted_extras_pass():
    assert check_requirements(BASE, base_request(staging="trim", metrics="sum")) == ()


def test_present_but_ineffective_operation_satisfies():
    # Structural presence is the whole MVP contract (§5.3): no data-effect proof.
    assert check_requirements(BASE, base_request(staging="trim,filter")) == ()


@pytest.mark.parametrize("operation", sorted(STAGING_VOCABULARY - {"trim", "filter"}))
def test_absent_staging_operation(operation):
    issues = check_requirements(BASE, base_request(staging=operation))
    assert codes(issues) == ("staging-missing",)
    assert issues[0].path == "staging"
    assert operation in issues[0].message


@pytest.mark.parametrize("feature", sorted(INTERMEDIATE_VOCABULARY - {"filter", "derive"}))
def test_absent_intermediate_feature(feature):
    issues = check_requirements(BASE, base_request(intermediate=feature))
    assert codes(issues) == ("intermediate-missing",)
    assert issues[0].path == "intermediate"


@pytest.mark.parametrize(
    ("joins", "expected"),
    [
        ("auto", ()),
        ("inner", ()),
        ("left", ("join-mode-mismatch",)),
        ("mixed", ("join-mode-mismatch",)),
    ],
)
def test_join_modes_against_inner_only(joins, expected):
    assert codes(check_requirements(BASE, base_request(joins=joins))) == expected


def test_join_strictness_on_mixed(v_admissions):
    features = extract_features(v_admissions)
    assert codes(
        check_requirements(
            features,
            base_request(
                scenario_id="education_admissions_001",
                domain="education",
                staging=None,
                intermediate=None,
                joins="inner",
                metrics="avg,count_rows",
            ),
        )
    ) == ("join-mode-mismatch",)
    assert (
        codes(
            check_requirements(
                features,
                base_request(
                    scenario_id="education_admissions_001",
                    domain="education",
                    staging=None,
                    intermediate=None,
                    joins="mixed",
                    metrics="avg,count_rows",
                ),
            )
        )
        == ()
    )


@pytest.mark.parametrize("function", sorted(METRIC_VOCABULARY - {"count_rows", "sum"}))
def test_absent_metric_function(function):
    issues = check_requirements(BASE, base_request(metrics=function))
    assert codes(issues) == ("metric-missing",)
    assert issues[0].path == "output_models"


def test_composite_required_but_absent():
    issues = check_requirements(BASE, base_request(composite_keys="required"))
    assert codes(issues) == ("composite-key-mismatch",)


def test_composite_forbidden_but_present():
    present = dataclasses.replace(BASE, has_composite_raw_pk=True)
    assert check_requirements(present, base_request(composite_keys="forbidden")) != ()
    assert codes(check_requirements(present, base_request(composite_keys="forbidden"))) == (
        "composite-key-mismatch",
    )
    assert check_requirements(present, base_request(composite_keys="required")) == ()


def test_id_and_domain_mismatch():
    assert codes(check_requirements(BASE, base_request(scenario_id="other_001"))) == (
        "id-mismatch",
    )
    assert codes(check_requirements(BASE, base_request(domain="other"))) == ("domain-mismatch",)


@pytest.mark.parametrize(
    ("max_rows", "size", "expected"),
    [
        (1_000, "small", ()),
        (1_001, "small", ("raw-size-exceeded",)),
        (10_000, "medium", ()),
        (10_001, "medium", ("raw-size-exceeded",)),
        (500, "large", ()),
    ],
)
def test_raw_size_preflight_boundaries(max_rows, size, expected):
    features = dataclasses.replace(BASE, max_raw_rows_max=max_rows)
    assert codes(check_requirements(features, base_request(size=size))) == expected


def test_issue_order_is_deterministic():
    request = base_request(
        scenario_id="other_001",
        domain="other",
        composite_keys="required",
        staging="cast",
        intermediate="deduplicate",
        joins="left",
        metrics="avg",
    )
    assert codes(check_requirements(BASE, request)) == (
        "id-mismatch",
        "domain-mismatch",
        "staging-missing",
        "intermediate-missing",
        "join-mode-mismatch",
        "metric-missing",
        "composite-key-mismatch",
    )


# ---------------------------------------------------------------------------
# Layer isolation through model surgery (no revalidation: structure only)
# ---------------------------------------------------------------------------


def _replace_scenario(validated, scenario):
    return dataclasses.replace(validated, scenario=scenario)


def test_stripped_intermediate_filters_lose_filter_feature(v_meters):
    before = extract_features(v_meters)
    assert "filter" in before.intermediate_features
    models = tuple(
        model.model_copy(update={"filters": ()}) if hasattr(model, "filters") else model
        for model in v_meters.scenario.intermediate_models
    )
    scenario = v_meters.scenario.model_copy(update={"intermediate_models": models})
    after = extract_features(_replace_scenario(v_meters, scenario))
    assert "filter" not in after.intermediate_features
    assert after.staging_ops == before.staging_ops


@pytest.mark.parametrize(
    ("payload", "expect_derive"),
    [
        (
            {
                "kind": "coalesce",
                "values": ({"kind": "column", "column": "x"}, {"kind": "literal", "value": 0}),
            },
            False,
        ),
        (
            {
                "kind": "coalesce",
                "values": (
                    {
                        "kind": "binary",
                        "operator": "add",
                        "left": {"kind": "column", "column": "x"},
                        "right": {"kind": "literal", "value": 1},
                    },
                    {"kind": "literal", "value": 0},
                ),
            },
            True,
        ),
        (
            {
                "kind": "date_part",
                "part": "year",
                "value": {"kind": "column", "column": "x"},
            },
            True,
        ),
    ],
)
def test_derive_detection_on_synthetic_expressions(v_yield, payload, expect_derive):
    expression = _EXPRESSION_ADAPTER.validate_python(payload)
    models = tuple(
        model.model_copy(
            update={
                "derived_columns": tuple(
                    column.model_copy(update={"expression": expression})
                    for column in model.derived_columns
                )
            }
        )
        if hasattr(model, "derived_columns")
        else model
        for model in v_yield.scenario.intermediate_models
    )
    assert any(getattr(model, "derived_columns", ()) for model in models)
    scenario = v_yield.scenario.model_copy(update={"intermediate_models": models})
    features = extract_features(_replace_scenario(v_yield, scenario))
    assert ("derive" in features.intermediate_features) is expect_derive


# ---------------------------------------------------------------------------
# Request normalization
# ---------------------------------------------------------------------------


def test_normalize_comma_selections_unique_and_stripped():
    request = normalize_request(
        scenario_id="demo_001",
        domain="demo",
        staging="trim, filter,,trim ",
        intermediate=["derive", "derive"],
        metrics="sum",
    )
    assert request.staging == frozenset({"trim", "filter"})
    assert request.intermediate == frozenset({"derive"})
    assert request.metrics == frozenset({"sum"})
    assert request.size == "small"
    assert request.seed == 0


@pytest.mark.parametrize("bad", ["polish", "trim,polish", "TRIM"])
def test_normalize_rejects_unknown_tokens(bad):
    with pytest.raises(ValueError, match="--staging"):
        normalize_request(scenario_id="demo_001", domain="demo", staging=bad)
    with pytest.raises(ValueError, match="--intermediate"):
        normalize_request(scenario_id="demo_001", domain="demo", intermediate="bogus")
    with pytest.raises(ValueError, match="--metrics"):
        normalize_request(scenario_id="demo_001", domain="demo", metrics="median")


@pytest.mark.parametrize(
    "kwargs",
    [
        {"size": "huge"},
        {"composite_keys": "sometimes"},
        {"joins": "outer"},
        {"seed": -1},
        {"seed": 2**63},
        {"seed": True},
        {"seed": "0"},
        {"scenario_id": "Bad"},
        {"domain": "not a domain!"},
    ],
)
def test_normalize_rejects_invalid_request_fields(kwargs):
    params = {"scenario_id": "demo_001", "domain": "demo"}
    params.update(kwargs)
    with pytest.raises(ValueError):
        normalize_request(**params)


def test_records_are_frozen():
    features = extract_features(load_validated("agriculture_coop_001"))
    with pytest.raises(dataclasses.FrozenInstanceError):
        features.domain = "x"
    with pytest.raises(dataclasses.FrozenInstanceError):
        base_request().seed = 1
    with pytest.raises(dataclasses.FrozenInstanceError):
        RequirementIssue(code="c", path="p", message="m").code = "d"


def test_classify_raw_size_boundaries():
    assert [classify_raw_size(n) for n in (1_000, 1_001, 10_000, 10_001, 100_000, 100_001)] == [
        "small",
        "medium",
        "medium",
        "large",
        "large",
        "outside-presets",
    ]
