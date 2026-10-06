# Clean failure: healthcare_visits_001 (2026-09-30)

- seed: 0
- reason: dbt-test-failure
- message: *.*: dbt-test-failure: *.*: dbt-test-failure
- workspace: `artifacts/corpus_cache/v1/.tmp-1d1d8a18c724499bb068bcf8aa0cbe46`

## Evidence

- failure_record: stage `dbt_build`, category `dbt-test-failure`, exit_status `1`
- non-passing dbt nodes (8):
- `test.dpd_pipeline.not_null__derived_not_null_j_visits_rx_rx_id.2b7d1041d2` fail: Got 42 results, configured to fail if != 0
- `model.dpd_pipeline.j_all_doctors` skipped:
- `test.dpd_pipeline.composite_unique__derived_unique_j_all_doctors.0abb68f4dc` skipped:
- `test.dpd_pipeline.not_null__derived_not_null_j_all_doctors_rx_id__rx_id.3cc222eecf` skipped:
- `test.dpd_pipeline.not_null__derived_not_null_j_all_doctors_visit_id__visit_id.573fa457d5` skipped:
- `model.dpd_pipeline.o_by_specialty` skipped:
- `test.dpd_pipeline.row_count_between__derived_row_count_o_by_specialty.05a5c22e35` skipped:
- `test.dpd_pipeline.row_count_between__specialty_rows.ae06f5bf23` skipped:
- raw row counts:
- raw.raw_visits: 162 rows
- raw.raw_rx: 211 rows
- (raw_patients, raw_doctors: see workspace DB)

## Triage (triaged pre-bulk; kept for the bulk review)

- Mechanism: `j_visits_rx` is a LEFT JOIN `stg_visits × stg_rx` ON `visit_id`
  with grain `[visit_id, rx_id]`. `rel_visit_rx` is one_to_many, so
  `raw_rx.visit_id` samples the visits universe **with replacement** (visits
  50..200, rx 20..300). Unmatched visits keep NULL right-side columns — at
  seed 0, 42 visits drew no rx (expected ≈ 44 from 162·(1−1/162)²¹¹).
  Correct SQL semantics, correct sampling; the skipped cascade
  (`j_all_doctors`, `o_by_specialty`) inherits the same nulls.
- Systematic vs seed luck (capacity math): structural, seed-flaky at best —
  no range adjustment within the language can guarantee full participation;
  even the best ratio (300 rx over 50 visits) covers all visits with only
  ≈88% probability.
- Owning layer (generator / semantic / scenario content): scenario content
  (corpus authoring). Grain on a right-side LEFT column demands full
  participation, which the language cannot express as a generatable
  constraint — and GENERATOR_SPEC §11.3 forbids the generator from
  synthesizing it. Secondary note: semantic validation proved structural
  grain-ness without flagging nullable-right-side-of-LEFT components (a
  possible omitted invariant for the contract owners, not fixed here).
- Candidate fix + required verification: corpus `j_visits_rx` LEFT → INNER
  (every rx row matches some visit by construction: output stays non-empty,
  grain components stay non-null and distinct given unique sides). Verify
  before editing: `j_all_doctors` (INNER, same grain) stays valid and
  explicit `specialty_rows` (1..1000) holds on the smaller output. No
  generator, spec, or test change is involved.
