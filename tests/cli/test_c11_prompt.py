from __future__ import annotations

import json
from pathlib import Path

import pytest

from data_pipeline_diagnostics.cli import prompt
from data_pipeline_diagnostics.cli.examples import select_examples
from data_pipeline_diagnostics.cli.features import AuthoringRequest, normalize_request
from data_pipeline_diagnostics.cli.prompt import (
    ResponseParseError,
    allocate_scenario_id,
    build_prompt,
    build_requirements,
    normalize_create_request,
    parse_response,
)
from data_pipeline_diagnostics.cli.providers import FakeTransport, NormalizedGeneration
from data_pipeline_diagnostics.scenario import (
    canonical_json,
    get_scenario_json_schema,
    parse_scenario_json,
    validate_semantics,
)

REPO = Path(__file__).resolve().parents[2]
SPEC_TEXT = (REPO / "docs" / "SCENARIO_SPEC.md").read_text(encoding="utf-8")
SCHEMA_JSON = json.dumps(get_scenario_json_schema(), separators=(",", ":"), ensure_ascii=False)
MINI_TEXT = (REPO / "tests" / "scenario" / "fixtures" / "valid" / "minimal.json").read_text()


def load_validated(scenario_id: str):
    raw = (REPO / "scenarios" / f"{scenario_id}.json").read_bytes()
    return validate_semantics(parse_scenario_json(raw))


@pytest.fixture(scope="module")
def pair():
    coop = load_validated("agriculture_coop_001")
    harvests = load_validated("agriculture_harvests_002")
    request = normalize_request(
        scenario_id="demo_001",
        domain="agriculture",
        staging="lower,filter",
        intermediate="derive",
        joins="inner",
        metrics="count_rows,sum",
        composite_keys="forbidden",
    )
    first, second, annotations = select_examples(request, [coop, harvests])
    return request, first, second, annotations


def create_args(**overrides):
    from data_pipeline_diagnostics.cli import app

    base = ["create", "--domain", "demo"]
    extra = [str(item) for pair in overrides.pop("extra", []) for item in pair]
    parsed = app.build_parser().parse_args(base + extra)
    for key, value in overrides.items():
        setattr(parsed, key, value)
    return parsed


def reply(text: str, finish: str = "stop") -> NormalizedGeneration:
    return NormalizedGeneration(text=text, finish=finish, usage=None, model_identity=None)


# ---------------------------------------------------------------------------
# Request normalization and id allocation
# ---------------------------------------------------------------------------


def test_normalize_defaults_and_allocation():
    request = normalize_create_request(create_args(), [])
    assert isinstance(request, AuthoringRequest)
    assert request.scenario_id == "demo_001"
    assert request.domain == "demo"
    assert request.size == "small"
    assert request.composite_keys == "auto"
    assert request.staging == frozenset()
    assert request.intermediate == frozenset()
    assert request.joins == "auto"
    assert request.metrics == frozenset()
    assert request.seed == 0


def test_normalize_dedup_and_explicit_id():
    args = create_args(extra=[("--staging", "trim, filter,,trim "), ("--id", "custom_001")])
    request = normalize_create_request(args, ["other_001"])
    assert request.scenario_id == "custom_001"
    assert request.staging == frozenset({"trim", "filter"})


def test_allocate_increments_past_taken():
    assert allocate_scenario_id("demo", []) == "demo_001"
    assert allocate_scenario_id("demo", ["demo_001"]) == "demo_002"
    assert allocate_scenario_id("demo", ["demo_001", "demo_002", "other_009"]) == "demo_003"


def test_id_collision_fails_locally():
    with pytest.raises(ValueError, match="collision"):
        normalize_create_request(create_args(extra=[("--id", "taken_001")]), ["taken_001"])


def test_bad_selection_and_id_fail_locally():
    with pytest.raises(ValueError, match="--staging"):
        normalize_create_request(create_args(extra=[("--staging", "bogus")]), [])
    with pytest.raises(ValueError, match="SCENARIO_ID"):
        normalize_create_request(create_args(extra=[("--id", "Bad")]), [])


# ---------------------------------------------------------------------------
# Prompt content
# ---------------------------------------------------------------------------


def test_prompt_contains_full_resources(pair):
    request, first, second, annotations = pair
    blocks = build_prompt(request, SPEC_TEXT, SCHEMA_JSON, [first, second], annotations)
    assert "JSON only" in blocks.instructions
    assert SPEC_TEXT in blocks.reference
    assert SCHEMA_JSON in blocks.reference
    assert "discriminator" in SCHEMA_JSON and "$defs" in SCHEMA_JSON
    compact_first = canonical_json(first.scenario).decode("utf-8")
    compact_second = canonical_json(second.scenario).decode("utf-8")
    assert compact_first in blocks.examples
    assert compact_second in blocks.examples
    assert "\n" not in compact_first
    assert annotations.format() in blocks.examples
    assert request.scenario_id in blocks.requirements
    assert request.domain in blocks.requirements
    assert "1 to 1000" in blocks.requirements
    assert f"data seed: {request.seed}" in blocks.requirements


