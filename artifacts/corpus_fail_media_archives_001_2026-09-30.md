# Clean failure: media_archives_001 (2026-09-30)

- seed: 0
- reason: unexpected-error
- message: *.*: unexpected-error: ValueError: formatted_id overflow: 10 exceeds 1 digit capacity
- workspace: `artifacts/corpus_cache/v1/.tmp-bc3900ed7e7b465c94f46a3de99cdb0e`

## Evidence

- failure_record: stage `raw_generate`, category `unexpected-error`, exit_status `None`
- non-passing dbt nodes (0):
  - none (no run_results — raw/generate stage)

## Triage (bulk review after the full corpus)

- Mechanism: `raw_reels.shelf` is `formatted_id` with `digits: 1`
  (capacity `S-1`..`S-9`, 9 values) against `rows` 18..54 — overflow at
  0-based row 9 is certain on every seed. Surfaced as
  `unexpected-error: ValueError` (G03 raises `ValueError`, not a stable
  `GenerationFailure` reason — taxonomy follow-up candidate, not a
  behavior bug).
- Systematic vs seed luck (capacity math): systematic — 9 < 18 ≤ rows,
  seed-independent.
- Owning layer (generator / semantic / scenario content): scenario
  content, with a semantic-validation gap note. The generator is correct
  (deterministic values, loud failure, nothing else generatable). T14's
  formatted_id capacity helper is exact but only guards PK feasibility;
  `shelf` is not a PK member so no rule fires — extending the rule to all
  members is a contract-owner decision that would invalidate this scenario
  anyway.
- Candidate fix + required verification: corpus widen `digits` 1→2
  (capacity 99 ≥ 54). Blast radius verified nil: `shelf` is a bare
  passthrough in `stg_reels` (no operations), referenced by no template,
  no test, and no downstream model. No generator/spec/test change.
