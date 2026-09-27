# G06: Nulls, keys, and uniqueness

## Goal
Apply null insertion and hard raw constraints per `docs/GENERATOR_SPEC.md` §§11.1–11.2 over
proposals from G03–G05. Prereq: G02–G05.

## Spec refs
- `docs/GENERATOR_SPEC.md` §§11.1–11.2 (hard constraints vs distributions, tuple-atomic
  composite nulls, deterministic local retry with versioned limit).

## Do
- Create `src/data_pipeline_diagnostics/generator/raw_constraints.py`:
  - Scalar nullable column: one Bernoulli draw per row from its `nulls/<table>/<column>`
    stream with `null_probability` (0.0 never, 1.0 always).
  - Composite FK: single tuple-level draw from its `nulls/foreign_key/...` stream; components
    all-null or all-non-null. Mismatch of `nullable`/`null_probability` across components is a
    semantic error (E111) and never arrives — keep a defensive assertion.
  - Enforce PK non-null+unique, `nullable:false` non-null, `unique:true` non-null-unique,
    using deterministic local retry/sampling-without-replacement with a module constant
    `RETRY_LIMIT` (your choice, e.g. 1000; document it; it is implementation-versioned).
  - Exhaustion/retry-limit -> raise structured `GenerationFailure` (never truncate/emit
    invalid rows).

## Tests — `tests/generator/test_g06_constraints.py`
- `null_probability` 0.0/1.0 exact behavior; ordinary nullable column yields both nulls and
  values on a fixed seed.
- Composite tuple atomicity: over 200 rows, no partially-null composite tuple.
- `unique:true` column has no duplicate non-null values on a fixed seed.
- Tiny-domain exhaustion raises `GenerationFailure` (e.g. 5 unique values into 10 rows).

## Accept
- [ ] Retries consume only owning streams; `RETRY_LIMIT` documented in module docstring.
- [ ] Tests green; full suite green; ruff clean; `git diff --check` clean.
- [ ] `STATE.md` bullet appended.
