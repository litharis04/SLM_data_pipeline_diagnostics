# G11: dbt project scaffolding

## Goal
Render the fixed dbt project shell per `docs/GENERATOR_SPEC.md` §§6, 13. No model SQL yet
(G12–G15); no tests yet (G16).

## Spec refs
- `docs/GENERATOR_SPEC.md` §§6 (instance layout), 13.1 (fixed config: names from G10,
  `../pipeline.duckdb` relative path, `dbt-duckdb`, 1 thread, table materialization, `main`
  schema, `raw` source, no packages), 13.2 (`source()` in staging, `ref()` downstream,
  `topological_order` for intermediates, declaration order otherwise; unknown variant fails).

## Do
- In `dbt_render.py`: `render_dbt_project(validated, destination)` writes
  `dbt_project.yml`, `profiles.yml` (relative `../pipeline.duckdb`, no secrets),
  `models/sources.yml` (one `raw` source, exact table identifiers),
  `models/staging|intermediate|output/` dirs, `macros/` dir, `models/assertions.yml`
  placeholder (filled in G16). All models materialized `table`, schema `main`, quoted
  identifiers. Deterministic file enumeration/order.
- Renderer dispatches on discriminator fields with exhaustive branches; unknown variant
  raises (never passthrough/omit).

## Tests — `tests/generator/test_g11_scaffold.py`
- Layout matches §6 `dbt/` subtree on `tests/scenario/fixtures/valid/minimal.json`.
- `profiles.yml` contains no credentials and points at `../pipeline.duckdb`.
- Thread count is 1 in profile/project config; no `packages.yml`, no network refs.
- Unknown discriminator value raises (construct a bad object programmatically).

## Accept
- [ ] `dbt parse`-able shell (full `dbt build` deferred to G17; parse check optional here).
- [ ] Tests green; full suite green; ruff clean; `git diff --check` clean.
- [ ] `STATE.md` bullet appended.
