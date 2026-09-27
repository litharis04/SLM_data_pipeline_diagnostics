# Generator task sequence

This directory turns `docs/GENERATOR_SPEC.md` into implementable slices for a lightweight
agent. `AGENTS.md`, this file, and the complete task file for the current step MUST be read
before work begins. Normative detail lives in `docs/GENERATOR_SPEC.md` (plus
`docs/PIPELINE_SPEC.md` §§5–9 and `docs/SCENARIO_SPEC.md` §17); task files define scope and
acceptance only and MUST NOT contradict the specs. On any disagreement, report it and follow
the spec unless the task explicitly updates it.

## Target layout

```text
src/data_pipeline_diagnostics/generator/
├── __init__.py      # public API: build_raw_plan, render_dbt_project, prepare_clean_instance
├── rng.py           # G02 named streams
├── raw_values.py    # G03–G05 mini-generator execution
├── raw_constraints.py  # G06 nulls/keys/uniqueness
├── relationships.py # G07 relationship sampling + row-count conditioning
├── raw_plan.py      # G08 RawPlan builder + ordering
├── physical.py      # G10 naming/quoting/literals/types + G09 Parquet/DuckDB I/O
├── dbt_render.py    # G11–G16 dbt project + SQL + assertion lowering
├── clean.py         # G17 clean control + failure records
├── records.py       # G18 instance/failure record schemas
└── cache.py         # G19 cache + handoff
tests/generator/
└── test_g*.py       # one file per task
```

Exact module splits MAY change; public function names MUST NOT (spec §4.4).

## Execution order

Sequential, in numeric order. Each task is scoped to ~10 minutes for a lightweight agent:

1. G01–G02: scaffold, API boundary, determinism foundation.
2. G03–G07: raw-data execution (values, Faker, constraints, relationships).
3. G08–G09: planning, ordering, Parquet/DuckDB materialization.
4. G10–G11: physical contract, dbt scaffolding.
5. G12–G16: SQL rendering + assertion lowering.
6. G17–G19: clean control, records, cache.
7. G20: end-to-end minimum gate (§21.0) + full-corpus dataset-build run (§21.4).

Later tasks consume earlier modules; do not skip ahead. G20 is the only task that runs dbt
against the full 144-scenario corpus.

## Shared working rules

- Compiler input is `ValidatedScenario` only; a bare `Scenario`, dict, or path MUST NOT reach
  compiler code (facade parses + validates first). Never read fault-injection config or
  oracle-only metadata; clean preparation has no fault label.
- Determinism: all randomness from G02 named streams; no `random` module-global state,
  no `hash()`, no wall-clock, no locale defaults. File enumeration and YAML/SQL ordering
  deterministic; generated text UTF-8 + LF + single final newline.
- Where the spec explicitly defers technique to tasks (cast-matrix corners beyond §14.2,
  CTE materialization barrier per §14.1, row-count retry limits per §9.3), choose the
  simplest deterministic option, document it in the task's test file header, and pin the
  behavior with a test. Do not invent new public contracts.
- Tests MUST run without network and without long cloud jobs.
- Per task: add the test file, run `pytest tests/generator/<file> -q` plus the full suite,
  run `ruff check` and `ruff format --check` (fix what you touched), run `git diff --check`.
- Append one concise bullet per completed task to `STATE.md` (generator section).
