# G19: Cache + clean-baseline handoff

## Goal
Content-addressed cache with atomic publication and immutable handoff per
`docs/GENERATOR_SPEC.md` §§7, 19. Prereq: G17–G18.

## Spec refs
- `docs/GENERATOR_SPEC.md` §§7.1–7.2 (scenario hash via scenario package; identity incl.
  Python `major.minor` (NOT patch), exact DuckDB/dbt/Faker/writer versions; no
  paths/hosts/pids/timestamps in identity; `<cache_root>/v1/<instance_digest>/` suffices, no
  service), 19 (reuse only on exact match + `SUCCESS` + record match + artifacts +
  integrity; sibling-temp-dir build; atomic/race-safe publish; fault work outside the cache
  dir on a copy/reconstruction).

## Do
- Create `cache.py`:
  - `identity_digest(validated, data_seed) -> str`: canonical-JSON SHA-256 over the §7.2
    components (single canonical constructor shared with G18 — deduplicate here, G18
    imports from here if needed).
  - `prepare_clean_instance`: check-then-build; hit requires ALL of: digest match,
    `SUCCESS` present, `instance_record.json` status success + path/key match, all artifacts
    present, digests verify. Miss builds in a sibling temp dir, publishes atomically
    (`os.replace` of the finished dir; never repair-in-place).
  - Handoff: return an isolated filesystem copy (or deterministic-reconstruction recipe) —
    fault injection MUST occur outside the cached dir. Document which one you implemented.

## Tests — `tests/generator/test_g19_cache.py`
- Repeat preparation of one identity returns a verified hit (build counter == 1).
- Pinned-version change (simulate by monkeypatching a version) -> miss.
- `data_seed` change and canonical-content change -> miss; Python *patch* change does NOT
  miss (unit-test the digest constructor with two patch strings).
- Mutating the handed-off copy leaves the cached baseline (digests) unchanged.

## Accept
- [ ] No cache service/DB; no partial-entry reuse.
- [ ] Tests green; full suite green; ruff clean; `git diff --check` clean.
- [ ] `STATE.md` bullet appended.
