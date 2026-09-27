# G03: Scalar mini-generators, part 1 (id / numeric / date / timestamp)

## Goal
Execute `formatted_id`, `integer_range`, `float_range`, `date_range`, `timestamp_range` per
`docs/GENERATOR_SPEC.md` §§10.1–10.3. Pure functions taking `(generator_config, rng, row_index)`.

## Spec refs
- `docs/GENERATOR_SPEC.md` §§10.1–10.3, §5.2 (type mapping), §11 (proposals vs constraints —
  here only the proposal; nulls/keys applied in G06).

## Do
- Create `src/data_pipeline_diagnostics/generator/raw_values.py` with one function per kind,
  dispatched on the config `kind` (exhaustive `match`/`if` chain; unknown kind raises).
- `formatted_id`: `prefix + str(start + i).zfill(digits)`; overflow beyond digit capacity
  raises (no RNG consumed).
- `integer_range`: uniform int from inclusive `[min, max]` via the passed stream.
- `float_range`: lattice `ceil(Decimal(min)*scale)..floor(Decimal(max)*scale)` from canonical
  decimal spelling of inputs (`Decimal(str(x))`, never binary expansion); empty lattice raises;
  result `int/scale` as float.
- `date_range`: uniform day offset over inclusive `[min, max]`.
- `timestamp_range`: normalize bounds to UTC, uniform inclusive microsecond offset, UTC result;
  never local time/DST/now.

## Tests — `tests/generator/test_g03_scalar1.py`
- Each kind: fixed-seed repeatability + value inside declared bounds.
- Edge bounds: single-day date range, `min+1==max` integer range, `decimal_places=0`.
- `formatted_id` zero-pad exact (`digits=3, start=7, i=0 -> "007"`-style with prefix) and
  overflow raises.
- Empty float lattice raises (e.g. `min=0.05, max=0.06, decimal_places=0`).
- Naive timestamps never produced (all outputs tz-aware UTC).

## Accept
- [ ] No RNG except the passed stream; `formatted_id` consumes none.
- [ ] Tests green; full suite green; ruff clean; `git diff --check` clean.
- [ ] `STATE.md` bullet appended.
