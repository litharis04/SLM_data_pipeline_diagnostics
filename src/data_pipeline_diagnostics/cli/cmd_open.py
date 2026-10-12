"""``plgen open`` — prepare a current-seed instance, fresh working copy (C08).

Thin wrapper over the shared :mod:`instances` flow with the entry's current
seed; see that module for exit codes and summary contents.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from data_pipeline_diagnostics.cli.instances import Prepare, run_instance


def run_open(
    workspace: Path,
    args: argparse.Namespace,
    *,
    prepare: Prepare | None = None,
    cache_root: Path | None = None,
) -> int:
    """Prepare the current-seed instance and print the §7 summary."""
    return run_instance(
        Path(workspace),
        args.scenario_id,
        None,
        command="open",
        prepare=prepare,
        cache_root=cache_root,
    )
