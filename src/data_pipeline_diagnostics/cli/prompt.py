"""`create` prompt assembly and strict response parsing (C11, §§6.1/6.3).

Pure offline logic: normalized request preparation with local id allocation,
four-block prompt construction from caller-supplied resources (C13 wires them
to installed package data), and strict single-document response parsing.
No repair loop, no materialization, no compilation here.
"""

from __future__ import annotations

import argparse
from collections.abc import Collection, Sequence
from dataclasses import dataclass

from data_pipeline_diagnostics.cli.examples import ExampleAnnotations
from data_pipeline_diagnostics.cli.features import (
    AuthoringRequest,
)
from data_pipeline_diagnostics.cli.features import (
    normalize_request as normalize_features_request,
)
from data_pipeline_diagnostics.cli.providers import NormalizedGeneration
from data_pipeline_diagnostics.cli.workspace import validate_scenario_id
from data_pipeline_diagnostics.scenario import (
    ScenarioParseError,
    ValidatedScenario,
    canonical_json,
    parse_scenario_json,
)

SIZE_BOUNDS = {
    "small": (1, 1_000),
    "medium": (1_001, 10_000),
    "large": (10_001, 100_000),
}


def allocate_scenario_id(domain: str, catalog_ids: Collection[str]) -> str:
    """Allocate the first free ``<domain>_<NNN>`` id (deterministic counter)."""
    taken = set(catalog_ids)
    counter = 1
    while True:
        suffix = f"{counter:03d}" if counter < 1000 else str(counter)
        candidate = validate_scenario_id(f"{domain}_{suffix}")
        if candidate not in taken:
            return candidate
        counter += 1


def normalize_create_request(
    args: argparse.Namespace, catalog_ids: Collection[str]
) -> AuthoringRequest:
    """Build the frozen request: ``--id`` collision fails locally, else allocate.

    Comma selections run through the C04 vocabulary checks, so unknown
    operation/metric names raise ``ValueError`` before any provider call.
    """
    requested = args.id
    if requested is not None:
        scenario_id = validate_scenario_id(requested)
        if scenario_id in set(catalog_ids):
            raise ValueError(f"identifier collision: {scenario_id!r} already exists")
    else:
        scenario_id = allocate_scenario_id(args.domain, catalog_ids)
    return normalize_features_request(
        scenario_id=scenario_id,
        domain=args.domain,
        size=args.size,
        composite_keys=args.composite_keys,
        staging=args.staging,
        intermediate=args.intermediate,
        joins=args.joins,
        metrics=args.metrics,
        seed=args.seed,
    )


def _selection_text(title: str, selected: frozenset[str]) -> str:
    if not selected:
        return f"{title}: none requested (author's choice)"
    return f"{title}: {', '.join(sorted(selected))}"


def build_instructions() -> str:
    """Block 1: concise authoring directives."""
    return "\n".join(
        [
            "Author one complete analytical-pipeline scenario as a single JSON object.",
            "Obey the fixed requirements in block 4 exactly: scenario_id and domain must match, "
            "every listed feature must occur at least once, and the composite-key, JOIN-mode, "
            "and raw-size policies apply as stated. Topology bounds: 3-4 raw tables, one staging "
            "model per raw table, 2-3 intermediate models, 1-2 output models.",
            "Return JSON only: one bare object (a single outer ```json fence is allowed, "
            "nothing else — no prose, no concatenated objects).",
            "Choose coherent business entities and meaningful data; prefer inputs where the "
            "requested operations are visible in the values.",
        ]
    )


def build_reference(spec_text: str, schema_json: str) -> str:
    """Block 2: full specification text plus compact JSON Schema, inlined."""
    return (
        "SCENARIO LANGUAGE SPECIFICATION (full text):\n"
        f"{spec_text}\n"
        "SCENARIO JSON SCHEMA (compact, authoritative for structure):\n"
        f"{schema_json}"
    )


def build_examples(examples: Sequence[ValidatedScenario], annotations: ExampleAnnotations) -> str:
    """Block 3: two compact bundled scenarios plus deterministic annotations."""
    if len(examples) != 2:
        raise ValueError("prompt examples must be exactly the selected pair")
    first_id = str(examples[0].scenario.scenario_id)
    second_id = str(examples[1].scenario.scenario_id)
    if (annotations.first_id, annotations.second_id) != (first_id, second_id):
        raise ValueError("annotations do not match the example pair")
    compact = []
    for item in examples:
        text = canonical_json(item.scenario).decode("utf-8")
        if "\n" in text:
            raise ValueError("example JSON must be single-line compact")
        compact.append(text)
    return "\n\n".join(
        [
            annotations.format(),
            f"Example 1 {first_id} scenario JSON (compact):\n{compact[0]}",
            f"Example 2 {second_id} scenario JSON (compact):\n{compact[1]}",
        ]
    )


