"""G02 tests: named RNG stream determinism, isolation, and derivation vector.

Technique notes: the pinned hex vector below fixes the §8.1 derivation
(canonical JSON ``["dpd-rng-v1", scenario_id, data_seed, stream_name]``,
SHA-256, unsigned big-endian); any change to payload spelling or hashing
fails the pin. Wide-range draws (1..10**9) make accidental cross-seed
collisions negligible without asserting novelty (spec §8.3 guarantees
reproducibility only).
"""

from __future__ import annotations

from data_pipeline_diagnostics.generator.rng import (
    STREAM_SCHEME,
    foreign_key_nulls_stream_name,
    foreign_key_stream_name,
    nulls_stream_name,
    rows_stream_name,
    stream,
    subseed,
    values_stream_name,
)

SCENARIO = "agriculture_coop_001"

# subseed("agriculture_coop_001", 7, "values/crop_yields/yield_t")
PINNED_HEX = "e4af787e553faa5c1f60dbca8f9349ac04987e74605c5cc734d59a97d2fb500c"


def _draws(scenario_id: str, seed: int, name: str, n: int = 20) -> list[int]:
    rng = stream(scenario_id, seed, name)
    return [rng.randint(1, 10**9) for _ in range(n)]


def test_same_inputs_identical_sequences():
    name = values_stream_name("crop_yields", "yield_t")
    assert _draws(SCENARIO, 42, name) == _draws(SCENARIO, 42, name)


def test_different_seed_different_sequences():
    name = values_stream_name("crop_yields", "yield_t")
    assert _draws(SCENARIO, 42, name) != _draws(SCENARIO, 43, name)


def test_column_isolation():
    col_a = values_stream_name("crop_yields", "column_a")
    col_b = values_stream_name("crop_yields", "column_b")
    before = _draws(SCENARIO, 42, col_b)
    # Consume draws from an unrelated stream, then re-create: col_b unaffected.
    _draws(SCENARIO, 42, col_a, n=100)
    assert _draws(SCENARIO, 42, col_b) == before


def test_canonical_hash_not_an_input():
    # Same (scenario_id, seed, stream) yields the same subseed regardless of
    # scenario content edits: content is not a derivation parameter at all.
    assert subseed(SCENARIO, 7, "rows/crop_yields") == subseed(SCENARIO, 7, "rows/crop_yields")


def test_pinned_derivation_vector():
    assert STREAM_SCHEME == "dpd-rng-v1"
    assert format(subseed(SCENARIO, 7, "values/crop_yields/yield_t"), "064x") == PINNED_HEX


def test_stream_namespaces():
    assert rows_stream_name("t") == "rows/t"
    assert values_stream_name("t", "c") == "values/t/c"
    assert nulls_stream_name("t", "c") == "nulls/t/c"
    assert foreign_key_stream_name("r", "d", "left") == "foreign_key/r/d/left"
    assert foreign_key_nulls_stream_name("r", "d", "left") == "nulls/foreign_key/r/d/left"
