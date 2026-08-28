"""Parsing and canonical serialization helpers (§18)."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from data_pipeline_diagnostics.scenario.errors import ScenarioParseError
from data_pipeline_diagnostics.scenario.models import Scenario

# ---------------------------------------------------------------------------
# Duplicate-key detection
# ---------------------------------------------------------------------------


def _check_duplicate_keys(data_str: str) -> None:
    """Raise ScenarioParseError if JSON contains duplicate keys."""

    def _hook(pairs: list[tuple[str, object]]) -> dict:
        seen: set[str] = set()
        for k, _ in pairs:
            if k in seen:
                raise ScenarioParseError(f"duplicate key '{k}'", code="P001", path=k)
            seen.add(k)
        return dict(pairs)

    try:
        json.loads(data_str, object_pairs_hook=_hook)
    except ScenarioParseError:
        raise
    except json.JSONDecodeError as e:
        raise ScenarioParseError(str(e), code="P004", path="", original=e) from e


# ---------------------------------------------------------------------------
# Depth limit for Expression/Condition (§10.1)
# ---------------------------------------------------------------------------

_MAX_DEPTH = 16


def _expression_depth(expr: object, current: int = 1) -> int:
    """Return max depth of Expression tree."""
    if current > _MAX_DEPTH:
        return current
    if not isinstance(expr, dict):
        # Pydantic model – convert via model_dump
        if hasattr(expr, "model_dump"):
            expr = expr.model_dump()  # type: ignore[union-attr]
        else:
            return current
    if not isinstance(expr, dict):
        return current
    kind = expr.get("kind")
    if kind == "binary":
        left = expr.get("left")
        right = expr.get("right")
        return max(
            _expression_depth(left, current + 1) if left else current,
            _expression_depth(right, current + 1) if right else current,
        )
    if kind == "date_part":
        val = expr.get("value")
        return _expression_depth(val, current + 1) if val else current
    if kind == "coalesce":
        vals = expr.get("values", [])
        if not isinstance(vals, (list, tuple)):
            return current
        return max((_expression_depth(v, current + 1) for v in vals), default=current)
    return current


def _condition_depth(cond: object, current: int = 1) -> int:
    if current > _MAX_DEPTH:
        return current
    if not isinstance(cond, dict):
        if hasattr(cond, "model_dump"):
            cond = cond.model_dump()  # type: ignore[union-attr]
        else:
            return current
    if not isinstance(cond, dict):
        return current
    kind = cond.get("kind")
    if kind == "comparison":
        left = cond.get("left")
        right = cond.get("right")
        return max(
            _expression_depth(left, current + 1) if left else current,
            _expression_depth(right, current + 1) if right else current,
        )
    if kind == "in":
        val = cond.get("value")
        return _expression_depth(val, current + 1) if val else current
    if kind == "is_null":
        val = cond.get("value")
        return _expression_depth(val, current + 1) if val else current
    if kind in ("all", "any"):
        conds = cond.get("conditions", [])
        return max((_condition_depth(c, current + 1) for c in conds), default=current)
    if kind == "not":
        inner = cond.get("condition")
        return _condition_depth(inner, current + 1) if inner else current
    return current


def _check_depth(scenario: Scenario) -> None:
    """Enforce max expression/condition depth."""
    for tbl in scenario.raw_tables:
        for col in tbl.columns:
            # Raw generators don't contain expressions
            pass
    for m in scenario.staging_models:
        for op in m.row_operations:
            if hasattr(op, "condition"):
                d = _condition_depth(getattr(op, "condition"))
                if d > _MAX_DEPTH:
                    raise ScenarioParseError(
                        f"condition depth {d} exceeds max {_MAX_DEPTH}",
                        code="P003",
                        path=f"staging_models.{m.name}",
                    )
    for m in scenario.intermediate_models:
        for dc in getattr(m, "derived_columns", []):
            d = _expression_depth(getattr(dc, "expression", None))
            if d > _MAX_DEPTH:
                raise ScenarioParseError(
                    f"expression depth {d} exceeds max {_MAX_DEPTH}",
                    code="P003",
                    path=f"intermediate_models.{m.name}.derived_columns.{dc.name}",
                )
        for f in getattr(m, "filters", []):
            d = _condition_depth(f)
            if d > _MAX_DEPTH:
                raise ScenarioParseError(
                    f"condition depth {d} exceeds max {_MAX_DEPTH}",
                    code="P003",
                    path=f"intermediate_models.{m.name}",
                )
        if hasattr(m, "join"):
            # Join doesn't have expressions directly
            pass
    for out in scenario.output_models:
        for f in getattr(out, "filters", []):
            d = _condition_depth(f)
            if d > _MAX_DEPTH:
                raise ScenarioParseError(
                    f"condition depth {d} exceeds max {_MAX_DEPTH}",
                    code="P003",
                    path=f"output_models.{out.name}",
                )


# ---------------------------------------------------------------------------
# Public parsing helpers
# ---------------------------------------------------------------------------

_DEFAULT_MAX_SIZE = 5 * 1024 * 1024  # 5 MB


def parse_scenario_json(data: bytes | str, *, max_size: int = _DEFAULT_MAX_SIZE) -> Scenario:
    """Parse Scenario from JSON bytes/str with strict validation."""
    if isinstance(data, bytes):
        try:
            data_str = data.decode("utf-8")
        except UnicodeDecodeError as e:
            raise ScenarioParseError("invalid UTF-8", code="P004", path="", original=e) from e
        data_bytes_len = len(data)
    else:
        data_str = data
        data_bytes_len = len(data_str.encode("utf-8"))

    if data_bytes_len > max_size:
        raise ScenarioParseError(
            f"document size {data_bytes_len} exceeds max {max_size}", code="P002", path=""
        )

    # Duplicate keys
    _check_duplicate_keys(data_str)

    # Strict Pydantic validation
    try:
        # MUST NOT use model_construct – use strict JSON path
        scenario = Scenario.model_validate_json(data_str, strict=True)
    except ValidationError as e:
        # Preserve locations – take first error for code/path, but keep original
        first = e.errors()[0] if e.errors() else {}
        loc = ".".join(str(p) for p in first.get("loc", []))
        raise ScenarioParseError(str(e), code="P004", path=loc, original=e) from e
    except ValueError as e:
        raise ScenarioParseError(str(e), code="P004", path="", original=e) from e

    # Depth limit
    _check_depth(scenario)

    return scenario


def parse_scenario_file(path: str | Path, *, max_size: int = _DEFAULT_MAX_SIZE) -> Scenario:
    """Parse Scenario from file path."""
    p = Path(path)
    data = p.read_bytes()
    return parse_scenario_json(data, max_size=max_size)


# ---------------------------------------------------------------------------
# Canonical serialization (§18.2)
# ---------------------------------------------------------------------------


def _normalize_timestamps(obj: object) -> object:
    """Recursively normalize datetime strings to UTC canonical form."""
    if isinstance(obj, dict):
        return {k: _normalize_timestamps(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_normalize_timestamps(v) for v in obj]
    if isinstance(obj, tuple):
        return tuple(_normalize_timestamps(v) for v in obj)
    if isinstance(obj, str):
        # Try to parse as datetime with offset and convert to UTC
        # Heuristic: if string contains 'T' and timezone
        try:
            # Handle ISO datetime with offset
            if "T" in obj and ("+" in obj or obj.endswith("Z") or "-" in obj[10:]):
                # Use fromisoformat with handling Z
                iso = obj.replace("Z", "+00:00")
                dt = datetime.fromisoformat(iso)
                if dt.tzinfo is not None:
                    dt_utc = dt.astimezone(UTC)
                    # Canonical: YYYY-MM-DDTHH:MM:SS+00:00 or with Z? Use +00:00
                    return dt_utc.isoformat()
        except Exception:
            pass
        return obj
    return obj


def canonical_json(scenario: Scenario) -> bytes:
    """Return canonical UTF-8 JSON bytes for scenario."""
    # mode="json" ensures dates/timestamps are ISO strings, exclude_none omits None, sort_keys for determinism
    dumped = scenario.model_dump(mode="json", exclude_none=True)
    # Normalize timestamps to UTC
    normalized = _normalize_timestamps(dumped)
    # Use compact separators and sorted keys for content hashing
    json_str = json.dumps(normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return json_str.encode("utf-8")


def scenario_content_hash(scenario: Scenario) -> str:
    """Hash of canonical JSON (for manifest)."""
    return hashlib.sha256(canonical_json(scenario)).hexdigest()
