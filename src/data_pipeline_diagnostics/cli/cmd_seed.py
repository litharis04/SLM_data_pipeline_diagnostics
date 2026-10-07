"""``plgen seed`` — rebuild under a requested seed, fresh working copy (C09).

Same prepare path as ``open`` through the shared :mod:`instances` flow; no
LLM, no logic change. Asserts the catalog ``scenario.json`` bytes are
identical before/after. Exits: ``2`` bad seed/id/invalid input, ``4``
authored saved-requirement/size failure, ``5`` imported clean-control or
infrastructure failure; failures leave the stored seed/pointer intact.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from data_pipeline_diagnostics.cli.instances import Prepare, run_instance

_MAX_DATA_SEED = 2**63 - 1


def run_seed(
    workspace: Path,
    args: argparse.Namespace,
    *,
    prepare: Prepare | None = None,
    cache_root: Path | None = None,
) -> int:
    """Rebuild the scenario under the requested seed and summarize like ``open``."""
    seed = args.data_seed
    if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed <= _MAX_DATA_SEED:
        print(
            f"plgen seed: invalid DATA_SEED {seed!r}: expected 0 <= DATA_SEED <= 2**63 - 1",
            file=sys.stderr,
        )
        return 2
    return run_instance(
        Path(workspace),
        args.scenario_id,
        seed,
        command="seed",
        prepare=prepare,
        cache_root=cache_root,
        assert_bytes_identical=True,
    )
