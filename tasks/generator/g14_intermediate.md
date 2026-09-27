# G14: Intermediate models

## Goal
Render transform/join/deduplicate intermediates per `docs/GENERATOR_SPEC.md` §§14.6–14.7, 14.9
(aggregates live in G15). Prereq: G13.

## Spec refs
- `docs/GENERATOR_SPEC.md` §§14.6 (project -> derived (one CTE, projected-namespace only, no
  inter-derived refs) -> filters; output order), 14.7 (INNER/LEFT equality on ordered pairs,
  side-qualified projection, no wildcards/suffixes; same derived/filter rules), 14.9
  (dedup refs source, ranks like §14.3, keeps column order), 14.1 (CTE phase boundaries).

## Do
- Transform: `source -> projection/renames -> derived (single CTE) -> filters (conjunction)`.
- Join: `left/right refs -> INNER|LEFT equality on ALL ordered pairs (null semantics!) ->
  explicit projection/rename -> derived -> filters`. Emit nothing implicit: no wildcard, no
  suffix, no extra predicate.
- Deduplicate: rank exactly as G13 over `keys`/`order_by`, return all source columns minus
  helper, unchanged order.
- Namespace violations (derived-on-derived refs) cannot arrive (semantic layer owns them) —
  defensive assert only.

## Tests — `tests/generator/test_g14_intermediate.py`
- Snapshots: transform with derived+filter; inner + left joins with composite keys;
  dedup model.
- Namespace: derived CTE references only projected names (text assertion on one snapshot).
- Join snapshot contains exactly the declared `ON` equalities, nothing more.

## Accept
- [ ] No `SELECT *`, no `NATURAL`, no `CROSS` in rendered output (grep assertions).
- [ ] Tests green; full suite green; ruff clean; `git diff --check` clean.
- [ ] `STATE.md` bullet appended.
