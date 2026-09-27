# G10: Fixed physical contract (naming, quoting, literals)

## Goal
Single source of truth for identifiers, literals, and types per `docs/GENERATOR_SPEC.md`
§§5.1–5.3. Prereq: G01. (G09 MAY have sketched the type map; this task owns it finally.)

## Spec refs
- `docs/GENERATOR_SPEC.md` §§5.1 (identity mappings, one quoting helper, Jinja-neutral
  literals), 5.2 (type map), 5.3 (raw/dbt layer agreement).

## Do
- In `physical.py`:
  - `quote_ident(name)`: the ONLY identifier quoter (double quotes, `"` escaped by doubling).
    Physical mapping: raw `T` -> `raw/T.parquet`, `"raw"."T"`, `source('raw','T')`; model `M` ->
    `M.sql`, ref `M`, `"main"."M"`; column `C` -> `C` verbatim.
  - Type-aware literal renderers: strings (single quotes doubled), integers, floats
    (finite round-trip), booleans `TRUE/FALSE`, dates (typed literal), timestamps (typed UTC
    literal). Jinja-delimiter text inside scenario strings MUST be neutralized so the
    observable contract holds (post-parse literal equals the scenario value; technique is
    your choice — simplest deterministic escaping wins).
  - Fixed constants: source name `raw`, schemas `raw`/`main`, project `dpd_pipeline`,
    profile `dpd_pipeline`, target `clean`. No policy/provider abstraction.
  - Generated text files: UTF-8, LF, one final newline; canonical `scenario.json` snapshot
    byte-identical to `canonical_json` output.

## Tests — `tests/generator/test_g10_physical.py`
- Quoting of reserved/odd identifiers (`select`, `with space`, embedded quote).
- Literal round-trips incl. apostrophe strings, negative ints, `0.1`-style floats.
- Jinja payload string (e.g. `{{ 7*7 }}` as a category value) renders to a literal that
  dbt parses as plain text (assert no bare `{{` survives outside `source(`/`ref(` calls in
  a rendered snippet — full-project check lands in G11).
- Type map exhaustiveness over the six `DataType`s.

## Accept
- [ ] Exactly one quoting helper; no f-string SQL assembly elsewhere (grep to prove it).
- [ ] Tests green; full suite green; ruff clean; `git diff --check` clean.
- [ ] `STATE.md` bullet appended.
