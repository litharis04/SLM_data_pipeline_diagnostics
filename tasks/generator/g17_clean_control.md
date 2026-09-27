# G17: Clean control + failure semantics

## Goal
Execute the clean build and enforce failure semantics per `docs/GENERATOR_SPEC.md` §§16–17.
Prereq: G09, G11–G16. First task that shells out to `dbt`.

## Spec refs
- `docs/GENERATOR_SPEC.md` §16 (fresh temp workspace; `dbt build --profiles-dir . --target
  clean --threads 1` from `dbt/`; fixed UTC env; success = models + ALL tests pass +
  readable manifest/run_results + integrity checks) and §17 (no seed-switching/weakening/
  truncation/SUCCESS-on-failure; `failure_record.json` exact schema; failure taxonomy incl.
  data-incompatible content).

## Do
- Create `clean.py`: `run_clean_build(rendered_dir) -> CleanResult` (fixed env incl.
  `TZ=UTC`, one thread, captures command + exit status + log to `dbt/logs/dbt.log`).
- Success validation: every model built, every test passed, `manifest.json` +
  `run_results.json` parse and match this project.
- Failure path: NEVER retry with another seed, omit, weaken severity, `TRY_CAST`-repair,
  or mark partial success. Preserve/copy failed workspace; write `failure_record.json`
  with the exact schema from §17 (identity/stage-enum/category/message/command/
  exit_status/paths; no fault label; big logs external).
- Requires `dbt-core`, `dbt-duckdb`, `duckdb` (already project deps).

## Tests — `tests/generator/test_g17_clean.py`
- Minimal fixture (`tests/scenario/fixtures/valid/minimal.json`) builds green end to end
  (marks the §21.0 e2e core; full record/cache assertions land in G18–G20).
- Deliberately failing fixture per custom-test family (reuse G16 fixtures): build fails,
  no `SUCCESS`, `failure_record.json` validates against the §17 schema (compile the
  expected keys in-test).
- Timestamps/durations excluded from any identity comparison (assert by rebuilding and
  comparing logical artifacts only — full form in G20).

## Accept
- [ ] `dbt` available in test env; tests green (dbt tests are slow — keep fixtures tiny).
- [ ] Full suite green; ruff clean; `git diff --check` clean; `STATE.md` bullet appended.
