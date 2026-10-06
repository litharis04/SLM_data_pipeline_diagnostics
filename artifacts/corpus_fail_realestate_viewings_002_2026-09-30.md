# Clean failure: realestate_viewings_002 (2026-09-30)

- seed: 0
- reason: dbt-test-failure
- message: *.*: dbt-test-failure: *.*: dbt-test-failure
- workspace: `artifacts/corpus_cache/v1/.tmp-510c3bc81e2a44ee91dc8bf202061e29`

## Evidence

- failure_record: stage `dbt_build`, category `dbt-test-failure`, exit_status `1`
- non-passing dbt nodes (3):
- `test.dpd_pipeline.composite_unique__besichtigung_einmalig.b94f46d19b` fail: Got 19 results, configured to fail if != 0
- `model.dpd_pipeline.o_preis_by_stadt` skipped: 
- `test.dpd_pipeline.row_count_between__derived_row_count_o_preis_by_stadt.4c478a72a8` skipped: 
- raw row counts:
- raw.raw_besichtigungen: 393 rows
- raw.raw_makler: 10 rows
- raw.raw_objekte: 12 rows

## Triage (bulk review after the full corpus)

- Mechanism: explicit `unique` over (`objekt_id`, `termin`) on `m_full`
  (INNER join preserving every `besichtigung` row) found 19 duplicate
  pairs. Duplicates arise when two viewings share object and day —
  join fan-out is innocent here (each side joins on its own PK side).
- Systematic vs seed luck (capacity math): heavily loaded
  probabilistic — 12 objects × 366 days = 4392 combos against 150..500
  rows; duplicates observed on seeds 0–5 (4..24 dups; seed 0: 19).
  Feasible combos exist, so no pigeonhole; but generation cannot promise
  pairwise uniqueness.
- Owning layer (generator / semantic / scenario content): scenario
  content. Per §11.4/§15.5 the generator MUST NOT reverse-engineer
  assertions into constraint solving, and it correctly did not — the
  test truthfully reported a violated runtime expectation. No generator,
  spec, or validation change is involved.
- Candidate fix + required verification: corpus authoring decision —
  drop/relax `besichtigung_einmalig`, or reshape counts/domain so the
  expectation holds reliably (no generator-side enforcement possible
  without violating §11.4). Verify green build after the edit.
