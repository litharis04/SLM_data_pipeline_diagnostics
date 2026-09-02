"""Semantic validation — cross-object checks (SCENARIO_SPEC §17)."""

from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING

from data_pipeline_diagnostics.scenario.errors import (
    ErrorCode,
    SemanticIssue,
    SemanticValidationError,
)
from data_pipeline_diagnostics.scenario.types import DataType

if TYPE_CHECKING:
    from data_pipeline_diagnostics.scenario.models import Scenario

# ---------------------------------------------------------------------------
# ValidatedScenario – distinct immutable wrapper
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ValidatedScenario:
    """Result of successful semantic validation."""

    scenario: object  # Scenario
    raw_by_name: dict
    staging_by_name: dict
    intermediate_by_name: dict
    output_by_name: dict
    relationships_by_name: dict
    topological_order: tuple
    lineage: dict  # model -> dict[column -> list[raw lineage]]
    derived_assertions: tuple
    # §17.1 resolved schemas and grains
    staging_schemas: dict  # staging name -> {col -> DataType}
    intermediate_schemas: dict
    output_schemas: dict
    resolved_grains: dict  # model name -> tuple[Identifier]
    resolved_keys: dict  # raw table -> PK


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ALLOWED_GENERATORS: dict[DataType, set[str]] = {
    DataType.string: {
        "formatted_id",
        "categorical",
        "random_string",
        "template_string",
        "foreign_key",
        "person_name",
        "email",
        "city",
        "street_address",
        "company_name",
        "phone_number",
    },
    DataType.integer: {"integer_range", "categorical", "foreign_key"},
    DataType.float: {"float_range", "categorical", "foreign_key"},
    DataType.boolean: {"boolean", "categorical", "foreign_key"},
    DataType.date: {"date_range", "foreign_key"},
    DataType.timestamp: {"timestamp_range", "foreign_key"},
}

_FAKER_KINDS = {"person_name", "email", "city", "street_address", "company_name", "phone_number"}


def _add_issue(
    issues: list[SemanticIssue], code: str, path: str, message: str, related: str | None = None
) -> None:
    issues.append(SemanticIssue(code=code, path=path, message=message, related=related))


def _scalar_to_datatype(value: object) -> DataType | None:
    """Internal scalar-to-DataType helper used everywhere semantic typing is required."""
    if isinstance(value, bool):
        return DataType.boolean
    if isinstance(value, int):
        return DataType.integer
    if isinstance(value, float):
        return DataType.float
    if isinstance(value, str):
        return DataType.string
    return None


def _infer_expression_type(expr: object, schema: dict[str, DataType]) -> DataType | None:
    """Infer DataType for Expression against schema.

    Returns DataType on success, None on unresolved column or statically invalid.
    Caller must distinguish via _check_expression_type.
    """
    if expr is None:
        return None
    if hasattr(expr, "model_dump"):
        expr = expr.model_dump()  # type: ignore[union-attr]
    if not isinstance(expr, dict):
        return None
    kind = expr.get("kind")
    if kind == "column":
        col = expr.get("column")
        return schema.get(col) if isinstance(col, str) else None
    if kind == "literal":
        return _scalar_to_datatype(expr.get("value"))
    if kind == "binary":
        left = _infer_expression_type(expr.get("left"), schema)
        right = _infer_expression_type(expr.get("right"), schema)
        op = expr.get("operator")
        if op in ("add", "subtract", "multiply", "divide"):
            # Require both operands numeric; unresolved (None) is handled by caller via missing-ref suppression
            if left is None or right is None:
                # If either is unresolved (column missing), return None to suppress cascade
                # But if one is present and the other is invalid, we need to report invalid
                # Check if the missing is due to unresolved column vs invalid type
                # For now, return None and let caller decide based on whether column exists
                return None
            if left in (DataType.integer, DataType.float) and right in (
                DataType.integer,
                DataType.float,
            ):
                if left == DataType.float or right == DataType.float or op == "divide":
                    return DataType.float
                return DataType.integer
            # Statically invalid: non-numeric operand
            return None
        return None
    if kind == "date_part":
        inner = expr.get("value")
        t = _infer_expression_type(inner, schema)
        # Require date or timestamp, not string
        if t in (DataType.date, DataType.timestamp):
            return DataType.integer
        return None
    if kind == "coalesce":
        vals = expr.get("values", [])
        types = [_infer_expression_type(v, schema) for v in vals]
        # Require all operands to have a common compatible type
        # Filter out unresolved (None due to missing column) – suppress
        present_types = [t for t in types if t is not None]
        if not present_types:
            return None
        # Check that all present types are compatible: either all same, or integer/float promotion
        # Numeric promotion: integer and float are compatible -> float
        # Otherwise, all must be equal
        first = present_types[0]
        for t in present_types[1:]:
            if t != first:
                # Allow integer/float promotion
                if {t, first} <= {DataType.integer, DataType.float}:
                    first = DataType.float
                else:
                    return None
        return first
    return None


def _is_column_unresolved(expr: object, schema: dict[str, DataType]) -> bool:
    """Check if expression contains an unresolved column (already reported as missing)."""
    if expr is None:
        return False
    if hasattr(expr, "model_dump"):
        expr = expr.model_dump()  # type: ignore[union-attr]
    if not isinstance(expr, dict):
        return False
    kind = expr.get("kind")
    if kind == "column":
        col = expr.get("column")
        return isinstance(col, str) and col not in schema
    if kind == "binary":
        return _is_column_unresolved(expr.get("left"), schema) or _is_column_unresolved(expr.get("right"), schema)
    if kind == "date_part":
        return _is_column_unresolved(expr.get("value"), schema)
    if kind == "coalesce":
        return any(_is_column_unresolved(v, schema) for v in expr.get("values", []))
    return False


def _check_expression_columns(
    expr: object, schema: dict[str, DataType], issues: list[SemanticIssue], path: str
) -> None:
    """Check that all ColumnExpression refs exist in schema."""
    if expr is None:
        return
    if hasattr(expr, "model_dump"):
        expr = expr.model_dump()  # type: ignore[union-attr]
    if not isinstance(expr, dict):
        return
    kind = expr.get("kind")
    if kind == "column":
        col = expr.get("column")
        if col not in schema:
            _add_issue(
                issues,
                ErrorCode.MISSING_REF,
                path,
                f"column '{col}' not in schema",
                related=str(col),
            )
    elif kind == "binary":
        _check_expression_columns(expr.get("left"), schema, issues, path)
        _check_expression_columns(expr.get("right"), schema, issues, path)
    elif kind == "date_part":
        _check_expression_columns(expr.get("value"), schema, issues, path)
    elif kind == "coalesce":
        for v in expr.get("values", []):
            _check_expression_columns(v, schema, issues, path)


def _check_condition(
    cond: object, schema: dict[str, DataType], issues: list[SemanticIssue], path: str
) -> None:
    if cond is None:
        return
    if hasattr(cond, "model_dump"):
        cond = cond.model_dump()  # type: ignore[union-attr]
    if not isinstance(cond, dict):
        return
    kind = cond.get("kind")
    if kind == "comparison":
        left = cond.get("left")
        right = cond.get("right")
        _check_expression_columns(left, schema, issues, path)
        _check_expression_columns(right, schema, issues, path)
        # type-compatibility: both sides should be comparable (same type or numeric)
        lt = _infer_expression_type(left, schema)
        rt = _infer_expression_type(right, schema)
        if lt is not None and rt is not None and lt != rt:
            # allow numeric promotion integer vs float
            if not ({lt, rt} <= {DataType.integer, DataType.float}):
                _add_issue(
                    issues,
                    ErrorCode.UNKNOWN,
                    path,
                    f"comparison type mismatch '{lt.value}' vs '{rt.value}'",
                    related=str(kind),
                )
    elif kind == "in":
        _check_expression_columns(cond.get("value"), schema, issues, path)
        # Check that options are compatible with value's inferred type
        val_type = _infer_expression_type(cond.get("value"), schema)
        if val_type is not None:
            for opt in cond.get("options", []):
                opt_type = _scalar_to_datatype(opt)
                if opt_type is not None and opt_type != val_type:
                    # Allow numeric promotion
                    if not ({opt_type, val_type} <= {DataType.integer, DataType.float}):
                        _add_issue(
                            issues,
                            ErrorCode.INVALID_CONDITION_TYPE,
                            path,
                            f"InCondition option type '{opt_type.value}' != value type '{val_type.value}'",
                            related=str(opt),
                        )
    elif kind == "is_null":
        _check_expression_columns(cond.get("value"), schema, issues, path)
    elif kind in ("all", "any"):
        for c in cond.get("conditions", []):
            _check_condition(c, schema, issues, path)
    elif kind == "not":
        _check_condition(cond.get("condition"), schema, issues, path)


def _collect_expression_columns(expr: object) -> list[str]:
    """Collect all column names referenced in an Expression."""
    if expr is None:
        return []
    if hasattr(expr, "model_dump"):
        expr = expr.model_dump()  # type: ignore[union-attr]
    if not isinstance(expr, dict):
        return []
    kind = expr.get("kind")
    if kind == "column":
        col = expr.get("column")
        return [col] if isinstance(col, str) else []
    if kind == "binary":
        return _collect_expression_columns(expr.get("left")) + _collect_expression_columns(
            expr.get("right")
        )
    if kind == "date_part":
        return _collect_expression_columns(expr.get("value"))
    if kind == "coalesce":
        cols: list[str] = []
        for v in expr.get("values", []):
            cols.extend(_collect_expression_columns(v))
        return cols
    return []


def _collect_condition_columns(cond: object) -> list[str]:
    """Collect all column names referenced in a Condition."""
    if cond is None:
        return []
    if hasattr(cond, "model_dump"):
        cond = cond.model_dump()  # type: ignore[union-attr]
    if not isinstance(cond, dict):
        return []
    kind = cond.get("kind")
    if kind == "comparison":
        return _collect_expression_columns(cond.get("left")) + _collect_expression_columns(
            cond.get("right")
        )
    if kind == "in":
        return _collect_expression_columns(cond.get("value"))
    if kind == "is_null":
        return _collect_expression_columns(cond.get("value"))
    if kind in ("all", "any"):
        cols: list[str] = []
        for c in cond.get("conditions", []):
            cols.extend(_collect_condition_columns(c))
        return cols
    if kind == "not":
        return _collect_condition_columns(cond.get("condition"))
    return []


def _raw_lineage_for_column(
    model_name: str,
    col_name: str,
    staging_lineage: dict[str, dict[str, list[str]]],
    intermediate_lineage: dict[str, dict[str, list[str]]],
) -> list[str]:
    """Return raw lineage for a column in a staging or intermediate model."""
    if model_name in staging_lineage:
        return staging_lineage[model_name].get(col_name, [f"{model_name}.{col_name}"])
    if model_name in intermediate_lineage:
        return intermediate_lineage[model_name].get(col_name, [f"{model_name}.{col_name}"])
    return [f"{model_name}.{col_name}"]


# ---------------------------------------------------------------------------
# Main validator
# ---------------------------------------------------------------------------


