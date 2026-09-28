"""Generator implementation versions (identity components, GENERATOR_SPEC §7.2).

Every output-affecting change to random sampling, physical naming, data
typing, SQL rendering, test lowering, or generated templates MUST bump the
matching version (invalidating the G19 cache). Absolute paths, hostnames,
timestamps, and invocation IDs never participate in identity.
"""

from __future__ import annotations

__all__ = [
    "DBT_RENDERER_VERSION",
    "GENERATOR_CONTRACT_VERSION",
    "RAW_GENERATOR_VERSION",
]

GENERATOR_CONTRACT_VERSION = "1.0.0"
RAW_GENERATOR_VERSION = "1.0.0"
DBT_RENDERER_VERSION = "1.0.0"
