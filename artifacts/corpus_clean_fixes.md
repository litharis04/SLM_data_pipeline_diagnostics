# Corpus clean-control fixes (T08) — verification notes

Three bulk-run failures fixed at the corpus layer, plus two static guards
and a failure-taxonomy follow-up. No language change.

## Fix 1 — `healthcare_visits_001`: `j_visits_rx` LEFT → INNER

Grain `[visit_id, rx_id]` took `rx_id` from the nullable side of a LEFT
JOIN; 42 unmatched visits kept NULL `rx_id` and the derived `not_null`
test truthfully failed. After the flip to INNER, every rx row matches some
visit by construction (FK sampled from the visits universe, rx non-empty),
so output stays non-empty and grain components stay non-null and distinct.
`j_all_doctors` (INNER over `j_visits_rx × stg_doctors`, same grain) stays
valid; explicit `specialty_rows` (1..1000 on `o_by_specialty`) holds
(groups ≤ 6 specialties, ≥ 1 group). Verified by clean rebuild (seed 0):
no `failure_record.json`.

Coverage companion (required — `F-JOIN-002` sat exactly at 5/5):
`j_visits_patients` INNER → LEFT in the same file. Its grain `[visit_id]`
traces to the left side only (new guard silent), and output is provably
identical to INNER (`patient_id` FK is non-null and sampled from the
universe, so every visit matches). `F-JOIN-002` stays at 5; no other claim
moves (verified by checker).

## Fix 2 — `media_archives_001`: `shelf` digits 1 → 2

`raw_reels.shelf` was `formatted_id` (`digits: 1`, capacity 9) against rows
18..54 — deterministic overflow on every seed. Capacity 99 now covers 54.
Blast radius nil (bare passthrough in `stg_reels`, no template/test/
downstream reference). Verified by clean rebuild (seed 0).

Coverage companion (required — `F-BND-001` sat exactly at 5/5):
new plain `formatted_id` column `booth_no` (`digits: 1`, prefix `B-`) on
`raw_studios` in `media_subtitles_001` (rows.max 6 ≤ capacity 9, passes the
new guard; unselected by staging, non-null values satisfy the derived
`not_null`). `F-BND-001` stays at 5; checker confirms no other row moves.

## Fix 3 — `realestate_viewings_002`: drop `besichtigung_einmalig`

Explicit `unique(objekt_id, termin)` on `m_full` contradicts the declared
volume (12 objects × 366 days against 150..500 rows); the generator was
right not to enforce it (§11.4/§15.5). Removed; structural guarantees
unaffected (it was explicit). Verified by clean rebuild (seed 0).
Claims recomputed: file drops `F-ASM-002`, `F-ASM-008`, `I-ASM-002`
(`F-ASM-021` retained via the remaining `relationships` assertion).

Coverage companion (required — `F-ASM-008` sat exactly at 5/5): new explicit
`unique(office_id, city)` named `office_identity` on `stg_offices` in
`realestate_agents_001` (domain-consistent neighbor). Passes
deterministically (`office_id` is distinct by PK); differs from the derived
single-column grain unique, so no `CONTRADICTORY_ASSERTION`. File gains
`F-ASM-002` + `F-ASM-008`. Verified by clean rebuild (seed 0).

Net `actual` effect: `F-ASM-002` 6→5→6, `F-ASM-008` 5→4→5, `F-BND-001`
5→4→5 (unchanged); `I-ASM-002` 5→4 (target 3, no action needed).

## Static guards (semantic validation)

1. `formatted_id` capacity for **every** raw column (`INVALID_PK`, same
   family as the T14 checks): reject when `10**digits - start < rows.max`.
   Deterministic overflow otherwise — proposals run per row index
   regardless of nullability, so zero false positives.
2. Grain nullability under LEFT joins (new stable `E136`
   `NULLABLE_GRAIN`): reject a grain component traceable only to the
   nullable (right) side — projected targets via their side, derived
   columns via the projected namespace they evaluate against (same
   structures as the T15 rules). The four other corpus LEFT joins carry
   left-only grains and stay silent.
3. Taxonomy: `formatted_id` overflow raises stable `GenerationFailure`
   (`formatted-id-overflow`) instead of bare `ValueError`
   (`GenerationFailure` moved to `raw_values`, re-exported from
   `raw_constraints`; `generate_scalar_column` pre-checks with full
   table/column context — exactly equivalent to the leaf condition).

Focused tests: 5 semantic (guard-1 reject + capacity boundary pass;
E136 on projected and derived components; left-only positive control),
updated overflow test (stable reason), new column-level context test.
Full corpus revalidation: **144/144 reach `ValidatedScenario`**.

## Verification summary

- `parse → validate_semantics` green for all edited files; 144/144 green.
- Clean rebuilds (seed 0, no `failure_record.json`): the three fixed
  scenarios plus both neighbor files.
- Claim checker (23 predicates × 144 files): only intended deltas, all
  applied to `COVERAGE.md`; `I-ASM-002` actual 5→4.
- Full suite: 512 passed; `ruff check` + `ruff format --check` clean.

Pre-existing note (out of scope, untouched): `transport_tracking_001`
satisfies `I-ASM-001` (`ts_present` over nullable string source) but does
not claim it — audit gap from T06, counts unaffected.