def validate_semantics(scenario: Scenario) -> ValidatedScenario:  # type: ignore[no-untyped-def]
    """Validate ``scenario`` semantically and return ``ValidatedScenario`` or raise."""
    issues: list[SemanticIssue] = []

    # Symbol tables
    raw_by_name: dict[str, object] = {}
    staging_by_name: dict[str, object] = {}
    intermediate_by_name: dict[str, object] = {}
    output_by_name: dict[str, object] = {}
    rel_by_name: dict[str, object] = {}
    assertion_by_name: dict[str, object] = {}

    # §17.2 unique raw-table names
    seen_raw: set[str] = set()
    for idx, tbl in enumerate(scenario.raw_tables):
        path = f"raw_tables[{idx}].name"
        if tbl.name in seen_raw:
            _add_issue(
                issues,
                ErrorCode.UNIQUE_RAW_TABLE,
                path,
                f"duplicate raw table name '{tbl.name}'",
                related=tbl.name,
            )
        else:
            seen_raw.add(tbl.name)
            if tbl.name not in raw_by_name:
                raw_by_name[tbl.name] = tbl

    # unique model names across staging|intermediate|output
    seen_models: dict[str, str] = {}
    for idx, m in enumerate(scenario.staging_models):
        path = f"staging_models[{idx}].name"
        if m.name in seen_models:
            _add_issue(
                issues,
                ErrorCode.UNIQUE_MODEL,
                path,
                f"duplicate model name '{m.name}'",
                related=m.name,
            )
        elif m.name in raw_by_name:
            _add_issue(
                issues,
                ErrorCode.RAW_MODEL_COLLISION,
                path,
                f"model name '{m.name}' collides with raw table",
                related=m.name,
            )
        else:
            seen_models[m.name] = "staging"
            staging_by_name[m.name] = m
    for idx, m in enumerate(scenario.intermediate_models):
        path = f"intermediate_models[{idx}].name"
        if m.name in seen_models:
            _add_issue(
                issues,
                ErrorCode.UNIQUE_MODEL,
                path,
                f"duplicate model name '{m.name}'",
                related=m.name,
            )
        elif m.name in raw_by_name:
            _add_issue(
                issues,
                ErrorCode.RAW_MODEL_COLLISION,
                path,
                f"model name '{m.name}' collides with raw table",
                related=m.name,
            )
        else:
            seen_models[m.name] = "intermediate"
            intermediate_by_name[m.name] = m
    for idx, m in enumerate(scenario.output_models):
        path = f"output_models[{idx}].name"
        if m.name in seen_models:
            _add_issue(
                issues,
                ErrorCode.UNIQUE_MODEL,
                path,
                f"duplicate model name '{m.name}'",
                related=m.name,
            )
        elif m.name in raw_by_name:
            _add_issue(
                issues,
                ErrorCode.RAW_MODEL_COLLISION,
                path,
                f"model name '{m.name}' collides with raw table",
                related=m.name,
            )
        else:
            seen_models[m.name] = "output"
            output_by_name[m.name] = m

    # unique relationship names
    seen_rel: set[str] = set()
    for idx, rel in enumerate(scenario.relationships):
        path = f"relationships[{idx}].name"
        if rel.name in seen_rel:
            _add_issue(
                issues,
                ErrorCode.UNIQUE_RELATIONSHIP,
                path,
                f"duplicate relationship name '{rel.name}'",
                related=rel.name,
            )
        else:
            seen_rel.add(rel.name)
            rel_by_name[rel.name] = rel

    # unique assertion names
    seen_assert: set[str] = set()
    for idx, a in enumerate(scenario.tests):
        path = f"tests[{idx}].name"
        if a.name in seen_assert:
            _add_issue(
                issues,
                ErrorCode.UNIQUE_ASSERTION,
                path,
                f"duplicate assertion name '{a.name}'",
                related=a.name,
            )
        else:
            seen_assert.add(a.name)
            assertion_by_name[a.name] = a

    # Build raw column maps
    raw_col_map: dict[str, dict[str, object]] = {}
    raw_col_type: dict[str, dict[str, DataType]] = {}
    for tbl in scenario.raw_tables:
        col_map: dict[str, object] = {}
        type_map: dict[str, DataType] = {}
        for col in tbl.columns:
            col_map[col.name] = col
            type_map[col.name] = col.type
        raw_col_map[tbl.name] = col_map
        raw_col_type[tbl.name] = type_map

    # Helper to estimate generator capacity for feasibility
    def _generator_capacity(col: object) -> int | None:
        gen = getattr(col, "generator", None)
        if gen is None:
            return None
        kind = getattr(gen, "kind", None)
        if kind == "formatted_id":
            digits = getattr(gen, "digits", 0)
            start = getattr(gen, "start", 1)
            try:
                return int(10**digits - start + 1)
            except Exception:
                return None
        if kind == "integer_range":
            try:
                return int(getattr(gen, "max") - getattr(gen, "min") + 1)
            except Exception:
                return None
        if kind == "categorical":
            vals = getattr(gen, "values", ())
            return len(vals)
        if kind == "random_string":
            # very large, treat as infinite
            return 10**9
        if kind == "boolean":
            return 2
        if kind in (
            "person_name",
            "email",
            "city",
            "street_address",
            "company_name",
            "phone_number",
        ):
            return 10**6
        return None

    # §17.3 Raw/keys/generators
    for idx, tbl in enumerate(scenario.raw_tables):
        base = f"raw_tables[{idx}]"
        # PK members exist & non-nullable
        for pk_col in tbl.primary_key:
            col = raw_col_map.get(tbl.name, {}).get(pk_col)
            if col is None:
                _add_issue(
                    issues,
                    ErrorCode.INVALID_PK,
                    f"{base}.primary_key",
                    f"PK column '{pk_col}' does not exist in table '{tbl.name}'",
                    related=pk_col,
                )
            elif getattr(col, "nullable", False):
                _add_issue(
                    issues,
                    ErrorCode.INVALID_PK,
                    f"{base}.primary_key",
                    f"PK column '{pk_col}' must be non-nullable",
                    related=pk_col,
                )
        # feasibility for PK/unique vs row_count – composite PK capacity is product
        max_rows = tbl.rows.max
        if tbl.primary_key:
            # Check composite PK capacity as product
            if len(tbl.primary_key) == 1:
                col = raw_col_map[tbl.name].get(tbl.primary_key[0])
                if col is not None and (getattr(col, "unique", False) or True):
                    cap = _generator_capacity(col)
                    if cap is not None and cap < max_rows:
                        _add_issue(
                            issues,
                            ErrorCode.INVALID_PK,
                            f"{base}.primary_key",
                            f"PK capacity {cap} < max rows {max_rows}",
                            related=tbl.primary_key[0],
                        )
            else:
                # Composite PK: product of capacities
                caps: list[int] = []
                for pk_col in tbl.primary_key:
                    col = raw_col_map[tbl.name].get(pk_col)
                    if col is not None:
                        cap = _generator_capacity(col)
                        if cap is not None:
                            caps.append(cap)
                if caps:
                    # product, but cap at large number to avoid overflow
                    prod = 1
                    for c in caps:
                        prod = min(prod * c, 10**12)
                    if prod < max_rows:
                        _add_issue(
                            issues,
                            ErrorCode.INVALID_PK,
                            f"{base}.primary_key",
                            f"composite PK capacity {prod} < max rows {max_rows}",
                            related=",".join(tbl.primary_key),
                        )
        for col in tbl.columns:
            if getattr(col, "unique", False) and col.name not in tbl.primary_key:
                cap = _generator_capacity(col)
                if cap is not None and cap < max_rows:
                    _add_issue(
                        issues,
                        ErrorCode.INVALID_PK,
                        f"{base}.columns[{col.name}]",
                        f"unique column '{col.name}' capacity {cap} < max rows {max_rows}",
                        related=col.name,
                    )
        # generator/type compatibility etc.
        for cidx, col in enumerate(tbl.columns):
            cpath = f"{base}.columns[{cidx}]"
            gen_kind = getattr(col.generator, "kind", None)
            allowed = _ALLOWED_GENERATORS.get(col.type)
            if allowed is not None and gen_kind not in allowed:
                _add_issue(
                    issues,
                    ErrorCode.GENERATOR_TYPE_MISMATCH,
                    f"{cpath}.generator.kind",
                    f"generator '{gen_kind}' not allowed for column type '{col.type.value}'",
                    related=gen_kind,
                )
            if gen_kind in _FAKER_KINDS and col.type != DataType.string:
                _add_issue(
                    issues,
                    ErrorCode.FAKER_TYPE,
                    f"{cpath}.generator.kind",
                    f"Faker generator '{gen_kind}' only allowed on string columns",
                    related=gen_kind,
                )
            if gen_kind == "categorical":
                values = getattr(col.generator, "values", ())
                expected_py: dict[DataType, tuple[str, ...]] = {
                    DataType.string: ("str",),
                    DataType.integer: ("int",),
                    DataType.float: ("float",),
                    DataType.boolean: ("bool",),
                    DataType.date: ("date",),
                    DataType.timestamp: ("datetime",),
                }
                exp = expected_py.get(col.type)
                if exp is not None:
                    for v in values:
                        actual = type(v).__name__
                        if col.type == DataType.string and actual != "str":
                            _add_issue(
                                issues,
                                ErrorCode.CATEGORICAL_HOMOGENEOUS,
                                f"{cpath}.generator.values",
                                f"categorical values must be homogeneous for column type '{col.type.value}'",
                                related=str(v),
                            )
                            break
                        if col.type == DataType.integer and actual != "int":
                            _add_issue(
                                issues,
                                ErrorCode.CATEGORICAL_HOMOGENEOUS,
                                f"{cpath}.generator.values",
                                f"categorical values must be homogeneous for column type '{col.type.value}'",
                                related=str(v),
                            )
                            break
                        if col.type == DataType.float and actual != "float":
                            _add_issue(
                                issues,
                                ErrorCode.CATEGORICAL_HOMOGENEOUS,
                                f"{cpath}.generator.values",
                                f"categorical values must be homogeneous for column type '{col.type.value}'",
                                related=str(v),
                            )
                            break
                        if col.type == DataType.boolean and actual != "bool":
                            _add_issue(
                                issues,
                                ErrorCode.CATEGORICAL_HOMOGENEOUS,
                                f"{cpath}.generator.values",
                                f"categorical values must be homogeneous for column type '{col.type.value}'",
                                related=str(v),
                            )
                            break
            if gen_kind == "template_string":
                template = getattr(col.generator, "template", "")
                placeholders = re.findall(r"\{([a-z][a-z0-9_]*)\}", template)
                for ph in placeholders:
                    if ph not in raw_col_map.get(tbl.name, {}):
                        _add_issue(
                            issues,
                            ErrorCode.TEMPLATE_PLACEHOLDER,
                            f"{cpath}.generator.template",
                            f"placeholder '{ph}' does not resolve to another column in same table '{tbl.name}'",
                            related=ph,
                        )
                    if ph == col.name:
                        _add_issue(
                            issues,
                            ErrorCode.TEMPLATE_PLACEHOLDER,
                            f"{cpath}.generator.template",
                            f"template column '{col.name}' must not reference itself",
                            related=ph,
                        )
            if gen_kind == "foreign_key":
                rel_name = getattr(col.generator, "relationship", None)
                if rel_name not in rel_by_name:
                    _add_issue(
                        issues,
                        ErrorCode.MISSING_REF,
                        f"{cpath}.generator.relationship",
                        f"relationship '{rel_name}' does not exist",
                        related=rel_name,
                    )
        # template cycle detection per table
        template_deps: dict[str, list[str]] = {}
        for col in tbl.columns:
            if getattr(col.generator, "kind", None) == "template_string":
                tmpl = getattr(col.generator, "template", "")
                deps = re.findall(r"\{([a-z][a-z0-9_]*)\}", tmpl)
                template_deps[col.name] = deps
        visited: dict[str, int] = {}

        def _dfs(node: str, stack: list[str]) -> bool:
            state = visited.get(node, 0)
            if state == 1:
                cycle = " -> ".join(stack + [node])
                _add_issue(
                    issues,
                    ErrorCode.TEMPLATE_CYCLE,
                    f"{base}.columns",
                    f"template dependencies have cycle: {cycle}",
                    related=node,
                )
                return True
            if state == 2:
                return False
            visited[node] = 1
            stack.append(node)
            for dep in template_deps.get(node, []):
                if dep in template_deps:
                    _dfs(dep, stack)
            stack.pop()
            visited[node] = 2
            return False

        for n in list(template_deps.keys()):
            if visited.get(n, 0) == 0:
                _dfs(n, [])
        # foreign_key generators – check all components for composite FK atomicity
        # Group FK columns by relationship
        fk_by_rel: dict[str, list[object]] = {}
        for col in tbl.columns:
            if getattr(col.generator, "kind", None) == "foreign_key":
                rel_name = getattr(col.generator, "relationship", None)
                fk_by_rel.setdefault(rel_name, []).append(col)
        for rel_name, cols in fk_by_rel.items():
            if rel_name not in rel_by_name:
                # already reported missing
                continue
            # All components should have same target_side
            sides = {getattr(c.generator, "target_side", None) for c in cols}
            if len(sides) != 1:
                _add_issue(
                    issues,
                    ErrorCode.FOREIGN_KEY_SIDE,
                    f"{base}.columns",
                    f"composite FK columns for relationship '{rel_name}' must have same target_side, got {sides}",
                    related=rel_name,
                )
            # Check atomicity: if relationship has composite key (arity >1), FK should have same number of columns
            # We can check later with relationship arity

    # Track FK ownership for conflict detection (§17.4 one dependent column not owned by conflicting relationships)
    fk_owner: dict[tuple[str, str], str] = {}  # (table, col) -> relationship name

    # §17.4 Relationships
    for idx, rel in enumerate(scenario.relationships):
        base = f"relationships[{idx}]"
        for side in ("left", "right"):
            ep = getattr(rel, side)
            tbl_name = ep.table
            if tbl_name not in raw_by_name:
                _add_issue(
                    issues,
                    ErrorCode.MISSING_REF,
                    f"{base}.{side}.table",
                    f"table '{tbl_name}' does not exist",
                    related=tbl_name,
                )
                continue
            for col_name in ep.columns:
                if col_name not in raw_col_map.get(tbl_name, {}):
                    _add_issue(
                        issues,
                        ErrorCode.MISSING_REF,
                        f"{base}.{side}.columns",
                        f"column '{col_name}' does not exist in table '{tbl_name}'",
                        related=col_name,
                    )
        if rel.cardinality in ("one_to_one", "one_to_many", "many_to_one"):
            left_len = len(rel.left.columns)
            right_len = len(rel.right.columns)
            if left_len != right_len:
                _add_issue(
                    issues,
                    ErrorCode.RELATIONSHIP_ARITY,
                    f"{base}",
                    f"arity mismatch left {left_len} vs right {right_len}",
                    related=rel.name,
                )
            if rel.left.table in raw_col_type and rel.right.table in raw_col_type:
                for lcol, rcol in zip(rel.left.columns, rel.right.columns):
                    ltype = raw_col_type.get(rel.left.table, {}).get(lcol)
                    rtype = raw_col_type.get(rel.right.table, {}).get(rcol)
                    if ltype is not None and rtype is not None and ltype != rtype:
                        _add_issue(
                            issues,
                            ErrorCode.RELATIONSHIP_TYPE,
                            f"{base}",
                            f"type mismatch '{lcol}' ({ltype.value}) vs '{rcol}' ({rtype.value})",
                            related=rel.name,
                        )

            def _is_unique_side(table: str, cols: tuple) -> bool:
                tbl = raw_by_name.get(table)
                if tbl is None:
                    return False
                if tuple(cols) == tuple(tbl.primary_key):
                    return True
                col_objs = [raw_col_map.get(table, {}).get(c) for c in cols]
                if all(getattr(c, "unique", False) for c in col_objs if c is not None):
                    return True
                return False

            if rel.cardinality == "one_to_many":
                if not _is_unique_side(rel.left.table, rel.left.columns):
                    _add_issue(
                        issues,
                        ErrorCode.UNIQUE_SIDE,
                        f"{base}.left",
                        "left side must be unique for one_to_many",
                        related=rel.name,
                    )
            elif rel.cardinality == "many_to_one":
                if not _is_unique_side(rel.right.table, rel.right.columns):
                    _add_issue(
                        issues,
                        ErrorCode.UNIQUE_SIDE,
                        f"{base}.right",
                        "right side must be unique for many_to_one",
                        related=rel.name,
                    )
            elif rel.cardinality == "one_to_one":
                if not _is_unique_side(rel.left.table, rel.left.columns) or not _is_unique_side(
                    rel.right.table, rel.right.columns
                ):
                    _add_issue(
                        issues,
                        ErrorCode.ONE_TO_ONE_UNIQUE,
                        f"{base}",
                        "both sides must be unique for one_to_one",
                        related=rel.name,
                    )

            def _has_fk(table: str, cols: tuple, target_side: str) -> bool:
                cmap = raw_col_map.get(table, {})
                for cname in cols:
                    col = cmap.get(cname)
                    if col is not None and getattr(col.generator, "kind", None) == "foreign_key":
                        if (
                            getattr(col.generator, "relationship", None) == rel.name
                            and getattr(col.generator, "target_side", None) == target_side
                        ):
                            return True
                return False

            # Check FK and also detect conflicting ownership
            if rel.cardinality == "one_to_many":
                for cname in rel.right.columns:
                    key = (rel.right.table, cname)
                    if key in fk_owner and fk_owner[key] != rel.name:
                        _add_issue(
                            issues,
                            ErrorCode.FOREIGN_KEY_SIDE,
                            f"{base}.right",
                            f"column '{cname}' already owned by relationship '{fk_owner[key]}'",
                            related=cname,
                        )
                    else:
                        fk_owner[key] = rel.name
                if not _has_fk(rel.right.table, rel.right.columns, "left"):
                    _add_issue(
                        issues,
                        ErrorCode.FOREIGN_KEY_SIDE,
                        f"{base}.right",
                        "dependent columns must have foreign_key generator targeting left",
                        related=rel.name,
                    )
                # Check that all components of composite FK are present and atomic
                if len(rel.right.columns) > 1:
                    fk_cols = [
                        c
                        for c in raw_col_map.get(rel.right.table, {}).values()
                        if getattr(getattr(c, "generator", None), "relationship", None) == rel.name
                    ]
                    if len(fk_cols) != len(rel.right.columns):
                        _add_issue(
                            issues,
                            ErrorCode.FOREIGN_KEY_SIDE,
                            f"{base}.right",
                            f"composite FK must have all components for relationship '{rel.name}'",
                            related=rel.name,
                        )
            elif rel.cardinality == "many_to_one":
                for cname in rel.left.columns:
                    key = (rel.left.table, cname)
                    if key in fk_owner and fk_owner[key] != rel.name:
                        _add_issue(
                            issues,
                            ErrorCode.FOREIGN_KEY_SIDE,
                            f"{base}.left",
                            f"column '{cname}' already owned by relationship '{fk_owner[key]}'",
                            related=cname,
                        )
                    else:
                        fk_owner[key] = rel.name
                if not _has_fk(rel.left.table, rel.left.columns, "right"):
                    _add_issue(
                        issues,
                        ErrorCode.FOREIGN_KEY_SIDE,
                        f"{base}.left",
                        "dependent columns must have foreign_key generator targeting right",
                        related=rel.name,
                    )
                if len(rel.left.columns) > 1:
                    fk_cols = [
                        c
                        for c in raw_col_map.get(rel.left.table, {}).values()
                        if getattr(getattr(c, "generator", None), "relationship", None) == rel.name
                    ]
                    if len(fk_cols) != len(rel.left.columns):
                        _add_issue(
                            issues,
                            ErrorCode.FOREIGN_KEY_SIDE,
                            f"{base}.left",
                            "composite FK must have all components",
                            related=rel.name,
                        )
            elif rel.cardinality == "one_to_one":
                has_left = _has_fk(rel.left.table, rel.left.columns, "right")
                has_right = _has_fk(rel.right.table, rel.right.columns, "left")
                if not (has_left ^ has_right):
                    _add_issue(
                        issues,
                        ErrorCode.FOREIGN_KEY_SIDE,
                        f"{base}",
                        "exactly one side must be foreign_key for one_to_one",
                        related=rel.name,
                    )
                # Track ownership for FK side
                fk_side_table = (
                    rel.left.table if has_left else rel.right.table if has_right else None
                )
                fk_side_cols = (
                    rel.left.columns if has_left else rel.right.columns if has_right else ()
                )
                if fk_side_table:
                    for cname in fk_side_cols:
                        key = (fk_side_table, cname)
                        if key in fk_owner and fk_owner[key] != rel.name:
                            _add_issue(
                                issues,
                                ErrorCode.FOREIGN_KEY_SIDE,
                                f"{base}",
                                f"column '{cname}' already owned by '{fk_owner[key]}'",
                                related=cname,
                            )
                        else:
                            fk_owner[key] = rel.name
        else:  # many_to_many
            bridge_table = getattr(rel, "bridge", None)
            if bridge_table is not None:
                btbl = bridge_table.table
                if btbl not in raw_by_name:
                    _add_issue(
                        issues,
                        ErrorCode.BRIDGE_TABLE,
                        f"{base}.bridge.table",
                        f"bridge table '{btbl}' does not exist",
                        related=btbl,
                    )
                elif btbl in (rel.left.table, rel.right.table):
                    _add_issue(
                        issues,
                        ErrorCode.BRIDGE_TABLE,
                        f"{base}.bridge.table",
                        "bridge table must be distinct from endpoints",
                        related=btbl,
                    )
                else:
                    # Check bridge column types match endpoint types
                    for lc, lcol in zip(bridge_table.left_columns, rel.left.columns):
                        bcol = raw_col_map.get(btbl, {}).get(lc)
                        ltype = raw_col_type.get(rel.left.table, {}).get(lcol)
                        btype = raw_col_type.get(btbl, {}).get(lc)
                        if ltype is not None and btype is not None and ltype != btype:
                            _add_issue(
                                issues,
                                ErrorCode.RELATIONSHIP_TYPE,
                                f"{base}.bridge.left_columns",
                                f"bridge column '{lc}' type {btype.value if btype else 'unknown'} != left endpoint type {ltype.value}",
                                related=lc,
                            )
                        # Check generator
                        if bcol is not None:
                            if (
                                getattr(bcol.generator, "kind", None) != "foreign_key"
                                or getattr(bcol.generator, "relationship", None) != rel.name
                                or getattr(bcol.generator, "target_side", None) != "left"
                            ):
                                _add_issue(
                                    issues,
                                    ErrorCode.FOREIGN_KEY_SIDE,
                                    f"{base}.bridge.left_columns",
                                    f"bridge left column '{lc}' must be foreign_key targeting left",
                                    related=lc,
                                )
                    for rc, rcol in zip(bridge_table.right_columns, rel.right.columns):
                        bcol = raw_col_map.get(btbl, {}).get(rc)
                        rtype = raw_col_type.get(rel.right.table, {}).get(rcol)
                        btype = raw_col_type.get(btbl, {}).get(rc)
                        if rtype is not None and btype is not None and rtype != btype:
                            _add_issue(
                                issues,
                                ErrorCode.RELATIONSHIP_TYPE,
                                f"{base}.bridge.right_columns",
                                f"bridge column '{rc}' type mismatch",
                                related=rc,
                            )
                        if bcol is not None:
                            if (
                                getattr(bcol.generator, "kind", None) != "foreign_key"
                                or getattr(bcol.generator, "relationship", None) != rel.name
                                or getattr(bcol.generator, "target_side", None) != "right"
                            ):
                                _add_issue(
                                    issues,
                                    ErrorCode.FOREIGN_KEY_SIDE,
                                    f"{base}.bridge.right_columns",
                                    f"bridge right column '{rc}' must be foreign_key targeting right",
                                    related=rc,
                                )
                    # Track ownership for bridge columns
                    for cname in bridge_table.left_columns + bridge_table.right_columns:
                        key = (btbl, cname)
                        if key in fk_owner and fk_owner[key] != rel.name:
                            _add_issue(
                                issues,
                                ErrorCode.FOREIGN_KEY_SIDE,
                                f"{base}.bridge",
                                f"bridge column '{cname}' already owned by '{fk_owner[key]}'",
                                related=cname,
                            )
                        else:
                            fk_owner[key] = rel.name
                if len(bridge_table.left_columns) != len(rel.left.columns):
                    _add_issue(
                        issues,
                        ErrorCode.RELATIONSHIP_ARITY,
                        f"{base}.bridge.left_columns",
                        "bridge left arity must match left endpoint",
                        related=rel.name,
                    )
                if len(bridge_table.right_columns) != len(rel.right.columns):
                    _add_issue(
                        issues,
                        ErrorCode.RELATIONSHIP_ARITY,
                        f"{base}.bridge.right_columns",
                        "bridge right arity must match right endpoint",
                        related=rel.name,
                    )

    # §17.5 Staging
    raw_names = {t.name for t in scenario.raw_tables}
    for tbl in scenario.raw_tables:
        cnt = sum(1 for s in scenario.staging_models if s.source == tbl.name)
        if cnt != 1:
            _add_issue(
                issues,
                ErrorCode.STAGING_1TO1,
                "staging_models",
                f"raw table '{tbl.name}' must have exactly one staging model, found {cnt}",
                related=tbl.name,
            )
    for s in scenario.staging_models:
        if s.source not in raw_names:
            _add_issue(
                issues,
                ErrorCode.MISSING_REF,
                f"staging_models[{s.name}].source",
                f"staging source '{s.source}' does not exist",
                related=s.source,
            )
            continue
        raw_cols = raw_col_map.get(s.source, {})
        raw_type_map = raw_col_type.get(s.source, {})
        # Build current type tracking for operation chain
        for col in s.columns:
            if col.source not in raw_cols:
                _add_issue(
                    issues,
                    ErrorCode.STAGING_SOURCE_COLUMN,
                    f"staging_models[{s.name}].columns",
                    f"source column '{col.source}' does not exist in raw table '{s.source}'",
                    related=col.source,
                )
                continue
            cur_type = raw_type_map.get(col.source)
            for op_idx, op in enumerate(col.operations):
                op_kind = getattr(op, "op", None)
                # Check trim/lower/upper/map_values only on string
                if op_kind in ("trim", "lower", "upper", "replace", "map_values"):
                    if cur_type != DataType.string:
                        _add_issue(
                            issues,
                            ErrorCode.STAGING_OPERATION_CHAIN,
                            f"staging_models[{s.name}].columns[{col.source}].operations[{op_idx}]",
                            f"operation '{op_kind}' only valid on string, got '{cur_type.value if cur_type else 'unknown'}'",
                            related=col.source,
                        )
                    # replace/map_values keep string
                    if op_kind == "replace":
                        cur_type = DataType.string
                    elif op_kind == "map_values":
                        cur_type = DataType.string
                elif op_kind == "cast":
                    target_type = getattr(op, "type", None)
                    if isinstance(target_type, str):
                        try:
                            target_type = DataType(target_type)
                        except Exception:
                            pass
                    # Check format
                    fmt = getattr(op, "format", None)
                    if (
                        target_type in (DataType.date, DataType.timestamp)
                        and cur_type != DataType.string
                    ):
                        _add_issue(
                            issues,
                            ErrorCode.STAGING_OPERATION_CHAIN,
                            f"staging_models[{s.name}].columns[{col.source}].operations[{op_idx}]",
                            f"cast to {target_type.value} requires string source, got '{cur_type.value if cur_type else 'unknown'}'",
                            related=col.source,
                        )
                    if target_type not in (DataType.date, DataType.timestamp) and fmt is not None:
                        _add_issue(
                            issues,
                            ErrorCode.STAGING_OPERATION_CHAIN,
                            f"staging_models[{s.name}].columns[{col.source}].operations[{op_idx}]",
                            "format only allowed for date/timestamp cast",
                            related=col.source,
                        )
                    cur_type = target_type if isinstance(target_type, DataType) else cur_type
                elif op_kind == "null_if":
                    vals = getattr(op, "values", ())
                    for v in vals:
                        # check type matches cur_type
                        actual = (
                            "string"
                            if isinstance(v, str)
                            else "integer"
                            if isinstance(v, int) and not isinstance(v, bool)
                            else "float"
                            if isinstance(v, float)
                            else "boolean"
                            if isinstance(v, bool)
                            else "unknown"
                        )
                        if cur_type and actual != cur_type.value:
                            # allow string values for any? But strict check
                            if actual != "unknown":
                                _add_issue(
                                    issues,
                                    ErrorCode.STAGING_OPERATION_CHAIN,
                                    f"staging_models[{s.name}].columns[{col.source}].operations[{op_idx}]",
                                    f"null_if value '{v}' type {actual} != column type {cur_type.value}",
                                    related=str(v),
                                )
                elif op_kind == "coalesce":
                    v = getattr(op, "value", None)
                    actual = (
                        "string"
                        if isinstance(v, str)
                        else "integer"
                        if isinstance(v, int) and not isinstance(v, bool)
                        else "float"
                        if isinstance(v, float)
                        else "boolean"
                        if isinstance(v, bool)
                        else "unknown"
                    )
                    if cur_type and actual != "unknown" and actual != cur_type.value:
                        _add_issue(
                            issues,
                            ErrorCode.STAGING_OPERATION_CHAIN,
                            f"staging_models[{s.name}].columns[{col.source}].operations[{op_idx}]",
                            f"coalesce value type {actual} != column type {cur_type.value}",
                            related=str(v),
                        )
                    # coalesce keeps same type (or maybe string)
        # grain columns exist after transformations
        target_names = {c.target for c in s.columns}
        for g in s.grain:
            if g not in target_names:
                _add_issue(
                    issues,
                    ErrorCode.STAGING_GRAIN,
                    f"staging_models[{s.name}].grain",
                    f"grain column '{g}' does not exist after transformations",
                    related=g,
                )
        # Build actual schema after column operations for row_operations checks
        actual_schema: dict[str, DataType] = {}
        for col in s.columns:
            cur_type = raw_type_map.get(col.source)
            for op in col.operations:
                op_kind = getattr(op, "op", None)
                if op_kind == "cast":
                    target_type = getattr(op, "type", None)
                    if isinstance(target_type, str):
                        try:
                            target_type = DataType(target_type)
                        except Exception:
                            pass
                    if isinstance(target_type, DataType):
                        cur_type = target_type
                elif op_kind in ("map_values", "replace"):
                    cur_type = DataType.string
                # trim/lower/upper keep string, null_if/coalesce keep same type
            if cur_type is not None:
                actual_schema[col.target] = cur_type
        # row_operations
        for op_idx, op in enumerate(s.row_operations):
            if getattr(op, "op", None) == "filter":
                cond = getattr(op, "condition", None)
                _check_condition(
                    cond,
                    actual_schema,
                    issues,
                    f"staging_models[{s.name}].row_operations[{op_idx}]",
                )
            elif getattr(op, "op", None) == "deduplicate":
                for k in getattr(op, "keys", []):
                    if k not in target_names:
                        _add_issue(
                            issues,
                            ErrorCode.MISSING_REF,
                            f"staging_models[{s.name}].row_operations[{op_idx}].keys",
                            f"key '{k}' not in staging output",
                            related=k,
                        )
                for sk in getattr(op, "order_by", []):
                    col = getattr(sk, "column", None)
                    if col not in target_names:
                        _add_issue(
                            issues,
                            ErrorCode.MISSING_REF,
                            f"staging_models[{s.name}].row_operations[{op_idx}].order_by",
                            f"order_by '{col}' not in staging output",
                            related=str(col),
                        )
                # Deterministic tie-breaking: order_by should not be subset of keys alone
                order_cols = [getattr(sk, "column", None) for sk in getattr(op, "order_by", [])]
                if set(order_cols) <= set(getattr(op, "keys", [])):
                    _add_issue(
                        issues,
                        ErrorCode.UNKNOWN,
                        f"staging_models[{s.name}].row_operations[{op_idx}].order_by",
                        "deduplication order_by must provide deterministic tie-breaking beyond keys",
                        related=",".join(order_cols),
                    )

    # §17.6 DAG & Intermediate
    all_model_names = set(staging_by_name.keys()) | set(intermediate_by_name.keys())
    for idx, m in enumerate(scenario.intermediate_models):
        base = f"intermediate_models[{idx}]"
        deps: list[str] = []
        if hasattr(m, "source"):
            deps.append(m.source)
        if hasattr(m, "left"):
            deps.extend([m.left, m.right])
        for dep in deps:
            if dep == m.name:
                _add_issue(
                    issues,
                    ErrorCode.DAG_CYCLE,
                    f"{base}.name",
                    f"self-dependency '{dep}'",
                    related=dep,
                )
            elif dep not in all_model_names:
                if dep in raw_by_name:
                    _add_issue(
                        issues,
                        ErrorCode.LAYER_DEPENDENCY,
                        f"{base}",
                        f"intermediate '{m.name}' must not reference raw table '{dep}'",
                        related=dep,
                    )
                elif dep in output_by_name:
                    _add_issue(
                        issues,
                        ErrorCode.LAYER_DEPENDENCY,
                        f"{base}",
                        f"intermediate '{m.name}' must not reference output model '{dep}'",
                        related=dep,
                    )
                else:
                    _add_issue(
                        issues,
                        ErrorCode.MISSING_REF,
                        f"{base}",
                        f"dependency '{dep}' does not exist",
                        related=dep,
                    )

    # Topological sort
    graph: dict[str, set[str]] = {}
    in_degree: dict[str, int] = {}
    for m in scenario.intermediate_models:
        graph[m.name] = set()
        in_degree[m.name] = 0
    for m in scenario.intermediate_models:
        deps: list[str] = []
        if hasattr(m, "source"):
            deps.append(m.source)
        if hasattr(m, "left"):
            deps.extend([m.left, m.right])
        for dep in deps:
            if dep in graph:
                graph[dep].add(m.name)
                in_degree[m.name] += 1
    queue: deque[str] = deque()
    idx_map = {m.name: i for i, m in enumerate(scenario.intermediate_models)}
    zero = [n for n, d in in_degree.items() if d == 0]
    zero.sort(key=lambda n: idx_map.get(n, 0))
    queue.extend(zero)
    topo: list[str] = []
    while queue:
        n = queue.popleft()
        topo.append(n)
        for succ in sorted(graph.get(n, []), key=lambda x: idx_map.get(x, 0)):
            in_degree[succ] -= 1
            if in_degree[succ] == 0:
                queue.append(succ)
        queue = deque(sorted(queue, key=lambda x: idx_map.get(x, 0)))
    if len(topo) != len(graph):
        remaining = [n for n, d in in_degree.items() if d > 0]
        for n in remaining:
            _add_issue(
                issues,
                ErrorCode.DAG_CYCLE,
                "intermediate_models",
                f"cycle detected involving '{n}'",
                related=n,
            )

    # Build schemas with proper lineage
    staging_schema: dict[str, dict[str, DataType]] = {}
    staging_lineage: dict[str, dict[str, list[str]]] = {}
    for s in scenario.staging_models:
        schema: dict[str, DataType] = {}
        lineage_map: dict[str, list[str]] = {}
        raw_types = raw_col_type.get(s.source, {})
        for col in s.columns:
            rt = raw_types.get(col.source)
            cur_type = rt
            lineage_entry = [f"{s.source}.{col.source}"]
            cast_type = None
            for op in col.operations:
                if getattr(op, "op", None) == "cast":
                    cast_type = getattr(op, "type", None)
                    if isinstance(cast_type, str):
                        try:
                            cast_type = DataType(cast_type)
                        except Exception:
                            cast_type = rt
                    cur_type = cast_type if isinstance(cast_type, DataType) else rt
            schema[col.target] = cur_type if cur_type else DataType.string
            lineage_map[col.target] = lineage_entry
        staging_schema[s.name] = schema
        staging_lineage[s.name] = lineage_map

    intermediate_schema: dict[str, dict[str, DataType]] = {}
    intermediate_lineage: dict[str, dict[str, list[str]]] = {}

    for name in topo:
        m = intermediate_by_name.get(name)
        if m is None:
            continue

        def _get_schema(dep: str) -> dict[str, DataType] | None:
            if dep in staging_schema:
                return staging_schema[dep]
            if dep in intermediate_schema:
                return intermediate_schema[dep]
            return None

        def _get_lineage(dep: str) -> dict[str, list[str]] | None:
            if dep in staging_lineage:
                return staging_lineage[dep]
            if dep in intermediate_lineage:
                return intermediate_lineage[dep]
            return None

        if m.operation == "transform":
            src_schema = _get_schema(m.source)
            src_lineage = _get_lineage(m.source)
            if src_schema is None or src_lineage is None:
                continue
            out_schema: dict[str, DataType] = {}
            out_lineage: dict[str, list[str]] = {}
            for pc in m.columns:
                if pc.source not in src_schema:
                    _add_issue(
                        issues,
                        ErrorCode.MISSING_REF,
                        f"intermediate_models[{name}].columns",
                        f"projection source '{pc.source}' not in '{m.source}'",
                        related=pc.source,
                    )
                else:
                    out_schema[pc.target] = src_schema[pc.source]
                    out_lineage[pc.target] = src_lineage.get(pc.source, [f"{m.source}.{pc.source}"])
            for dc in m.derived_columns:
                # Per §12.3: derived columns evaluated only against projected target names
                projected_schema = dict(out_schema)  # snapshot of projected only
                inferred = _infer_expression_type(dc.expression, projected_schema)
                declared = (
                    dc.type
                    if isinstance(dc.type, DataType)
                    else DataType(dc.type)
                    if isinstance(dc.type, str)
                    else None
                )
                if inferred is None and not _is_column_unresolved(dc.expression, projected_schema):
                    _add_issue(
                        issues,
                        ErrorCode.INVALID_EXPRESSION_TYPE,
                        f"intermediate_models[{name}].derived_columns[{dc.name}]",
                        "invalid expression: statically incompatible types",
                        related=dc.name,
                    )
                elif inferred is not None and declared is not None and inferred != declared:
                    _add_issue(
                        issues,
                        ErrorCode.INVALID_EXPRESSION_TYPE,
                        f"intermediate_models[{name}].derived_columns[{dc.name}]",
                        f"declared type '{declared.value}' != inferred '{inferred.value}'",
                        related=dc.name,
                    )
                _check_expression_columns(
                    dc.expression,
                    projected_schema,
                    issues,
                    f"intermediate_models[{name}].derived_columns[{dc.name}]",
                )
                if dc.name in out_schema:
                    _add_issue(
                        issues,
                        ErrorCode.UNKNOWN,
                        f"intermediate_models[{name}].derived_columns",
                        f"derived column '{dc.name}' collides with projected",
                        related=dc.name,
                    )
                out_schema[dc.name] = declared if declared else DataType.string
                # Proper raw lineage: combine raw lineage of all columns in expression
                expr_cols = _collect_expression_columns(dc.expression)
                raw_lin: list[str] = []
                for ec in expr_cols:
                    if ec in src_lineage:
                        raw_lin.extend(src_lineage[ec])
                    elif ec in out_lineage:
                        raw_lin.extend(out_lineage[ec])
                    else:
                        raw_lin.append(f"{m.source}.{ec}")
                out_lineage[dc.name] = sorted(set(raw_lin)) if raw_lin else [f"{m.name}.{dc.name}"]
                # Also check condition filters
            for f in getattr(m, "filters", []):
                _check_condition(f, out_schema, issues, f"intermediate_models[{name}].filters")
            for g in m.grain:
                if g not in out_schema:
                    _add_issue(
                        issues,
                        ErrorCode.GRAIN_IMPOSSIBLE,
                        f"intermediate_models[{name}].grain",
                        f"grain '{g}' not in output schema",
                        related=g,
                    )
            # Grain vs cardinality – for transform, grain should be from input grain lineage
            # Simplified check: grain should be subset of output and should be unique – already checked
            intermediate_schema[name] = out_schema
            intermediate_lineage[name] = out_lineage

        elif m.operation == "join":
            left_schema = _get_schema(m.left)
            right_schema = _get_schema(m.right)
            left_lineage = _get_lineage(m.left)
            right_lineage = _get_lineage(m.right)
            if (
                left_schema is None
                or right_schema is None
                or left_lineage is None
                or right_lineage is None
            ):
                continue
            for pair in m.join.on:
                if pair.left not in left_schema:
                    _add_issue(
                        issues,
                        ErrorCode.JOIN_KEY_MISMATCH,
                        f"intermediate_models[{name}].join.on",
                        f"left key '{pair.left}' not in '{m.left}'",
                        related=pair.left,
                    )
                if pair.right not in right_schema:
                    _add_issue(
                        issues,
                        ErrorCode.JOIN_KEY_MISMATCH,
                        f"intermediate_models[{name}].join.on",
                        f"right key '{pair.right}' not in '{m.right}'",
                        related=pair.right,
                    )
                lt = left_schema.get(pair.left)
                rt = right_schema.get(pair.right)
                if lt is not None and rt is not None and lt != rt:
                    _add_issue(
                        issues,
                        ErrorCode.JOIN_KEY_MISMATCH,
                        f"intermediate_models[{name}].join.on",
                        f"join key type mismatch '{lt.value}' vs '{rt.value}'",
                        related=pair.left,
                    )
                # Check lineage: join keys must be supported by raw relationship
                # Get raw lineage for each side
                left_raw_lin = left_lineage.get(pair.left, [])
                right_raw_lin = right_lineage.get(pair.right, [])

                # Each lineage is like ["raw_a.id"] – extract table and column
                def _parse_raw(lin: str) -> tuple[str, str] | None:
                    if "." in lin:
                        t, c = lin.split(".", 1)
                        return (t, c)
                    return None

                left_raw = _parse_raw(left_raw_lin[0]) if left_raw_lin else None
                right_raw = _parse_raw(right_raw_lin[0]) if right_raw_lin else None
                if left_raw and right_raw:
                    found = False
                    for rel in scenario.relationships:
                        # Check if relationship connects these raw tables/columns (in either direction)
                        if (
                            rel.left.table == left_raw[0]
                            and rel.right.table == right_raw[0]
                            and left_raw[1] in rel.left.columns
                            and right_raw[1] in rel.right.columns
                        ) or (
                            rel.left.table == right_raw[0]
                            and rel.right.table == left_raw[0]
                            and right_raw[1] in rel.left.columns
                            and left_raw[1] in rel.right.columns
                        ):
                            # Also check arity matches – for direct relationships, single column check is enough
                            found = True
                            break
                        # For composite keys, check if pair is part of relationship
                        # Simplified: check if left_raw and right_raw are in any relationship together
                        if (rel.left.table, rel.right.table) == (left_raw[0], right_raw[0]) or (
                            rel.left.table,
                            rel.right.table,
                        ) == (right_raw[0], left_raw[0]):
                            # Check if columns are part of relationship
                            if (
                                left_raw[1] in rel.left.columns
                                and right_raw[1] in rel.right.columns
                            ):
                                found = True
                            elif (
                                left_raw[1] in rel.right.columns
                                and right_raw[1] in rel.left.columns
                            ):
                                found = True
                    if not found:
                        _add_issue(
                            issues,
                            ErrorCode.JOIN_KEY_MISMATCH,
                            f"intermediate_models[{name}].join.on",
                            f"join keys '{pair.left}'/'{pair.right}' not supported by traceable raw relationship lineage",
                            related=pair.left,
                        )
                # Grain vs cardinality: validate grain against join cardinality
                # For inner join, grain should be combination or from one side; for left join, grain must be from left
                # Simplified: for left join, grain must be subset of left side's grain or output; for inner, grain must be unique
                # We check that grain is subset of output (already) and that for left join, grain columns are from left
                if m.join.type == "left":
                    for g in m.grain:
                        # Find which side g comes from
                        g_side = None
                        for jc in m.columns:
                            if jc.target == g:
                                g_side = jc.side
                                break
                        if g_side is None:
                            for dc in m.derived_columns:
                                if dc.name == g:
                                    # For derived, check its expression lineage
                                    expr_cols = _collect_expression_columns(dc.expression)
                                    # If any expression column is from right, then derived is from right
                                    for ec in expr_cols:
                                        if ec in right_schema:
                                            g_side = "right"
                                        elif ec in left_schema:
                                            if g_side is None:
                                                g_side = "left"
                                            elif g_side == "right":
                                                g_side = "both"
                        # For left join, grain must be from left (preserves left grain)
                        if g_side == "right":
                            _add_issue(
                                issues,
                                ErrorCode.GRAIN_IMPOSSIBLE,
                                f"intermediate_models[{name}].grain",
                                f"grain '{g}' for left join must be from left side, got right",
                                related=g,
                            )
                        elif g_side == "both":
                            _add_issue(
                                issues,
                                ErrorCode.GRAIN_IMPOSSIBLE,
                                f"intermediate_models[{name}].grain",
                                f"grain '{g}' for left join must be from left side only",
                                related=g,
                            )
            out_schema = {}
            out_lineage = {}
            for jc in m.columns:
                src_schema = left_schema if jc.side == "left" else right_schema
                src_lineage = left_lineage if jc.side == "left" else right_lineage
                if jc.source not in src_schema:
                    _add_issue(
                        issues,
                        ErrorCode.MISSING_REF,
                        f"intermediate_models[{name}].columns",
                        f"projection source '{jc.source}' not in {jc.side} '{getattr(m, jc.side)}'",
                        related=jc.source,
                    )
                else:
                    if jc.target in out_schema:
                        _add_issue(
                            issues,
                            ErrorCode.UNKNOWN,
                            f"intermediate_models[{name}].columns",
                            f"duplicate output name '{jc.target}'",
                            related=jc.target,
                        )
                    out_schema[jc.target] = src_schema[jc.source]
                    out_lineage[jc.target] = src_lineage.get(
                        jc.source, [f"{getattr(m, jc.side)}.{jc.source}"]
                    )
            for dc in m.derived_columns:
                inferred = _infer_expression_type(dc.expression, out_schema)
                declared = (
                    dc.type
                    if isinstance(dc.type, DataType)
                    else DataType(dc.type)
                    if isinstance(dc.type, str)
                    else None
                )
                if inferred is None and not _is_column_unresolved(dc.expression, out_schema):
                    _add_issue(
                        issues,
                        ErrorCode.INVALID_EXPRESSION_TYPE,
                        f"intermediate_models[{name}].derived_columns[{dc.name}]",
                        "invalid expression: statically incompatible types",
                        related=dc.name,
                    )
                elif inferred is not None and declared is not None and inferred != declared:
                    _add_issue(
                        issues,
                        ErrorCode.INVALID_EXPRESSION_TYPE,
                        f"intermediate_models[{name}].derived_columns[{dc.name}]",
                        f"declared type '{declared.value}' != inferred '{inferred.value}'",
                        related=dc.name,
                    )
                _check_expression_columns(
                    dc.expression,
                    out_schema,
                    issues,
                    f"intermediate_models[{name}].derived_columns[{dc.name}]",
                )
                if dc.name in out_schema:
                    _add_issue(
                        issues,
                        ErrorCode.UNKNOWN,
                        f"intermediate_models[{name}].derived_columns",
                        f"collision '{dc.name}'",
                        related=dc.name,
                    )
                out_schema[dc.name] = declared if declared else DataType.string
                # Proper raw lineage for derived
                expr_cols = _collect_expression_columns(dc.expression)
                raw_lin: list[str] = []
                for ec in expr_cols:
                    # Expression is evaluated against out_schema (projected)
                    # Find which side it comes from
                    if ec in left_schema:
                        raw_lin.extend(left_lineage.get(ec, [f"{m.left}.{ec}"]))
                    elif ec in right_schema:
                        raw_lin.extend(right_lineage.get(ec, [f"{m.right}.{ec}"]))
                    elif ec in out_schema:
                        raw_lin.extend(out_lineage.get(ec, [f"{m.name}.{ec}"]))
                    else:
                        raw_lin.append(f"{m.name}.{ec}")
                out_lineage[dc.name] = sorted(set(raw_lin)) if raw_lin else [f"{m.name}.{dc.name}"]
            for f in getattr(m, "filters", []):
                _check_condition(f, out_schema, issues, f"intermediate_models[{name}].filters")
            for g in m.grain:
                if g not in out_schema:
                    _add_issue(
                        issues,
                        ErrorCode.GRAIN_IMPOSSIBLE,
                        f"intermediate_models[{name}].grain",
                        f"grain '{g}' not in output",
                        related=g,
                    )
            intermediate_schema[name] = out_schema
            intermediate_lineage[name] = out_lineage

        elif m.operation == "aggregate":
            src_schema = _get_schema(m.source)
            src_lineage = _get_lineage(m.source)
            if src_schema is None or src_lineage is None:
                continue
            for pc in m.group_by:
                if pc.source not in src_schema:
                    _add_issue(
                        issues,
                        ErrorCode.MISSING_REF,
                        f"intermediate_models[{name}].group_by",
                        f"source '{pc.source}' not in '{m.source}'",
                        related=pc.source,
                    )
            for f in getattr(m, "filters", []):
                _check_condition(f, src_schema, issues, f"intermediate_models[{name}].filters")
            for met in m.metrics:
                if hasattr(met, "column"):
                    col_name = getattr(met, "column", None)
                    if col_name is not None and col_name not in src_schema:
                        _add_issue(
                            issues,
                            ErrorCode.METRIC_TYPE,
                            f"intermediate_models[{name}].metrics",
                            f"metric column '{col_name}' not in source",
                            related=col_name,
                        )
                    elif col_name is not None:
                        ctype = src_schema.get(col_name)
                        if met.function in ("sum", "avg", "conditional_sum") and ctype not in (
                            DataType.integer,
                            DataType.float,
                        ):
                            _add_issue(
                                issues,
                                ErrorCode.METRIC_TYPE,
                                f"intermediate_models[{name}].metrics",
                                f"metric '{met.function}' requires numeric column, got '{ctype.value if ctype else 'unknown'}'",
                                related=met.name,
                            )
                        if met.function in ("min", "max") and ctype not in (
                            DataType.integer,
                            DataType.float,
                            DataType.date,
                            DataType.timestamp,
                        ):
                            _add_issue(
                                issues,
                                ErrorCode.METRIC_TYPE,
                                f"intermediate_models[{name}].metrics",
                                f"metric '{met.function}' requires numeric/date/timestamp",
                                related=met.name,
                            )
                if hasattr(met, "condition"):
                    cond = getattr(met, "condition", None)
                    _check_condition(
                        cond, src_schema, issues, f"intermediate_models[{name}].metrics[{met.name}]"
                    )
            out_schema = {}
            out_lineage = {}
            for pc in m.group_by:
                src_type = src_schema.get(pc.source)
                if src_type is not None:
                    out_schema[pc.target] = src_type
                    out_lineage[pc.target] = src_lineage.get(pc.source, [f"{m.source}.{pc.source}"])
            for met in m.metrics:
                if met.function in ("count_rows", "count", "count_distinct", "conditional_count"):
                    out_schema[met.name] = DataType.integer
                elif met.function in ("sum", "avg", "conditional_sum"):
                    out_schema[met.name] = DataType.float
                elif met.function in ("min", "max"):
                    col_name = getattr(met, "column", None)
                    ctype = src_schema.get(col_name) if col_name else DataType.string
                    out_schema[met.name] = ctype if ctype else DataType.string
                else:
                    out_schema[met.name] = DataType.string
                # Proper raw lineage for metric: column + condition columns
                raw_lin: list[str] = []
                if hasattr(met, "column"):
                    col = getattr(met, "column", None)
                    if col and col in src_lineage:
                        raw_lin.extend(src_lineage[col])
                if hasattr(met, "condition"):
                    cond = getattr(met, "condition", None)
                    for cc in _collect_condition_columns(cond):
                        if cc in src_lineage:
                            raw_lin.extend(src_lineage[cc])
                        elif cc in src_schema:
                            raw_lin.append(f"{m.source}.{cc}")
                out_lineage[met.name] = (
                    sorted(set(raw_lin)) if raw_lin else [f"{m.name}.{met.name}"]
                )
            group_targets = {c.target for c in m.group_by}
            for g in m.grain:
                if g not in group_targets:
                    _add_issue(
                        issues,
                        ErrorCode.GRAIN_IMPOSSIBLE,
                        f"intermediate_models[{name}].grain",
                        f"grain '{g}' must be subset of group_by targets",
                        related=g,
                    )
            intermediate_schema[name] = out_schema
            intermediate_lineage[name] = out_lineage

        elif m.operation == "deduplicate":
            src_schema = _get_schema(m.source)
            src_lineage = _get_lineage(m.source)
            if src_schema is None or src_lineage is None:
                continue
            for k in m.keys:
                if k not in src_schema:
                    _add_issue(
                        issues,
                        ErrorCode.MISSING_REF,
                        f"intermediate_models[{name}].keys",
                        f"key '{k}' not in source",
                        related=k,
                    )
            for sk in m.order_by:
                if sk.column not in src_schema:
                    _add_issue(
                        issues,
                        ErrorCode.MISSING_REF,
                        f"intermediate_models[{name}].order_by",
                        f"order_by '{sk.column}' not in source",
                        related=sk.column,
                    )
            # Deterministic tie-breaking: order_by should extend beyond keys
            order_cols = [sk.column for sk in m.order_by]
            if set(order_cols) <= set(m.keys):
                _add_issue(
                    issues,
                    ErrorCode.UNKNOWN,
                    f"intermediate_models[{name}].order_by",
                    "deduplication order_by must provide deterministic tie-breaking beyond keys",
                    related=",".join(order_cols),
                )
            for g in m.grain:
                if g not in src_schema:
                    _add_issue(
                        issues,
                        ErrorCode.GRAIN_IMPOSSIBLE,
                        f"intermediate_models[{name}].grain",
                        f"grain '{g}' not in source",
                        related=g,
                    )
            intermediate_schema[name] = dict(src_schema)
            intermediate_lineage[name] = (
                dict(src_lineage) if src_lineage else {k: [f"{m.source}.{k}"] for k in src_schema}
            )

    # §17.7 Output & Assertions
    output_schemas: dict[str, dict[str, DataType]] = {}
    output_lineage: dict[str, dict[str, list[str]]] = {}
    for idx, out in enumerate(scenario.output_models):
        base = f"output_models[{idx}]"
        if out.source not in intermediate_by_name:
            _add_issue(
                issues,
                ErrorCode.MISSING_REF,
                f"{base}.source",
                f"output source '{out.source}' is not an intermediate model",
                related=out.source,
            )
            continue
        src_schema = intermediate_schema.get(out.source)
        src_lineage = intermediate_lineage.get(out.source)
        if src_schema is None or src_lineage is None:
            continue
        for pc in out.group_by:
            if pc.source not in src_schema:
                _add_issue(
                    issues,
                    ErrorCode.MISSING_REF,
                    f"{base}.group_by",
                    f"source '{pc.source}' not in '{out.source}'",
                    related=pc.source,
                )
        group_targets = {c.target for c in out.group_by}
        for g in out.grain:
            if g not in group_targets:
                _add_issue(
                    issues,
                    ErrorCode.GRAIN_IMPOSSIBLE,
                    f"{base}.grain",
                    f"grain '{g}' must reference group_by target",
                    related=g,
                )
        for d in out.dimensions:
            if d not in group_targets:
                _add_issue(
                    issues,
                    ErrorCode.GRAIN_IMPOSSIBLE,
                    f"{base}.dimensions",
                    f"dimension '{d}' must reference group_by target",
                    related=d,
                )
        for met in out.metrics:
            if hasattr(met, "column"):
                col_name = getattr(met, "column", None)
                if col_name is not None and col_name not in src_schema:
                    _add_issue(
                        issues,
                        ErrorCode.METRIC_TYPE,
                        f"{base}.metrics",
                        f"metric column '{col_name}' not in source",
                        related=col_name,
                    )
                elif col_name is not None:
                    ctype = src_schema.get(col_name)
                    if met.function in ("sum", "avg", "conditional_sum") and ctype not in (
                        DataType.integer,
                        DataType.float,
                    ):
                        _add_issue(
                            issues,
                            ErrorCode.METRIC_TYPE,
                            f"{base}.metrics",
                            f"metric '{met.function}' requires numeric",
                            related=met.name,
                        )
            if hasattr(met, "condition"):
                cond = getattr(met, "condition", None)
                _check_condition(cond, src_schema, issues, f"{base}.metrics[{met.name}]")
        for f in getattr(out, "filters", []):
            _check_condition(f, src_schema, issues, f"{base}.filters")
        # Build output schema
        out_schema: dict[str, DataType] = {}
        out_line: dict[str, list[str]] = {}
        for pc in out.group_by:
            src_type = src_schema.get(pc.source)
            if src_type is not None:
                out_schema[pc.target] = src_type
                out_line[pc.target] = src_lineage.get(pc.source, [f"{out.source}.{pc.source}"])
        for met in out.metrics:
            if met.function in ("count_rows", "count", "count_distinct", "conditional_count"):
                out_schema[met.name] = DataType.integer
            elif met.function in ("sum", "avg", "conditional_sum"):
                out_schema[met.name] = DataType.float
            elif met.function in ("min", "max"):
                col_name = getattr(met, "column", None)
                ctype = src_schema.get(col_name) if col_name else DataType.string
                out_schema[met.name] = ctype if ctype else DataType.string
            else:
                out_schema[met.name] = DataType.string
            # Proper raw lineage for output metrics
            raw_lin: list[str] = []
            if hasattr(met, "column"):
                col = getattr(met, "column", None)
                if col and col in src_lineage:
                    raw_lin.extend(src_lineage[col])
            if hasattr(met, "condition"):
                cond = getattr(met, "condition", None)
                for cc in _collect_condition_columns(cond):
                    if cc in src_lineage:
                        raw_lin.extend(src_lineage[cc])
                    elif cc in src_schema:
                        raw_lin.append(f"{out.source}.{cc}")
            if raw_lin:
                out_line[met.name] = sorted(set(raw_lin))
            else:
                # For count_rows without column, lineage is via group_by
                # Use group_by lineage as fallback
                if met.function == "count_rows":
                    # count_rows has no column, but still should have raw lineage via group_by
                    gb_lin: list[str] = []
                    for pc in out.group_by:
                        if pc.source in src_lineage:
                            gb_lin.extend(src_lineage[pc.source])
                    out_line[met.name] = (
                        sorted(set(gb_lin)) if gb_lin else [f"{out.source}.{met.name}"]
                    )
                else:
                    out_line[met.name] = [f"{out.source}.{met.name}"]
        output_schemas[out.name] = out_schema
        output_lineage[out.name] = out_line

    # Assertions
    for idx, a in enumerate(scenario.tests):
        base = f"tests[{idx}]"
        if (
            a.model not in raw_by_name
            and a.model not in staging_by_name
            and a.model not in intermediate_by_name
            and a.model not in output_by_name
        ):
            _add_issue(
                issues,
                ErrorCode.MISSING_REF,
                f"{base}.model",
                f"assertion model '{a.model}' does not exist",
                related=a.model,
            )
            continue
        # Check columns existence against model's schema
        model_schema = None
        if a.model in raw_col_type:
            model_schema = raw_col_type[a.model]
        elif a.model in staging_schema:
            model_schema = staging_schema[a.model]
        elif a.model in intermediate_schema:
            model_schema = intermediate_schema[a.model]
        elif a.model in output_schemas:
            model_schema = output_schemas[a.model]
        if model_schema is not None:
            # For not_null, unique, accepted_values, relationships, column_range
            cols_to_check: list[str] = []
            if hasattr(a, "columns"):
                cols_to_check.extend(getattr(a, "columns", []))
            if hasattr(a, "column"):
                cols_to_check.append(getattr(a, "column"))
            for c in cols_to_check:
                if c not in model_schema:
                    _add_issue(
                        issues,
                        ErrorCode.MISSING_REF,
                        f"{base}",
                        f"assertion column '{c}' not in model '{a.model}'",
                        related=c,
                    )
            # For relationships to_columns and to_model
            if hasattr(a, "to_columns"):
                to_model = getattr(a, "to_model", None)
                # Check to_model exists (including raw)
                if (
                    to_model not in raw_by_name
                    and to_model not in staging_by_name
                    and to_model not in intermediate_by_name
                    and to_model not in output_by_name
                    and to_model not in raw_col_type
                    and to_model not in staging_schema
                    and to_model not in intermediate_schema
                    and to_model not in output_schemas
                ):
                    # Check raw, staging, intermediate, output – if not found, report
                    if (
                        to_model not in raw_by_name
                        and to_model not in staging_schema
                        and to_model not in intermediate_schema
                        and to_model not in output_schemas
                    ):
                        _add_issue(
                            issues,
                            ErrorCode.MISSING_REF,
                            f"{base}.to_model",
                            f"to_model '{to_model}' does not exist",
                            related=to_model,
                        )
                    to_schema = None
                else:
                    to_schema = None
                    if to_model in raw_col_type:
                        to_schema = raw_col_type[to_model]
                    elif to_model in staging_schema:
                        to_schema = staging_schema[to_model]
                    elif to_model in intermediate_schema:
                        to_schema = intermediate_schema[to_model]
                    elif to_model in output_schemas:
                        to_schema = output_schemas[to_model]
                    if to_schema is not None:
                        for c in getattr(a, "to_columns", []):
                            if c not in to_schema:
                                _add_issue(
                                    issues,
                                    ErrorCode.MISSING_REF,
                                    f"{base}.to_columns",
                                    f"to_column '{c}' not in model '{to_model}'",
                                    related=c,
                                )
                        # Check source/target types are exactly equal for relationships assertion
                        if model_schema is not None and to_schema is not None:
                            cols = getattr(a, "columns", [])
                            to_cols = getattr(a, "to_columns", [])
                            if len(cols) == len(to_cols):
                                for sc, tc in zip(cols, to_cols):
                                    stype = model_schema.get(sc)
                                    ttype = to_schema.get(tc)
                                    if stype is not None and ttype is not None and stype != ttype:
                                        _add_issue(
                                            issues,
                                            ErrorCode.RELATIONSHIP_TYPE,
                                            f"{base}",
                                            f"relationships assertion type mismatch '{sc}' ({stype.value}) vs '{tc}' ({ttype.value})",
                                            related=a.name,
                                        )
            # Check accepted_values values type matches column type
            if getattr(a, "type", None) == "accepted_values":
                col = getattr(a, "column", None)
                if col is not None and model_schema is not None:
                    col_type = model_schema.get(col)
                    if col_type is not None:
                        for v in getattr(a, "values", []):
                            actual = (
                                "string"
                                if isinstance(v, str)
                                else "integer"
                                if isinstance(v, int) and not isinstance(v, bool)
                                else "float"
                                if isinstance(v, float)
                                else "boolean"
                                if isinstance(v, bool)
                                else "unknown"
                            )
                            if actual != "unknown" and col_type.value != actual:
                                # For string column, actual must be string, etc.
                                # Allow numeric promotion? For now strict
                                if col_type.value != actual:
                                    _add_issue(
                                        issues,
                                        ErrorCode.CATEGORICAL_HOMOGENEOUS,
                                        f"{base}.values",
                                        f"accepted_values value '{v}' type {actual} != column '{col}' type {col_type.value}",
                                        related=str(v),
                                    )
                                    break
            if getattr(a, "type", None) == "column_range":
                col = getattr(a, "column", None)
                if col is not None and model_schema is not None:
                    col_type = model_schema.get(col)
                    if col_type is not None:
                        for bound_name in ("min", "max"):
                            bound_val = getattr(a, bound_name, None)
                            if bound_val is not None:
                                actual = (
                                    "string"
                                    if isinstance(bound_val, str)
                                    else "integer"
                                    if isinstance(bound_val, int) and not isinstance(bound_val, bool)
                                    else "float"
                                    if isinstance(bound_val, float)
                                    else "boolean"
                                    if isinstance(bound_val, bool)
                                    else "unknown"
                                )
                                if actual != "unknown" and col_type.value != actual:
                                    _add_issue(
                                        issues,
                                        ErrorCode.UNKNOWN,
                                        f"{base}.{bound_name}",
                                        f"column_range {bound_name} type {actual} != column '{col}' type {col_type.value}",
                                        related=str(bound_val),
                                    )
    # Check duplicate/contradictory assertions
    # Build map from (model, normalized assertion) to count
    seen_assertions: dict[tuple, list[str]] = {}
    for a in scenario.tests:
        # Normalize: for not_null unique, key is (model, type, tuple(sorted columns)) – but order matters for unique
        if a.type in ("not_null", "unique"):
            key = (a.model, a.type, tuple(a.columns))
        elif a.type == "accepted_values":
            key = (a.model, a.type, a.column, tuple(a.values))
        elif a.type == "relationships":
            key = (a.model, a.type, tuple(a.columns), a.to_model, tuple(a.to_columns))
        elif a.type == "row_count":
            key = (a.model, a.type, a.min, a.max)
        elif a.type == "column_range":
            key = (a.model, a.type, a.column, a.min, a.max, a.inclusive)
        else:
            key = (a.model, a.type)
        if key in seen_assertions:
            _add_issue(
                issues,
                ErrorCode.CONTRADICTORY_ASSERTION,
                f"tests[{a.name}]",
                f"duplicate assertion with same effective meaning as '{seen_assertions[key][0]}'",
                related=a.name,
            )
        else:
            seen_assertions[key] = [a.name]
    # Also check contradictory: e.g., not_null vs column_range with same column but contradictory? For simplicity, check that if we have not_null on column and also column_range that allows null? Not needed
    # Check explicit vs derived: for each explicit assertion, check if it duplicates a derived one
    # Derived will be generated below – for now, just check that explicit assertions don't exactly duplicate derived
    # We'll generate derived first, then check

    # §17.8 Connectivity
    dep_map: dict[str, list[str]] = {}
    for s in scenario.staging_models:
        dep_map[s.name] = [s.source]
    for m in scenario.intermediate_models:
        deps: list[str] = []
        if hasattr(m, "source"):
            deps.append(m.source)
        if hasattr(m, "left"):
            deps.extend([m.left, m.right])
        dep_map[m.name] = deps
    for o in scenario.output_models:
        dep_map[o.name] = [o.source]
    reachable: set[str] = set()
    queue = deque([o.name for o in scenario.output_models])
    visited = set()
    while queue:
        cur = queue.popleft()
        if cur in visited:
            continue
        visited.add(cur)
        reachable.add(cur)
        for dep in dep_map.get(cur, []):
            if dep not in visited:
                queue.append(dep)
                reachable.add(dep)
    for s in scenario.staging_models:
        if s.name not in reachable:
            _add_issue(
                issues,
                ErrorCode.DISCONNECTED,
                f"staging_models[{s.name}]",
                f"staging model '{s.name}' is not ancestor of any output",
                related=s.name,
            )
    for m in scenario.intermediate_models:
        if m.name not in reachable:
            _add_issue(
                issues,
                ErrorCode.DISCONNECTED,
                f"intermediate_models[{m.name}]",
                f"intermediate model '{m.name}' is not ancestor of any output",
                related=m.name,
            )
    for tbl in scenario.raw_tables:
        stg_for_raw = [s for s in scenario.staging_models if s.source == tbl.name]
        if not stg_for_raw:
            continue
        if not any(s.name in reachable for s in stg_for_raw):
            _add_issue(
                issues,
                ErrorCode.DISCONNECTED,
                f"raw_tables[{tbl.name}]",
                f"raw table '{tbl.name}' does not reach any output",
                related=tbl.name,
            )

    # Check every significant column has raw lineage
    # For each output column, check lineage leads to raw
    for out_name, schema in output_schemas.items():
        for col, lineage_list in output_lineage.get(out_name, {}).items():
            # Check if lineage contains raw table
            found_raw = False
            for lin in lineage_list:
                for raw_name in raw_by_name:
                    if lin.startswith(raw_name + "."):
                        found_raw = True
            if not found_raw:
                # For count_rows, lineage is output metric itself, no raw lineage expected
                is_count_rows = any(
                    mm.name == col and mm.function == "count_rows"
                    for m in scenario.output_models
                    if m.name == out_name
                    for mm in m.metrics
                )
                if is_count_rows:
                    continue
                _add_issue(
                    issues,
                    ErrorCode.UNKNOWN,
                    f"output_models[{out_name}].lineage",
                    f"column '{col}' has no raw lineage",
                    related=col,
                )

    issues_sorted = sorted(issues, key=lambda i: (i.path, i.code))
    if issues_sorted:
        raise SemanticValidationError(issues_sorted)

    # Build derived assertions comprehensively
    derived: list[dict] = []
    # PK / grain unique + not_null
    for tbl in scenario.raw_tables:
        if tbl.primary_key:
            # unique for PK
            derived.append(
                {
                    "name": f"derived_unique_{tbl.name}",
                    "model": tbl.name,
                    "type": "unique",
                    "columns": tbl.primary_key,
                }
            )
            # not_null for each PK column
            for pk_col in tbl.primary_key:
                derived.append(
                    {
                        "name": f"derived_not_null_{tbl.name}_{pk_col}",
                        "model": tbl.name,
                        "type": "not_null",
                        "columns": (pk_col,),
                    }
                )
        for col in tbl.columns:
            if getattr(col, "unique", False):
                derived.append(
                    {
                        "name": f"derived_unique_{tbl.name}_{col.name}",
                        "model": tbl.name,
                        "type": "unique",
                        "columns": (col.name,),
                    }
                )
            if not getattr(col, "nullable", False):
                # not_null for non-nullable columns (but PK already covered)
                if col.name not in tbl.primary_key:
                    derived.append(
                        {
                            "name": f"derived_not_null_{tbl.name}_{col.name}",
                            "model": tbl.name,
                            "type": "not_null",
                            "columns": (col.name,),
                        }
                    )
    for s in scenario.staging_models:
        # grain unique + not_null
        derived.append(
            {
                "name": f"derived_unique_{s.name}",
                "model": s.name,
                "type": "unique",
                "columns": s.grain,
            }
        )
        for gcol in s.grain:
            derived.append(
                {
                    "name": f"derived_not_null_{s.name}_{gcol}",
                    "model": s.name,
                    "type": "not_null",
                    "columns": (gcol,),
                }
            )
    for m in scenario.intermediate_models:
        # grain unique + not_null for intermediate
        derived.append(
            {
                "name": f"derived_unique_{m.name}",
                "model": m.name,
                "type": "unique",
                "columns": m.grain,
            }
        )
        for gcol in m.grain:
            derived.append(
                {
                    "name": f"derived_not_null_{m.name}_{gcol}",
                    "model": m.name,
                    "type": "not_null",
                    "columns": (gcol,),
                }
            )
    # relationships
    for rel in scenario.relationships:
        # For each relationship, derive relationships assertion for dependent side
        if rel.cardinality == "one_to_many":
            derived.append(
                {
                    "name": f"derived_rel_{rel.name}",
                    "model": rel.right.table,
                    "type": "relationships",
                    "columns": rel.right.columns,
                    "to_model": rel.left.table,
                    "to_columns": rel.left.columns,
                }
            )
        elif rel.cardinality == "many_to_one":
            derived.append(
                {
                    "name": f"derived_rel_{rel.name}",
                    "model": rel.left.table,
                    "type": "relationships",
                    "columns": rel.left.columns,
                    "to_model": rel.right.table,
                    "to_columns": rel.right.columns,
                }
            )
        elif rel.cardinality == "one_to_one":
            # Find FK side and collect all FK columns for composite keys
            fk_side = None
            to_table = None
            to_cols = None
            fk_cols_list: list[str] = []
            for tbl in scenario.raw_tables:
                # Determine which side this table is on
                side_cols = None
                target_table = None
                target_cols = None
                if tbl.name == rel.left.table:
                    side_cols = rel.left.columns
                    target_table = rel.right.table
                    target_cols = rel.right.columns
                elif tbl.name == rel.right.table:
                    side_cols = rel.right.columns
                    target_table = rel.left.table
                    target_cols = rel.left.columns
                else:
                    continue
                # Collect all FK cols for this relationship on this table
                cols_for_rel = [
                    c.name
                    for c in tbl.columns
                    if getattr(getattr(c, "generator", None), "kind", None) == "foreign_key"
                    and getattr(c.generator, "relationship", None) == rel.name
                ]
                # Check if this table's FK cols match the side's columns (for composite, all must match)
                if set(cols_for_rel) == set(side_cols) and cols_for_rel:
                    fk_side = tbl.name
                    fk_cols_list = cols_for_rel
                    to_table = target_table
                    to_cols = target_cols
                    break
                # Also handle case where FK cols are subset but we still want to capture all
                if cols_for_rel:
                    # If any FK found on this side, consider it the FK side
                    if fk_side is None:
                        fk_side = tbl.name
                        fk_cols_list = cols_for_rel
                        to_table = target_table
                        to_cols = target_cols
            if fk_side and fk_cols_list:
                # Ensure order follows side_cols order
                # Sort fk_cols_list to match side_cols order if composite
                side_order = rel.left.columns if fk_side == rel.left.table else rel.right.columns
                # Reorder fk_cols_list to match side_order where possible
                ordered = [c for c in side_order if c in fk_cols_list]
                if set(ordered) != set(fk_cols_list):
                    ordered = fk_cols_list
                derived.append(
                    {
                        "name": f"derived_rel_{rel.name}",
                        "model": fk_side,
                        "type": "relationships",
                        "columns": tuple(ordered),
                        "to_model": to_table,
                        "to_columns": to_cols,
                    }
                )
            else:
                # Fallback: use right as dependent
                derived.append(
                    {
                        "name": f"derived_rel_{rel.name}",
                        "model": rel.right.table,
                        "type": "relationships",
                        "columns": rel.right.columns,
                        "to_model": rel.left.table,
                        "to_columns": rel.left.columns,
                    }
                )
        elif rel.cardinality == "many_to_many":
            # For M:N, derive for bridge
            bridge = getattr(rel, "bridge", None)
            if bridge:
                derived.append(
                    {
                        "name": f"derived_rel_{rel.name}_left",
                        "model": bridge.table,
                        "type": "relationships",
                        "columns": bridge.left_columns,
                        "to_model": rel.left.table,
                        "to_columns": rel.left.columns,
                    }
                )
                derived.append(
                    {
                        "name": f"derived_rel_{rel.name}_right",
                        "model": bridge.table,
                        "type": "relationships",
                        "columns": bridge.right_columns,
                        "to_model": rel.right.table,
                        "to_columns": rel.right.columns,
                    }
                )
    # output non-empty
    for out in scenario.output_models:
        derived.append(
            {
                "name": f"derived_row_count_{out.name}",
                "model": out.name,
                "type": "row_count",
                "min": 1,
            }
        )

    # Check explicit vs derived duplicate – if explicit duplicates a derived, report
    # We already have seen_assertions for explicit, but we should check against derived
    # For simplicity, if explicit assertion equals derived (same model, type, columns...), report
    derived_keys: set[tuple] = set()
    for d in derived:
        # Normalize
        if d["type"] in ("not_null", "unique"):
            key = (d["model"], d["type"], tuple(d["columns"]))
        elif d["type"] == "relationships":
            key = (
                d["model"],
                d["type"],
                tuple(d["columns"]),
                d["to_model"],
                tuple(d["to_columns"]),
            )
        elif d["type"] == "row_count":
            # non-empty is min=1, explicit with min=1 would be duplicate? But explicit with min=1 max maybe different
            key = (d["model"], d["type"])
        else:
            key = (d["model"], d["type"])
        derived_keys.add(key)
    for a in scenario.tests:
        if a.type in ("not_null", "unique"):
            key = (a.model, a.type, tuple(a.columns))
            if key in derived_keys:
                _add_issue(
                    issues,
                    ErrorCode.CONTRADICTORY_ASSERTION,
                    f"tests[{a.name}]",
                    f"explicit assertion duplicates derived assertion for '{a.model}'",
                    related=a.name,
                )
                continue
        elif a.type == "relationships":
            key = (a.model, a.type, tuple(a.columns), a.to_model, tuple(a.to_columns))
            if key in derived_keys:
                _add_issue(
                    issues,
                    ErrorCode.CONTRADICTORY_ASSERTION,
                    f"tests[{a.name}]",
                    f"explicit assertion duplicates derived assertion for '{a.model}'",
                    related=a.name,
                )
                continue
        elif a.type == "row_count":
            # Duplicate: explicit row_count with min=1 and no max duplicates derived non-empty
            if getattr(a, "min", None) == 1 and getattr(a, "max", None) is None:
                key = (a.model, "row_count")
                if key in derived_keys:
                    _add_issue(
                        issues,
                        ErrorCode.CONTRADICTORY_ASSERTION,
                        f"tests[{a.name}]",
                        f"explicit assertion duplicates derived assertion for '{a.model}'",
                        related=a.name,
                    )
                    continue
            # Contradictory: derived is min=1 (non-empty), explicit with max=0 contradicts (says max 0 but derived says at least 1)
            # Also explicit with max <1 is contradictory
            if getattr(a, "max", None) is not None and getattr(a, "max") < 1:
                key = (a.model, "row_count")
                if key in derived_keys:
                    _add_issue(
                        issues,
                        ErrorCode.CONTRADICTORY_ASSERTION,
                        f"tests[{a.name}]",
                        f"explicit row_count max {a.max} contradicts derived min=1 for '{a.model}'",
                        related=a.name,
                    )
                    continue
            # Also check explicit min > derived max? Derived has no max, so not needed
            continue
        else:
            continue

    # If we added issues for duplicate with derived, need to re-sort and raise if any
    if any(i.code == ErrorCode.CONTRADICTORY_ASSERTION for i in issues):
        # Re-sort and raise
        issues_sorted = sorted(issues, key=lambda i: (i.path, i.code))
        raise SemanticValidationError(issues_sorted)

    # Build lineage with raw lineage
    full_lineage: dict[str, dict[str, list[str]]] = {}
    # staging lineage already has raw lineage
    for k, v in staging_lineage.items():
        full_lineage[k] = v
    for k, v in intermediate_lineage.items():
        full_lineage[k] = v
    for k, v in output_lineage.items():
        full_lineage[k] = v
    # Also raw lineage is itself
    for tbl in scenario.raw_tables:
        full_lineage[tbl.name] = {c.name: [f"{tbl.name}.{c.name}"] for c in tbl.columns}

    # Resolved schemas and grains
    resolved_grains: dict[str, tuple] = {}
    for s in scenario.staging_models:
        resolved_grains[s.name] = s.grain
    for m in scenario.intermediate_models:
        resolved_grains[m.name] = m.grain
    for o in scenario.output_models:
        resolved_grains[o.name] = o.grain

    resolved_keys: dict[str, tuple] = {}
    for tbl in scenario.raw_tables:
        resolved_keys[tbl.name] = tbl.primary_key

    # Wrap dicts to make ValidatedScenario deeply immutable (frozen only superficially)
    def _freeze_dict(d: dict) -> MappingProxyType:
        return MappingProxyType(dict(d))

    def _freeze_nested(d: dict) -> MappingProxyType:
        return MappingProxyType({k: MappingProxyType(dict(v)) if isinstance(v, dict) else v for k, v in d.items()})

    return ValidatedScenario(
        scenario=scenario,
        raw_by_name=_freeze_dict(raw_by_name),
        staging_by_name=_freeze_dict(staging_by_name),
        intermediate_by_name=_freeze_dict(intermediate_by_name),
        output_by_name=_freeze_dict(output_by_name),
        relationships_by_name=_freeze_dict(rel_by_name),
        topological_order=tuple(topo),
        lineage=_freeze_nested(full_lineage),
        derived_assertions=tuple(derived),
        staging_schemas=_freeze_nested(staging_schema),
        intermediate_schemas=_freeze_nested(intermediate_schema),
        output_schemas=_freeze_nested(output_schemas),
        resolved_grains=_freeze_dict(resolved_grains),
        resolved_keys=_freeze_dict(resolved_keys),
    )
