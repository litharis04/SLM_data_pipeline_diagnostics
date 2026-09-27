# G08: RawPlan builder + generation-unit ordering

## Goal
Build the immutable internal `RawPlan` and execute units in dependency order per
`docs/GENERATOR_SPEC.md` §§4.1, 9.1–9.2. Prereq: G02–G07.

## Spec refs
- `docs/GENERATOR_SPEC.md` §§4.1 (RawPlan contents/limits), 4.3 (no shared IR), 9.1 (units,
  FK-universe-before-dependent, declaration-order tie-break), 9.2 (defensive cycle failure).

## Do
- Create `src/data_pipeline_diagnostics/generator/raw_plan.py`:
  - `build_raw_plan(validated) -> RawPlan` with ONLY raw facts: table order, row-count specs,
    leaf/template evaluation order, composite FK groups with ordered target bindings, hard
    constraints, stream names. No dbt/SQL/paths/fault metadata (assert by test via `__dict__`
    inspection or explicit allowlist).
  - Unit ordering: row count -> ordinary columns -> template columns (after placeholders) ->
    FK tuples (after target universe exists; target need not be fully serialized) ->
    serialization. Independent units in scenario declaration order; relationships via
    resolved direction, never name inference.
  - Defensive cycle failure naming tables/columns/relationships (E135 already rejects
    upstream; this is the component-boundary assert).
- Wire G03–G07 executors under the plan (no new semantics, just orchestration).

## Tests — `tests/generator/test_g08_raw_plan.py`
- Template-after-placeholder order on a diamond template dependency (fixed seed works).
- FK-before-target-universe: dependent samples only existing target keys (property check on
  a 3-table chain fixture).
- Declaration-order determinism: reordered JSON table order with no dependencies still
  yields identical data (plan sorts to declaration order deterministically).
- `RawPlan` contains no `dbt`/`sql`/`path`/`fault` attributes.

## Accept
- [ ] `build_raw_plan` is pure (no RNG draws, no I/O).
- [ ] Tests green; full suite green; ruff clean; `git diff --check` clean.
- [ ] `STATE.md` bullet appended.
