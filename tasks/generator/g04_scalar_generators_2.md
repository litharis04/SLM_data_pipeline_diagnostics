# G04: Scalar mini-generators, part 2 (categorical / boolean / strings / template)

## Goal
Execute `categorical`, `boolean`, `random_string`, `template_string` per
`docs/GENERATOR_SPEC.md` §§10.4–10.6. Prereq: G03 (same module, same dispatch).

## Spec refs
- `docs/GENERATOR_SPEC.md` §§10.4–10.6; type preservation (`true` vs `1`) per §10.4.

## Do
- Extend `raw_values.py` dispatch:
  - `categorical`: uniform without weights; with weights normalize by sum in declaration
    order, zero-weight never selected; preserve JSON scalar type exactly (bool distinct from
    int); `unique` columns select without replacement with renormalization (exhaustion raises —
    caller in G06 decides retry vs failure, here raise `ExhaustedDomain`).
  - `boolean`: `rng.random() < true_probability`.
  - `random_string`: length uniform from `[min_length, max_length]`, each char uniform from
    `alphabet` in declaration order.
  - `template_string`: pure render after placeholder values are supplied as a dict; text forms
    per §10.6 table (float via `repr` of the `DOUBLE` value for the pinned runtime, bool
    `true`/`false`, date ISO, timestamp UTC ISO-8601 `+00:00`); any referenced null -> null
    result; no escaping, no nested lookup, no Jinja.

## Tests — `tests/generator/test_g04_scalar2.py`
- Categorical type fidelity: `values=(True, 1)` can yield both a `bool` and an `int` across
  seeds (assert types, not just values).
- Zero-weight value never selected over 200 draws with a fixed seed.
- `boolean` with `true_probability` 0.0/1.0 deterministic.
- `random_string` respects length bounds and alphabet membership.
- Template: mixed-type row renders per table; null placeholder -> null; literal braces already
  rejected at parse time (no test needed beyond render).

## Accept
- [ ] Exhaustion raises a dedicated error (no silent truncation).
- [ ] Tests green; full suite green; ruff clean; `git diff --check` clean.
- [ ] `STATE.md` bullet appended.