def test_prompt_has_no_forbidden_sources(pair):
    request, first, second, annotations = pair
    blocks = build_prompt(request, SPEC_TEXT, SCHEMA_JSON, [first, second], annotations)
    full = "\n".join([blocks.instructions, blocks.reference, blocks.examples, blocks.requirements])
    for token in ("COVERAGE.md", "tasks/cli", "tasks/generator"):
        assert token not in full
    for token in ("PIPELINE_SPEC.md", "SCENARIO_AUTHORING.md"):
        assert full.count(token) == SPEC_TEXT.count(token)


def test_requirements_block_marks_delegated_choices():
    request = normalize_request(scenario_id="d_001", domain="d")
    text = build_requirements(request)
    assert "author's choice" in text
    assert "do not add seed fields" in text


def test_examples_annotations_mismatch_rejected(pair):
    _, first, second, annotations = pair
    with pytest.raises(ValueError, match="annotations"):
        prompt.build_examples([second, first], annotations)
    with pytest.raises(ValueError, match="exactly"):
        prompt.build_examples([first], annotations)


def test_system_user_split_feeds_adapters(pair):
    from data_pipeline_diagnostics.cli.providers import OpenRouterAdapter

    request, first, second, annotations = pair
    blocks = build_prompt(request, SPEC_TEXT, SCHEMA_JSON, [first, second], annotations)
    system, user = blocks.to_system_user()
    assert system == blocks.instructions
    assert blocks.reference in user and blocks.examples in user and blocks.requirements in user
    fake = FakeTransport(
        [{"model": "m", "choices": [{"message": {"content": MINI_TEXT}, "finish_reason": "stop"}]}]
    )
    adapter = OpenRouterAdapter(model="m", api_key="k", transport=fake)
    assert adapter.generate(system=system, user=user).finish == "stop"
    assert fake.calls[0]["provider"] == "openrouter"


# ---------------------------------------------------------------------------
# Strict response parsing
# ---------------------------------------------------------------------------


def test_parse_bare_and_fenced_json():
    parsed = parse_response(reply(MINI_TEXT))
    assert parsed["scenario_id"] == "minimal_fixture"
    fenced = parse_response(reply("```json\n" + MINI_TEXT + "\n```"))
    assert fenced == parsed
    assert parse_response(reply("```\n" + MINI_TEXT + "\n```")) == parsed


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("here you go " + MINI_TEXT, "not-json"),
        ("[1, 2]", "not-json"),
        (MINI_TEXT + MINI_TEXT, "multiple-documents"),
        (MINI_TEXT + " done", "trailing-text"),
        ("```json\n" + MINI_TEXT + "\n``` trailing", "trailing-text"),
        ("```json\n" + MINI_TEXT, "trailing-text"),
        ("```python\n" + MINI_TEXT + "\n```", "invalid-json"),
        ("   \n  ", "empty"),
        ("{oops", "invalid-json"),
        ("{}", "invalid-json"),
        ('{"a": 1, "a": 2}', "invalid-json"),
    ],
)
def test_parse_rejection_shapes(text, code):
    with pytest.raises(ResponseParseError) as exc:
        parse_response(reply(text))
    assert exc.value.code == code


@pytest.mark.parametrize(
    ("finish", "code"),
    [("length", "truncated"), ("refusal", "refusal"), ("error", "provider-error")],
)
def test_parse_finish_classes(finish, code):
    with pytest.raises(ResponseParseError) as exc:
        parse_response(reply(MINI_TEXT, finish))
    assert exc.value.code == code


def test_semantically_invalid_candidate_classified_not_compiled(monkeypatch):
    import data_pipeline_diagnostics.scenario as scenario_module

    calls: list[str] = []

    def boom(scenario):
        calls.append("validate")
        raise AssertionError("must not compile")

    monkeypatch.setattr(scenario_module, "validate_semantics", boom)
    import data_pipeline_diagnostics.generator.cache as cache_module

    monkeypatch.setattr(
        cache_module, "prepare_clean_instance", lambda *a, **k: calls.append("prepare")
    )
    document = json.loads(MINI_TEXT)
    document["staging_models"][0]["source"] = "nope_table"
    parsed = parse_response(reply(json.dumps(document)))
    assert parsed["staging_models"][0]["source"] == "nope_table"
    assert calls == []
