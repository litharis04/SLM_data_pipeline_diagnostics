# G20: End-to-end minimum gate + full-corpus dataset-build run

## Goal
Prove the §21.0 minimum gate and run the §21.4 dataset-build gate (all 144 corpus scenarios
x one clean seed). Prereq: ALL of G01–G19. This is the ONLY task that builds the full corpus.

## Spec refs
- `docs/GENERATOR_SPEC.md` §§21.0 (minimum gate), 21.1–21.4 (full conformance), 22
  (readiness = minimum gate + quality gates; corpus run is the dataset-build gate).

## Do
- Part A — minimum gate (must be cheap enough for CI):
  - one positive fixture per closed-union variant renders + builds (reuse G03–G16 fixtures;
    add any missing variant so coverage is complete);
  - one deliberately failing clean control per custom-test family (G16–G17 fixtures);
  - repeatability / stream isolation / stochastic-seed-variation (G02 fixtures);
  - one full `prepare_clean_instance` incl. cache-hit-on-repeat (G19).
  - Collect these as `tests/generator/test_g20_gate.py` (mostly re-exports/assertions over
    earlier fixtures — do NOT duplicate heavy builds; reuse small fixtures).
- Part B — dataset-build gate (slow, run once, may be split across sessions):
  - script `scripts/run_corpus_clean.py` (new `scripts/` dir): for each
    `scenarios/*.json` x seed(s), `prepare_clean_instance`, record per-scenario
    pass/fail + `failure_record.json` path. On ANY failure: stop, keep the workspace, and
    report per spec §17 / `PIPELINE_SPEC.md` §7 (defect, omitted invariant, or
    data-incompatible content — fix at the responsible layer, never weaken).
  - Success criterion: 144/144 clean baselines cached. (Additional seeds only where the
    future fault catalog marks contexts seed-sensitive — out of scope here.)

## Tests / evidence
- `test_g20_gate.py` green in CI time.
- Part B evidence: committed run log under `artifacts/corpus_clean_<date>.md` (counts +
  any failures); code changes it forces go through their owning tasks/specs.

## Accept
- [ ] Minimum gate green; full suite green; ruff clean; `git diff --check` clean.
- [ ] Corpus run 144/144 (or a precise failure report with owning layer per failure).
- [ ] `STATE.md`: readiness bullet (generator ready per §22) or the blocking defect list.
