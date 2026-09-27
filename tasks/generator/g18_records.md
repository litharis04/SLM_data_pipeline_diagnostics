# G18: Instance + failure records

## Goal
Emit the reproducibility passport per `docs/GENERATOR_SPEC.md` §18 (record schemas were
fixed in the spec review; implement them exactly). Prereq: G17.

## Spec refs
- `docs/GENERATOR_SPEC.md` §§18.1–18.3 (role vs dbt manifest, exact `raw_tables[]`/`dbt`/
  `artifacts[]`/`versions` (incl. `python` major.minor vs `python_full` provenance)/
  `randomness` fields, no inline rows/logs/secrets/fault metadata, `SUCCESS`-last
  publication order).

## Do
- Create `records.py`:
  - `write_instance_record(...) -> dict`: `record_version "1.0"`, `status "success"`,
    `identity{instance_digest, scenario_id, scenario_schema_version, scenario_sha256,
    data_seed}`, `versions{...}` (read tool versions via `importlib.metadata`; `faker_locale
    "de_DE"`), `randomness{stream_scheme: "dpd-rng-v1", streams: [...]}`,
    `raw_tables[]`, `dbt{}`, `artifacts[]` — exact field sets from §18.2, no extras in
    those sections. `instance_digest` = SHA-256 of canonical JSON of the §7.2 identity
    object (define that canonical object HERE if not done in G19 — coordinate, no dup).
  - SHOULD crosswalks: model->`unique_id`/relation, assertion->`unique_id`s, realized dbt
    row counts, logical table checksums (from `manifest.json` + DB queries).
  - Failure record writer lives in G17's `clean.py`; this task only asserts its schema
    conformance helper (`validate_failure_record`) if not already present.

## Tests — `tests/generator/test_g18_records.py`
- Schema-exactness: record keys equal the spec sets (no extras in the three strict
  sections); `python_full` present and excluded from digest recomputation.
- `instance_digest` stable across two writes of the same build.
- Negative: record contains no raw rows, no secrets, no `fault` keys (recursive scan).

## Accept
- [ ] `SUCCESS` marker written strictly after the record (order test with a fake publisher).
- [ ] Tests green; full suite green; ruff clean; `git diff --check` clean.
- [ ] `STATE.md` bullet appended.
