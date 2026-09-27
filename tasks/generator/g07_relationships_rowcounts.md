# G07: Relationship sampling + row-count conditioning

## Goal
Sample FK tuples per cardinality and condition row counts on local raw constraints, per
`docs/GENERATOR_SPEC.md` §§9.3–9.4 and 11.3. Prereq: G02, G06.

## Spec refs
- `docs/GENERATOR_SPEC.md` §§9.3 (row counts), 9.4 (row identity/order), 11.3 (sampling
  table), 9.2 (cycles are E135 upstream; keep a defensive assertion).

## Do
- Create `src/data_pipeline_diagnostics/generator/relationships.py`:
  - Row counts: exact when `min==max`, else uniform int from `rows/<table>` stream.
  - If sampled counts make a one-to-one/structural constraint impossible, deterministically
    condition/resample ONLY involved row-count streams (bounded retries, same `RETRY_LIMIT`
    style as G06; document the bound). Unresolvable -> `GenerationFailure` (never switch seed).
  - Target key universe: distinct wholly-non-null tuples in target row-index order.
  - Sampling: `one_to_many`/`many_to_one` with replacement from the value stream
    `foreign_key/<rel>/<dep>/<side>`; `one_to_one` without replacement on the dependent side;
    `many_to_many` each bridge endpoint independently with replacement; composite tuples
    atomically; unique-constrained dependents sample without replacement (overrides default).
  - Empty/too-small universe, orphans, partial-null tuples, synthetic keys: all prohibited —
    raise `GenerationFailure`.
- Rows carry internal zero-based index (never serialized); serialize ascending index, columns
  in declaration order.

## Tests — `tests/generator/test_g07_relationships.py`
- one_to_many / many_to_one: every non-null dependent tuple present in target universe.
- one_to_one: no duplicate dependent tuples; too-small universe raises.
- many_to_many bridge: both sides resolve; independence smoke check (fixed seed repeatable).
- Composite FK atomicity end-to-end (with G06 null layer).
- Row-count conditioning: crafted 1:1 scenario with incompatible first proposals still
  materializes deterministically (same seed -> same counts).

## Accept
- [ ] Only owning streams consumed on retry; failure never switches `data_seed`.
- [ ] Tests green; full suite green; ruff clean; `git diff --check` clean.
- [ ] `STATE.md` bullet appended.
