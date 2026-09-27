# G13: Row operations, expressions, conditions

## Goal
Render staging row ops, scalar expressions, and conditions per `docs/GENERATOR_SPEC.md`
§§14.3–14.5. Prereq: G12 (reuse its CTE/barrier conventions).

## Spec refs
- `docs/GENERATOR_SPEC.md` §§14.3 (filter TRUE-only; `deduplicate` via `row_number()`,
  explicit `NULLS LAST`, NO implicit tie-breaker, helper column hidden), 14.4 (expression
  table incl. safe division returning `DOUBLE`, full parenthesization, ISO `day_of_week`
  1=Monday..7=Sunday), 14.5 (three-valued comparisons, `IN`/`IS NULL`/`AND`/`OR`/`NOT`).

## Do
- Column ops form CTE-1; each row operation gets one row-set boundary in declared order.
- `filter`: `WHERE <cond>` keeping SQL `TRUE` only (no `IS NOT DISTINCT FROM` rewrites).
- `deduplicate`: `row_number() OVER (PARTITION BY keys ORDER BY <order_by> ... NULLS LAST)`,
  keep rn=1, drop helper; output columns in `StagingModel.columns` order.
- Expressions: `column/literal/binary/date_part/coalesce`; safe division exactly as the §14.4
  `CASE` template; every nesting parenthesized.
- Conditions: `comparison/in/is_null/all/any/not` fully parenthesized; `in` honors `negated`.

## Tests — `tests/generator/test_g13_rows_exprs.py`
- Snapshot per expression/condition variant incl. safe division (null and zero denominator
  render) and `day_of_week`.
- Deduplication snapshot: `NULLS LAST` present on every sort term; no extra sort key in SQL.
- Filter snapshot: `NOT`/`IN`/`IS NULL` parenthesization exact.

## Accept
- [ ] Runtime behavior of snapshots verified once against DuckDB in G17, not here.
- [ ] Tests green; full suite green; ruff clean; `git diff --check` clean.
- [ ] `STATE.md` bullet appended.
