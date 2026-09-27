# G16: Healthy-assertion lowering (dbt tests)

## Goal
Lower explicit + structural assertions to dbt generic tests per `docs/GENERATOR_SPEC.md` §15.
Prereq: G11–G15.

## Spec refs
- `docs/GENERATOR_SPEC.md` §§15.1 (explicit + structural sets; consume
  `ValidatedScenario.derived_assertions`; dedup by semantic identity keeping origin/name),
  15.2 (lowering table; vendor custom macros locally, no `dbt-utils`; all severity error),
  15.3 (exact null/unique/`accepted_values`/`relationships`/`row_count`/`column_range`
  semantics incl. composite-null handling), 15.4 (sources vs models; `unique_id` crosswalk
  SHOULD).

## Do
- In `dbt_render.py` + `macros/generated_assertions.sql`: implement the §15.2 lowering table.
  Custom macros to vendor: `composite_unique`, `composite_relationships`,
  `row_count_between`, `column_range` (null semantics per §15.3: composite keys skip
  any-null-component rows; relationships ignore wholly-null dependents, fail partial-null).
- Raw-table assertions attach to `source()`; staging/intermediate/output to `ref()` models;
  relationship targets use `source()`/`ref()` accordingly. Deterministic physical test names
  from logical name + role (stable suffix/hash helper for length).
- Write `models/assertions.yml` (G11 placeholder) with the lowered tests.

## Tests — `tests/generator/test_g16_assertions.py`
- Selection: one-column `not_null`/`unique` -> built-in; composite/row_count/range ->
  custom macros (text assertions on `assertions.yml`).
- Semantics pins: inclusive vs exclusive `column_range`; one-sided bounds;
  `accepted_values` ignores nulls; composite `unique` excludes any-null-component rows.
- Deduplication: identical explicit+derived assertion renders once with origin traceable.

## Accept
- [ ] No `dbt-utils`/external packages; no warning-severity tests.
- [ ] Tests green; full suite green; ruff clean; `git diff --check` clean.
- [ ] `STATE.md` bullet appended.
