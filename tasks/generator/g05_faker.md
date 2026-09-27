# G05: Faker-backed generators (`de_DE` only)

## Goal
Dispatch the six Faker leaf kinds per `docs/GENERATOR_SPEC.md` §10.7 using `faker==40.39.0`.

## Spec refs
- `docs/GENERATOR_SPEC.md` §10.7 (+ §10 preamble: proposal only); observable seeding contract
  (subseed of the column's `values/<table>/<column>` stream into a private `random.Random`).

## Do
- Extend `raw_values.py` (or a `faker_values.py` it calls) with the exact dispatch table:
  `person_name->name()`, `email->email()`, `city->city()`,
  `street_address->street_address()`, `company_name->company()`, `phone_number->phone_number()`.
- One isolated `Faker("de_DE")` per column value stream: `Faker.seed_instance(subseed)` (or
  equivalent private-`Random` wiring); never the global RNG; never `.unique` shared across
  columns. Non-`de_DE` locale cannot arrive (contract narrows to `Literal["de_DE"]`) — assert
  defensively and raise.
- Faker MUST NOT influence row counts, nulls, keys, or uniqueness (it only supplies the
  proposal string).

## Tests — `tests/generator/test_g05_faker.py`
- All six kinds produce non-empty strings on a fixed seed, repeatably (same subseed ->
  same sequence).
- Cross-column independence: two Faker columns with different stream names diverge.
- `locale="en_US"` config raises (defensive; contract already forbids it).
- Spot-check `de_DE` flavor (e.g. `city()` draws from German provider data — assert ASCII
  run completes; keep assertions locale-robust, not value-pinned, since Faker data files can
  change across pins; the pin itself is asserted via `importlib.metadata.version("faker")`).

## Accept
- [ ] `faker` version pinned in `pyproject.toml` (already `==40.39.0`; assert in test).
- [ ] Tests green; full suite green; ruff clean; `git diff --check` clean.
- [ ] `STATE.md` bullet appended.
