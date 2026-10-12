"""Bundled scenario loader seam (C02).

Stable signature owned by this module; C13 switches the backing store to
installed package data without changing it.
"""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path

_BUNDLED_RESOURCE = "bundled_scenarios"


def _repository_scenarios_dir() -> Path:
    """Development source: the repository ``scenarios/`` corpus directory."""
    return Path(__file__).resolve().parents[3] / "scenarios"


def _read_source_dir(source: Path) -> list[tuple[str, bytes]]:
    found: list[tuple[str, bytes]] = []
    for path in sorted(source.glob("*.json")):
        found.append((path.stem, path.read_bytes()))
    return found


def iter_bundled_scenarios() -> list[tuple[str, bytes]]:
    """Return ``(scenario_id, json_bytes)`` pairs in deterministic id order.

    Prefers installed ``bundled_scenarios`` package data (C13); falls back to
    the repository ``scenarios/`` directory as the development source. The
    source tree is only read, never written.
    """
    try:
        resource = files("data_pipeline_diagnostics") / _BUNDLED_RESOURCE
        if resource.is_dir():
            return sorted(
                ((p.name.removesuffix(".json"), p.read_bytes()) for p in resource.glob("*.json")),
            )
    except ImportError, FileNotFoundError, NotADirectoryError:
        pass
    return _read_source_dir(_repository_scenarios_dir())