def build_requirements(request: AuthoringRequest) -> str:
    """Block 4: normalized fixed requirements with bounds and the seed note."""
    lower, upper = SIZE_BOUNDS[request.size]
    seed = request.seed
    return "\n".join(
        [
            "Fixed requirements (do not change these; they stay frozen across repairs):",
            f"scenario_id: {request.scenario_id}",
            f"domain: {request.domain}",
            f"size preset: {request.size} (largest raw table must hold {lower} to {upper} rows)",
            f"composite keys: {request.composite_keys}",
            _selection_text("staging operations", request.staging),
            _selection_text("intermediate features", request.intermediate),
            f"joins: {request.joins}",
            _selection_text("output metric functions", request.metrics),
            f"data seed: {seed} (supplied separately to the materializer; "
            "do not add seed fields to the JSON).",
        ]
    )


@dataclass(frozen=True)
class PromptBlocks:
    """The four §6.1 prompt blocks with contents inlined (never paths)."""

    instructions: str
    reference: str
    examples: str
    requirements: str

    def to_system_user(self) -> tuple[str, str]:
        """Split for the provider adapters: directives vs materials."""
        user = "\n\n".join([self.reference, self.examples, self.requirements])
        return self.instructions, user


def build_prompt(
    request: AuthoringRequest,
    spec_text: str,
    schema_json: str,
    examples: Sequence[ValidatedScenario],
    annotations: ExampleAnnotations,
) -> PromptBlocks:
    """Assemble the four §6.1 blocks from caller-supplied resources."""
    return PromptBlocks(
        instructions=build_instructions(),
        reference=build_reference(spec_text, schema_json),
        examples=build_examples(examples, annotations),
        requirements=build_requirements(request),
    )


class ResponseParseError(ValueError):
    """Strict response rejection with a stable machine-readable ``code``."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _extract_document(text: str) -> str:
    """Return the single JSON document text or raise with a shape code."""
    stripped = text.strip()
    if not stripped:
        raise ResponseParseError("empty", "provider returned an empty candidate")
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if len(lines) < 2 or lines[-1].strip() != "```":
            raise ResponseParseError(
                "trailing-text", "code fence around the candidate is not closed cleanly"
            )
        if lines[0].strip() not in ("```", "```json"):
            raise ResponseParseError("invalid-json", "outer fence must be ``` or ```json")
        return _extract_bare("\n".join(lines[1:-1]))
    return _extract_bare(stripped)


def _extract_bare(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("{"):
        raise ResponseParseError("not-json", "candidate holds no JSON object")
    depth = 0
    in_string = False
    escaped = False
    for index, char in enumerate(stripped):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                rest = stripped[index + 1 :].strip()
                if not rest:
                    return stripped[: index + 1]
                if rest.startswith("{") or rest.startswith("["):
                    raise ResponseParseError(
                        "multiple-documents", "candidate holds concatenated JSON objects"
                    )
                raise ResponseParseError(
                    "trailing-text", "candidate holds text after the JSON object"
                )
    raise ResponseParseError("invalid-json", "candidate holds unbalanced JSON")


def parse_response(normalized: NormalizedGeneration) -> dict:
    """Parse one provider reply strictly into a JSON dict (never compiled).

    Finish handling first (``refusal``/``error``/``length`` abort the
    candidate), then single-document extraction, then ``parse_scenario_json``
    for the duplicate-key/size/depth guards. Returns plain JSON data.
    """
    if normalized.finish == "refusal":
        raise ResponseParseError("refusal", "provider refused the request")
    if normalized.finish == "error":
        raise ResponseParseError("provider-error", "provider reported an error result")
    if normalized.finish == "length":
        raise ResponseParseError("truncated", "provider truncated the candidate")
    document = _extract_document(normalized.text)
    try:
        scenario = parse_scenario_json(document)
    except ScenarioParseError as exc:
        raise ResponseParseError("invalid-json", f"invalid candidate: {exc}") from exc
    dumped = scenario.model_dump(mode="json")
    if not isinstance(dumped, dict):
        raise ResponseParseError("invalid-json", "candidate is not a JSON object")
    return dumped
