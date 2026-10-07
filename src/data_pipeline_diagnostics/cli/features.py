"""Shared structural feature extraction and request checks (C04, §5.2).

Pure in-memory logic over the typed scenario models: no SQL, no network.
Used by ``create``/``seed`` request checking and deterministic example
selection. Checks are structural-presence only (§5.3): a present operation
satisfies its requirement even when it has no observable data effect.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from pydantic import TypeAdapter, ValidationError

from data_pipeline_diagnostics.cli.workspace import validate_scenario_id
from data_pipeline_diagnostics.scenario import DomainName, ValidatedScenario

# Raw-layer size preset ceilings (§4.2): inclusive upper bounds on M_raw,
# the maximum realized (or declared, for preflight) row count over raw
# tables. Single source of truth; cmd_list reuses the table + classifier.
SIZE_PRESET_CEILINGS = {"small": 1_000, "medium": 10_000, "large": 100_000}

SIZE_CHOICES = ("small", "medium", "large")
COMPOSITE_CHOICES = ("auto", "required", "forbidden")
JOINS_CHOICES = ("auto", "inner", "left", "mixed")

STAGING_VOCABULARY = frozenset(
    {
        "trim",
        "lower",
        "upper",
        "replace",
        "map_values",
        "null_if",
        "coalesce",
        "cast",
        "filter",
        "deduplicate",
    }
)
INTERMEDIATE_VOCABULARY = frozenset({"filter", "derive", "deduplicate"})
METRIC_VOCABULARY = frozenset(
    {
        "count_rows",
        "count",
        "count_distinct",
        "sum",
        "avg",
        "min",
        "max",
        "conditional_count",
        "conditional_sum",
    }
)

_MAX_DATA_SEED = 2**63 - 1

_DOMAIN_ADAPTER = TypeAdapter(DomainName)


@dataclass(frozen=True)
class ScenarioFeatures:
    """Immutable structural summary of a validated scenario (§5.2)."""

    scenario_id: str
    domain: str
    has_composite_raw_pk: bool
    staging_ops: frozenset[str]
    intermediate_features: frozenset[str]
    join_types: frozenset[str]
    metric_functions: frozenset[str]
    max_raw_rows_max: int


@dataclass(frozen=True)
class AuthoringRequest:
    """Frozen normalized user requirements (fixed before request 1)."""

    scenario_id: str
    domain: str
    size: str
    composite_keys: str
    staging: frozenset[str]
    intermediate: frozenset[str]
    joins: str
    metrics: frozenset[str]
    seed: int


@dataclass(frozen=True)
class RequirementIssue:
    """One deterministic request violation (CLI-owned, not a core error)."""

    code: str
    path: str
    message: str


def classify_raw_size(max_rows: int) -> str:
    """Map realized ``M_raw`` onto its §4.2 size category."""
    if max_rows <= SIZE_PRESET_CEILINGS["small"]:
        return "small"
    if max_rows <= SIZE_PRESET_CEILINGS["medium"]:
        return "medium"
    if max_rows <= SIZE_PRESET_CEILINGS["large"]:
        return "large"
    return "outside-presets"


def satisfies_join_mode(join_types: frozenset[str], mode: str) -> bool:
    """Strict whole-example JOIN-mode predicate shared by checks and ranking."""
    if mode == "auto":
        return True
    expected = {"inner"} if mode == "inner" else {"left"} if mode == "left" else {"inner", "left"}
    if mode not in ("inner", "left", "mixed"):
        return False
    return set(join_types) == expected


def satisfies_composite_key(has_composite_raw_pk: bool, mode: str) -> bool:
    """Composite-key predicate shared by checks and ranking."""
    if mode == "auto":
        return True
    if mode == "required":
        return has_composite_raw_pk
    if mode == "forbidden":
        return not has_composite_raw_pk
    return False


def _contains_derivation(expression: object) -> bool:
    """True when the expression holds a ``binary``/``date_part`` node (nested)."""
    kind = getattr(expression, "kind", None)
    if kind in ("binary", "date_part"):
        return True
    if kind == "coalesce":
        return any(_contains_derivation(value) for value in getattr(expression, "values", ()))
    return False


def extract_features(validated: ValidatedScenario) -> ScenarioFeatures:
    """Extract the §5.2 structural summary from a validated scenario."""
    scenario = validated.scenario
    staging_ops = {
        str(operation.op)
        for model in scenario.staging_models
        for column in model.columns
        for operation in column.operations
    } | {
        str(operation.op) for model in scenario.staging_models for operation in model.row_operations
    }
    intermediates = list(scenario.intermediate_models)
    intermediate_features = set()
    if any(len(getattr(model, "filters", ())) > 0 for model in intermediates):
        intermediate_features.add("filter")
    if any(
        _contains_derivation(derived.expression)
        for model in intermediates
        for derived in getattr(model, "derived_columns", ())
    ):
        intermediate_features.add("derive")
    if any(model.operation == "deduplicate" for model in intermediates):
        intermediate_features.add("deduplicate")
    return ScenarioFeatures(
        scenario_id=str(scenario.scenario_id),
        domain=str(scenario.domain),
        has_composite_raw_pk=any(len(table.primary_key) > 1 for table in scenario.raw_tables),
        staging_ops=frozenset(staging_ops),
        intermediate_features=frozenset(intermediate_features),
        join_types=frozenset(
            str(model.join.type) for model in intermediates if model.operation == "join"
        ),
        metric_functions=frozenset(
            str(metric.function) for output in scenario.output_models for metric in output.metrics
        ),
        max_raw_rows_max=max(table.rows.max for table in scenario.raw_tables),
    )


def check_requirements(
    features: ScenarioFeatures, request: AuthoringRequest
) -> tuple[RequirementIssue, ...]:
    """Check normalized requirements; deterministic order, one pass."""
    issues: list[RequirementIssue] = []
    if features.scenario_id != request.scenario_id:
        issues.append(
            RequirementIssue(
                code="id-mismatch",
                path="scenario_id",
                message=(
                    f"scenario id {features.scenario_id!r} "
                    f"does not equal required {request.scenario_id!r}"
                ),
            )
        )
    if features.domain != request.domain:
        issues.append(
            RequirementIssue(
                code="domain-mismatch",
                path="domain",
                message=(
                    f"scenario domain {features.domain!r} "
                    f"does not equal required {request.domain!r}"
                ),
            )
        )
    for operation in sorted(request.staging):
        if operation not in features.staging_ops:
            issues.append(
                RequirementIssue(
                    code="staging-missing",
                    path="staging",
                    message=f'required staging operation "{operation}" is absent',
                )
            )
    for feature in sorted(request.intermediate):
        if feature not in features.intermediate_features:
            issues.append(
                RequirementIssue(
                    code="intermediate-missing",
                    path="intermediate",
                    message=f'required intermediate feature "{feature}" is absent',
                )
            )
    if request.joins != "auto" and not satisfies_join_mode(features.join_types, request.joins):
        expected = (
            {"inner"}
            if request.joins == "inner"
            else {"left"}
            if request.joins == "left"
            else {"inner", "left"}
        )
        issues.append(
            RequirementIssue(
                code="join-mode-mismatch",
                path="joins",
                message=(
                    f'join mode "{request.joins}" requires exactly '
                    f"{sorted(expected)}, found {sorted(features.join_types)}"
                ),
            )
        )
    for function in sorted(request.metrics):
        if function not in features.metric_functions:
            issues.append(
                RequirementIssue(
                    code="metric-missing",
                    path="output_models",
                    message=f'required metric function "{function}" is absent from output models',
                )
            )
    if request.composite_keys == "required" and not satisfies_composite_key(
        features.has_composite_raw_pk, "required"
    ):
        issues.append(
            RequirementIssue(
                code="composite-key-mismatch",
                path="raw_tables",
                message="composite raw primary key is required but no raw table declares one",
            )
        )
    if request.composite_keys == "forbidden" and not satisfies_composite_key(
        features.has_composite_raw_pk, "forbidden"
    ):
        issues.append(
            RequirementIssue(
                code="composite-key-mismatch",
                path="raw_tables",
                message="composite raw primary keys are forbidden but one is declared",
            )
        )
    ceiling = SIZE_PRESET_CEILINGS[request.size]
    if features.max_raw_rows_max > ceiling:
        issues.append(
            RequirementIssue(
                code="raw-size-exceeded",
                path="raw_tables",
                message=(
                    f"maximum declared raw rows.max {features.max_raw_rows_max} exceeds "
                    f'the "{request.size}" preset ceiling of {ceiling}'
                ),
            )
        )
    return tuple(issues)


def _normalize_selection(
    raw: str | Iterable[str] | None, vocabulary: frozenset[str], option: str
) -> frozenset[str]:
    if raw is None:
        return frozenset()
    tokens = raw.split(",") if isinstance(raw, str) else list(raw)
    selected: set[str] = set()
    for token in tokens:
        if not isinstance(token, str):
            raise ValueError(f"invalid {option} selection {token!r}: expected text")
        item = token.strip()
        if item == "":
            continue
        if item not in vocabulary:
            raise ValueError(
                f'invalid {option} selection "{item}": expected one of {sorted(vocabulary)}'
            )
        selected.add(item)
    return frozenset(selected)


def normalize_request(
    *,
    scenario_id: str,
    domain: str,
    size: str = "small",
    composite_keys: str = "auto",
    staging: str | Iterable[str] | None = None,
    intermediate: str | Iterable[str] | None = None,
    joins: str = "auto",
    metrics: str | Iterable[str] | None = None,
    seed: int = 0,
) -> AuthoringRequest:
    """Build a frozen normalized request; ``ValueError`` on invalid input."""
    clean_id = validate_scenario_id(scenario_id)
    try:
        clean_domain = _DOMAIN_ADAPTER.validate_python(domain)
    except ValidationError as exc:
        raise ValueError(f"invalid domain {domain!r}") from exc
    if size not in SIZE_CHOICES:
        raise ValueError(f'invalid size "{size}": expected one of {list(SIZE_CHOICES)}')
    if composite_keys not in COMPOSITE_CHOICES:
        raise ValueError(
            f'invalid composite-keys "{composite_keys}": expected one of {list(COMPOSITE_CHOICES)}'
        )
    if joins not in JOINS_CHOICES:
        raise ValueError(f'invalid joins "{joins}": expected one of {list(JOINS_CHOICES)}')
    if not isinstance(seed, int) or isinstance(seed, bool) or not 0 <= seed <= _MAX_DATA_SEED:
        raise ValueError(f"invalid seed {seed!r}: expected 0 <= seed <= 2**63 - 1")
    return AuthoringRequest(
        scenario_id=clean_id,
        domain=clean_domain,
        size=size,
        composite_keys=composite_keys,
        staging=_normalize_selection(staging, STAGING_VOCABULARY, "--staging"),
        intermediate=_normalize_selection(intermediate, INTERMEDIATE_VOCABULARY, "--intermediate"),
        joins=joins,
        metrics=_normalize_selection(metrics, METRIC_VOCABULARY, "--metrics"),
        seed=seed,
    )
