# Corpus clean run (2026-09-30)

Scope: offset=36 limit=12 seed=0 cache=`artifacts/corpus_cache` — 4/5 passed.

| scenario | result | detail | seconds |
| --- | --- | --- | --- |
| healthcare_appointments_001 | PASS | 4a0efe5fe37229917f67313845d687a8fe23280141e81793629f84a7bbb75969 | 3.8 |
| healthcare_dosage_001 | PASS | 640f8ed257ddddb06fa95aa1294e5ca143cedada4da2271435bc4af4df3c108a | 3.2 |
| healthcare_labs_001 | PASS | 5d68b0f754a9e515080a862b8604d42666ffc23037067a481392134f7c8edbab | 3.6 |
| healthcare_outreach_001 | PASS | c716a4c9420e5035d59687f5087b1f7a438ee62351b4d9cee135d9f640438322 | 3.4 |
| healthcare_protocols_001 | FAIL | composite-pk-unresolvable: *.*: composite-pk-unresolvable: raw_arms.*: composite-pk-unresolvable: row 24: no unseen PK tuple within 1000 attempts | 0.0 |

## Failure analysis: `healthcare_protocols_001` / `raw_arms` (STOP)

The run stopped here per the stop-on-first-failure rule; scenarios 42–48
were not attempted. Failed workspace preserved at
`artifacts/corpus_cache/v1/.tmp-1d0fc1a0ae294316aa8119f6c7ba758b/`
(`failure_record.json`: stage `raw_generate`, category
`composite-pk-unresolvable`).

Failing table: `raw_arms`, composite PK (`protocol_id` [FK → `raw_protocols`],
`arm_no` [integer_range 1..3]), rows 16..32 (ranged). Joint-sampling tier:
retry (open-domain member — correct classification).

- **Capacity (pigeonhole, proven)**: `raw_protocols` has exactly 8 rows with
  an injective `formatted_id` PK → target universe is exactly 8 distinct
  keys. `arm_no` ∈ {1, 2, 3}. Maximum distinct PK tuples: 8 × 3 = 24.
  The failure hit 0-based row 24, i.e. the run needed ≥ 25 rows — strictly
  more than the 24 the domain allows. No sampler on any seed can satisfy
  this count; switching seeds is (and was) pointless.
- **Not a sampler defect**: the retry path is sequentially strong — per-row
  without-replacement with a 1000-attempt budget succeeds with near
  certainty on every *feasible* count. It exhausted precisely because the
  sampled count was infeasible. The `raw_grades` class of defect (missing
  joint sampling) is already fixed; this is a different layer.
- **Not a scenario defect either**: the declared range 16..32 *contains*
  feasible combinations (16..24). Per GENERATOR_SPEC §9.3, the generator
  "MUST NOT fail merely because the first proposals are incompatible when
  the declared ranges contain a combination known to satisfy those local
  raw constraints" — it MUST condition/resample the involved row-count
  streams instead. G07 conditions only 1:1/unique links today; composite-PK
  capacity (computable statically from row-count bounds plus categorical /
  integer / date / FK-target-rows domains) is not conditioned.
- **Owning layer**: generator — row-count conditioning for composite-PK
  capacity (G07 `sample_row_counts` extension with per-table capacity caps:
  resample capped tables, fail fast when the range itself is infeasible).
  No scenario edit, no weakening, no seed games.
- **Proposed fix (awaiting approval, not implemented in this turn)**:
  `_pk_capacity_upper` from plan facts (categorical len, integer span,
  date span, boolean 2, FK target `rows.max`; faker/random/template/float/
  timestamp uncapped) threaded through `sample_row_counts` as caps;
  exact-table and `rows.min` violations fail fast as range-level
  pigeonholes; generation-time exhaustion stays as the honest backstop.
  The joint sampler itself needs no change.
