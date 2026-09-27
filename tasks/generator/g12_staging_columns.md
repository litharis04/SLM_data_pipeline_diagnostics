# G12: Staging column SQL

## Goal
Render staging column pipelines per `docs/GENERATOR_SPEC.md` §14.2 (plus §14.1 literal/CTE
rules). Prereq: G10–G11.

## Spec refs
- `docs/GENERATOR_SPEC.md` §§14.1 (structured dispatch, CTE phase boundaries, materialization
  barrier, typed literals), 14.2 (chain semantics; `map_values` keep/null/`error()` with exact
  `dpd_unmapped_value(<model>, <column>)` message; strict `CAST`, `strptime` with format).

## Do
- Each staging column starts from its quoted raw source; operations wrap in declaration
  order; target alias applied once at the end.
- Implement `trim/lower/upper/replace/map_values/null_if/coalesce/cast` exactly per the §14.2
  table. `error()` via DuckDB `error('dpd_unmapped_value(m, c)')` (message carries NO data
  value). Strict `CAST` only; string->date/timestamp with `format` via `strptime` then cast
  to `DATE` / `TIMESTAMPTZ` under UTC; without format, DuckDB strict cast.
- Where an error-producing cast or `map_values(error)` could be optimized away/reordered,
  apply ONE deterministic barrier technique for the whole renderer (your choice, e.g. a
  `MATERIALIZED`-style CTE boundary or per-operation CTE chain; document it in the test-file
  header and use it consistently in G13–G15).

## Tests — `tests/generator/test_g12_staging_columns.py`
- Snapshot (exact-string) test per column op, incl. a 3-op type-changing chain.
- `map_values` all three `on_unmapped` modes; `error` message exact, value-free.
- `cast` with/without `format`; invalid input raises at `dbt build` (covered in G17; here
  assert strict `CAST`, no `TRY_CAST`, in SQL text).

## Accept
- [ ] No `TRY_CAST` anywhere in rendered SQL (grep assertion).
- [ ] Tests green; full suite green; ruff clean; `git diff --check` clean.
- [ ] `STATE.md` bullet appended.
