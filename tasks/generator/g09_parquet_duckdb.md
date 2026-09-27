# G09: Parquet output + DuckDB loading

## Goal
Write one Parquet file per raw table and load them into `pipeline.duckdb` per
`docs/GENERATOR_SPEC.md` §§5.2–5.3, 12. Prereq: G08 (values), G10 types may be read ahead
for the DuckDB type map — implement the minimal map here if G10 is not done (single source
of truth MUST then move to `physical.py` in G10; leave a TODO if so).

## Spec refs
- `docs/GENERATOR_SPEC.md` §§5.1 (paths `raw/<T>.parquet`, quoted `"raw"."<T>"`), 5.2 (type
  map incl. UTC timestamps, `BIGINT`/`DOUBLE`/`VARCHAR`/`BOOLEAN`/`DATE`/`TIMESTAMPTZ`), 5.3
  (exact columns/order, no extra columns), 12 (fresh DB, UTC session, `raw` schema, verify,
  checkpoint before hash).

## Do
- In `physical.py` (create if needed): `DUCKDB_TYPE = {string: VARCHAR, integer: BIGINT,
  float: DOUBLE, boolean: BOOLEAN, date: DATE, timestamp: TIMESTAMP WITH TIME ZONE}`;
  timestamps normalized to UTC before write; session timezone UTC.
- Writer: exact declared columns in declaration order, no index/lineage/seed/label columns;
  fixed compression/row-group settings as module constants (record them for the versions
  section later).
- Loader: fresh `pipeline.duckdb` in the given workspace dir; `CREATE SCHEMA raw`; one typed
  table per Parquet; verify names/order/types/row counts; checkpoint/close before return.
  Mismatch -> `GenerationFailure` (integration check, not a validation stage).

## Tests — `tests/generator/test_g09_parquet_duckdb.py`
- Round-trip on all six types incl. pre-1970 date and non-UTC-offset timestamp (stored UTC).
- Column order and exactness: extra-column writer output rejected by loader verification
  (negative test with a hand-made bad Parquet).
- Freshness: loading twice into the same dir replaces, never merges, state.

## Accept
- [ ] Needs `pyarrow` (or DuckDB-native writer): if missing from deps, add the pin via `uv add`.
- [ ] Tests green; full suite green; ruff clean; `git diff --check` clean.
- [ ] `STATE.md` bullet appended.
