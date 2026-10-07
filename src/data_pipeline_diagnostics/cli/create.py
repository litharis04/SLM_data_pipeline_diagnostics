"""``plgen create`` — bounded authoring lifecycle to a verified instance (C12, §6.3).

At most five generation requests against frozen reference material; each
repair carries only the latest candidate plus current structured errors.
Four-stage acceptance per attempt (strict parse → semantic → structural +
size preflight → clean build → realized size from the instance record).
Data-dependent build failures are repairable feedback; infra, provider, and
refusal outcomes abort. Success publishes atomically (scenario files, full
entry, catalog entry last); failures never register a catalog entry and never
invalidate cached baselines. ``KeyboardInterrupt`` propagates: no
publication, no pointer switch.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Mapping
from pathlib import Path

from data_pipeline_diagnostics.cli.cmd_connect import ProviderProfile
from data_pipeline_diagnostics.cli.examples import select_examples
from data_pipeline_diagnostics.cli.features import (
    AuthoringRequest,
    RequirementIssue,
    ScenarioFeatures,
    check_requirements,
    classify_raw_size,
    extract_features,
)
from data_pipeline_diagnostics.cli.instances import (
    Prepare,
    publish_working_copy,
    scrubbed_build,
)
from data_pipeline_diagnostics.cli.prompt import (
    SIZE_BOUNDS,
    ResponseParseError,
    build_prompt,
    normalize_create_request,
    parse_response,
)
from data_pipeline_diagnostics.cli.providers import (
    GeminiAdapter,
    NormalizedGeneration,
    OpenRouterAdapter,
    ProviderError,
    Sender,
    sanitize_error,
)
from data_pipeline_diagnostics.cli.workspace import (
    atomic_write_bytes,
    atomic_write_json,
    catalog_path,
    config_path,
    ensure_workspace,
    instance_pointer,
)
from data_pipeline_diagnostics.generator.cache import CleanInstance, prepare_clean_instance
from data_pipeline_diagnostics.generator.raw_constraints import GenerationFailure
from data_pipeline_diagnostics.scenario import (
    ScenarioParseError,
    SemanticValidationError,
    ValidatedScenario,
    canonical_json,
    get_scenario_json_schema,
    parse_scenario_json,
    scenario_content_hash,
    validate_semantics,
)

MAX_ATTEMPTS = 5

# Build failures the author can repair with new content (bad authored cast,
# unmapped category, empty output). Every other generator failure is
# infrastructure: abort instead of asking the LLM to compensate for it.
DATA_DEPENDENT_BUILD_REASONS = frozenset({"dbt-test-failure", "dbt-model-error"})


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[3]


def load_spec_text() -> str:
    """Full spec text: installed resource first, repo checkout as dev fallback."""
    try:
        from importlib.resources import files

        resource = files("data_pipeline_diagnostics") / "SCENARIO_SPEC.md"
        if resource.is_file():
            return resource.read_text(encoding="utf-8")
    except ImportError, FileNotFoundError, NotADirectoryError, OSError:
        pass
    return (_repository_root() / "docs" / "SCENARIO_SPEC.md").read_text(encoding="utf-8")


def load_schema_json() -> str:
    """Compact Pydantic-derived JSON Schema (no dropped definitions)."""
    return json.dumps(get_scenario_json_schema(), separators=(",", ":"), ensure_ascii=False)


def load_bundled() -> list[ValidatedScenario]:
    """Accepted bundled corpus as validated scenarios (C13 makes it installed data)."""
    from data_pipeline_diagnostics.cli.bundle import iter_bundled_scenarios

    return [validate_semantics(parse_scenario_json(data)) for _, data in iter_bundled_scenarios()]


def _read_json_file(path: Path) -> dict[str, object] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError, ValueError:
        return None
    return data if isinstance(data, dict) else None


def _allocate_run_dir(root: Path, scenario_id: str) -> Path:
    authoring = root / "authoring"
    authoring.mkdir(parents=True, exist_ok=True)
    candidate = authoring / scenario_id
    if not candidate.exists():
        candidate.mkdir()
        return candidate
    counter = 2
    while True:
        numbered = authoring / f"{scenario_id}_{counter:02d}"
        if not numbered.exists():
            numbered.mkdir()
            return numbered
        counter += 1


def _repair_block(candidate_text: str, issues: list[RequirementIssue]) -> str:
    lines = [
        "Previous candidate (return a complete replacement scenario, never a patch):",
        candidate_text,
        "Structured errors to fix (code | path | message):",
    ]
    lines.extend(f"[{issue.code}] {issue.path}: {issue.message}" for issue in issues)
    lines.append(
        "Keep the original requirements, scenario_id, and data seed; "
        "do not relax assertions or omit requested features."
    )
    return "\n".join(lines)


def _realized_issues(
    record: Mapping[str, object], request: AuthoringRequest
) -> list[RequirementIssue]:
    tables = record.get("raw_tables")
    counts: list[int] = []
    if not isinstance(tables, list) or not tables:
        raise ValueError("instance record holds no raw_tables")
    for table in tables:
        if not isinstance(table, dict):
            raise ValueError("instance record holds a malformed raw table")
        count = table.get("row_count")
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            raise ValueError("instance record holds a malformed row_count")
        counts.append(count)
    realized = max(counts)
    lower, upper = SIZE_BOUNDS[request.size]
    if realized > upper:
        return [
            RequirementIssue(
                code="raw-size-exceeded",
                path="raw_tables",
                message=(
                    f"realized max raw rows {realized} exceeds "
                    f'the "{request.size}" preset ceiling of {upper}'
                ),
            )
        ]
    if realized < lower:
        return [
            RequirementIssue(
                code="raw-size-below",
                path="raw_tables",
                message=(
                    f"realized max raw rows {realized} is below "
                    f'the "{request.size}" preset lower bound of {lower}'
                ),
            )
        ]
    return []


def _usage_dict(result: NormalizedGeneration) -> dict[str, object] | None:
    if result.usage is None:
        return None
    return {
        "input_tokens": result.usage.input_tokens,
        "output_tokens": result.usage.output_tokens,
        "reasoning_tokens": result.usage.reasoning_tokens,
    }


def publish_scenario(
    root: Path,
    request: AuthoringRequest,
    validated: ValidatedScenario,
    instance: CleanInstance,
    record: Mapping[str, object],
    provenance: Mapping[str, object],
    features: ScenarioFeatures,
    realized_max: int,
    realized_category: str,
) -> tuple[Path, Path]:
    """Atomically publish scenario files, full entry, and the catalog entry last."""
    scenario_id = request.scenario_id
    target = root / "scenarios" / scenario_id
    target.mkdir(parents=True, exist_ok=True)
    atomic_write_bytes(target / "scenario.json", canonical_json(validated.scenario))
    workdir, _ = publish_working_copy(target, instance)
    ceiling_key = request.size
    entry: dict[str, object] = {
        "origin": "authored",
        "seed": request.seed,
        "scenario_hash": scenario_content_hash(validated.scenario),
        "instance": instance_pointer(workdir, instance.instance_digest),
        "requirements": request.to_dict(),
        "provenance": dict(provenance),
        "checks": {
            "requirement_issues": [],
            "size_preflight": {
                "max_declared": features.max_raw_rows_max,
                "preset": ceiling_key,
            },
            "realized": {"max_raw_rows": realized_max, "category": realized_category},
        },
    }
    atomic_write_json(target / "entry.json", entry)
    catalog = _read_json_file(catalog_path(root))
    if catalog is None or not isinstance(catalog.get("scenario_ids"), list):
        raise ValueError(f"unreadable catalog {catalog_path(root)}")
    ids = list(catalog["scenario_ids"])
    if scenario_id in ids:
        raise ValueError(f"identifier collision at publish: {scenario_id!r}")
    ids.append(scenario_id)
    atomic_write_json(
        catalog_path(root),
        {"bootstrap_major_minor": catalog.get("bootstrap_major_minor"), "scenario_ids": ids},
    )
    return target / "scenario.json", workdir


def _check_candidate(
    candidate: dict[str, object], request: AuthoringRequest
) -> tuple[ValidatedScenario | None, list[RequirementIssue]]:
    try:
        scenario = parse_scenario_json(json.dumps(candidate))
    except ScenarioParseError as exc:
        return None, [
            RequirementIssue(code="invalid-syntax", path=exc.path or "candidate", message=str(exc))
        ]
    try:
        validated = validate_semantics(scenario)
    except SemanticValidationError as exc:
        return None, [
            RequirementIssue(code=issue.code, path=issue.path, message=issue.message)
            for issue in exc.issues
        ]
    return validated, list(check_requirements(extract_features(validated), request))


def run_create(
    workspace: Path,
    args: argparse.Namespace,
    *,
    sender: Sender | None = None,
    prepare: Prepare | None = None,
    cache_root: Path | None = None,
    spec_text: str | None = None,
    schema_json: str | None = None,
    bundled: list[ValidatedScenario] | None = None,
    env: Mapping[str, str] | None = None,
) -> int:
    """Run the bounded authoring lifecycle and publish on full acceptance."""
    root = Path(workspace)
    ensure_workspace(root)
    catalog = _read_json_file(catalog_path(root))
    if catalog is None or not isinstance(catalog.get("scenario_ids"), list):
        print(f"plgen create: unreadable catalog {catalog_path(root)}", file=sys.stderr)
        return 5
    try:
        request = normalize_create_request(args, catalog["scenario_ids"])
    except ValueError as exc:
        print(f"plgen create: invalid request: {exc}", file=sys.stderr)
        return 2
    config = _read_json_file(config_path(root))
    if config is None or not isinstance(config.get("profiles"), dict):
        print(f"plgen create: unreadable config {config_path(root)}", file=sys.stderr)
        return 5
    profiles = config["profiles"]
    assert isinstance(profiles, dict)
    active = config.get("active_provider")
    stored = profiles.get(active) if isinstance(active, str) else None
    if not isinstance(stored, dict):
        print("plgen create: no active provider profile (run plgen connect first)", file=sys.stderr)
        return 3
    try:
        profile = ProviderProfile.from_dict(stored)
    except ValueError as exc:
        print(f"plgen create: unreadable active profile: {exc}", file=sys.stderr)
        return 5
    environ = os.environ if env is None else env
    key = environ.get(profile.key_env)
    if key is None or not key.strip():
        print(
            f"plgen create: missing credential: env var {profile.key_env} is empty or missing",
            file=sys.stderr,
        )
        return 3
    try:
        text = spec_text if spec_text is not None else load_spec_text()
        schema = schema_json if schema_json is not None else load_schema_json()
        bundle = bundled if bundled is not None else load_bundled()
        first, second, annotations = select_examples(request, list(bundle))
        blocks = build_prompt(request, text, schema, [first, second], annotations)
    except Exception as exc:
        print(f"plgen create: cannot load authoring resources: {exc}", file=sys.stderr)
        return 5
    system, user_fixed = blocks.to_system_user()
    if profile.provider == "openrouter":
        adapter: OpenRouterAdapter | GeminiAdapter = OpenRouterAdapter(
            model=profile.model, api_key=key, transport=sender
        )
    else:
        adapter = GeminiAdapter(model=profile.model, api_key=key, transport=sender)
    build = prepare or prepare_clean_instance
    cache_dir = Path(cache_root) if cache_root is not None else root / "cache"
    run_dir = _allocate_run_dir(root, request.scenario_id)
    (run_dir / "request.json").write_text(
        json.dumps(
            {
                "requirements": request.to_dict(),
                "provider": profile.provider,
                "model": profile.model,
                "key_env": profile.key_env,
                "seed": request.seed,
                "max_output_tokens": profile.max_output_tokens,
                "examples": [annotations.first_id, annotations.second_id],
                "absent": list(annotations.absent),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    last_text = ""
    last_issues: list[RequirementIssue] = []
    for attempt in range(1, MAX_ATTEMPTS + 1):
        user = (
            user_fixed
            if attempt == 1
            else f"{user_fixed}\n\n{_repair_block(last_text, last_issues)}"
        )
        print(
            f"plgen create: attempt {attempt}/{MAX_ATTEMPTS}: requesting candidate "
            f"via {profile.provider} model {profile.model}",
            file=sys.stderr,
        )
        try:
            result = adapter.generate(
                system=system, user=user, max_output_tokens=profile.max_output_tokens
            )
        except ProviderError as exc:
            print(f"plgen create: provider failure: {sanitize_error(exc)}", file=sys.stderr)
            return 3
        print(
            f"plgen create: attempt {attempt}/{MAX_ATTEMPTS}: validating candidate",
            file=sys.stderr,
        )
        issues: list[RequirementIssue]
        validated: ValidatedScenario | None = None
        build_record: str | None = None
        try:
            candidate = parse_response(result)
        except ResponseParseError as exc:
            if exc.code == "refusal":
                print(
                    "plgen create: provider refused the request "
                    "(a refusal is not proof the pipeline is impossible)",
                    file=sys.stderr,
                )
                return 3
            if exc.code == "provider-error":
                print(f"plgen create: provider error result: {exc.message}", file=sys.stderr)
                return 3
            issues = [RequirementIssue(code=exc.code, path="candidate", message=exc.message)]
        else:
            validated, issues = _check_candidate(candidate, request)
        if not issues:
            assert validated is not None
            print(
                f"plgen create: attempt {attempt}/{MAX_ATTEMPTS}: building clean instance",
                file=sys.stderr,
            )
            try:
                instance = scrubbed_build(build, validated, request.seed, cache_dir, config)
            except GenerationFailure as exc:
                record_path = Path(getattr(exc, "workspace", cache_dir)) / "failure_record.json"
                if exc.reason in DATA_DEPENDENT_BUILD_REASONS:
                    issues = [
                        RequirementIssue(
                            code="build-failed",
                            path="instance",
                            message=(f"{exc.reason}: {exc.detail} (failure record: {record_path})"),
                        )
                    ]
                    build_record = str(record_path)
                    validated = None
                else:
                    print(
                        f"plgen create: infrastructure failure "
                        f"({exc.reason}: {exc.detail}; see {record_path})",
                        file=sys.stderr,
                    )
                    return 5
            else:
                print(
                    f"plgen create: attempt {attempt}/{MAX_ATTEMPTS}: checking realized size",
                    file=sys.stderr,
                )
                try:
                    record = json.loads(
                        (instance.instance_dir / "instance_record.json").read_text(encoding="utf-8")
                    )
                    if not isinstance(record, dict):
                        raise ValueError("instance record must be a JSON object")
                    issues = _realized_issues(record, request)
                except (OSError, ValueError) as exc:
                    print(f"plgen create: unreadable instance record: {exc}", file=sys.stderr)
                    return 5
        (run_dir / f"attempt_{attempt:02d}.json").write_text(
            json.dumps(
                {
                    "attempt": attempt,
                    "finish": result.finish,
                    "model_identity": result.model_identity,
                    "usage": _usage_dict(result),
                    "candidate_text": result.text,
                    "issues": [
                        {"code": i.code, "path": i.path, "message": i.message} for i in issues
                    ],
                    "failure_record": build_record,
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        if not issues:
            assert validated is not None
            features = extract_features(validated)
            tables = record.get("raw_tables")
            assert isinstance(tables, list)
            counts = [int(table["row_count"]) for table in tables]
            realized_max = max(counts)
            category = classify_raw_size(realized_max)
            provenance = {
                "provider": profile.provider,
                "model": profile.model,
                "key_env": profile.key_env,
                "model_identity": result.model_identity,
                "usage": _usage_dict(result),
            }
            scenario_file, workdir = publish_scenario(
                root,
                request,
                validated,
                instance,
                record,
                provenance,
                features,
                realized_max,
                category,
            )
            print(
                f"created {request.scenario_id} seed={request.seed} size={category} ({realized_max} rows)"
            )
            print(f"scenario: {scenario_file.resolve()}")
            print(f"instance: {workdir.resolve()}")
            return 0
        last_text, last_issues = result.text, issues
    assert last_issues
    final = last_issues[0]
    (run_dir / "failure_record.json").write_text(
        json.dumps(
            {
                "status": "attempts-exhausted",
                "attempts": MAX_ATTEMPTS,
                "final": {"code": final.code, "path": final.path, "message": final.message},
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    print(
        f"plgen create: failed after {MAX_ATTEMPTS} attempts: "
        f"[{final.code}] {final.path}: {final.message}",
        file=sys.stderr,
    )
    print(f"diagnostics: {run_dir.resolve()}", file=sys.stderr)
    return 4
