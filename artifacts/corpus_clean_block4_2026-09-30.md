# Corpus clean run (2026-09-30)

Scope: offset=36 limit=12 seed=0 cache=`artifacts/corpus_cache` — 8/9 passed.

| scenario | result | detail | seconds |
| --- | --- | --- | --- |
| healthcare_appointments_001 | PASS | e26bbd63d6d0bf22bd3a5678646ea442841e70f85d3c22f9c1cd7522c452d089 | 3.9 |
| healthcare_dosage_001 | PASS | effcadbf730ac1bd1a615c649e0c4f8a030f7e6738bc1f7a72baedc48cdb6be5 | 3.2 |
| healthcare_labs_001 | PASS | 6c4a288f784cff64cdc4ea3b02ef2a920ed9cdffb2f886755a67f9aacc7764fb | 3.4 |
| healthcare_outreach_001 | PASS | 098187eb5c595a80591e2567390779b7faf9996a0d60c4b039a3c640e7a4657b | 3.4 |
| healthcare_protocols_001 | PASS | 53032d6e103f6215323d14c5d78b8daf9b3bb95dc4bab8227d64ca196a43cef8 | 3.8 |
| healthcare_screenings_001 | PASS | 2633512455decf03601cd9f8616317ed9ef92f3bcc141fd7d13ffe8a2947d2d2 | 3.5 |
| healthcare_transfers_001 | PASS | 30582d24d59ec309ff4dcebc9e5b01f9659aeb3154f9ec1b846d7fe47e1857c9 | 3.5 |
| healthcare_trials_001 | PASS | 57feeb71c68b41992d381279414c60d8a1a6c1833ed0ef87dd5255634dfd1f04 | 3.5 |
| healthcare_visits_001 | FAIL | dbt-test-failure: *.*: dbt-test-failure: *.*: dbt-test-failure | 3.6 |

## Failure analysis: `healthcare_visits_001` / `j_visits_rx.rx_id` (STOP)

The run stopped here per the stop-on-first-failure rule; scenarios 46–48
were not attempted. Failed workspace preserved at
`artifacts/corpus_cache/v1/.tmp-1d1d8a18c724499bb068bcf8aa0cbe46/`
(`failure_record.json`: stage `dbt_build`, category `dbt-test-failure`).

Failing node: `test … not_null__derived_not_null_j_visits_rx_rx_id` —
42 null `rx_id` values in `j_visits_rx` (plus skipped cascade:
`j_all_doctors`, `o_by_specialty` and their tests, which would hit the same
nulls through the inherited grain).

- **Mechanism**: `j_visits_rx` is a LEFT JOIN `stg_visits × stg_rx` ON
  `visit_id`, grain `[visit_id, rx_id]`. `rel_visit_rx` is one_to_many, so
  `raw_rx.visit_id` samples the visits universe **with replacement** over
  ranged counts (visits 50..200, rx 20..300). Unmatched visits keep their
  row with NULL right-side columns — at seed 0, 42 visits drew no rx.
  Correct SQL semantics, correct sampling.
- **Not a generator defect**: the executor preserved LEFT-join semantics
  exactly and sampled per the §11.3 table. GENERATOR_SPEC §11.3 is explicit:
  "Version 1 does not synthesize coverage or participation guarantees that
  are absent from the scenario language." Forcing every visit to match
  would be exactly such synthesis — forbidden.
- **Owning layer — scenario content (corpus authoring)**: grain
  `[visit_id, rx_id]` together with LEFT-join optionality demands full
  participation, which the language cannot express as a generatable
  constraint — the declaration set is unsatisfiable-by-construction
  whenever any visit lacks an rx (the overwhelmingly likely outcome at
  20..300 rx over 50..200 visits; seed-dependent flake at best).
  Secondary note: semantic validation proved structural grain-ness (no
  E-code) without flagging nullable-right-side-of-LEFT grain components —
  a possible omitted invariant for the contract owners, not fixed here.
- **Candidate corpus fix (not applied — awaiting approval, blast radius)**:
  `j_visits_rx` LEFT → INNER (every rx row matches some visit by
  construction, so output stays non-empty and grain components stay
  non-null and distinct given unique `visit_id`/`rx_id` sides). Required
  verification before any edit: `j_all_doctors` (INNER over
  `j_visits_rx × stg_doctors`, grain `[visit_id, rx_id]`) stays valid, and
  explicit `specialty_rows` (`row_count` 1..1000 on `o_by_specialty`)
  still holds on the smaller output. No generator, spec, or test change
  is involved.
