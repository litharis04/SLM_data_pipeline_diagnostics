# G15: Aggregates + output models

## Goal
Render aggregate intermediates and output models per `docs/GENERATOR_SPEC.md` §14.8. Prereq:
G13–G14.

## Spec refs
- `docs/GENERATOR_SPEC.md` §14.8 (filters pre-aggregation as conjunction; group-by aliasing;
  metric SQL table; output columns = group targets then metrics; `group_by` alone defines
  grouping — `grain`/`dimensions` are metadata only).

## Do
- Filters apply pre-aggregation (conjunction, TRUE-only); group by every declared source
  expression aliased to target names; metrics in declaration order with exact §14.8 SQL:
  `count_rows->COUNT(*)` (`BIGINT`); `count/count_distinct` non-null (distinct) counts;
  `sum/avg` cast `DOUBLE`; `min/max` type-preserving; `conditional_count` (0 when none);
  `conditional_sum` (`DOUBLE`, null when no qualifying non-null value).
- Output models follow the same shape over exactly one intermediate source. Standard DuckDB
  null-grouping semantics; empty filtered input yields no rows (caught by derived non-empty
  assertion in G16/G17, not here).

## Tests — `tests/generator/test_g15_aggregate_output.py`
- Snapshot per metric function (all nine) incl. conditional variants.
- Output snapshot: group targets first, then metrics, in declaration order.
- `grain`-only difference (same `group_by`, narrower grain) does NOT change SQL
  (text-compare two renders).

## Accept
- [ ] No window functions, no ratios, no percentile SQL (spec §14.8 scope).
- [ ] Tests green; full suite green; ruff clean; `git diff --check` clean.
- [ ] `STATE.md` bullet appended.
