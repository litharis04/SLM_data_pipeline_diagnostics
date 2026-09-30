"""Named deterministic RNG streams (GENERATOR_SPEC §8).

All stochastic choices in later generator code MUST come from :func:`stream`.
A single mutable scenario-wide RNG is prohibited; each stream is an isolated
``random.Random`` instance seeded from its own subseed, so adding or changing
one column MUST NOT shift unrelated streams. Besides the five §8.2
namespaces, the raw executor allocates ``pk/<table>`` for joint
composite-PK shuffles (additive per the spec's "at minimum" wording).
"""

from __future__ import annotations

import hashlib
import json
import random

__all__ = [
    "STREAM_SCHEME",
    "foreign_key_nulls_stream_name",
    "foreign_key_stream_name",
    "nulls_stream_name",
    "pk_stream_name",
    "rows_stream_name",
    "stream",
    "subseed",
    "values_stream_name",
]

STREAM_SCHEME = "dpd-rng-v1"

MIN_DATA_SEED = 0
MAX_DATA_SEED = 2**63 - 1


def _check_seed(data_seed: object) -> int:
    if type(data_seed) is not int:
        raise ValueError(f"data_seed must be a strict int, got {type(data_seed).__name__}")
    if not MIN_DATA_SEED <= data_seed <= MAX_DATA_SEED:
        raise ValueError(f"data_seed {data_seed} out of range [0, 2**63 - 1]")
    return data_seed


def _check_text(value: object, name: str) -> str:
    if type(value) is not str or not value:
        raise TypeError(f"{name} must be a non-empty str, got {type(value).__name__}")
    return value


def subseed(scenario_id: str, data_seed: int, stream_name: str) -> int:
    """Derive an isolated stream subseed per spec §8.1.

    ``payload`` is the canonical JSON of ``["dpd-rng-v1", scenario_id,
    data_seed, stream_name]`` (no insignificant whitespace, UTF-8); the
    subseed is the unsigned big-endian integer of its SHA-256 digest. The
    canonical scenario hash is deliberately NOT an input.
    """
    _check_text(scenario_id, "scenario_id")
    _check_text(stream_name, "stream_name")
    seed = _check_seed(data_seed)
    payload = json.dumps(
        [STREAM_SCHEME, scenario_id, seed, stream_name],
        separators=(",", ":"),
        ensure_ascii=False,
    )
    digest = hashlib.sha256(payload.encode("utf-8")).digest()
    return int.from_bytes(digest, "big")


def stream(scenario_id: str, data_seed: int, stream_name: str) -> random.Random:
    """Return a fresh isolated PRNG for one named stream (seed version 2)."""
    rng = random.Random()
    rng.seed(subseed(scenario_id, data_seed, stream_name), version=2)
    return rng


def rows_stream_name(table: str) -> str:
    """Row-count stream for one raw table: ``rows/<table>``."""
    return f"rows/{_check_text(table, 'table')}"


def pk_stream_name(table: str) -> str:
    """Joint primary-key shuffle stream for one raw table: ``pk/<table>``."""
    return f"pk/{_check_text(table, 'table')}"


def values_stream_name(table: str, column: str) -> str:
    """Value stream for one column: ``values/<table>/<column>``."""
    return f"values/{_check_text(table, 'table')}/{_check_text(column, 'column')}"


def nulls_stream_name(table: str, column: str) -> str:
    """Null-decision stream for one column: ``nulls/<table>/<column>``."""
    return f"nulls/{_check_text(table, 'table')}/{_check_text(column, 'column')}"


def foreign_key_stream_name(relationship: str, dependent_table: str, target_side: str) -> str:
    """FK value stream for one dependent tuple: ``foreign_key/<rel>/<dep>/<side>``."""
    return (
        f"foreign_key/{_check_text(relationship, 'relationship')}"
        f"/{_check_text(dependent_table, 'dependent_table')}"
        f"/{_check_text(target_side, 'target_side')}"
    )


def foreign_key_nulls_stream_name(relationship: str, dependent_table: str, target_side: str) -> str:
    """FK null-decision stream: ``nulls/foreign_key/<rel>/<dep>/<side>``."""
    return (
        f"nulls/foreign_key/{_check_text(relationship, 'relationship')}"
        f"/{_check_text(dependent_table, 'dependent_table')}"
        f"/{_check_text(target_side, 'target_side')}"
    )
