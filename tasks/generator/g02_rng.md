# G02: Named RNG streams

## Goal
Deterministic randomness foundation per `docs/GENERATOR_SPEC.md` §8. All later stochastic
code MUST use this module; a single scenario-wide mutable RNG is prohibited.

## Spec refs
- `docs/GENERATOR_SPEC.md` §§8.1–8.3 (derivation, namespaces, seed-variation meaning).

## Do
- Create `src/data_pipeline_diagnostics/generator/rng.py`:
  - `subseed(scenario_id: str, data_seed: int, stream_name: str) -> int`: SHA-256 over UTF-8
    of canonical JSON `["dpd-rng-v1", scenario_id, data_seed, stream_name]`, unsigned
    big-endian int. Reuse the scenario package's canonical scalar spelling (no new JSON
    encoder quirks: `json.dumps` with `separators=(",", ":")`, `ensure_ascii=False`).
  - `stream(scenario_id, data_seed, stream_name) -> random.Random`: fresh isolated
    `random.Random()` seeded `rng.seed(subseed, version=2)`.
  - Stream-name constructors for the five required namespaces:
    `rows/<table>`, `values/<table>/<column>`, `nulls/<table>/<column>`,
    `foreign_key/<rel>/<dep>/<side>`, `nulls/foreign_key/<rel>/<dep>/<side>`.
  - `STREAM_SCHEME = "dpd-rng-v1"` constant for the instance record.
- No Faker wiring here (G05 consumes `subseed`).

## Tests — `tests/generator/test_g02_rng.py`
- Same inputs -> identical draw sequences; different `data_seed` -> different sequences
  (on a wide-range stream, e.g. 1..10**9).
- Column isolation: changing one column's generator config (hence its stream draws) MUST NOT
  shift another column's stream sequence (two streams, draw from one, re-create, compare).
- Canonical hash is NOT an input: same `(scenario_id, seed, stream)` under edited scenario
  content yields the same subseed (assert by calling `subseed` directly).
- `subseed` matches a recorded hex fixture (pin one known vector in the test).

## Accept
- [ ] No module-global random state; `random` used only via fresh instances.
- [ ] Tests green; full suite green; ruff clean; `git diff --check` clean.
- [ ] `STATE.md` bullet appended.
