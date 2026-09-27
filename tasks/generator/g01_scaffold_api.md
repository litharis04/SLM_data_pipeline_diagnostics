# G01: Scaffold generator package + public API boundary

## Goal
Create `src/data_pipeline_diagnostics/generator/` with the public compiler surface from
`docs/GENERATOR_SPEC.md` §§3–4. Nothing executes yet; later tasks fill the modules.

## Spec refs
- `docs/GENERATOR_SPEC.md` §§3.1–3.3 (ValidatedScenario boundary, external inputs), §4
  (components, suggested public shape), §5.1 (fixed names live here later).

## Do
- Create package `src/data_pipeline_diagnostics/generator/__init__.py` exporting:
  `build_raw_plan(validated) -> RawPlan`,
  `render_dbt_project(validated, destination) -> RenderedDbtProject`,
  `prepare_clean_instance(validated, data_seed, cache_root) -> CleanInstance`.
- Define minimal immutable placeholder types `RawPlan`, `RenderedDbtProject`, `CleanInstance`
  (fields grow in later tasks; keep them frozen dataclasses).
- Every public function MUST reject a bare `Scenario`/dict/path with `TypeError` (accept only
  `ValidatedScenario`). A thin facade `prepare_clean_instance_from_json(path_or_bytes, ...)`
  MAY exist and MUST run `parse_scenario_json -> validate_semantics -> prepare_clean_instance`.
- Validate `data_seed`: strict int, `0 <= seed <= 2**63 - 1`, else `ValueError`.
- Re-export nothing fault/oracle-related.

## Tests — `tests/generator/test_g01_api.py`
- Bare `Scenario` (and dict, and path string) rejected by all three functions.
- `data_seed` -1 and `2**63` rejected; 0 and `2**63 - 1` accepted (mock internals).
- Facade on `tests/scenario/fixtures/valid/minimal.json` reaches the compiler entry (may stub
  execution; assert validation ran, i.e. invalid fixture raises before any build).

## Accept
- [ ] Package imports cleanly; public names exactly as spec §4.4.
- [ ] Boundary tests green; full suite green; ruff clean; `git diff --check` clean.
- [ ] `STATE.md` bullet appended.
