"""Deterministic bundled-example pair selection (C05, §6.2).

Reuses the C04 feature extractor and shared predicates: no LLM, no text
search, no materialization, no personal-catalog access. The caller passes
the accepted bundled corpus (C11 passes the installed bundle); personal
edits or deletions can never influence the outcome.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from data_pipeline_diagnostics.cli import features as _features
from data_pipeline_diagnostics.cli.features import AuthoringRequest, ScenarioFeatures
from data_pipeline_diagnostics.scenario import (
    ValidatedScenario,
    canonical_json,
    scenario_content_hash,
)

_FEATURE_CACHE: dict[str, ScenarioFeatures] = {}


def clear_feature_cache() -> None:
    """Drop cached per-scenario extraction results (tests, mostly)."""
    _FEATURE_CACHE.clear()


def requested_tags(request: AuthoringRequest) -> tuple[str, ...]:
    """Requested feature tags: staging/intermediate/metrics/JOIN/composite only.

    Identifiers, domains, sizes, and seeds are never matching tags.
    """
    tags = {f"staging:{operation}" for operation in request.staging}
    tags |= {f"intermediate:{feature}" for feature in request.intermediate}
    tags |= {f"metric:{function}" for function in request.metrics}
    if request.joins == "inner":
        tags.add("join:inner")
    elif request.joins == "left":
        tags.add("join:left")
    elif request.joins == "mixed":
        tags.update({"join:inner", "join:left"})
    if request.composite_keys == "required":
        tags.add("composite-pk:required")
    elif request.composite_keys == "forbidden":
        tags.add("composite-pk:forbidden")
    return tuple(sorted(tags))


def covered_tags(features: ScenarioFeatures) -> set[str]:
    """Requested-tag vocabulary entries this scenario's features satisfy."""
    covered = {f"staging:{operation}" for operation in features.staging_ops}
    covered |= {f"intermediate:{feature}" for feature in features.intermediate_features}
    covered |= {f"metric:{function}" for function in features.metric_functions}
    covered |= {f"join:{join_type}" for join_type in features.join_types}
    if features.has_composite_raw_pk:
        covered.add("composite-pk:required")
    else:
        covered.add("composite-pk:forbidden")
    return covered


def _predicate_count(features: ScenarioFeatures, request: AuthoringRequest) -> int:
    """Individually satisfied explicit composite-key/JOIN-mode predicates."""
    count = 0
    if request.composite_keys != "auto" and _features.satisfies_composite_key(
        features.has_composite_raw_pk, request.composite_keys
    ):
        count += 1
    if request.joins != "auto" and _features.satisfies_join_mode(
        features.join_types, request.joins
    ):
        count += 1
    return count


@dataclass(frozen=True)
class ExampleAnnotations:
    """Deterministic annotations for the selected pair (prompt block 3)."""

    first_id: str
    second_id: str
    covered_first: tuple[str, ...]
    covered_second: tuple[str, ...]
    absent: tuple[str, ...]

    def format(self) -> str:
        def render(tags: tuple[str, ...]) -> str:
            return ", ".join(tags) if tags else "(none)"

        return "\n".join(
            [
                f"Example 1 {self.first_id} covers: {render(self.covered_first)}",
                f"Example 2 {self.second_id} covers: {render(self.covered_second)}",
                f"Requested but absent from both examples: {render(self.absent)}",
            ]
        )


@dataclass(frozen=True)
class _Candidate:
    validated: ValidatedScenario
    scenario_id: str
    features: ScenarioFeatures
    covered: frozenset[str] = field(compare=False)
    predicates: int = field(compare=False)
    compact_len: int = field(compare=False)


def _cached_features(validated: ValidatedScenario) -> ScenarioFeatures:
    digest = scenario_content_hash(validated.scenario)
    cached = _FEATURE_CACHE.get(digest)
    if cached is None:
        cached = _features.extract_features(validated)
        _FEATURE_CACHE[digest] = cached
    return cached


def select_examples(
    request: AuthoringRequest, bundled: list[ValidatedScenario]
) -> tuple[ValidatedScenario, ValidatedScenario, ExampleAnnotations]:
    """Select the best distinct example pair under the §6.2 ordered ranking.

    Returns the winning input objects in lexicographic id order plus their
    annotations. Uncoverable requirements are kept and reported, never dropped.
    """
    if len(bundled) < 2:
        raise ValueError("example selection needs at least two bundled scenarios")
    tags = requested_tags(request)
    wanted = set(tags)
    candidates = []
    for validated in bundled:
        features = _cached_features(validated)
        compact = canonical_json(validated.scenario).decode("utf-8")
        candidates.append(
            _Candidate(
                validated=validated,
                scenario_id=str(validated.scenario.scenario_id),
                features=features,
                covered=frozenset(covered_tags(features) & wanted),
                predicates=_predicate_count(features, request),
                compact_len=len(compact),
            )
        )
    candidates.sort(key=lambda item: item.scenario_id)
    best: tuple[int, int, int, tuple[str, str]] | None = None
    best_pair: tuple[_Candidate, _Candidate] | None = None
    for left in range(len(candidates)):
        for right in range(left + 1, len(candidates)):
            first, second = candidates[left], candidates[right]
            union = first.covered | second.covered
            key = (
                -len(union),
                -(first.predicates + second.predicates),
                first.compact_len + second.compact_len,
                (first.scenario_id, second.scenario_id),
            )
            if best is None or key < best:
                best = key
                best_pair = (first, second)
    assert best_pair is not None
    first, second = sorted(best_pair, key=lambda item: item.scenario_id)
    covered_first = tuple(sorted(first.covered))
    covered_second = tuple(sorted(second.covered))
    annotations = ExampleAnnotations(
        first_id=first.scenario_id,
        second_id=second.scenario_id,
        covered_first=covered_first,
        covered_second=covered_second,
        absent=tuple(sorted(wanted - set(covered_first) - set(covered_second))),
    )
    return first.validated, second.validated, annotations
