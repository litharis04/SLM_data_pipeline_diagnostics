# Clean failure: healthcare_protocols_001 (2026-09-30)

- seed: 0
- reason: composite-pk-unresolvable
- message: raw_arms.*: composite-pk-unresolvable: row 24: no unseen PK tuple within 1000 attempts
- workspace: `artifacts/corpus_cache/v1/.tmp-1d0fc1a0ae294316aa8119f6c7ba758b`

## Evidence

- failure_record: stage `raw_generate`, category `composite-pk-unresolvable`, exit_status `null`
- non-passing dbt nodes (0):
  - none (no run_results — raw/generate stage)
- raw row counts:
  - (no pipeline.duckdb — generation never completed)
- table facts (`scenarios/healthcare_protocols_001.json`):
  - `raw_arms` PK (`protocol_id` [FK → `raw_protocols`], `arm_no` [integer_range 1..3]), rows 16..32
  - `raw_protocols`: exactly 8 rows, injective `formatted_id` PK → target universe exactly 8 keys
  - joint-sampling tier: retry (open-domain member — correct classification)

## Triage (triaged pre-bulk; kept for the bulk review)

- Mechanism: joint PK retry exhausted at 0-based row 24, i.e. the run needed ≥ 25 distinct tuples.
- Systematic vs seed luck (capacity math): systematic pigeonhole — 8 × 3 = 24 maximum distinct tuples < 25 needed. No sampler or seed can satisfy this count.
- Owning layer (generator / semantic / scenario content): generator — the declared range 16..32 *contains* feasible combinations (16..24), so per GENERATOR_SPEC §9.3 the generator MUST condition/resample the involved row-count streams instead of failing. G07 conditions only 1:1/unique links today; composite-PK capacity is not conditioned. Not a sampler defect (retry is sequentially strong; it failed only on an infeasible count) and not a scenario defect (feasible combos exist in-range).
- Candidate fix + required verification: `_pk_capacity_upper` from plan facts (categorical len, integer span, date span, boolean 2, FK target `rows.max`; faker/random/template/float/timestamp uncapped) threaded through `sample_row_counts` as caps; exact-table and `rows.min` violations fail fast as range-level pigeonholes; generation-time exhaustion stays as the honest backstop. Verify: protocols seed 0 builds green with arms count ≤ 24, full suite green.
