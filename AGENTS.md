## 1. Project purpose

This project generates reproducible local data pipelines for learning and experimentation.
A declarative scenario describes synthetic tables, relationships, transformations, joins, and
analytical outputs. The generator produces Parquet data, a DuckDB database, a runnable dbt
project with blocking tests, and an instance record.

The scenario language, example corpus, and materializer are implemented. The next deliverable
is a CLI for authoring scenarios from a user's domain and pipeline requirements through an
external LLM API, materializing them, and rebuilding existing scenarios with another `data_seed`.

## 2. Architecture and boundaries

```text
user request -> optional external LLM -> scenario JSON
scenario JSON -> strict parsing -> semantic validation -> ValidatedScenario
  -> seeded raw data / Parquet -> DuckDB
  -> dbt staging -> intermediate -> output tables -> dbt tests
  -> instance record + verified cache + isolated working copy
```

The LLM authors declarative scenarios; the compiler derives raw data and SQL from the validated
contract. Materialization is deterministic for fixed scenario content, seed, and compatible
implementation/runtime versions. Preparing an existing scenario does not require an LLM.

## 3. Sources of truth and repository map

- `docs/PIPELINE_SPEC.md`: architecture, instance lifecycle, caching, and failure semantics.
- `docs/SCENARIO_SPEC.md`: scenario language and validation contract.
- `docs/SCENARIO_AUTHORING.md`: example-corpus authoring and coverage rules.
- `docs/GENERATOR_SPEC.md`: raw generation, SQL/dbt rendering, execution, and records.
- `docs/CLI_SPEC.md`: CLI commands, API connections, user requirements, and authoring lifecycle.
- `src/data_pipeline_diagnostics/`: implemented scenario contract and pipeline generator.
- `scenarios/`: accepted examples and their coverage plan; `tasks/`: implementation instructions.
- `tests/`: pytest checks; `artifacts/`: schemas, build evidence, reports, and generated instances.
- `STATE.md`: implementation status and history, not a specification.

## 4. Working rules

- Core compiler operations accept `ValidatedScenario`; validate authored JSON before compilation.
- Use named seed streams for synthetic data. Keep LLM authoring separate from deterministic
  materialization and preserve the authored scenario needed to reproduce an instance.
- Keep API keys out of version control, logs, and generated artifacts. Tests run offline and
  mock external LLM calls.
- Preserve immutable cache entries and bump the relevant implementation version for
  output-affecting changes.
- Do not weaken validators or blocking tests to admit a generated scenario or failed build.
- Change public APIs or data contracts only when required by the task, updating docs and tests.
- Prefer small, testable vertical slices and run all quality gates relevant to the change.
- Update `STATE.md` when a task materially changes project state.
- Report implementation/specification disagreements; follow the task specification when it
  explicitly updates the affected contract, otherwise follow the existing specification.

## 5. Task execution

1. Read the complete task, referenced specifications, and relevant existing implementation.
2. Make the requested changes directly in the repository and run the required validation.
3. Re-read the task and verify every checklist item against the actual repository state.
4. Update required state/documentation and report completion only when all items are met.

Report any missing checks or unmet requirements explicitly; do not mark incomplete work complete.
